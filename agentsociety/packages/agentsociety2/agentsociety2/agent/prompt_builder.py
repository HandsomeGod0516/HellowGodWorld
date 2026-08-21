"""Prompt构建模块。

提供模块化的系统提示词构建功能，支持静态段/动态段分离以实现缓存优化。

模块结构
========

- :class:`PromptBuilder`: 模块化Prompt构建器
- :class:`PromptSection`: Prompt片段
- :class:`ToolTableBuilder`: 工具表 Markdown（与 PersonAgent 共用）
- :class:`PromptCacheManager`: 静态段跨次复用（分段 system prompt）

设计理念
========

PromptBuilder采用链式API，各部分可独立配置：

1. 按优先级组织各部分
2. 支持动态注入上下文
3. 静态段/动态段分离，优化 Token 缓存
4. 清晰的职责分离

缓存策略
========

静态段（可长期缓存）：
- 工具协议说明
- 执行规则
- 工具表定义
- 技能目录（不变部分）

动态段（每次重建）：
- 时间上下文
- Workspace 快照
- Agent 状态

示例
====

基本使用::

    from agentsociety2.agent.prompt_builder import PromptBuilder

    builder = PromptBuilder()
    builder.add_identity(1, "Alice", profile)
    builder.add_tool_protocol()
    prompt = builder.build()

分段构建（静态段由 :class:`PromptCacheManager` 跨次复用）::

    manager = PromptCacheManager()
    builder = PromptBuilder()
    # ... add 静态段 ...
    static_text, _ = manager.get_or_build_static(builder, base="")
    # ... add 动态段 ...
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

    :ivar title: 片段标题。
    :ivar content: 片段内容。
    :ivar priority: 优先级（越高越靠前）。
    :ivar is_static: 是否为静态段（可缓存）。
    """

    title: str
    content: str
    priority: int = 0
    is_static: bool = False

    def render(self) -> str:
        """渲染片段。

        :return: 渲染后的字符串，空内容返回空字符串。
        """
        if not self.content:
            return ""
        return f"\n# {self.title}\n{self.content}\n"


class PromptBuilder:
    """模块化Prompt构建器。

    提供链式API构建系统提示词，各部分按优先级排序。
    支持静态段/动态段分离，优化 Token 缓存。

    :ivar _sections: Prompt 片段列表。

    Example:

        >>> builder = PromptBuilder()
        >>> builder.add_identity(1, "Alice", profile)
        >>> builder.add_tool_protocol()
        >>> prompt = builder.build()
    """

    def __init__(self):
        """初始化构建器。"""
        self._sections: list[PromptSection] = []

    def add_section(
        self, title: str, content: str, priority: int = 0, is_static: bool = False
    ) -> "PromptBuilder":
        """添加Prompt片段。

        :param title: 片段标题。
        :param content: 片段内容。
        :param priority: 优先级。
        :param is_static: 是否为静态段（可缓存）。
        :return: self，支持链式调用。
        """
        if content:
            self._sections.append(PromptSection(title, content, priority, is_static))
        return self

    def _compute_static_cache_key(self) -> str:
        """计算静态段缓存键。

        :return: 基于静态段内容的哈希键。
        """
        static_sections = [s for s in self._sections if s.is_static]
        content = "|".join(
            f"{s.title}:{s.content}"
            for s in sorted(static_sections, key=lambda x: -x.priority)
        )
        return hashlib.md5(content.encode()).hexdigest()[:16]

    def add_identity(self, agent_id: int, name: str, profile: Any) -> "PromptBuilder":
        """添加Agent身份信息（动态段）。

        :param agent_id: Agent ID。
        :param name: Agent名称。
        :param profile: Agent画像。
        :return: self。
        """
        identity = {"id": agent_id, "name": name, "profile": profile}
        content = json.dumps(identity, ensure_ascii=False, indent=2)
        return self.add_section(
            "Agent Identity", content, priority=100, is_static=False
        )

    def add_world_description(self, description: str) -> "PromptBuilder":
        """添加世界描述（静态段，通常不变）。

        :param description: 世界描述文本。
        :return: self。
        """
        if not description:
            return self
        content = f"Environment-specific modules, tools, and conventions:\n\n{description.strip()}"
        return self.add_section(
            "World Description", content, priority=95, is_static=True
        )

    def add_workspace_structure(self, structure: str) -> "PromptBuilder":
        """添加工作区结构说明（静态段）。

        :param structure: 结构说明文本。
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
        """添加Agent上下文（动态段）。

        :param context: 上下文字典。
        :param max_chars: 最大字符数。
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
        """添加工作区摘要（动态段）。

        :param summary: 摘要文本。
        :return: self。
        """
        if not summary:
            return self
        return self.add_section(
            "Workspace Summary", summary, priority=75, is_static=False
        )

    def add_recovery_context(self, context: str) -> "PromptBuilder":
        """添加会话恢复上下文（动态段）。

        :param context: 恢复上下文。
        :return: self。
        """
        if not context:
            return self
        return self.add_section(
            "Session Recovery", context, priority=70, is_static=False
        )

    def add_state_snapshot(self, state: dict[str, Any]) -> "PromptBuilder":
        """添加预加载状态快照（动态段）。

        :param state: 状态字典。
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
        """添加工具协议说明（静态段，可缓存）。

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
        """添加工具表（静态段）。

        :param tool_table: 工具表文本。
        :return: self。
        """
        if not tool_table:
            return self
        return self.add_section("Tools", tool_table, priority=50, is_static=True)

    def add_skill_catalog(self, catalog: dict[str, Any]) -> "PromptBuilder":
        """添加技能目录（半静态，技能列表不变时缓存有效）。

        :param catalog: 技能目录字典。
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
        """添加已激活技能列表（动态段）。

        :param skills: 技能名称集合。
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
        """添加环境约束（动态段）。

        :param constraints: 约束说明。
        :return: self。
        """
        if not constraints:
            return self
        return self.add_section(
            "Constraints", constraints, priority=30, is_static=False
        )

    def build(self, base: str = "") -> str:
        """构建完整Prompt。

        :param base: 基础提示词（可选）。
        :return: 完整的系统提示词。
        """
        sorted_sections = sorted(self._sections, key=lambda s: -s.priority)
        parts = [base] if base else []
        for section in sorted_sections:
            rendered = section.render()
            if rendered:
                parts.append(rendered)
        return "\n".join(parts)

    def build_static(self, base: str = "") -> str:
        """构建静态段（可缓存部分）。

        跨请求复用请配合 :class:`PromptCacheManager`；本方法在单次 builder 上无状态缓存。

        :param base: 基础提示词（可选）。
        :return: 静态段文本。
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
        """构建动态段（每次重建部分）。

        动态段包含时间上下文、Workspace 快照、Agent 状态等变化内容。

        :param base: 基础提示词（可选，通常为空）。
        :return: 动态段文本。
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
    """Prompt 缓存管理器。

    管理 Agent 的 Prompt 缓存生命周期，追踪缓存命中率和 Token 节省。

    :ivar cache_hits: 缓存命中次数。
    :ivar cache_misses: 缓存未命中次数。
    :ivar tokens_saved: 节省的 Token 数（估算）。

    Example:

        >>> manager = PromptCacheManager()
        >>> static_prompt = manager.get_or_build_static(builder)
        >>> # 使用 static_prompt + cache_control 调用 LLM
    """

    def __init__(self):
        """初始化缓存管理器。"""
        self._cached_static: Optional[str] = None
        self._cache_key: Optional[str] = None
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.tokens_saved: int = 0

    def get_or_build_static(
        self, builder: PromptBuilder, base: str = ""
    ) -> tuple[str, bool]:
        """获取或构建静态段。

        :param builder: PromptBuilder 实例。
        :param base: 基础提示词。
        :return: (静态段文本, 是否命中缓存) 元组。
        """
        new_key = builder._compute_static_cache_key()

        if self._cached_static is not None and self._cache_key == new_key:
            self.cache_hits += 1
            # 估算节省的 Token（粗略：字符数 / 4）
            self.tokens_saved += len(self._cached_static) // 4
            return self._cached_static, True

        # 缓存未命中，构建并缓存
        self.cache_misses += 1
        static_prompt = builder.build_static(base)
        self._cached_static = static_prompt
        self._cache_key = new_key
        return static_prompt, False

    def invalidate(self) -> None:
        """失效缓存。"""
        self._cached_static = None
        self._cache_key = None

    def stats(self) -> dict:
        """获取缓存统计。

        :return: 统计数据字典。
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
    """PersonAgent 工具表的单一数据源（完整版 + 精简版 Markdown）。"""

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
        """完整工具表（含参数列）。"""
        lines = ["| Tool | Arguments | Purpose |", "|------|-----------|----------|"]
        for name, args, purpose in cls.TOOLS:
            lines.append(f"| {name} | {args} | {purpose} |")
        return "\n".join(lines)

    @classmethod
    def render_minimal(cls) -> str:
        """精简工具表（省 token）。"""
        lines = ["| Tool | Purpose |", "|------|---------|"]
        for name, purpose in cls.TOOLS_MINIMAL:
            lines.append(f"| {name} | {purpose} |")
        return "\n".join(lines)
