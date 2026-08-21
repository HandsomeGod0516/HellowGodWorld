# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentServer → Gateway 下行推送抽象與 WebSocket 預設實現。"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GatewayPushTransport(Protocol):
    async def send_push(self, msg: dict[str, Any]) -> None:
        """向 Gateway 傳送一條 server_push 語義的訊息（與 AgentWebSocketServer.send_push 入參一致）。"""
        ...


class WebSocketGatewayPushTransport:
    """透過程序內 AgentWebSocketServer 單例推送（分離部署 + WebSocket 預設路徑）。"""

    async def send_push(self, msg: dict[str, Any]) -> None:
        from jiuwenclaw.server.agent_ws_server import AgentWebSocketServer

        await AgentWebSocketServer.get_instance().send_push(msg)
