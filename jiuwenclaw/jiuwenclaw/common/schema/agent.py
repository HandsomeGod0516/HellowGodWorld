# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Agent 請求與響應模型."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jiuwenclaw.common.schema.message import ReqMethod


@dataclass
class PermissionContext:
    """許可權上下文 - 統一承載許可權判定所需的身份與場景資訊.

    Attributes:
        principal_user_id: 許可權 owner（channel config 的 my_user_id）
        triggering_user_id: 觸發者（IM sender）
        channel_id: 渠道標識
        group_digital_avatar: 是否為數字分身場景
        web_user_id: 預留：第二期 web 端本人審批
    """

    principal_user_id: str = ""
    triggering_user_id: str = ""
    channel_id: str = ""
    group_digital_avatar: bool = False
    web_user_id: str = ""

    @property
    def scene(self) -> str:
        """從 channel_id + group_digital_avatar 派生，不要求外部顯式賦值."""
        if self.channel_id == "web":
            return "web"
        if self.group_digital_avatar:
            return "group_digital_avatar"
        return "normal_im"

    @property
    def owner_scope_key(self) -> tuple[str, str]:
        """用於 owner_scopes 配置查詢的 key: (channel_id, principal_user_id)."""
        return (self.channel_id, self.principal_user_id)

    def to_dict(self) -> dict[str, Any]:
        """序列化為 dict（供 Gateway→AgentServer WebSocket 傳輸）."""
        return {
            "principal_user_id": self.principal_user_id,
            "triggering_user_id": self.triggering_user_id,
            "channel_id": self.channel_id,
            "group_digital_avatar": self.group_digital_avatar,
            "web_user_id": self.web_user_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PermissionContext:
        """從 dict 反序列化."""
        return cls(
            principal_user_id=data.get("principal_user_id", ""),
            triggering_user_id=data.get("triggering_user_id", ""),
            channel_id=data.get("channel_id", ""),
            group_digital_avatar=data.get("group_digital_avatar", False),
            web_user_id=data.get("web_user_id", ""),
        )


@dataclass
class AgentRequest:
    """Agent 請求（Gateway → AgentServer）."""

    request_id: str
    channel_id: str = ""
    session_id: str | None = None
    chat_id: str | None = None
    req_method: ReqMethod | None = None
    params: dict = field(default_factory=dict)
    is_stream: bool = False
    timestamp: float = 0.0
    metadata: dict[str, Any] | None = None
    enable_memory: bool | None = None
    permission_context: PermissionContext | None = None


@dataclass
class AgentResponse:
    """Agent 響應（AgentServer → Gateway，非流式完整響應）."""

    request_id: str
    channel_id: str
    ok: bool = True
    payload: dict | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class AgentResponseChunk:
    """Agent 響應片段（AgentServer → Gateway，流式）."""

    request_id: str
    channel_id: str
    payload: dict | None = None
    is_complete: bool = False
