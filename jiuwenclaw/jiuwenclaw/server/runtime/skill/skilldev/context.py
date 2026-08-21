# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillDevContext — 每個階段的執行上下文.

Context 不是 Agent，它是每階段 StageHandler 的執行環境：
- 持有 deps（外部依賴）和 state（執行時狀態）的引用
- 提供 emit() 向前端推送事件
- 提供 create_stage_agent() 為當前階段建立隔離的 ReActAgent

每階段獨立 Agent 的核心價值：
    - 工具隔離：PLAN 只有搜尋，GENERATE 才有檔案寫入
    - Prompt 隔離：每階段有焦點明確的專屬 system prompt
    - 記憶體隔離：階段結束 Agent 即釋放，無殘留上下文
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, AsyncIterator

from jiuwenclaw.server.runtime.skill.skilldev.deps import SkillDevDeps
from jiuwenclaw.server.runtime.skill.skilldev.schema import (
    SkillDevEvent,
    SkillDevEventType,
    SkillDevState,
)

logger = logging.getLogger(__name__)


class SkillDevContext:
    """階段執行上下文.

    由 Pipeline 在每階段入口建立，傳遞給 StageHandler.execute()。
    """

    def __init__(
        self,
        task_id: str,
        deps: SkillDevDeps,
        state: SkillDevState,
        workspace: Path,
        event_queue: asyncio.Queue,
    ) -> None:
        self.task_id = task_id
        self.deps = deps
        self.state = state
        self.workspace = workspace
        self._event_queue = event_queue

    async def emit(self, event_type: SkillDevEventType, payload: dict) -> None:
        """向前端推送一個事件（放入 Pipeline 的事件佇列）."""
        event = SkillDevEvent(
            event_type=event_type,
            payload={"task_id": self.task_id, **payload},
            task_id=self.task_id,
        )
        await self._event_queue.put(event)

    @staticmethod 
    def create_stage_agent(
        stage_name: str,
        system_prompt: str,
        tools: list[str] | None = None,
        max_iterations: int = 20,
    ):
        """為當前階段建立隔離的 ReActAgent.

        Args:
            stage_name:     階段標識，用於 agent 命名（除錯/日誌用）
            system_prompt:  該階段專屬的 system prompt
            tools:          工具名白名單，如 ["file_read", "file_write", "web_search"]
            max_iterations: ReAct 最大迴圈次數

        Returns:
            配置完畢的 ReActAgent 例項（尚未執行）

        待實現: 接入 openjiuwen ReActAgent 的實際構造邏輯，參考 JiuWenClaw.create_instance()
        """
        # 待實現: 實際實現
        # from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig
        # from openjiuwen.core.runner import Runner
        # from jiuwenclaw.agentserver.react_agent import JiuClawReActAgent
        #
        # agent_card = AgentCard(name=f"skilldev_{self.task_id}_{stage_name}")
        # agent = JiuClawReActAgent(agent_card)
        # config = ReActAgentConfig(
        #     model_name=self.deps.model_name,
        #     model_client_config=self.deps.model_client_config,
        #     max_iterations=max_iterations,
        #     prompt_template=[{"role": "system", "content": system_prompt}],
        # )
        # agent.configure(config)
        # if tools:
        #     self._register_tools(agent, tools)
        # return agent
        logger.info(
            "[SkillDevContext] create_stage_agent: stage=%s tools=%s max_iterations=%d",
            stage_name,
            tools,
            max_iterations,
        )
        raise NotImplementedError("create_stage_agent 尚未接入 openjiuwen，待實現")

    def _register_tools(self, agent, tool_names: list[str]) -> None:
        """根據工具名白名單將工具註冊到 Agent.

        待實現: 接入實際工具註冊邏輯
        """
        # file_read / file_write / shell → 由 SysOperationCard 提供
        # web_search 等 → 從 MCP 工具中篩選
        raise NotImplementedError("_register_tools 尚未實現")
