"""仿真社会编排模块。

本模块提供 :class:`AgentSociety` 类，是 AgentSociety2 框架的核心编排器，
负责协调智能体和环境模块的仿真运行。

主要功能：

- **仿真初始化**: 初始化智能体、环境路由器和回放写入器
- **时间推进**: 通过 ``step()`` 和 ``run()`` 方法推进仿真时间
- **交互接口**: 提供 ``ask()`` 和 ``intervene()`` 方法与仿真交互
- **状态持久化**: 支持 ``dump()`` 和 ``load()`` 保存和恢复仿真状态

Example::

    from datetime import datetime
    from pathlib import Path
    from agentsociety2.society import AgentSociety

    # 创建仿真
    society = AgentSociety(
        agents=[agent1, agent2],
        env_router=router,
        start_t=datetime.now(),
        run_dir=Path("./run"),
    )

    # 使用上下文管理器运行
    async with society:
        await society.run(num_steps=100, tick=3600)
        answer = await society.ask("当前有多少智能体？")
"""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Sequence

from agentsociety2.env import RouterBase
from agentsociety2.agent import AgentBase
from agentsociety2.society.helper import AgentSocietyHelper
from agentsociety2.society.questionnaire import (
    Questionnaire,
    QuestionnaireResponse,
    QuestionnaireRunner,
)
from agentsociety2.storage import (
    ColumnDef,
    ReplayDatasetSpec,
    ReplayWriter,
    TableSchema,
)
from agentsociety2.storage.replay_metadata import (
    AGENT_PROFILE_DATASET_CAPABILITY,
    AGENT_PROFILE_DATASET_ID,
    AGENT_PROFILE_TABLE_NAME,
)

__all__ = ["AgentSociety"]


def _json_safe_profile(profile: Any) -> dict[str, Any]:
    """Convert an arbitrary profile payload into a JSON-safe dict."""
    if not isinstance(profile, dict):
        profile = {"raw": str(profile)}
    try:
        return json.loads(json.dumps(profile, ensure_ascii=False, default=str))
    except Exception:
        return {"raw": str(profile)}


class AgentSociety:
    """仿真社会编排器，协调智能体和环境模块的仿真运行。

    AgentSociety 是框架的核心类，负责管理仿真生命周期：

    - 初始化智能体和环境模块
    - 推进仿真时间
    - 处理外部问答和干预请求
    - 持久化仿真状态

    Attributes:
        current_time: 当前仿真时间
        step_count: 已执行的仿真步数

    Example::

        from datetime import datetime
        from pathlib import Path
        from agentsociety2.society import AgentSociety

        society = AgentSociety(
            agents=[agent1, agent2],
            env_router=router,
            start_t=datetime.now(),
            run_dir=Path("./run"),
        )

        async with society:
            await society.run(num_steps=100, tick=3600)
    """

    def __init__(
        self,
        agents: Sequence[AgentBase],
        env_router: RouterBase,
        start_t: datetime,
        run_dir: Optional[Path] = None,
        enable_replay: bool = True,
        replay_writer: Optional[ReplayWriter] = None,
    ):
        """创建仿真编排器。

        :param agents: 智能体列表。
        :param env_router: 环境路由器。
        :param start_t: 仿真开始时间。
        :param run_dir: 可选。运行目录（用于落地回放 sqlite 等）。
        :param enable_replay: 是否启用回放记录。
        :param replay_writer: 可选。外部传入的回放写入器；若提供则不会在 :meth:`init` 内部创建。
            该写入器仅用于环境模块回放。
        """
        self._env_router = env_router
        self._agents = list(agents)
        self._t = start_t
        self._should_terminate: bool = False
        self._step_count: int = 0

        self._run_dir = run_dir
        self._enable_replay = enable_replay
        self._replay_writer: Optional[ReplayWriter] = replay_writer
        self._agent_profiles_persisted = False
        self._questionnaire_runner = QuestionnaireRunner()

        self._helper = AgentSocietyHelper(
            env_router=self._env_router,
            agents=self._agents,
        )

    @property
    def current_time(self) -> datetime:
        """:returns: 当前仿真时间。"""
        return self._t

    @property
    def step_count(self) -> int:
        """:returns: 已执行的仿真步数。"""
        return self._step_count

    async def _persist_agent_profiles_once(self) -> None:
        if self._replay_writer is None or self._agent_profiles_persisted:
            return

        columns = [
            ColumnDef(
                "id",
                "INTEGER",
                nullable=False,
                logical_type="entity_id",
                description="Unique agent identifier.",
            ),
            ColumnDef(
                "name",
                "TEXT",
                nullable=False,
                logical_type="label",
                description="Agent display name.",
            ),
            ColumnDef(
                "profile",
                "JSON",
                nullable=False,
                logical_type="json",
                description="Static agent profile payload captured at simulation init.",
            ),
            ColumnDef(
                "created_at",
                "TIMESTAMP",
                nullable=False,
                logical_type="timestamp",
                description="When the agent profile snapshot was persisted.",
            ),
        ]
        await self._replay_writer.register_table(
            TableSchema(
                name=AGENT_PROFILE_TABLE_NAME,
                columns=columns,
                primary_key=["id"],
                indexes=[["name"]],
            )
        )
        await self._replay_writer.register_dataset(
            ReplayDatasetSpec(
                dataset_id=AGENT_PROFILE_DATASET_ID,
                table_name=AGENT_PROFILE_TABLE_NAME,
                module_name="AgentSociety",
                kind="entity_static",
                title="Agent Profiles",
                description="Static agent profiles persisted once when the simulation initializes.",
                entity_key="id",
                default_order=["id"],
                capabilities=[AGENT_PROFILE_DATASET_CAPABILITY, "entity_static"],
            ),
            columns,
        )

        rows = [
            {
                "id": agent.id,
                "name": agent.name,
                "profile": _json_safe_profile(agent.get_profile()),
                "created_at": self._t,
            }
            for agent in self._agents
        ]
        if rows:
            await self._replay_writer.write_batch(AGENT_PROFILE_TABLE_NAME, rows)
        self._agent_profiles_persisted = True

    async def init(self):
        # Replay writer: use provided one or create it before env init so
        # modules that register replay tables during init see a ready writer.
        if (
            self._replay_writer is None
            and self._enable_replay
            and self._run_dir is not None
        ):
            db_path = self._run_dir / "sqlite.db"
            self._replay_writer = ReplayWriter(db_path)
            await self._replay_writer.init()

        if self._replay_writer is not None:
            await self._persist_agent_profiles_once()
            self._env_router.set_replay_writer(self._replay_writer)

        await self._env_router.init(self._t)
        for agent in self._agents:
            await agent.init(env=self._env_router)

    async def add_agents(self, agents: Sequence[AgentBase]) -> None:
        """Add initialized agents to a live society before the next step."""
        existing_ids = {agent.id for agent in self._agents}
        new_agents = [agent for agent in agents if agent.id not in existing_ids]
        if not new_agents:
            return

        for agent in new_agents:
            await agent.init(env=self._env_router)
            self._agents.append(agent)

        if self._replay_writer is not None:
            rows = [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "profile": _json_safe_profile(agent.get_profile()),
                    "created_at": self._t,
                }
                for agent in new_agents
            ]
            await self._replay_writer.write_batch(AGENT_PROFILE_TABLE_NAME, rows)

    async def close(self):
        for agent in self._agents:
            await agent.close()
        await self._env_router.close()

        # Close replay writer
        if self._replay_writer is not None:
            await self._replay_writer.close()
            self._replay_writer = None

    # context manager
    async def __aenter__(self):
        await self.init()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()

    async def step(self, tick: int):
        """推进一次仿真步（先 agents 后 env）。

        :param tick: 本步时间跨度（秒）。
        """
        tasks = []
        for agent in self._agents:
            tasks.append(agent.step(tick, self._t))
        await asyncio.gather(*tasks)
        await self._env_router.step(tick, self._t)
        self._t += timedelta(seconds=tick)
        self._env_router.sync_simulation_clock(self._t)
        self._step_count += 1

    async def run(self, num_steps: int, tick: int):
        """运行多步仿真。

        :param num_steps: 运行步数上限。
        :param tick: 每步时间跨度（秒）。
        """
        for _ in range(num_steps):
            if self._should_terminate:
                break
            await self.step(tick)

    async def ask(self, question: str) -> str:
        """向仿真系统提问（由 helper 协调 agents/env 作答）。

        :param question: 问题文本。
        :returns: 答案文本。
        """
        return await self._helper.ask(question)

    async def intervene(self, instruction: str) -> str:
        """对仿真进行干预（由 helper 协调执行）。

        :param instruction: 干预指令文本。
        :returns: 执行结果/反馈文本。
        """
        return await self._helper.intervene(instruction)

    async def run_questionnaire(
        self,
        questionnaire: Questionnaire,
        target_agent_ids: list[int] | None = None,
    ) -> QuestionnaireResponse:
        """向目标 agents 发放问卷并返回结构化结果。"""
        return await self._questionnaire_runner.run(
            questionnaire,
            self._agents,
            t=self._t,
            step_count=self._step_count,
            target_agent_ids=target_agent_ids,
        )

    # ---- Dump & Load ----
    async def dump(self) -> dict:
        """导出可序列化的仿真状态。

        :returns: 包含 ``society``、``env_router``、``agents`` 的字典。
        """
        agents_dump: list[dict] = []
        for a in self._agents:
            try:
                agents_dump.append(
                    {
                        "class": a.__class__.__name__,
                        "id": a.id,
                        "dump": await a.dump(),
                    }
                )
            except Exception:
                continue

        return {
            "society": {
                "t": self._t.isoformat(),
            },
            "env_router": await self._env_router.dump(),
            "agents": agents_dump,
        }

    async def load(self, dump_data: dict):
        """从 :meth:`dump` 的输出恢复仿真状态。

        :param dump_data: 由 :meth:`dump` 产生的字典。
        """
        try:
            soc = dump_data.get("society") or {}
            t_str = soc.get("t")
            if isinstance(t_str, str) and len(t_str) > 0:
                from datetime import datetime as _dt

                self._t = _dt.fromisoformat(t_str)
        except Exception:
            pass

        # env router
        env_dump = dump_data.get("env_router") or {}
        if isinstance(env_dump, dict):
            try:
                await self._env_router.load(env_dump)
            except Exception:
                pass

        # agents: map by (class, id)
        by_key = {}
        for a in self._agents:
            by_key[(a.__class__.__name__, a.id)] = a
        for item in dump_data.get("agents", []) or []:
            try:
                key = (item.get("class"), item.get("id"))
                a = by_key.get(key)
                if a is None:
                    continue
                d = item.get("dump") or {}
                await a.load(d)
            except Exception:
                continue
