# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Gateway 受控通道 slash 指令：單一解析與登錄檔（無 IO）.

與架構說明 docs/zh/SLASH_COMMAND_ARCHITECTURE.md 一致：此處僅 A 類通道控制與後設資料登記，
客戶端專有命令（如 /resume）僅記錄在 FIRST_BATCH_REGISTRY 中，不在 Gateway 內執行。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

# ---------------------------------------------------------------------------
# 合法控制訊息全集（用於 IM 入站管線跳過 LLM 改寫等，須與 Gateway 攔截語義一致）
# ---------------------------------------------------------------------------


class GatewaySlashCommand(str, Enum):
    """Gateway 當前支援解析的受控通道 slash 指令（A 類）。"""

    NEW_SESSION = "/new_session"
    MODE = "/mode"
    SWITCH = "/switch"
    SKILLS = "/skills"
    SKILLS_LIST = "/skills list"


class ModeSubcommand(str, Enum):
    """`/mode` 支援的子命令。"""

    AGENT = "agent"
    CODE = "code"
    TEAM = "team"
    AGENT_PLAN = "agent.plan"
    AGENT_FAST = "agent.fast"
    CODE_PLAN = "code.plan"
    CODE_NORMAL = "code.normal"


_VALID_MODE_LINES: frozenset[str] = frozenset(
    f"{GatewaySlashCommand.MODE.value} {sub.value}" for sub in ModeSubcommand
)


class SwitchSubcommand(str, Enum):
    """`/switch` 支援的子命令。"""

    PLAN = "plan"
    FAST = "fast"
    NORMAL = "normal"


_VALID_SWITCH_LINES: frozenset[str] = frozenset(
    f"{GatewaySlashCommand.SWITCH.value} {sub.value}" for sub in SwitchSubcommand
)

CONTROL_MESSAGE_TEXTS: frozenset[str] = frozenset(
    {
        GatewaySlashCommand.NEW_SESSION.value,
        *_VALID_MODE_LINES,
        *_VALID_SWITCH_LINES,
        GatewaySlashCommand.SKILLS_LIST.value,
    }
)


class ParsedControlAction(str, Enum):
    """parse_channel_control_text 的判定結果。"""

    NONE = "none"
    NEW_SESSION_OK = "new_session_ok"
    NEW_SESSION_BAD = "new_session_bad"
    MODE_OK = "mode_ok"
    MODE_BAD = "mode_bad"
    SWITCH_OK = "switch_ok"
    SWITCH_BAD = "switch_bad"
    SKILLS_OK = "skills_ok"


@dataclass(frozen=True)
class ParsedChannelControl:
    """受控通道使用者整行文字解析結果（與 message_handler 原語義一致）。"""

    action: ParsedControlAction
    mode_subcommand: str | None = None
    """mode_ok 時為 agent|code|team|agent.plan|agent.fast|code.plan|code.normal 之一。"""
    switch_subcommand: str | None = None
    """switch_ok 時為 plan|fast|normal 之一。"""


def parse_channel_control_text(text: str) -> ParsedChannelControl:
    """解析單條使用者文字是否為 /new_session、/mode、/switch、/skills list 控制指令。

    - 含換行則視為非控制（與原 _handle_channel_control 一致）。
    - /new_session 僅整行精確匹配為合法；帶字尾為非法但仍為控制指令。
    - /mode 僅白名單整行合法；支援 agent|code|team 及四個直達模式值；其它以 /mode 開頭且單行非法。
    - /switch 僅白名單整行合法；其它以 /switch 開頭且單行非法。
    - /skills list 僅整行精確匹配（/skills 本身不再觸發）。
    """
    if not text:
        return ParsedChannelControl(ParsedControlAction.NONE)
    if "\n" in text:
        return ParsedChannelControl(ParsedControlAction.NONE)
    t = text.strip()
    normalized = " ".join(t.split())
    if t == GatewaySlashCommand.NEW_SESSION.value:
        return ParsedChannelControl(ParsedControlAction.NEW_SESSION_OK)
    if t.startswith(GatewaySlashCommand.NEW_SESSION.value):
        return ParsedChannelControl(ParsedControlAction.NEW_SESSION_BAD)
    if normalized == GatewaySlashCommand.SKILLS_LIST.value:
        return ParsedChannelControl(ParsedControlAction.SKILLS_OK)
    if t in _VALID_MODE_LINES:
        parts = t.split()
        sub = parts[1] if len(parts) >= 2 else ""
        return ParsedChannelControl(ParsedControlAction.MODE_OK, mode_subcommand=sub)
    if t in _VALID_SWITCH_LINES:
        parts = t.split()
        sub = parts[1] if len(parts) >= 2 else ""
        return ParsedChannelControl(ParsedControlAction.SWITCH_OK, switch_subcommand=sub)
    if t.startswith(GatewaySlashCommand.MODE.value):
        return ParsedChannelControl(ParsedControlAction.MODE_BAD)
    if t.startswith(GatewaySlashCommand.SWITCH.value):
        return ParsedChannelControl(ParsedControlAction.SWITCH_BAD)
    return ParsedChannelControl(ParsedControlAction.NONE)


def is_control_like_for_im_batching(text: str) -> bool:
    """飛書/企微等：控制類訊息不走合併視窗（與歷史行為一致並補全 mode 變體與 /skills list）。

    單條文字、且為已知控制句、或以 /mode / /switch / /new_session 為字首（含非法變體）時返回 True。
    """
    if not text:
        return False
    if "\n" in text:
        return False
    t = text.strip()
    normalized = " ".join(t.split())
    if t in CONTROL_MESSAGE_TEXTS:
        return True
    if normalized == GatewaySlashCommand.SKILLS_LIST.value:
        return True
    if t.startswith(f"{GatewaySlashCommand.MODE.value} "):
        return True
    if t.startswith(f"{GatewaySlashCommand.SWITCH.value} "):
        return True
    if t.startswith(GatewaySlashCommand.SWITCH.value):
        return True
    if t.startswith(GatewaySlashCommand.NEW_SESSION.value):
        return True
    return False


# ---------------------------------------------------------------------------
# 第一批命令登錄檔（後設資料；resume 等為 client scope）
# ---------------------------------------------------------------------------

SlashScope = Literal["gateway", "client"]


@dataclass(frozen=True)
class SlashCommandEntry:
    id: str
    canonical_text: str
    scope: SlashScope
    req_method: str | None
    notes: str


FIRST_BATCH_REGISTRY: tuple[SlashCommandEntry, ...] = (
    SlashCommandEntry(
        id="new_session",
        canonical_text=GatewaySlashCommand.NEW_SESSION.value,
        scope="gateway",
        req_method=None,
        notes="受控通道重置 session_id；由 MessageHandler 攔截，不轉發 Agent 對話。",
    ),
    SlashCommandEntry(
        id="mode",
        canonical_text=f"{GatewaySlashCommand.MODE.value} agent|code|team|agent.plan|agent.fast|code.plan|code.normal",
        scope="gateway",
        req_method=None,
        notes="受控通道切換模式：一級模式 agent/code/team（對映到預設子模式）或直達 agent.plan/agent.fast/code.plan/code.normal；寫入 params.mode。",
    ),
    SlashCommandEntry(
        id="switch",
        canonical_text=f"{GatewaySlashCommand.SWITCH.value} plan|fast|normal",
        scope="gateway",
        req_method=None,
        notes="受控通道切換二級模式：agent 下 plan/fast，code 下 plan/normal。",
    ),
    SlashCommandEntry(
        id="skills",
        canonical_text=GatewaySlashCommand.SKILLS_LIST.value,
        scope="gateway",
        req_method="skills.list",
        notes="受控通道整行 /skills list 時 Gateway 調 skills.list 並以通知回覆；CLI 同路徑見 builtins/skills.ts。",
    ),
    SlashCommandEntry(
        id="resume",
        canonical_text="/resume",
        scope="client",
        req_method="command.resume",
        notes="CLI 會話恢復；另用 session.list。IM 受控通道本階段不解析，後續可擴充套件。",
    ),
    SlashCommandEntry(
        id="workspace_dir",
        canonical_text="/workspace_dir [get|set <path>|clear]",
        scope="client",
        req_method=None,
        notes="TUI 本地儲存工作區路徑；隨 chat.send params.workspace_dir 發往 Gateway/AgentServer。",
    ),
)


def format_skills_list_for_notice(payload: dict[str, Any] | None, *, max_items: int = 50) -> str:
    """將 skills.list 響應 payload 格式化為適合 IM 的純文字。"""
    if not payload or not isinstance(payload, dict):
        return "暫無技能資料。"
    err = payload.get("error")
    if isinstance(err, str) and err.strip():
        return f"獲取技能列表失敗：{err.strip()}"
    skills = payload.get("skills")
    if not isinstance(skills, list) or not skills:
        return "當前無可用技能。"
    lines: list[str] = ["【技能列表】"]
    for i, item in enumerate(skills[:max_items], 1):
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("title") or "?").strip()
            desc = str(item.get("description") or "").strip()
            src = str(item.get("source") or "").strip()
            suffix = f" ({src})" if src else ""
            if desc:
                short = desc if len(desc) <= 200 else desc[:200] + "…"
                lines.append(f"{i}. {name}{suffix}\n   {short}")
            else:
                lines.append(f"{i}. {name}{suffix}")
        else:
            lines.append(f"{i}. {item}")
    if len(skills) > max_items:
        lines.append(f"... 共 {len(skills)} 項，僅顯示前 {max_items} 項。")
    return "\n".join(lines)


# 供單測校驗與外部只讀引用（與 _VALID_MODE_LINES 相同）
VALID_MODE_LINES: frozenset[str] = _VALID_MODE_LINES
VALID_MODE_SUBCOMMANDS: tuple[str, ...] = tuple(sub.value for sub in ModeSubcommand)
VALID_SWITCH_LINES: frozenset[str] = _VALID_SWITCH_LINES
VALID_SWITCH_SUBCOMMANDS: tuple[str, ...] = tuple(sub.value for sub in SwitchSubcommand)
