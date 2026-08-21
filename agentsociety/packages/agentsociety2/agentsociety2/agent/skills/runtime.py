"""Agent 技能執行時（workspace + skill 執行）。

該模組提供 :class:`~agentsociety2.agent.skills.runtime.AgentSkillRuntime`，用於把 PersonAgent 的
"工作目錄隔離、檔案讀寫、thread/tool 日誌、skill 啟用與執行"等細節集中在一個元件內，
避免 agent 主體過度膨脹。

模組功能
========

- **Workspace 管理**: 獨立工作區、檔案讀寫、路徑安全檢查
- **Skill 執行**: 技能啟用、讀取、執行
- **日誌管理**: Thread 訊息、工具呼叫、會話狀態持久化
- **上下文維護**: AGENT.md 自動更新、狀態同步
- **行為追蹤**: 模擬行為事件記錄與統計
- **檔案發現**: 在 AGENT.md 中自動維護檔案索引

類結構
======

- :class:`AgentSkillRuntime`: 主執行時類

示例
====

基本使用::

    from agentsociety2.agent.skills import SkillRegistry
    from agentsociety2.agent.skills.runtime import AgentSkillRuntime

    registry = SkillRegistry()
    registry.scan_builtin()

    runtime = AgentSkillRuntime(agent_id=1, registry=registry)
    runtime.ensure_agent_work_dir(env_router)

    # 檔案操作
    runtime.workspace_write("state/test.json", '{"key": "value"}')
    content = runtime.workspace_read("state/test.json")

    # 狀態同步
    runtime.sync_state_to_context()

    # 檔案清單
    runtime.write_file_manifest()
"""

from __future__ import annotations
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from agentsociety2.agent.skills import SkillRegistry
from agentsociety2.agent.tool import jr_dumps, jr_parse

logger = logging.getLogger(__name__)

AGENT_DOCUMENT = "AGENT.md"
RUNTIME_DIR = ".runtime"
RUNTIME_LOG_DIR = f"{RUNTIME_DIR}/logs"
FILE_INDEX_START = "<!-- AGENT_FILE_INDEX_START -->"
FILE_INDEX_END = "<!-- AGENT_FILE_INDEX_END -->"


class AgentSkillRuntime:
    """獨立的 Skill 執行時元件。

    PersonAgent 僅透過組合使用該元件，避免把 skill/workspace 執行細節堆在 agent 主體裡。

    :ivar _agent_id: Agent ID。
    :ivar _registry: Skill 登錄檔。
    :ivar _agent_work_dir: Agent 工作目錄。
    :ivar _state_config: 狀態檔案配置（可選）。
    """

    def __init__(
        self,
        agent_id: int,
        registry: SkillRegistry,
        state_config: Any = None,
    ) -> None:
        """初始化執行時。

        :param agent_id: Agent ID。
        :param registry: Skill 登錄檔。
        :param state_config: 狀態檔案配置（StateConfig 例項）。
        """
        self._agent_id = agent_id
        self._registry = registry
        self._agent_work_dir: Path | None = None
        self._state_config = state_config

    def ensure_agent_work_dir(self, env_obj: Any) -> Path:
        """確保 agent 工作目錄已初始化並返回其路徑。

        :param env_obj: 通常為 env_router；若其包含 ``run_dir`` 屬性則以其為基準目錄，
            否則退化為當前工作目錄。
        :returns: agent 工作目錄路徑（形如 ``<run_dir>/agents/agent_0001``）。
        """
        if self._agent_work_dir is not None:
            return self._agent_work_dir

        # 優先從 env_router 獲取 run_dir
        run_dir = getattr(env_obj, "run_dir", None)
        if run_dir is not None:
            base_path = Path(run_dir)
        else:
            base_path = Path.cwd()

        self._agent_work_dir = (
            base_path / "agents" / f"agent_{self._agent_id:04d}"
        ).resolve()
        self._agent_work_dir.mkdir(parents=True, exist_ok=True)
        return self._agent_work_dir

    def ensure_standard_workspace_dirs(self) -> None:
        """確保 workspace 標準目錄結構存在。

        建立以下目錄：
        - ``state/`` - Skills 狀態檔案
        - ``memory/`` - 長期記憶
        - ``input/`` - 外部輸入
        - ``custom/skills/`` - 自定義技能
        - ``.runtime/logs/`` - 執行時日誌
        """
        if self._agent_work_dir is None:
            raise RuntimeError("Agent workspace is not initialized")

        standard_dirs = ["state", "memory", "input", "custom/skills", RUNTIME_LOG_DIR]
        for dir_name in standard_dirs:
            dir_path = self._agent_work_dir / dir_name
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(
                    f"Agent {self._agent_id}: failed to create workspace dir '{dir_name}': {e}"
                )

    def _resolve_workspace_path(self, relative_path: str) -> Path:
        """將相對路徑解析到 workspace 內並做越界保護。"""
        if self._agent_work_dir is None:
            raise RuntimeError("Agent workspace is not initialized")
        work_dir = self._agent_work_dir
        target = (work_dir / relative_path).resolve()
        if target != work_dir and work_dir not in target.parents:
            raise ValueError(f"Path escapes agent workspace: {relative_path}")
        return target

    def workspace_root(self) -> Path:
        """:returns: workspace 根目錄路徑。"""
        if self._agent_work_dir is None:
            raise RuntimeError("Agent workspace is not initialized")
        return self._agent_work_dir

    def workspace_read(self, relative_path: str) -> str:
        """讀取 workspace 內檔案內容。

        :param relative_path: 相對 workspace 的路徑。
        :returns: 檔案文字內容；若檔案不存在則返回空字串。
        """
        target = self._resolve_workspace_path(relative_path)
        if not target.exists() or not target.is_file():
            return ""
        return target.read_text(encoding="utf-8")

    def workspace_write(self, relative_path: str, content: str) -> str:
        """寫入 workspace 內檔案（UTF-8）。

        :param relative_path: 相對 workspace 的路徑。
        :param content: 寫入內容。
        :returns: 實際寫入的絕對路徑字串。
        """
        target = self._resolve_workspace_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return str(target)

    def workspace_exists(self, relative_path: str) -> bool:
        """:returns: workspace 內路徑是否存在。"""
        target = self._resolve_workspace_path(relative_path)
        return target.exists()

    def workspace_delete(self, relative_path: str) -> bool:
        """刪除 workspace 內檔案（僅檔案，目錄不刪除）。"""
        target = self._resolve_workspace_path(relative_path)
        if not target.exists() or target.is_dir():
            return False
        target.unlink()
        return True

    def workspace_list(self, relative_path: str = ".") -> list[str]:
        """列出 workspace 內檔案（遞迴）。

        :param relative_path: 相對 workspace 的根路徑。
        :returns: 檔案相對路徑列表（相對 workspace 根）。
        """
        work_dir = self.workspace_root()  # raises RuntimeError if not initialized
        root = self._resolve_workspace_path(relative_path)
        if not root.exists():
            return []
        if root.is_file():
            return [str(root.relative_to(work_dir))]
        return sorted(
            str(p.relative_to(work_dir)) for p in root.rglob("*") if p.is_file()
        )

    def skill_list(self, names: list[str]) -> list[dict[str, Any]]:
        return self._registry.list_selection_metadata(names=names, only_enabled=True)

    def skill_activate(self, name: str) -> str:
        return self._registry.activate(name)

    def skill_read(self, name: str, relative_path: str) -> str:
        return self._registry.read(name, relative_path)

    async def execute(
        self,
        skill_name: str,
        args: dict[str, Any],
        codegen_executor: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None,
    ) -> dict[str, Any]:
        """執行某個 skill（轉發到 registry）。

        :param skill_name: skill 名稱。
        :param args: 執行引數（由 skill 指令碼/協議自行定義）。
        :param codegen_executor: 可選。用於把 skill 內部的 codegen 排程回 env 的執行器。
        :returns: 執行結果字典（由 :class:`~agentsociety2.agent.skills.SkillRegistry` 約定）。
        :raises RuntimeError: workspace 未初始化時丟擲。
        """
        if self._agent_work_dir is None:
            raise RuntimeError("Agent workspace is not initialized")
        work_dir = self._agent_work_dir
        return await self._registry.execute(
            skill_name=skill_name,
            args=args,
            agent_work_dir=work_dir,
            codegen_executor=codegen_executor,
        )

    def persist_session_state(
        self,
        tick: int,
        t: datetime,
        selected_skills: set[str],
        activated_skills: set[str] | None = None,
        token_usage: dict[str, Any] | None = None,
        runtime_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """落地當前會話狀態到 workspace，並追加到歷史記錄。

        :param tick: 當前 tick。
        :param t: 當前模擬時間。
        :param selected_skills: 本步可見/可選技能集合。
        :param activated_skills: 可選。已啟用技能集合。
        :param token_usage: 可選。按模型統計的累計 token 使用量。
        :param runtime_snapshot: 可選。面向除錯/前端展示的完整 agent 快照。
        """
        state = {
            "agent_id": self._agent_id,
            "tick": tick,
            "time": t.isoformat(),
            "selected_skills": sorted(selected_skills),
            "activated_skills": sorted(activated_skills or set()),
            "token_usage": token_usage or {},
        }
        if runtime_snapshot:
            for key in (
                "mounted_skill_ids",
                "last_skill_decision",
                "last_skill_result",
                "last_environment_effects",
                "skill_states",
            ):
                if key in runtime_snapshot:
                    state[key] = runtime_snapshot[key]
        snapshot = {
            **state,
            **(runtime_snapshot or {}),
        }
        self.workspace_write(f"{RUNTIME_LOG_DIR}/session_state.json", jr_dumps(state))
        self.workspace_write(
            f"{RUNTIME_LOG_DIR}/agent_state_snapshot.json",
            jr_dumps(snapshot, indent=2),
        )
        self.append_session_state_event(state)

    def append_session_state_event(self, state: dict[str, Any]) -> None:
        """追加 session_state 事件到 runtime 日誌。"""
        if self._agent_work_dir is None:
            raise RuntimeError("Agent workspace is not initialized")
        path = self._agent_work_dir / RUNTIME_LOG_DIR / "session_state_history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(jr_dumps(state, indent=None) + "\n")

    def append_tool_log(self, entry: dict[str, Any]) -> None:
        """追加單條工具呼叫日誌（jsonl）。"""
        if self._agent_work_dir is None:
            raise RuntimeError("Agent workspace is not initialized")
        log_path = self._agent_work_dir / RUNTIME_LOG_DIR / "tool_calls.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(jr_dumps(entry, indent=None) + "\n")

    def append_step_replay(
        self,
        tick: int,
        t: datetime,
        selected_skills: set[str],
        tool_history: list[dict[str, Any]],
    ) -> None:
        """追加 step 回放記錄（jsonl）。"""
        if self._agent_work_dir is None:
            raise RuntimeError("Agent workspace is not initialized")
        replay_path = self._agent_work_dir / RUNTIME_LOG_DIR / "step_replay.jsonl"
        replay_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "tick": tick,
            "time": t.isoformat(),
            "selected_skills": sorted(selected_skills),
            "tool_history": tool_history,
        }
        with replay_path.open("a", encoding="utf-8") as f:
            f.write(jr_dumps(record, indent=None) + "\n")

    def read_json(self, relative_path: str, default: Any) -> Any:
        """讀取工作目錄中的 JSON 檔案；空內容返回 default。"""
        raw = self.workspace_read(relative_path)
        if not raw:
            return default
        return jr_parse(raw)

    def read_recent_tool_logs(self, limit: int = 20) -> list[dict[str, Any]]:
        """讀取最近 N 條工具呼叫日誌。"""
        if self._agent_work_dir is None:
            raise RuntimeError("Agent workspace is not initialized")
        path = self._agent_work_dir / RUNTIME_LOG_DIR / "tool_calls.jsonl"
        if not path.exists():
            return []
        if limit > 0:
            recent_lines: deque[str] = deque(maxlen=limit)
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        recent_lines.append(line)
            source = list(recent_lines)
        else:
            with path.open("r", encoding="utf-8") as f:
                source = [line for line in f if line.strip()]
        return [jr_parse(line) for line in source]

    def append_thread_message(
        self,
        role: str,
        content: str,
        tick: int,
        t: datetime,
        *,
        tool_result_full: Optional[dict[str, Any]] = None,
    ) -> None:
        """追加 thread 訊息到 runtime thread 日誌。

        ``content`` 為餵給 LLM 的文字；若提供 ``tool_result_full``，則同條記錄落盤完整工具結果
        （讀取 thread 時仍只用 ``content`` 構造 messages）。
        """
        if self._agent_work_dir is None:
            raise RuntimeError("Agent workspace is not initialized")
        path = self._agent_work_dir / RUNTIME_LOG_DIR / "thread_messages.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "tick": tick,
            "time": t.isoformat(),
            "role": role,
            "content": content,
        }
        if tool_result_full is not None:
            entry["tool_result_full"] = tool_result_full
        with path.open("a", encoding="utf-8") as f:
            f.write(jr_dumps(entry, indent=None) + "\n")

    def read_recent_thread_messages(self, limit: int = 40) -> list[dict[str, str]]:
        """讀取最近 N 條 thread 訊息並轉換為 LLM messages 結構。"""
        if self._agent_work_dir is None:
            raise RuntimeError("Agent workspace is not initialized")
        path = self._agent_work_dir / RUNTIME_LOG_DIR / "thread_messages.jsonl"
        if not path.exists():
            return []
        if limit > 0:
            recent_lines: deque[str] = deque(maxlen=limit)
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        recent_lines.append(line)
            recent = list(recent_lines)
        else:
            with path.open("r", encoding="utf-8") as f:
                recent = [line.rstrip("\n") for line in f if line.strip()]
        messages: list[dict[str, str]] = []
        for line in recent:
            if not line.strip():
                continue
            obj = jr_parse(line)
            role = str(obj.get("role", "")).strip()
            content = str(obj.get("content", ""))
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        return messages

    def build_workspace_structure_prompt(self) -> str:
        """構建 workspace 結構說明（Claude/Cursor 風格：極簡 + 動態發現）。

        不維護複雜的 manifest/registry，只提供目錄約定，讓 Agent 自己探索。
        狀態檔案示例從配置或實際檔案動態生成。

        :returns: workspace 結構說明文字。
        """
        lines = [
            "## Workspace Structure",
            "",
            'All files are in the workspace root. Use `workspace_list(".")` to see what exists.',
            "",
            "### Generated Context Files",
            "- `AGENT.md` - concise agent context and generated file index",
            "",
            "### Directory Convention",
            "- `state/` - Skill state files (dynamically discovered)",
            "- `state/*.json` - current structured state",
            "- `state/*.jsonl` - event/history streams",
            "- `memory/` - optional long-term memory directory",
            "- `input/` - External input from environment",
            "- `.runtime/` - Runtime internals (logs, checkpoints, WAL)",
            "",
            "### Quick Discovery",
            '- `workspace_list("state/")` - See all skill state files',
            '- `workspace_read("AGENT.md")` - Read concise current context and file index',
            "",
            "Let the agent discover what it needs dynamically.",
        ]

        # 動態新增當前存在的 state 檔案
        if self._agent_work_dir is not None:
            state_dir = self._agent_work_dir / "state"
            if state_dir.exists() and state_dir.is_dir():
                state_files = sorted(
                    f.name
                    for f in state_dir.iterdir()
                    if f.is_file() and (f.suffix == ".json" or f.suffix == ".txt")
                )
                if state_files:
                    lines.append("")
                    lines.append("### Current State Files")
                    for filename in state_files:
                        lines.append(f"- `state/{filename}`")

        return "\n".join(lines)

    @staticmethod
    def _replace_generated_file_index(content: str, manifest: str) -> str:
        """替換 AGENT.md 中的自動檔案索引塊，保留人工內容。"""
        block = f"{FILE_INDEX_START}\n{manifest.rstrip()}\n{FILE_INDEX_END}"
        if FILE_INDEX_START in content and FILE_INDEX_END in content:
            before, _, rest = content.partition(FILE_INDEX_START)
            _, _, after = rest.partition(FILE_INDEX_END)
            return f"{before.rstrip()}\n\n{block}\n{after.lstrip()}".strip()
        if content.strip():
            return f"{content.rstrip()}\n\n{block}"
        return block

    def refresh_workspace_documents(self) -> None:
        """同步工作區動態文件。"""
        self.sync_state_to_context()
        self.write_file_manifest()
        self.emit_behavior_event(
            "state_sync",
            {
                "document": AGENT_DOCUMENT,
                "workspace_files": len(self.workspace_list(".")),
            },
            name="refresh_workspace_documents",
            output_summary={"document": AGENT_DOCUMENT},
        )

    # ==================== AGENT.md Support ====================

    def read_agent_context(self) -> dict[str, Any]:
        """讀取 AGENT.md 檔案內容。

        該檔案是agent的自我宣告檔案，包含當前任務、重要上下文等資訊。
        使用YAML frontmatter格式，便於程式解析。

        :returns: 解析後的上下文字典，包含metadata和content兩部分。
        """
        content = self.workspace_read(AGENT_DOCUMENT)
        if not content:
            return {"metadata": {}, "content": ""}

        return self._parse_context_md(content)

    def _parse_context_md(self, content: str) -> dict[str, Any]:
        """解析 AGENT.md 檔案（YAML frontmatter + markdown）。

        :param content: 檔案原始內容。
        :returns: {"metadata": {...}, "content": "markdown內容"}
        """
        metadata: dict[str, Any] = {}
        body = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    from ruamel.yaml import YAML

                    yaml = YAML(typ="safe")
                    metadata = yaml.load(parts[1]) or {}
                except Exception as e:
                    # 不靜默回落：記錄解析錯誤，保留 body 以便人工修復
                    logger.warning("AGENT.md YAML frontmatter parse failed: %s", e)
                    metadata = {
                        "_agent_md_parse_error": str(e),
                        "_agent_md_parse_error_type": type(e).__name__,
                    }
                body = parts[2].strip()

        return {"metadata": metadata, "content": body}

    def update_agent_context(self, updates: dict[str, Any]) -> None:
        """更新 AGENT.md（合併而非覆蓋）。

        :param updates: 要更新的metadata欄位。
        """
        existing = self.read_agent_context()
        existing["metadata"].update(updates)
        self._write_agent_context(existing["metadata"], existing["content"])

    def set_agent_context_content(self, content: str) -> None:
        """設定 AGENT.md 的內容部分（保留metadata）。

        :param content: 新的markdown內容。
        """
        existing = self.read_agent_context()
        self._write_agent_context(existing["metadata"], content)

    def _write_agent_context(self, metadata: dict[str, Any], content: str) -> None:
        """寫入 AGENT.md 檔案。"""
        from ruamel.yaml import YAML
        from io import StringIO

        yaml = YAML()
        yaml.default_flow_style = False

        stream = StringIO()
        stream.write("---\n")
        yaml.dump(metadata, stream)
        stream.write("---\n\n")
        stream.write(content)

        self.workspace_write(AGENT_DOCUMENT, stream.getvalue())

    def auto_update_agent_context(
        self,
        current_task: str | None = None,
        active_goal: str | None = None,
        priority: str | None = None,
        notes: str | None = None,
    ) -> None:
        """自動更新 AGENT.md（模擬人行為追蹤）。

        根據模擬人當前狀態自動維護上下文檔案，支援跨會話持久化。

        :param current_task: 當前任務描述。
        :param active_goal: 活躍目標。
        :param priority: 優先順序。
        :param notes: 額外備註。
        """
        updates: dict[str, Any] = {}
        if current_task is not None:
            updates["current_task"] = current_task
        if active_goal is not None:
            updates["active_goal"] = active_goal
        if priority is not None:
            updates["priority"] = priority
        if updates:
            updates["last_updated"] = datetime.now(timezone.utc).isoformat()
            self.update_agent_context(updates)

        if notes is not None:
            existing = self.read_agent_context()
            existing_content = existing.get("content", "")
            # 追加時間戳備註
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_content = (
                f"{existing_content}\n\n## [{timestamp}]\n{notes}\n"
                if existing_content
                else f"## [{timestamp}]\n{notes}\n"
            )
            # 對標 CLAUDE.md：保持簡潔，避免無限增長
            max_chars = 2000
            if self._state_config is not None:
                max_chars = int(
                    getattr(self._state_config, "agent_md_max_chars", max_chars)
                )
            self.set_agent_context_content(new_content.strip()[:max_chars])

    @staticmethod
    def _flatten_summary_value(value: Any, max_len: int = 100) -> str:
        """將狀態欄位壓縮成適合放入上下文的短文字。"""
        if value is None:
            return ""
        if isinstance(value, str):
            return value[:max_len]
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (int, float)):
            return f"{value:.3g}" if isinstance(value, float) else str(value)
        if isinstance(value, list):
            parts = [
                AgentSkillRuntime._flatten_summary_value(v, max_len=40)
                for v in value[:3]
            ]
            text = ", ".join(p for p in parts if p)
            if len(value) > 3:
                text += f", +{len(value) - 3} more"
            return text[:max_len]
        if isinstance(value, dict):
            parts: list[str] = []
            for k, v in list(value.items())[:4]:
                flattened = AgentSkillRuntime._flatten_summary_value(v, max_len=40)
                if flattened:
                    parts.append(f"{k}={flattened}")
            return "; ".join(parts)[:max_len]
        return str(value)[:max_len]

    @classmethod
    def _summarize_state_json(
        cls,
        data: Any,
        *,
        summary_field: str = "",
        max_len: int = 100,
    ) -> str:
        """從任意 JSON 狀態中提取通用短摘要。

        執行時不理解具體 skill 的欄位語義。技能若希望被穩定摘要，可寫入
        ``_summary`` 或 ``summary``；否則這裡只展示少量頂層結構，幫助定位檔案。
        """
        if not isinstance(data, dict) or not data:
            return ""
        if summary_field:
            value = data.get(summary_field)
            text = cls._flatten_summary_value(value, max_len=max_len)
            if text:
                return text

        for key in ("_summary", "summary"):
            if key in data:
                text = cls._flatten_summary_value(data[key], max_len=max_len)
                if text:
                    return text[:max_len]

        scalar_parts: list[str] = []
        structural_parts: list[str] = []
        for key, value in data.items():
            if key.startswith("_"):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                text = cls._flatten_summary_value(value, max_len=32)
                if text:
                    scalar_parts.append(f"{key}={text}")
            elif isinstance(value, list):
                structural_parts.append(f"{key}[{len(value)}]")
            elif isinstance(value, dict):
                structural_parts.append(f"{key}{{{len(value)}}}")
            if len(scalar_parts) >= 3:
                break
        if scalar_parts:
            return "; ".join(scalar_parts)[:max_len]

        if structural_parts:
            return "; ".join(structural_parts[:4])[:max_len]
        return ""

    @classmethod
    def _describe_state_json(cls, data: Any, max_len: int = 100) -> str:
        """從自描述狀態檔案中提取檔案用途。

        只讀取通用後設資料，不解釋任何具體 skill 欄位。
        推薦技能寫入 ``_meta.purpose`` 或 ``_meta.description``。
        """
        if not isinstance(data, dict):
            return ""
        meta = data.get("_meta")
        if not isinstance(meta, dict):
            return ""
        for key in ("purpose", "description", "owner", "skill"):
            text = cls._flatten_summary_value(meta.get(key), max_len=max_len)
            if text:
                return text
        return ""

    def _memory_file_candidates(self) -> list[Path]:
        """返回當前 workspace 中可能的長期記憶檔案路徑。"""
        if self._agent_work_dir is None:
            return []
        return [
            self._agent_work_dir / "state" / "memory.jsonl",
            self._agent_work_dir / "memory" / "memory.jsonl",
            self._agent_work_dir / "memory.jsonl",
        ]

    @staticmethod
    def _count_nonempty_lines(path: Path) -> int:
        """統計非空行數。"""
        try:
            with path.open("r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0

    def sync_state_to_context(self) -> None:
        """將當前狀態同步到 AGENT.md。

        只做通用檔案發現和短摘要，不理解具體 skill 的欄位語義。
        """
        if self._agent_work_dir is None:
            return

        context = self.read_agent_context()
        metadata = dict(context.get("metadata", {}) or {})
        state_summary: dict[str, str] = {}
        state_purpose: dict[str, str] = {}
        state_files_index: list[str] = []
        max_len = 100
        if self._state_config is not None:
            max_len = getattr(self._state_config, "summary_max_length", 100)

        state_dir = self._agent_work_dir / "state"
        if state_dir.exists() and state_dir.is_dir():
            state_files_index = sorted(
                p.relative_to(self._agent_work_dir).as_posix()
                for p in state_dir.rglob("*")
                if p.is_file()
                and p.suffix in {".json", ".jsonl", ".txt"}
                and not any(
                    part.startswith(".")
                    for part in p.relative_to(self._agent_work_dir).parts
                )
            )
            for state_file in sorted(state_dir.rglob("*.json")):
                rel_path = state_file.relative_to(self._agent_work_dir).as_posix()
                data = self.read_json(rel_path, {})
                if data:
                    purpose = self._describe_state_json(data, max_len=max_len)
                    if purpose:
                        state_purpose[rel_path] = purpose
                    summary = self._summarize_state_json(data, max_len=max_len)
                    if summary:
                        state_summary[rel_path] = summary

        if not state_files_index and self._agent_work_dir is not None:
            state_dir = self._agent_work_dir / "state"
            if state_dir.exists() and state_dir.is_dir():
                state_files_index = sorted(
                    f"state/{p.name}"
                    for p in state_dir.iterdir()
                    if p.is_file()
                    and p.suffix in {".json", ".jsonl", ".txt"}
                    and not p.name.startswith(".")
                )

        memory_counts = {
            str(path.relative_to(self._agent_work_dir)): self._count_nonempty_lines(
                path
            )
            for path in self._memory_file_candidates()
            if path.exists()
        }

        if state_summary or state_files_index or memory_counts:
            metadata["state_summary"] = state_summary
            metadata["state_purpose"] = state_purpose
            metadata["state_files"] = state_files_index[:80]
            if memory_counts:
                metadata["memory_files"] = memory_counts
            metadata["last_sync"] = datetime.now(timezone.utc).isoformat()
            self._write_agent_context(metadata, str(context.get("content", "")))

    def build_workspace_summary(self) -> str:
        """生成 workspace 內容摘要。

        用於在step開始時讓agent快速瞭解workspace狀態。
        動態發現 state/ 目錄下的所有狀態檔案。

        :returns: workspace摘要文字。
        """
        if self._agent_work_dir is None:
            return ""

        summary = []

        context = self.read_agent_context()
        if context.get("metadata"):
            task = context["metadata"].get("current_task", "")
            if task:
                summary.append(f"**Current Task**: {task}")

        state_dir = self._agent_work_dir / "state"
        if state_dir.exists():
            state_files = sorted(state_dir.rglob("*.json"))
            if state_files:
                summary.append(f"**state/**: {len(state_files)} files")
                for state_file in state_files:
                    try:
                        data = jr_parse(state_file.read_text())
                        value = self._summarize_state_json(data, max_len=80)
                        if value:
                            key = state_file.relative_to(
                                self._agent_work_dir
                            ).as_posix()
                            summary.append(f"  - {key}: {value}")
                    except Exception:
                        pass

        memory_parts = []
        for memory_file in self._memory_file_candidates():
            if memory_file.exists():
                rel = memory_file.relative_to(self._agent_work_dir)
                line_count = self._count_nonempty_lines(memory_file)
                memory_parts.append(f"`{rel.as_posix()}`: {line_count} entries")
        if memory_parts:
            summary.append("**memory**: " + "; ".join(memory_parts))

        return "\n".join(summary) if summary else ""

    # ==================== Behavior Tracking (Observability) ====================

    def emit_behavior_event(
        self,
        event_type: str,
        data: dict[str, Any],
        tick: int | None = None,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        name: str | None = None,
        input_summary: dict[str, Any] | None = None,
        output_summary: dict[str, Any] | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """傳送結構化行為事件到追蹤日誌。

        採用通用 trace/span 形態，不繫結具體 skill 語義。

        :param event_type: 事件型別（如 "tool_call", "skill_activate", "decision"）。
        :param data: 事件資料。
        :param tick: 當前 tick（可選）。
        :param trace_id: 當前 step/run 的追蹤 ID。
        :param span_id: 當前操作 span ID。
        :param parent_span_id: 父 span ID。
        :param name: 操作名稱。
        :param input_summary: 輸入摘要，不寫大內容或隱私內容。
        :param output_summary: 輸出摘要。
        :param error: 錯誤摘要。
        :param duration_ms: 操作耗時毫秒。
        """
        if self._agent_work_dir is None:
            return

        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": self._agent_id,
            "event_type": event_type,
            "tick": tick,
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "name": name,
            "input_summary": input_summary or {},
            "output_summary": output_summary or {},
            "error": error,
            "duration_ms": duration_ms,
            "data": data,
        }

        # 追加到行為追蹤日誌
        path = self._agent_work_dir / RUNTIME_LOG_DIR / "behavior_trace.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(jr_dumps(event, indent=None) + "\n")

    def get_behavior_summary(self, limit: int = 100) -> dict[str, Any]:
        """獲取行為摘要統計。

        :param limit: 讀取的事件數量上限。
        :return: 行為摘要字典。
        """
        if self._agent_work_dir is None:
            return {}

        path = self._agent_work_dir / RUNTIME_LOG_DIR / "behavior_trace.jsonl"
        if not path.exists():
            return {}

        # 讀取最近事件
        events: list[dict] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        events.append(jr_parse(line))
                    except Exception:
                        pass

        if not events:
            return {}

        recent = events[-limit:] if len(events) > limit else events

        # 統計
        tool_counts: dict[str, int] = {}
        skill_activations: dict[str, int] = {}
        errors: list[dict] = []

        for event in recent:
            event_type = event.get("event_type", "")
            data = event.get("data", {})

            if event_type == "tool_call":
                tool = event.get("name") or data.get("tool", "unknown")
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
            elif event_type == "skill_activate":
                skill = event.get("name") or data.get("skill", "unknown")
                skill_activations[skill] = skill_activations.get(skill, 0) + 1
            elif event_type == "error":
                errors.append(
                    {
                        "tool": event.get("name") or data.get("tool", "unknown"),
                        "error": str(event.get("error") or data.get("error", ""))[:100],
                    }
                )

        return {
            "total_events": len(recent),
            "tool_usage": tool_counts,
            "skill_activations": skill_activations,
            "recent_errors": errors[-5:],
            "last_tick": recent[-1].get("tick") if recent else None,
        }

    # ==================== File Discovery ====================

    def build_file_manifest(self) -> str:
        """構建 workspace 檔案清單。

        動態掃描 workspace 目錄，生成 Markdown 格式的短檔案索引。

        :returns: Markdown 格式的檔案清單。
        """
        if self._agent_work_dir is None:
            return ""

        workspace_root = self._agent_work_dir
        lines = [
            "# Workspace Files",
            "",
            "Generated index for quick discovery. Use `workspace_list` for the live file tree.",
            "",
            f"**Root**: `{workspace_root}`",
            "",
        ]

        ignored_dirs = {RUNTIME_DIR, "__pycache__"}

        root_files = sorted(
            p
            for p in workspace_root.iterdir()
            if p.is_file() and not p.name.startswith(".") and p.name != AGENT_DOCUMENT
        )
        if root_files:
            lines.append("## Root Files")
            for file_path in root_files:
                rel = file_path.relative_to(workspace_root)
                size_str = self._format_file_size(file_path.stat().st_size)
                lines.append(f"- `{rel.as_posix()}` ({size_str})")
            lines.append("")

        state_dir = workspace_root / "state"
        if state_dir.exists() and state_dir.is_dir():
            state_files = sorted(
                p
                for p in state_dir.rglob("*")
                if p.is_file()
                and not any(
                    part.startswith(".") for part in p.relative_to(workspace_root).parts
                )
            )
            if state_files:
                lines.append("## state/")
                for file_path in state_files[:120]:
                    rel = file_path.relative_to(workspace_root)
                    size_str = self._format_file_size(file_path.stat().st_size)
                    extra = ""
                    if file_path.suffix == ".json":
                        try:
                            data = jr_parse(file_path.read_text(encoding="utf-8"))
                            purpose = self._describe_state_json(data, max_len=80)
                            state_summary = self._summarize_state_json(
                                data,
                                max_len=80,
                            )
                            details = [x for x in [purpose, state_summary] if x]
                            if details:
                                extra = " - " + " | ".join(details)
                        except Exception:
                            extra = " - unreadable json"
                    elif file_path.suffix == ".jsonl":
                        count = self._count_nonempty_lines(file_path)
                        extra = f" - {count} entries"
                    lines.append(f"- `{rel.as_posix()}` ({size_str}){extra}")
                if len(state_files) > 120:
                    lines.append(f"- ... {len(state_files) - 120} more state files")
                lines.append("")

        for dirname in ["memory", "input", "custom"]:
            dir_path = workspace_root / dirname
            if not dir_path.exists() or not dir_path.is_dir():
                continue
            files = sorted(
                p
                for p in dir_path.rglob("*")
                if p.is_file()
                and not any(
                    part.startswith(".") for part in p.relative_to(workspace_root).parts
                )
            )
            lines.append(f"## {dirname}/")
            for file_path in files[:80]:
                rel = file_path.relative_to(workspace_root)
                size_str = self._format_file_size(file_path.stat().st_size)
                extra = ""
                if file_path.suffix == ".jsonl":
                    extra = f" - {self._count_nonempty_lines(file_path)} entries"
                lines.append(f"- `{rel.as_posix()}` ({size_str}){extra}")
            if len(files) > 80:
                lines.append(f"- ... {len(files) - 80} more files")
            lines.append("")

        summarized_dirs = []
        for dirname in sorted(ignored_dirs):
            dir_path = workspace_root / dirname
            if dir_path.exists() and dir_path.is_dir():
                count = sum(1 for p in dir_path.rglob("*") if p.is_file())
                summarized_dirs.append(
                    f"- `{dirname}/` ({count} files, summarized only)"
                )
        if summarized_dirs:
            lines.append("## Runtime Directories")
            lines.extend(summarized_dirs)
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _format_file_size(size: int) -> str:
        """格式化檔案大小。"""
        if size < 1024:
            return f"{size}B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        else:
            return f"{size / (1024 * 1024):.1f}MB"

    def write_file_manifest(self) -> None:
        """將檔案索引寫入 AGENT.md 的自動生成區塊。"""
        manifest = self.build_file_manifest()
        if manifest:
            existing = self.read_agent_context()
            content = self._replace_generated_file_index(
                str(existing.get("content", "")),
                manifest,
            )
            self._write_agent_context(dict(existing.get("metadata", {}) or {}), content)
