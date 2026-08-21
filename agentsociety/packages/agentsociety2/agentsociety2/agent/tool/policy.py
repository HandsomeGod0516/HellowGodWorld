"""工具執行策略（policy-first 攔截）。

本模組提供統一的工具執行護欄，將“是否允許執行某個工具”從
:class:`~agentsociety2.agent.person.PersonAgent` 主流程中抽離，以獲得更清晰的邊界、
更一致的失敗語義，以及更容易的單元測試。

該 policy **只做判定**，不執行 I/O；呼叫方應把返回的結構化錯誤物件寫入 thread 作為
``TOOL_RESULT_JSON``，讓模型在同一步內自我糾錯。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agentsociety2.agent.tool.security import BashSecurityChecker


_GUARDED_ACTIONS = frozenset(
    {
        "workspace_read",
        "workspace_write",
        "workspace_list",
        "bash",
        "glob",
        "grep",
        "codegen",
        "batch",
    }
)


def _normalize_allowed_tools(raw: list[str]) -> set[str]:
    """將 skill frontmatter 的 allowed-tools 歸一到 tool_name 集合。

    :param raw: SKILL.md frontmatter 的 allowed_tools 列表。
    :returns: 歸一化後的 tool_name 集合。
    """
    if not raw:
        return set()

    mapping = {
        "read": "workspace_read",
        "write": "workspace_write",
        "workspace_read": "workspace_read",
        "workspace_write": "workspace_write",
        "workspace_list": "workspace_list",
        "activate_skill": "activate_skill",
        "read_skill": "read_skill",
        "execute_skill": "execute_skill",
        "bash": "bash",
        "grep": "grep",
        "glob": "glob",
        "codegen": "codegen",
        "batch": "batch",
        "enable_skill": "enable_skill",
        "disable_skill": "disable_skill",
        "done": "done",
    }
    out: set[str] = set()
    for item in raw:
        s = str(item).strip()
        if not s:
            continue
        base = s.split("(", 1)[0].strip().lower()
        mapped = mapping.get(base)
        if mapped:
            out.add(mapped)
    return out


def _as_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


@dataclass(frozen=True)
class ToolPolicyContext:
    """Policy 判定所需的最小上下文。

    :param active_skill_scope: 當前 active scope（skill name）。
    :param allowed_tools: 當前 scope 下允許的 tool 集合；None 表示不限制。
    :param workspace_root: workspace 根路徑（用於 bash 安全檢查的路徑上下文）。
    """

    active_skill_scope: str
    allowed_tools: set[str] | None
    workspace_root: str = ""


class ToolPolicy:
    """統一的 tool 執行攔截器。"""

    def __init__(self) -> None:
        self._bash_checker = BashSecurityChecker()

    @staticmethod
    def allowed_tools_for_scope(skill_info: Any | None) -> set[str] | None:
        """從 skill metadata 計算 allowed-tools 集合。

        :param skill_info: skill metadata 物件（通常為 :class:`~agentsociety2.agent.skills.SkillInfo`）。
        :returns: allowed-tools 集合；無約束時返回 None。
        """
        if skill_info is None:
            return None
        raw = getattr(skill_info, "allowed_tools", None)
        if not isinstance(raw, list) or not raw:
            return None
        normalized = _normalize_allowed_tools([str(x) for x in raw])
        return normalized or None

    def check(
        self, *, action: str, args: Any, ctx: ToolPolicyContext
    ) -> dict[str, Any] | None:
        """檢查某次工具呼叫是否允許。

        :param action: 工具名稱。
        :param args: 工具引數（LLM 輸出，可能不是 dict）。
        :param ctx: policy 上下文。
        :returns: None 表示允許；否則返回結構化錯誤物件（可直接寫入 ``TOOL_RESULT_JSON``）。
        """
        action = str(action or "").strip()
        if not action:
            return {"action": action, "ok": False, "error": "empty tool_name"}

        # 1) allowed-tools 攔截（僅對會產生副作用/開銷的工具）
        if action in _GUARDED_ACTIONS and ctx.allowed_tools is not None:
            if action not in ctx.allowed_tools:
                return {
                    "action": action,
                    "ok": False,
                    "error": f"blocked by allowed-tools of active skill: {ctx.active_skill_scope}",
                    "policy": {
                        "kind": "allowed_tools",
                        "active_skill_scope": ctx.active_skill_scope,
                        "allowed_tools": sorted(ctx.allowed_tools),
                    },
                }

        # 2) bash 安全攔截（在執行前做 deterministic 防護）
        if action == "bash":
            d = _as_dict(args)
            command = str(d.get("command", "") or d.get("cmd", "") or "").strip()
            if not command:
                return {"action": action, "ok": False, "error": "empty command"}
            ok, reason = self._bash_checker.check(
                command, workspace=ctx.workspace_root or None
            )
            if not ok:
                return {
                    "action": action,
                    "ok": False,
                    "error": f"blocked: {reason}",
                    "policy": {"kind": "bash_security", "reason": reason},
                }

        return None
