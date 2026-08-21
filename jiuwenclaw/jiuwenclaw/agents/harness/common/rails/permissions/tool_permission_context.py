"""Tool permission channel context.

The openjiuwen permission rail uses host callbacks that need to know which
channel is executing (web/acp/tui). We keep this as a ContextVar owned by
jiuwenclaw so request handlers can set/reset it without depending on the
legacy permissions implementation.
"""

from __future__ import annotations

import contextvars

# 當前 asyncio Task 的 channel_id（供工具許可權/宿主確認判斷）；由介面層在 run_agent 前 set、結束後 reset。
TOOL_PERMISSION_CHANNEL_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jiuwenclaw_tool_permission_channel_id",
    default="",
)


__all__ = ["TOOL_PERMISSION_CHANNEL_ID"]

