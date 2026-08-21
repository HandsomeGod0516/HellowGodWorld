"""Prompt構建模組。

提供模組化的系統提示詞構建功能，支援靜態段/動態段分離以實現快取最佳化。

模組結構
========

- :class:`PromptBuilder`: 模組化Prompt構建器
- :class:`PromptSection`: Prompt片段
- :class:`ToolTableBuilder`: 工具表 Markdown（與 PersonAgent 共用）
- :class:`PromptCacheManager`: 靜態段跨次複用（分段 system prompt）

設計理念
========

PromptBuilder採用鏈式API，各部分可獨立配置：

1. 按優先順序組織各部分
2. 支援動態注入上下文
3. 靜態段/動態段分離，最佳化 Token 快取
4. 清晰的職責分離

快取策略
========

靜態段（可長期快取）：
- 工具協議說明
- 執行規則
- 工具表定義
- 技能目錄（不變部分）

動態段（每次重建）：
- 時間上下文
- Workspace 快照
- Agent 狀態

示例
====

基本使用::

    from agentsociety2.agent.prompt_builder import PromptBuilder

    builder = PromptBuilder()
    builder.add_identity(1, "Alice", profile)
    builder.add_tool_protocol()
    prompt = builder.build()

分段構建（靜態段由 :class:`PromptCacheManager` 跨次複用）::

    manager = PromptCacheManager()
    builder = PromptBuilder()
    # ... add 靜態段 ...
    static_text, _ = manager.get_or_build_static(builder, base="")
    # ... add 動態段 ...
    dynamic_text = builder.build_dynamic()
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar, Optional


@dataclass
class PromptSection:
    """Prompt片段。

    :ivar title: 片段標題。
    :ivar content: 片段內容。
    :ivar priority: 優先順序（越高越靠前）。
    :ivar is_static: 是否為靜態段（可快取）。
    """

    title: str
    content: str
    priority: int = 0
    is_static: bool = False

    def render(self) -> str:
        """渲染片段。

        :return: 渲染後的字串，空內容返回空字串。
        """
        if not self.content:
            return ""
        return f"\n# {self.title}\n{self.content}\n"


class PromptBuilder:
    """模組化Prompt構建器。

    提供鏈式API構建系統提示詞，各部分按優先順序排序。
    支援靜態段/動態段分離，最佳化 Token 快取。

    :ivar _sections: Prompt 片段列表。

    Example:

        >>> builder = PromptBuilder()
        >>> builder.add_identity(1, "Alice", profile)
        >>> builder.add_tool_protocol()
        >>> prompt = builder.build()
    """

    def __init__(self):
        """初始化構建器。"""
        self._sections: list[PromptSection] = []

    def add_section(
        self, title: str, content: str, priority: int = 0, is_static: bool = False
    ) -> "PromptBuilder":
        """新增Prompt片段。

        :param title: 片段標題。
        :param content: 片段內容。
        :param priority: 優先順序。
        :param is_static: 是否為靜態段（可快取）。
        :return: self，支援鏈式呼叫。
        """
        if content:
            self._sections.append(PromptSection(title, content, priority, is_static))
        return self

    def _compute_static_cache_key(self) -> str:
        """計算靜態段快取鍵。

        :return: 基於靜態段內容的雜湊鍵。
        """
        static_sections = [s for s in self._sections if s.is_static]
        content = "|".join(
            f"{s.title}:{s.content}"
            for s in sorted(static_sections, key=lambda x: -x.priority)
        )
        return hashlib.md5(content.encode()).hexdigest()[:16]

    def add_identity(self, agent_id: int, name: str, profile: Any) -> "PromptBuilder":
        """新增Agent身份資訊（動態段）。

        :param agent_id: Agent ID。
        :param name: Agent名稱。
        :param profile: Agent畫像。
        :return: self。
        """
        identity = {"id": agent_id, "name": name, "profile": profile}
        content = json.dumps(identity, ensure_ascii=False, indent=2)
        return self.add_section(
            "Agent Identity", content, priority=100, is_static=False
        )

    def add_world_description(self, description: str) -> "PromptBuilder":
        """新增世界描述（靜態段，通常不變）。

        :param description: 世界描述文字。
        :return: self。
        """
        if not description:
            return self
        content = f"Environment-specific modules, tools, and conventions:\n\n{description.strip()}"
        return self.add_section(
            "World Description", content, priority=95, is_static=True
        )

    def add_workspace_structure(self, structure: str) -> "PromptBuilder":
        """新增工作區結構說明（靜態段）。

        :param structure: 結構說明文字。
        :return: self。
        """
        if not structure:
            return self
        return self.add_section(
            "Workspace Structure", structure, priority=92, is_static=True
        )

    def add_context(
        self, context: dict[str, Any], max_chars: int = 2000
    ) -> "PromptBuilder":
        """新增Agent上下文（動態段）。

        :param context: 上下文字典。
        :param max_chars: 最大字元數。
        :return: self。
        """
        if not context:
            return self

        lines = ["This is your self-declared context. Edit via workspace_write."]

        metadata = context.get("metadata", {})
        if metadata:
            lines.append("\n## Current State")
            for key in ["current_task", "active_goal", "priority"]:
                if key in metadata:
                    lines.append(f"- **{key}**: {metadata[key]}")

        content = context.get("content", "")
        if content:
            lines.append(f"\n## Notes\n{content[:max_chars]}")

        return self.add_section(
            "Agent Context", "\n".join(lines), priority=80, is_static=False
        )

    def add_workspace_summary(self, summary: str) -> "PromptBuilder":
        """新增工作區摘要（動態段）。

        :param summary: 摘要文字。
        :return: self。
        """
        if not summary:
            return self
        return self.add_section(
            "Workspace Summary", summary, priority=75, is_static=False
        )

    def add_recovery_context(self, context: str) -> "PromptBuilder":
        """新增會話恢復上下文（動態段）。

        :param context: 恢復上下文。
        :return: self。
        """
        if not context:
            return self
        return self.add_section(
            "Session Recovery", context, priority=70, is_static=False
        )

    def add_state_snapshot(self, state: dict[str, Any]) -> "PromptBuilder":
        """新增預載入狀態快照（動態段）。

        :param state: 狀態字典。
        :return: self。
        """
        if not state:
            return self

        content = (
            "Snapshot of workspace files. May be stale after writes.\n"
            f"```json\n{json.dumps(state, ensure_ascii=False, indent=1)}\n```"
        )
        return self.add_section(
            "Workspace State", content, priority=60, is_static=False
        )

    def add_tool_protocol(self) -> "PromptBuilder":
        """新增工具協議說明（靜態段，可快取）。

        :return: self。
        """
        content = """Respond ONLY with valid JSON: {tool_name, arguments, done, summary}.
- `arguments` must be a JSON object (use {} if no parameters).
- For execute_skill use arguments.args; for codegen use arguments.ctx.
- For activate_skill set arguments.skill_name.

# Skills
The catalog lists name + short description only (progressive disclosure).
Use `activate_skill` to load full SKILL.md, then follow it.

# Execution Rules
- Do not invent tools. `tool_name` must match the Tools table.
- Never set tool_name to a skill name. Use activate_skill.
- Prefer skill-driven execution: activate -> read/execute -> workspace -> done.
- Long files: use `workspace_read` with offset/limit for pagination.
- Keep `summary` concise and factual."""
        return self.add_section("Tool Protocol", content, priority=55, is_static=True)

    def add_tools(self, tool_table: str) -> "PromptBuilder":
        """新增工具表（靜態段）。

        :param tool_table: 工具表文字。
        :return: self。
        """
        if not tool_table:
            return self
        return self.add_section("Tools", tool_table, priority=50, is_static=True)

    def add_skill_catalog(self, catalog: dict[str, Any]) -> "PromptBuilder":
        """新增技能目錄（半靜態，技能列表不變時快取有效）。

        :param catalog: 技能目錄字典。
        :return: self。
        """
        if not catalog:
            return self

        return self.add_section(
            "Skill Catalog",
            json.dumps(catalog, ensure_ascii=False, indent=1),
            priority=45,
            is_static=True,
        )

    def add_activated_skills(self, skills: set[str]) -> "PromptBuilder":
        """新增已啟用技能列表（動態段）。

        :param skills: 技能名稱集合。
        :return: self。
        """
        if not skills:
            return self

        return self.add_section(
            "Activated Skills",
            json.dumps(sorted(skills), ensure_ascii=False),
            priority=40,
            is_static=False,
        )

    def add_constraints(self, constraints: Optional[str]) -> "PromptBuilder":
        """新增環境約束（動態段）。

        :param constraints: 約束說明。
        :return: self。
        """
        if not constraints:
            return self
        return self.add_section(
            "Constraints", constraints, priority=30, is_static=False
        )

    def build(self, base: str = "") -> str:
        """構建完整Prompt。

        :param base: 基礎提示詞（可選）。
        :return: 完整的系統提示詞。
        """
        sorted_sections = sorted(self._sections, key=lambda s: -s.priority)
        parts = [base] if base else []
        for section in sorted_sections:
            rendered = section.render()
            if rendered:
                parts.append(rendered)
        return "\n".join(parts)

    def build_static(self, base: str = "") -> str:
        """構建靜態段（可快取部分）。

        跨請求複用請配合 :class:`PromptCacheManager`；本方法在單次 builder 上無狀態快取。

        :param base: 基礎提示詞（可選）。
        :return: 靜態段文字。
        """
        static_sections = [s for s in self._sections if s.is_static]
        sorted_sections = sorted(static_sections, key=lambda s: -s.priority)
        parts = [base] if base else []
        for section in sorted_sections:
            rendered = section.render()
            if rendered:
                parts.append(rendered)
        return "\n".join(parts)

    def build_dynamic(self, base: str = "") -> str:
        """構建動態段（每次重建部分）。

        動態段包含時間上下文、Workspace 快照、Agent 狀態等變化內容。

        :param base: 基礎提示詞（可選，通常為空）。
        :return: 動態段文字。
        """
        dynamic_sections = [s for s in self._sections if not s.is_static]
        sorted_sections = sorted(dynamic_sections, key=lambda s: -s.priority)
        parts = [base] if base else []
        for section in sorted_sections:
            rendered = section.render()
            if rendered:
                parts.append(rendered)
        return "\n".join(parts)

    def clear(self) -> "PromptBuilder":
        """清空所有片段。

        :return: self。
        """
        self._sections.clear()
        return self


class PromptCacheManager:
    """Prompt 快取管理器。

    管理 Agent 的 Prompt 快取生命週期，追蹤快取命中率和 Token 節省。

    :ivar cache_hits: 快取命中次數。
    :ivar cache_misses: 快取未命中次數。
    :ivar tokens_saved: 節省的 Token 數（估算）。

    Example:

        >>> manager = PromptCacheManager()
        >>> static_prompt = manager.get_or_build_static(builder)
        >>> # 使用 static_prompt + cache_control 呼叫 LLM
    """

    def __init__(self):
        """初始化快取管理器。"""
        self._cached_static: Optional[str] = None
        self._cache_key: Optional[str] = None
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.tokens_saved: int = 0

    def get_or_build_static(
        self, builder: PromptBuilder, base: str = ""
    ) -> tuple[str, bool]:
        """獲取或構建靜態段。

        :param builder: PromptBuilder 例項。
        :param base: 基礎提示詞。
        :return: (靜態段文字, 是否命中快取) 元組。
        """
        new_key = builder._compute_static_cache_key()

        if self._cached_static is not None and self._cache_key == new_key:
            self.cache_hits += 1
            # 估算節省的 Token（粗略：字元數 / 4）
            self.tokens_saved += len(self._cached_static) // 4
            return self._cached_static, True

        # 快取未命中，構建並快取
        self.cache_misses += 1
        static_prompt = builder.build_static(base)
        self._cached_static = static_prompt
        self._cache_key = new_key
        return static_prompt, False

    def invalidate(self) -> None:
        """失效快取。"""
        self._cached_static = None
        self._cache_key = None

    def stats(self) -> dict:
        """獲取快取統計。

        :return: 統計資料字典。
        """
        total = self.cache_hits + self.cache_misses
        hit_rate = self.cache_hits / total if total > 0 else 0.0
        return {
            "hits": self.cache_hits,
            "misses": self.cache_misses,
            "hit_rate": hit_rate,
            "tokens_saved": self.tokens_saved,
        }


class ToolTableBuilder:
    """PersonAgent 工具表的單一資料來源（完整版 + 精簡版 Markdown）。"""

    TOOLS: ClassVar[tuple[tuple[str, str, str], ...]] = (
        (
            "activate_skill",
            "skill_name, arguments",
            "Load skill instructions (optional args)",
        ),
        (
            "read_skill",
            "skill_name, path, offset?, limit?",
            "Read skill file (paginate with offset/limit)",
        ),
        ("execute_skill", "skill_name, args", "Run a skill's subprocess script"),
        ("bash", "command, timeout_sec", "Shell command in workspace"),
        ("codegen", "instruction, ctx", "Send instruction to the environment"),
        (
            "workspace_read",
            "path, offset?, limit?",
            "Read workspace file (paginate with offset/limit)",
        ),
        ("workspace_write", "path, content", "Write file"),
        ("workspace_list", "path", "List files"),
        ("glob", "glob, path", "Find files by pattern"),
        ("grep", "pattern, glob, path", "Search file contents"),
        ("enable_skill", "skill_name", "Reveal a hidden skill"),
        ("disable_skill", "skill_name", "Hide a skill"),
        ("batch", "operations", "Execute multiple operations in one call"),
        ("done", "(done=true, summary)", "Finish this step"),
    )

    TOOLS_MINIMAL: ClassVar[tuple[tuple[str, str], ...]] = (
        ("activate_skill", "Load and activate a skill by name"),
        ("read_skill", "Read skill documentation files"),
        ("execute_skill", "Execute skill's subprocess"),
        ("bash", "Run shell commands"),
        ("codegen", "Send instructions to simulation environment"),
        ("workspace_read", "Read files from your workspace"),
        ("workspace_write", "Write files to your workspace"),
        ("workspace_list", "List workspace directory contents"),
        ("glob", "Find files by pattern"),
        ("grep", "Search file contents"),
        ("enable_skill", "Make a hidden skill visible"),
        ("disable_skill", "Hide a skill from catalog"),
        ("batch", "Execute multiple operations together"),
        ("done", "Finish this simulation step"),
    )

    @classmethod
    def render(cls) -> str:
        """完整工具表（含引數列）。"""
        lines = ["| Tool | Arguments | Purpose |", "|------|-----------|----------|"]
        for name, args, purpose in cls.TOOLS:
            lines.append(f"| {name} | {args} | {purpose} |")
        return "\n".join(lines)

    @classmethod
    def render_minimal(cls) -> str:
        """精簡工具表（省 token）。"""
        lines = ["| Tool | Purpose |", "|------|---------|"]
        for name, purpose in cls.TOOLS_MINIMAL:
            lines.append(f"| {name} | {purpose} |")
        return "\n".join(lines)
