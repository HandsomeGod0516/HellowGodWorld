# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""WebChannel - WebSocket 通道實現.

提供可擴充套件的方法處理器序號產生器制 (`register_method`) 和連線鉤子 (`on_connect`)，
使上層應用可以靈活控制每個 req method 的行為，而無需修改通道本身。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

import aiohttp

from jiuwenclaw.common.utils import get_agent_workspace_dir
from jiuwenclaw.gateway.channel_manager.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.common.security.ws_origin import (
    extract_handshake_request,
    forbidden_origin_response,
    get_header_value,
    is_allowed_browser_origin,
)
from jiuwenclaw.common.schema.message import Message, Mode, ReqMethod

logger = logging.getLogger(__name__)

# ── 型別別名 ──────────────────────────────────────────────
# 方法處理器簽名: (ws, req_id, params, session_id) -> None
MethodHandler = Callable[..., Awaitable[None]]
# 連線鉤子簽名: (ws) -> None | Awaitable[None]
ConnectHook = Callable[..., Any]


@dataclass
class WebChannelConfig:
    """WebChannel 配置."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 19000
    path: str = "/ws"
    allow_from: list[str] = field(default_factory=list)


class WebChannel(BaseChannel):
    """Web 前端 WebSocket 通道.

    核心職責：
    1. 管理 WebSocket 連線生命週期
    2. 解析幀協議 (req / res / event)
    3. 將入站訊息釋出到 RobotMessageRouter
    4. 將方法路由委託給透過 `register_method` 註冊的處理器
    """

    name = "web"

    def __init__(self, config: WebChannelConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: WebChannelConfig = config
        self._server: Any = None
        self._clients: set[Any] = set()
        self._on_message_cb: Callable[[Message], Any] | None = None
        self._method_handlers: dict[str, MethodHandler] = {}
        self._connect_hooks: list[ConnectHook] = []

    # ── 公共屬性 ──────────────────────────────────────────

    @property
    def channel_id(self) -> str:
        """返回唯一 Channel 標識."""
        return self.name

    @property
    def clients(self) -> set[Any]:
        """當前活躍的 WebSocket 客戶端集合（只讀副本）."""
        return set(self._clients)

    # ── 擴充套件註冊 API ──────────────────────────────────────

    def register_method(self, method: str, handler: MethodHandler) -> None:
        """註冊 req method 處理器.

        handler 簽名: ``async def handler(ws, req_id, params, session_id) -> None``
        handler 應透過 `send_response` / `send_event` 向客戶端回覆。
        """
        self._method_handlers[method] = handler

    def on_connect(self, callback: ConnectHook) -> None:
        """註冊連線建立鉤子，新客戶端接入時依次呼叫."""
        self._connect_hooks.append(callback)

    def on_message(self, callback: Callable[[Message], None]) -> None:
        """註冊訊息接收回撥（替代預設的 router.publish_user_messages）。"""
        self._on_message_cb = callback

    # ── 幀傳送 API（公開給處理器使用）─────────────────────

    async def send_response(
            self,
            ws: Any,
            req_id: str,
            *,
            ok: bool,
            payload: dict[str, Any] | None = None,
            error: str | None = None,
            code: str | None = None,
    ) -> None:
        """向指定客戶端傳送 ``res`` 幀."""
        frame: dict[str, Any] = {
            "type": "res",
            "id": req_id,
            "ok": ok,
            "payload": payload or {},
        }
        if not ok:
            frame["error"] = error or "request failed"
            if code:
                frame["code"] = code
        try:
            await ws.send(json.dumps(frame, ensure_ascii=False))
        except Exception as e:
            if bool(getattr(ws, "closed", False)):
                logger.debug("WebChannel send_response skipped on closed websocket: id={} err={}", req_id, e)
                return
            raise

    async def send_event(
            self,
            ws: Any,
            event: str,
            payload: dict[str, Any],
            *,
            seq: int | None = None,
            stream_id: str | None = None,
    ) -> None:
        """向指定客戶端傳送 ``event`` 幀."""
        frame: dict[str, Any] = {"type": "event", "event": event, "payload": payload}
        if seq is not None:
            frame["seq"] = seq
        if stream_id is not None:
            frame["stream_id"] = stream_id
        try:
            await ws.send(json.dumps(frame, ensure_ascii=False))
        except Exception as e:
            if bool(getattr(ws, "closed", False)):
                logger.debug("WebChannel send_event skipped on closed websocket: event={} err={}", event, e)
                return
            raise

    async def broadcast_event(
            self,
            event: str,
            payload: dict[str, Any],
            *,
            seq: int | None = None,
            stream_id: str | None = None,
    ) -> None:
        """向所有已連線客戶端廣播 ``event`` 幀."""
        frame: dict[str, Any] = {"type": "event", "event": event, "payload": payload}
        if seq is not None:
            frame["seq"] = seq
        if stream_id is not None:
            frame["stream_id"] = stream_id
        await self._broadcast(frame)

    async def _download_file(self, url: str) -> bytes | None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.read()
                    else:
                        logger.warning("WebChannel 檔案下載失敗: {}, 狀態碼: {}", url, response.status)
                        return None
        except Exception as e:
            logger.warning("WebChannel 檔案下載異常: {}, 錯誤: {}", url, e)
            return None

    async def _process_files(self, params: dict[str, Any]) -> dict[str, Any]:
        files = params.get("files")
        if not files or not isinstance(files, list):
            return params

        downloaded_files = []
        workspace_dir = str(get_agent_workspace_dir())

        for file_info in files:
            if not isinstance(file_info, dict):
                downloaded_files.append(file_info)
                continue

            file_url = file_info.get("url") or file_info.get("uri") or ""
            file_name = file_info.get("name") or file_info.get("filename") or "unknown_file"

            if file_url:
                file_content = await self._download_file(file_url)
                if file_content:
                    try:
                        os.makedirs(workspace_dir, exist_ok=True)
                        file_path = os.path.join(workspace_dir, file_name)
                        with open(file_path, "wb") as f:
                            f.write(file_content)
                        file_info["path"] = file_path
                    except Exception as e:
                        logger.warning("WebChannel 檔案儲存失敗: {}", e)

            downloaded_files.append(file_info)

        params["files"] = downloaded_files
        return params

    # ── Channel 生命週期 ──────────────────────────────────

    async def start(self) -> None:
        """啟動 WebSocket 服務並監聽客戶端連線."""
        if self._running:
            logger.warning("WebChannel 已在執行")
            return
        if not self.config.enabled:
            logger.warning("WebChannel 未啟用（enabled=False）")
            return

        try:
            from websockets.legacy.server import serve as ws_serve
        except Exception:  # pragma: no cover
            import websockets

            ws_serve = websockets.serve

        self._server = await ws_serve(
            self._connection_handler,
            self.config.host,
            self.config.port,
            process_request=self._process_request,
            ping_interval=20,
            ping_timeout=20,
        )
        self._running = True
        logger.info(
            f"WebChannel 已啟動: ws://{self.config.host}:{self.config.port}{self.config.path}"
        )
        await self._server.wait_closed()

    async def stop(self) -> None:
        """停止 WebSocket 服務並清理連線."""
        self._running = False

        close_tasks = [client.close(code=1001, reason="server shutdown") for client in list(self._clients)]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        self._clients.clear()

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("WebChannel 已停止")

    async def connect(self) -> None:
        """相容方法：呼叫 start."""
        await self.start()

    async def disconnect(self) -> None:
        """相容方法：呼叫 stop."""
        await self.stop()

    async def _process_request(self, *args: Any) -> Any:
        """在握手階段執行 Origin 校驗，相容 legacy/new websockets APIs。"""
        path, request_headers = extract_handshake_request(args)
        origin = get_header_value(request_headers, "Origin")
        allowed = is_allowed_browser_origin(origin)
        logger.info(
            "WebChannel 握手檢查 path=%s origin=%s allowed=%s",
            path,
            origin,
            allowed,
        )
        if allowed:
            return None

        logger.warning(
            "WebChannel 握手拒絕 path=%s origin=%s reason=origin_not_allowed",
            path,
            origin,
        )
        return forbidden_origin_response(args)

    async def send(self, msg: Message) -> None:
        """向客戶端傳送訊息（預設封裝為 event 幀廣播）."""
        if not self._clients:
            return

        # 響應幀：優先按 res 語義透傳，避免誤封裝為 chat.final
        if msg.type == "res":
            if isinstance(msg.payload, dict):
                res_payload = {**msg.payload}
            elif msg.payload is None:
                res_payload = {}
            else:
                res_payload = {"content": str(msg.payload)}

            frame: dict[str, Any] = {
                "type": "res",
                "id": msg.id,
                "ok": bool(msg.ok),
                "payload": res_payload,
            }
            if not msg.ok:
                error_text = res_payload.get("error")
                if isinstance(error_text, str) and error_text:
                    frame["error"] = error_text
                code_text = res_payload.get("code")
                if isinstance(code_text, str) and code_text:
                    frame["code"] = code_text
            await self._broadcast(frame)
            return

        # 確定事件名稱
        event_name = "chat.final"
        if msg.event_type is not None:
            event_name = msg.event_type.value
        elif isinstance(msg.payload, dict):
            payload_event_type = msg.payload.get("event_type")
            if isinstance(payload_event_type, str) and payload_event_type.strip():
                event_name = payload_event_type.strip()

        # 根據事件型別構造 payload
        payload = {}

        if isinstance(msg.payload, dict):
            # 對於需要傳遞完整結構化資料的事件型別
            if event_name in ("connection.ack", "todo.updated", "chat.tool_call", "chat.tool_result",
                              "chat.processing_status", "chat.interrupt_result", "chat.evolution_status",
                              "chat.error", "heartbeat.relay",
                              "context.compressed", "chat.ask_user_question", "chat.subtask_update",
                              "history.message",
                              "chat.session_result", "chat.usage_metadata",
                              "chat.usage_summary") or event_name.startswith("team."):
                # 傳遞完整 payload，保留所有欄位
                payload = {**msg.payload}
                # 確保包含 session_id
                if "session_id" not in payload and msg.session_id:
                    payload["session_id"] = msg.session_id
            else:
                # 對於純文字訊息（chat.delta, chat.final, chat.error 等），提取 content
                content = str(msg.payload.get("content", "") or "")
                if not content and not getattr(msg, "ok", True) and msg.payload.get("error"):
                    content = str(msg.payload.get("error", ""))
                payload = {
                    "session_id": msg.session_id,
                    "content": content,
                }
                # 定時任務推送：附帶 cron 後設資料，供前端識別並替換佔位訊息（避免誤寫入流式氣泡）
                if event_name == "chat.final":
                    cron_extra = msg.payload.get("cron")
                    if isinstance(cron_extra, dict):
                        payload["cron"] = cron_extra
        else:
            # payload 不是 dict，嘗試從 params 提取
            content = str((msg.params or {}).get("content", "") or "")
            payload = {
                "session_id": msg.session_id,
                "content": content,
            }

        frame = {
            "type": "event",
            "event": event_name,
            "payload": payload,
        }
        await self._broadcast(frame)

        # interrupt_result 根據 intent 決定 is_processing 狀態
        if event_name == "chat.interrupt_result":
            intent = payload.get("intent", "cancel") if isinstance(payload, dict) else "cancel"
            is_processing = intent in ("pause", "supplement", "resume")
            await self._broadcast({
                "type": "event",
                "event": "chat.processing_status",
                "payload": {"session_id": msg.session_id, "is_processing": is_processing},
            })

    def get_metadata(self) -> ChannelMetadata:
        """獲取 Channel 後設資料."""
        return ChannelMetadata(
            channel_id=self.channel_id,
            source="websocket",
            extra={"host": self.config.host, "port": self.config.port, "path": self.config.path},
        )

    # ── 內部實現 ──────────────────────────────────────────

    async def _connection_handler(self, ws: Any, path: str | None = None) -> None:
        raw_path = path if path is not None else getattr(ws, "path", "")
        parsed = urlparse(raw_path)
        request_path = parsed.path or raw_path
        if request_path != self.config.path:
            await ws.close(code=1008, reason=f"unsupported path: {request_path}")
            return

        query = parse_qs(parsed.query)
        remote = getattr(ws, "remote_address", None)
        self._clients.add(ws)
        logger.info(f"WebChannel 新連線: remote={remote} query={query}")

        # 觸發連線鉤子（如傳送 connection.ack）
        for hook in self._connect_hooks:
            try:
                result = hook(ws)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:  # pragma: no cover
                logger.warning("WebChannel on_connect hook error: {}", e)

        try:
            async for raw in ws:
                await self._handle_raw_message(ws, raw, query)
        except Exception as e:  # pragma: no cover - 連線生命週期容錯
            logger.warning("WebChannel 連線異常: %s", e)
        finally:
            self._clients.discard(ws)
            logger.info(f"WebChannel 連線關閉: remote={remote}")

    async def _handle_raw_message(self, ws: Any, raw: str, query: dict[str, list[str]]) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await self.send_response(ws, "", ok=False, error="invalid json", code="BAD_REQUEST")
            return

        if not isinstance(data, dict):
            await self.send_response(ws, "", ok=False, error="invalid request", code="BAD_REQUEST")
            return

        req_type = data.get("type")
        req_id = data.get("id")
        method = data.get("method")
        params = data.get("params")

        if req_type != "req" or not isinstance(req_id, str) or not isinstance(method, str):
            await self.send_response(
                ws,
                req_id if isinstance(req_id, str) else "",
                ok=False,
                error="invalid request",
                code="BAD_REQUEST",
            )
            return
        if not isinstance(params, dict):
            params = {}

        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = self._make_session_id()

        params = await self._process_files(params)

        user_message = Message(
            id=req_id,
            type="req",
            channel_id=self.channel_id,
            session_id=session_id,
            params=params,
            timestamp=time.time(),
            ok=True,
            req_method=self._parse_req_method(method),
            mode=self._parse_mode(params.get("mode")),
            metadata={"query": query, "method": method},
        )

        # 釋出到 route 或回撥
        handled_by_callback = False
        if self._on_message_cb is not None:
            result = self._on_message_cb(user_message)
            if inspect.isawaitable(result):
                result = await result
            handled_by_callback = bool(result)
        else:
            await self.bus.publish_user_messages(user_message)

        if handled_by_callback:
            return

        # 路由到已註冊的方法處理器
        handler = self._method_handlers.get(method)
        if handler is not None:
            try:
                await handler(ws, req_id, params, session_id)
            except Exception as e:
                # 客戶端斷開（如服務關閉時 code=1001）不再嘗試回包，避免二次異常噪音。
                ws_closed = bool(getattr(ws, "closed", False))
                if ws_closed:
                    logger.warning(
                        "WebChannel method handler aborted on closed websocket ({}): {}",
                        method, e,
                    )
                    return

                logger.error("WebChannel method handler error ({}): {}", method, e)
                try:
                    await self.send_response(
                        ws, req_id, ok=False,
                        error=f"handler error: {e}", code="INTERNAL_ERROR",
                    )
                except Exception as send_err:
                    logger.warning(
                        "WebChannel failed to send handler error response ({}): {}",
                        method, send_err,
                    )
        else:
            await self.send_response(
                ws, req_id, ok=False,
                error=f"unknown method: {method}", code="METHOD_NOT_FOUND",
            )

    async def _broadcast(self, frame: dict[str, Any]) -> None:
        data = json.dumps(frame, ensure_ascii=False)
        if not self._clients:
            return
        await asyncio.gather(*[client.send(data) for client in list(self._clients)], return_exceptions=True)

    @staticmethod
    def _parse_req_method(method: str) -> ReqMethod | None:
        for item in ReqMethod:
            if item.value == method:
                return item
        return None

    @staticmethod
    def _parse_mode(raw_mode: Any) -> Mode:
        return Mode.from_raw(raw_mode, default=Mode.AGENT_PLAN)

    @staticmethod
    def _make_session_id() -> str:
        # 與前端 generateSessionId 保持一致：毫秒時間戳(16進位制) + 6位隨機16進位制
        ts = format(int(time.time() * 1000), "x")
        suffix = secrets.token_hex(3)
        return f"sess_{ts}_{suffix}"
