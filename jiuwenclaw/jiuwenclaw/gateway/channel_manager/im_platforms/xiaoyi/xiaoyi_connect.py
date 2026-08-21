# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""XiaoyiChannel - 華為小藝 A2A 協議客戶端."""

from __future__ import annotations

import logging
import asyncio
import base64
import hmac
import hashlib
import inspect
import json
import os
import ssl
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, List, Optional
from urllib.parse import urlparse

import aiohttp

from jiuwenclaw.gateway.channel_manager.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.common.schema.message import EventType, Message, ReqMethod
from jiuwenclaw.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_utils.push import XiaoYiPushService, PushConfig
from jiuwenclaw.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_utils.formatter import (
    get_status_state_for_event,
    get_status_text_for_event,
    should_send_as_status_update,
)

logger = logging.getLogger(__name__)

FILE_TYPE_TO_MIME_TYPE: dict[str, str] = {
    "txt": "text/plain",
    "html": "text/html",
    "css": "text/css",
    "js": "application/javascript",
    "json": "application/json",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
    "zip": "application/zip",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "mp3": "audio/mpeg",
    "mp4": "video/mp4",
}

# 全域性 XiaoyiChannel 例項引用（供手機端工具呼叫使用）
_xiaoyi_channel_instance: Optional["XiaoyiChannel"] = None


def get_xiaoyi_channel() -> Optional["XiaoyiChannel"]:
    """獲取全域性 XiaoyiChannel 例項（供手機端工具呼叫使用）."""
    return _xiaoyi_channel_instance


@dataclass
class DataEvent:
    """Data-only 事件資料結構（工具執行結果）."""
    intent_name: str
    outputs: dict
    status: str
    session_id: str = ""
    task_id: str = ""


@dataclass
class XiaoyiChannelConfig:
    """小藝通道配置（客戶端模式）."""

    enabled: bool = False
    mode: str = "xiaoyi_channel"  # xiaoyi_channel or xiaoyi_claw
    ak: str = ""
    sk: str = ""
    agent_id: str = ""
    ws_url1: str = ""
    ws_url2: str = ""
    enable_streaming: bool = True
    # Push notification configuration
    uid: str = ""
    api_key: str = ""
    api_id: str = ""
    push_id: str = ""
    push_url: str = ""
    file_upload_url: str = ""
    # Task timeout in milliseconds (default: 1 hour)
    task_timeout_ms: int = 3600000
    # Session cleanup timeout in milliseconds (default: 1 hour)
    session_cleanup_timeout_ms: int = 3600000


def _generate_signature(sk: str, timestamp: str) -> str:
    """生成 HMAC-SHA256 簽名（Base64 編碼）."""
    h = hmac.new(
        sk.encode("utf-8"),
        timestamp.encode("utf-8"),
        hashlib.sha256,
    )
    return base64.b64encode(h.digest()).decode("utf-8")


class XYFileUploadService:
    def __init__(self, base_url: str, api_key: str, uid: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.uid = uid
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.session.close()

    async def upload_file(self, file_path: str, object_type: str = "TEMPORARY_MATERIAL_DOC") -> Optional[str]:
        try:
            with open(file_path, 'rb') as f:
                file_content = f.read()

            file_name = os.path.basename(file_path)
            file_size = len(file_content)
            file_sha256 = hashlib.sha256(file_content).hexdigest()

            prepare_url = f"{self.base_url}/osms/v1/file/manager/prepare"
            prepare_data = {
                "objectType": object_type,
                "fileName": file_name,
                "fileSha256": file_sha256,
                "fileSize": file_size,
                "fileOwnerInfo": {
                    "uid": self.uid,
                    "teamId": self.uid,
                },
                "useEdge": False,
            }

            headers = {
                "Content-Type": "application/json",
                "x-uid": self.uid,
                "x-api-key": self.api_key,
                "x-request-from": "openclaw",
            }

            async with self.session.post(prepare_url, json=prepare_data, headers=headers) as resp:
                if not resp.ok:
                    raise Exception(f"Prepare failed: HTTP {resp}")

                prepare_resp = await resp.json()
                if prepare_resp.get("code") != "0":
                    raise RuntimeError(f"Prepare failed: {prepare_resp.get('desc', 'Unknown error')}")

            object_id = prepare_resp.get("objectId")
            draft_id = prepare_resp.get("draftId")
            upload_infos = prepare_resp.get("uploadInfos", [])

            if not upload_infos:
                raise RuntimeError("No upload information returned")

            upload_info = upload_infos[0]
            upload_url = upload_info.get("url")
            upload_method = upload_info.get("method", "PUT")
            upload_headers = upload_info.get("headers", {})

            async with self.session.request(
                    upload_method,
                    upload_url,
                    data=file_content,
                    headers=upload_headers
            ) as resp:
                if not resp.ok:
                    raise RuntimeError(f"Upload failed: HTTP {resp.status}")

            complete_url = f"{self.base_url}/osms/v1/file/manager/complete"
            complete_data = {
                "objectId": object_id,
                "draftId": draft_id,
            }

            async with self.session.post(complete_url, json=complete_data, headers=headers) as resp:
                if not resp.ok:
                    raise RuntimeError(f"Complete failed: HTTP {resp.status}")

                complete_resp = await resp.json()
                if complete_resp.get("code") != "0":
                    raise RuntimeError(f"Complete failed: {complete_resp.get('desc', 'Unknown error')}")

            return object_id

        except Exception as e:
            logger.error(f"[XY File Upload] Error: {e}")
            return None


def _generate_auth_headers(config: XiaoyiChannelConfig) -> dict[str, str]:
    """生成鑑權 Header."""
    if config.mode == "xiaoyi_claw":
        return {
            "x-uid": config.uid,
            "x-api-key": config.api_key,
            "x-agent-id": config.agent_id,
            "x-request-from": "openclaw"
        }
    timestamp = str(int(time.time() * 1000))
    signature = _generate_signature(config.sk, timestamp)
    return {
        "x-access-key": config.ak,
        "x-sign": signature,
        "x-ts": timestamp,
        "x-agent-id": config.agent_id
    }


class XiaoyiChannel(BaseChannel):
    """小藝通道：作為客戶端連線到小藝伺服器，實現 A2A 協議."""

    name = "xiaoyi"

    def __init__(self, config: XiaoyiChannelConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: XiaoyiChannelConfig = config
        self._ws_connections: dict[str, Any] = {}  # Dual channel connections
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._running = False
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}  # Heartbeat tasks for each channel
        self._connect_tasks: dict[str, asyncio.Task] = {}  # Connection tasks for each channel
        self._session_task_map: dict[str, str] = {}
        self._session_heartbeat_tasks: dict[str, asyncio.Task] = {}  # Response heartbeat tasks for each session
        self._stream_text_buffers: dict[str, str] = {}
        self._task_last_activity: dict[str, float] = {}
        self._on_message_cb: Callable[[Message], Any] | None = None
        # Task timeout management
        self._session_active: set[str] = set()  # Active sessions (concurrent request detection)
        self._task_timeout_tasks: dict[str, asyncio.Task] = {}  # 1-hour task timeout tasks
        self._session_timeout_tasks: dict[str, asyncio.Task] = {}  # 60-second periodic timeout tasks
        self._sessions_waiting_for_push: dict[str, str] = {}  # {session: task} waiting for push
        # Session cleanup management
        self._sessions_marked_for_cleanup: dict[str, dict[str, Any]] = {}  # Session cleanup state
        # File upload service configuration
        self.file_upload_config = {
            "baseUrl": config.file_upload_url,
            "apiKey": config.api_key,
            "uid": config.uid,
        }
        # Save additional configuration fields
        self.api_id = config.api_id
        self.push_id = config.push_id
        self._accumulated_texts: dict[str, str] = {}  # Accumulated text per session for push notification
        # Data-event 處理器：intent_name -> list of handlers
        self._data_event_handlers: dict[str, List[Callable[[DataEvent], Any]]] = {}
        # InvokeJarvisGUIAgentResponse 原始事件回撥列表
        self._gui_agent_handlers: List[Callable[[dict[str, Any]], Any]] = []
        # GUI 工具互斥：避免併發註冊多個 handler 導致回包串單；不影響其他工具併發
        self._gui_tool_lock = asyncio.Lock()

    @property
    def channel_id(self) -> str:
        return self.name

    @property
    def gui_tool_lock(self) -> asyncio.Lock:
        """供 xiaoyi_gui_agent 序列執行，避免多路 GUI 回包互相喚醒。"""
        return self._gui_tool_lock

    @property
    def clients(self) -> set[Any]:
        return set()

    def on_message(self, callback: Callable[[Message], None]) -> None:
        self._on_message_cb = callback

    async def start(self) -> None:
        if self._running:
            logger.warning("XiaoyiChannel 已在執行")
            return
        if not self.config.enabled:
            logger.warning("XiaoyiChannel 未啟用（enabled=False）")
            return
        if self.config.mode == "xiaoyi_channel":
            if not self.config.ak or not self.config.sk or not self.config.agent_id:
                logger.error("XiaoyiChannel 未配置 ak/sk/agent_id")
                return

        self._running = True
        # 註冊全域性例項（供 tools 使用）
        global _xiaoyi_channel_instance
        _xiaoyi_channel_instance = self
        logger.info("XiaoyiChannel 已註冊為全域性例項")

        # Start dual channel connections
        for url_key, url in [("ws_url1", self.config.ws_url1), ("ws_url2", self.config.ws_url2)]:
            if url:
                self._connect_tasks[url_key] = asyncio.create_task(self._reconnect_loop(url_key, url))
        logger.info("XiaoyiChannel 已啟動（客戶端模式，雙通道）")

    async def stop(self) -> None:
        global _xiaoyi_channel_instance

        self._running = False
        # 登出全域性例項
        if _xiaoyi_channel_instance is self:
            _xiaoyi_channel_instance = None
            logger.info("XiaoyiChannel 已登出全域性例項")
        # Cancel all heartbeat tasks
        for url_key in list(self._heartbeat_tasks.keys()):
            if self._heartbeat_tasks[url_key]:
                self._heartbeat_tasks[url_key].cancel()
                self._heartbeat_tasks[url_key] = None
        # Cancel all connection tasks
        for url_key in list(self._connect_tasks.keys()):
            if self._connect_tasks[url_key]:
                self._connect_tasks[url_key].cancel()
                self._connect_tasks[url_key] = None
        # Cancel all session heartbeat tasks
        for session_id in list(self._session_heartbeat_tasks.keys()):
            if self._session_heartbeat_tasks[session_id]:
                self._session_heartbeat_tasks[session_id].cancel()
                self._session_heartbeat_tasks[session_id] = None
        # Cancel all task timeout tasks
        for session_id in list(self._task_timeout_tasks.keys()):
            if self._task_timeout_tasks[session_id]:
                self._task_timeout_tasks[session_id].cancel()
                self._task_timeout_tasks[session_id] = None
        # Cancel all session timeout tasks
        for session_id in list(self._session_timeout_tasks.keys()):
            if self._session_timeout_tasks[session_id]:
                self._session_timeout_tasks[session_id].cancel()
                self._session_timeout_tasks[session_id] = None
        # Close all websocket connections
        for url_key, ws in list(self._ws_connections.items()):
            if ws:
                try:
                    await ws.close()
                except Exception as e:
                    logger.warning(f"關閉 WebSocket 連線失敗 ({url_key}): {e}")
                self._ws_connections[url_key] = None
        self._heartbeat_tasks.clear()
        self._connect_tasks.clear()
        self._session_heartbeat_tasks.clear()
        self._task_timeout_tasks.clear()
        self._session_timeout_tasks.clear()
        self._ws_connections.clear()
        self._session_active.clear()
        self._sessions_waiting_for_push.clear()
        self._sessions_marked_for_cleanup.clear()
        self._accumulated_texts.clear()
        logger.info("XiaoyiChannel 已停止")

    def _extract_platform_receive_info(self, msg: Message) -> tuple[str, str]:
        """
        從訊息中提取小藝平臺會話 ID 與任務 ID。
        優先使用 metadata（避免 \new_session 覆蓋 session_id 後無法回發），否則回退到 session_id 與 _session_task_map。
        """
        meta = getattr(msg, "metadata", None) or {}
        platform_session_id = (meta.get("xiaoyi_session_id") or "").strip()
        platform_task_id = (meta.get("xiaoyi_task_id") or "").strip()
        if platform_session_id or platform_task_id:
            return (
                platform_session_id or (msg.session_id or ""),
                platform_task_id or platform_session_id,
            )
        task_id = msg.id or ""
        session_id = self._session_task_map.get(task_id, task_id)
        return session_id, task_id

    async def send(self, msg: Message) -> None:
        """傳送訊息到小藝服務端（A2A 格式，雙通道傳送）."""
        if not self._ws_connections:
            return
        logger.info(f"XiaoyiChannel 傳送訊息: {msg}")
        session_id, task_id = self._extract_platform_receive_info(msg)
        # Handle chat.file event
        if self.config.mode == "xiaoyi_claw" and msg.event_type == EventType.CHAT_FILE:
            files = msg.payload.get("files", {}) if isinstance(msg.payload, dict) else {}
            if files:
                for file_info in files:
                    # Convert file path to file info dict if it's a string
                    if isinstance(file_info, dict):
                        file_path = file_info.get("path", "")
                        file_name = file_info.get("name", os.path.basename(file_path))
                    else:
                        file_path = str(file_info)
                        file_name = os.path.basename(file_path)
                    file_info = {
                        "success": True,
                        "result_type": "file_created",
                        "fullPath": file_path,
                        "fileName": file_name
                    }

                    # Send file response
                    for url_key, ws in self._ws_connections.items():
                        if ws:
                            try:
                                await self._send_file_response(session_id, task_id, file_info, url_key)
                            except Exception as e:
                                logger.warning(f"XiaoyiChannel 傳送檔案響應失敗 ({url_key}): {e}")
            return

        if should_send_as_status_update(msg.event_type):
            status_text = get_status_text_for_event(msg.event_type, msg.payload)
            status_state = get_status_state_for_event(msg.event_type, msg.payload)
            for url_key in list(self._ws_connections.keys()):
                await self._send_status_update_with_state(
                    task_id, session_id, status_text, status_state, url_key
                )
            return

        content = ""
        cron_job_name = ""
        if isinstance(msg.payload, dict):
            content = msg.payload.get("content", "\n")
            if isinstance(content, dict):
                content = content.get("output", str(content))
            content = str(content)
            cron_job_name = msg.payload.get("cron", {}).get("job_name", "")
        elif msg.payload:
            content = str(msg.payload)

        # 推送訊息傳送
        if msg.id.startswith("cron-push"):
            await self._send_push_notification(cron_job_name, content)
            return

        # 如果禁用流式，總是作為完整訊息傳送
        if not self.config.enable_streaming:
            append = False
            last_chunk = True
            final = True
        else:
            # 流式模式：按事件型別計算增量與是否結束
            is_delta = msg.event_type == EventType.CHAT_DELTA
            last_chunk = msg.event_type == EventType.CHAT_FINAL
            is_final = msg.payload.get("is_complete", False)
            last_chunk = True if is_final else last_chunk

            # 獲取之前傳送的文字
            previous_text = self._accumulated_texts.get(session_id, "")

            # 累積當前文字
            self._accumulated_texts[session_id] = content

            # 計算增量文字
            if is_delta:
                incremental_text = content[len(previous_text):]
            else:
                incremental_text = content

            # 在訊息流中，總是使用 append=true, isFinal=false
            append = True
            final = False
            last_chunk = last_chunk
            final = is_final

        # Get accumulated text for this session (for push notification)
        accumulated_text = self._accumulated_texts.get(session_id, "")
        self._accumulated_texts[session_id] = content

        # Send to all active connections
        for url_key, ws in self._ws_connections.items():
            if ws:
                try:
                    await self._send_text_response(
                        session_id,
                        task_id,
                        content,
                        url_key,
                        append=append,
                        last_chunk=last_chunk,
                        is_final=final
                    )
                except Exception as e:
                    logger.warning(f"XiaoyiChannel 傳送訊息失敗 ({url_key}): {e}")

        if final and session_id:
            await self._stop_session_heartbeat(session_id)
            # Clean up tasks and mark session as completed
            self._clear_task_timeout(session_id)
            self._clear_session_timeout(session_id)
            self._mark_session_completed(session_id)

            # Check if session was waiting for push and send notification
            if self._is_session_waiting_for_push(session_id, task_id) and accumulated_text:
                summary = accumulated_text[:30] + "..." if len(accumulated_text) > 30 else accumulated_text
                await self._send_push_notification(summary, "後臺任務已完成：" + summary)
                self._clear_session_waiting_for_push(session_id, task_id)

            # Clear accumulated text
            self._accumulated_texts.pop(session_id, None)

    def get_metadata(self) -> ChannelMetadata:
        return ChannelMetadata(
            channel_id=self.channel_id,
            source="websocket",
            extra={
                "mode": "client",
                "ws_url1": self.config.ws_url1,
                "ws_url2": self.config.ws_url2,
                "agent_id": self.config.agent_id,
            },
        )

    async def _reconnect_loop(self, url_key: str, url: str) -> None:
        """自動重連迴圈（雙通道）."""
        while self._running:
            try:
                await self._connect(url_key, url)
                if not self._running:
                    break
                # 連線被遠端正常關閉時也做退避，避免瞬時重連刷屏。
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"XiaoyiChannel 連線失敗 ({url}): {e}")
                await asyncio.sleep(5)

    async def _connect(self, url_key: str, url: str) -> None:
        """連線到小藝伺服器（雙通道）."""
        import websockets

        headers = _generate_auth_headers(self.config)
        parsed = urlparse(url)
        is_ip = bool(parsed.hostname and parsed.hostname.replace(".", "").isdigit())

        ssl_context = ssl.create_default_context()
        if is_ip:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(
                url,
                additional_headers=headers,
                ssl=ssl_context,
                ping_interval=15,
                ping_timeout=15,
                close_timeout=5,
        ) as ws:
            self._ws_connections[url_key] = ws
            self._send_locks[url_key] = asyncio.Lock()
            logger.info(f"XiaoyiChannel 已連線 {url_key}: {url}")

            # 傳送初始化訊息（必須在 heartbeat 之前）
            await self._send_init_message(url_key)

            # 啟動心跳
            self._heartbeat_tasks[url_key] = asyncio.create_task(self._heartbeat_loop(url_key))

            try:
                async for raw in ws:
                    await self._handle_raw_message(raw)
            except Exception as e:
                logger.warning(f"XiaoyiChannel 連線異常 ({url_key}): {e}")
            finally:
                if self._heartbeat_tasks.get(url_key):
                    self._heartbeat_tasks[url_key].cancel()
                    self._heartbeat_tasks[url_key] = None
                self._ws_connections[url_key] = None
                self._send_locks.pop(url_key, None)
                close_code = getattr(ws, "close_code", None)
                close_reason = getattr(ws, "close_reason", None)
                logger.info(
                    f"XiaoyiChannel 連線關閉 {url_key}: {url} (code={close_code}, reason={close_reason})"
                )

    async def _send_init_message(self, url_key: str) -> None:
        """傳送初始化訊息 (clawd_bot_init) 到指定通道."""
        ws = self._ws_connections.get(url_key)
        if not ws:
            return
        init_message = {
            "msgType": "clawd_bot_init",
            "agentId": self.config.agent_id,
        }
        try:
            await self._safe_ws_send(url_key, init_message)
            logger.info(f"XiaoyiChannel 已傳送初始化訊息 ({url_key})")
        except Exception as e:
            logger.warning(f"XiaoyiChannel 傳送初始化訊息失敗 ({url_key}): {e}")
            raise

    async def _heartbeat_loop(self, url_key: str) -> None:
        """應用層心跳迴圈（20秒間隔）."""
        while self._running and self._ws_connections.get(url_key):
            try:
                heartbeat = {"msgType": "heartbeat", "agentId": self.config.agent_id}
                await self._safe_ws_send(url_key, heartbeat)
            except Exception as e:
                logger.warning(f"XiaoyiChannel 心跳傳送失敗 ({url_key}): {e}")
                ws = self._ws_connections.get(url_key)
                if ws:
                    try:
                        await ws.close()
                    except Exception as close_error:
                        logger.warning(f"XiaoyiChannel 關閉連線失敗 ({url_key}): {close_error}")
                break
            await asyncio.sleep(20)

    async def _handle_raw_message(self, raw: str | bytes) -> None:
        """處理接收到的原始訊息，轉換為 JiuwenClaw 內部格式."""
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"XiaoyiChannel JSON 解析失敗: {e}")
            return

        msg_type = message.get("msgType")
        method = message.get("method")

        # 新增詳細日誌用於診斷工具訊息
        if method or (msg_type and msg_type != "heartbeat"):
            logger.info(f"[XiaoyiChannel] _handle_raw_message: msg_type={msg_type},"
                        f"method={method}, sessionId={message.get('sessionId', 'N/A')}")

        if msg_type == "heartbeat":
            return

        # 根級直連 A2A（jsonrpc 2.0）須含 params.sessionId，否則整幀丟棄
        if message.get("jsonrpc") == "2.0":
            params_root = message.get("params")
            if not isinstance(params_root, dict):
                params_root = {}
            sid = params_root.get("sessionId")
            if sid is None or (isinstance(sid, str) and not sid.strip()):
                logger.warning(
                    "XiaoyiChannel 直連 A2A 缺少有效 params.sessionId，跳過本幀（與 xy_channel 一致）"
                )
                return

        await self._dispatch_gui_agent_events(message)

        # 檢查是否是 data-only 訊息（工具執行結果）
        data_event = self._extract_data_event(message)
        if data_event:
            logger.info(f"XiaoyiChannel 收到 data-event: {data_event.intent_name}, status={data_event.status}")
            await self._handle_data_event(data_event)
            return

        # GUI / UploadExeResult 等已在 _dispatch_gui_agent_events 與 _extract_data_event 中處理，勿再落 unknown method。
        if msg_type == "data":
            return

        method = message.get("method")
        if method == "message/stream":
            await self._handle_message_stream(message)
        elif method == "clearContext":
            await self._handle_clear_context(message)
        elif method == "tasks/cancel":
            await self._handle_tasks_cancel(message)
        else:
            # 服務端 JSON-RPC 僅含 data parts 的工具回包（如純 GUI 響應）無 method 欄位
            if not method and not msg_type and message.get("jsonrpc") == "2.0":
                parts = self._get_a2a_parts(message)
                if parts and all(p.get("kind") == "data" for p in parts):
                    return
            logger.warning(f"XiaoyiChannel 未知方法: {method}")

    async def _handle_message_stream(self, message: dict[str, Any]) -> None:
        """處理 message/stream 訊息，轉換為 JiuwenClaw Message."""
        session_id = message.get("sessionId") or message.get("params", {}).get("sessionId", "")
        task_id = message.get("params", {}).get("id", ) or ""
        user_message = message.get("params", {}).get("message", {})
        parts = user_message.get("parts", [])

        # Mark session as active
        self._mark_session_active(session_id)
        self._session_task_map[task_id] = session_id

        # ==================== PROCESS PARTS (TEXT & FILES) ====================
        text = ""
        file_attachments: list[str] = []
        media_files: list[dict[str, Any]] = []

        for part in parts:
            kind = part.get("kind")
            if kind == "text" and part.get("text"):
                text += part.get("text", "")
            elif kind == "file" and part.get("file"):
                file_info = part["file"]
                uri = file_info.get("uri")
                mime_type = file_info.get("mimeType", "")
                name = file_info.get("name", "")

                if not uri:
                    logger.warning(f"XiaoYi: File part without URI, skipping: {name}")
                    continue

                try:
                    media_files.append({"uri": uri, "mime_type": mime_type, "name": name})

                    # For text-based files, extract content inline
                    from jiuwenclaw.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_utils.media import \
                        is_text_mime_type, extract_text_from_url
                    if is_text_mime_type(mime_type):
                        try:
                            text_content = await extract_text_from_url(uri, 5_000_000, 30_000)
                            text += f"\n\n[檔案內容: {name}]\n{text_content}"
                            file_attachments.append(f"[檔案: {name}]")
                            logger.info(f"XiaoYi: Successfully extracted text from: {name}")
                        except Exception:
                            logger.warning(f"XiaoYi: Text extraction failed for {name}, will download as binary")
                            file_attachments.append(f"[檔案: {name}]")
                    else:
                        file_attachments.append(f"[檔案: {name}]")
                except Exception as e:
                    logger.error(f"XiaoYi: Failed to process file {name}: {e}")
                    file_attachments.append(f"[檔案處理失敗: {name}]")
            elif kind == "data":
                data = part.get("data", {})
                if isinstance(data, dict):
                    push_id = data.get("variables", {}).get("systemVariables", {}).get("push_id", "")
                    self.config.push_id = push_id if push_id else self.config.push_id
        # =================================================================

        # Log summary of processed attachments
        if file_attachments:
            logger.info(f"XiaoYi: Processed {len(file_attachments)} file(s): {', '.join(file_attachments)}")

        # ==================== DOWNLOAD AND SAVE MEDIA FILES ====================
        media_payload: dict[str, Any] = {}
        if media_files:
            logger.info(f"XiaoYi: Downloading {len(media_files)} media file(s)...")
            from jiuwenclaw.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_utils.media import (
                MediaFile,
                MediaDownloadOptions,
                download_and_save_media_list,
                build_xiaoyi_media_payload,
            )
            files_to_download = [
                MediaFile(uri=f["uri"], mime_type=f["mime_type"], name=f["name"])
                for f in media_files
            ]
            options = MediaDownloadOptions(max_bytes=30_000_000, timeout_ms=60_000)
            downloaded_media = await download_and_save_media_list(files_to_download, options)
            logger.info(f"XiaoYi: Successfully downloaded {len(downloaded_media)}/{len(media_files)} file(s)")
            media_payload = build_xiaoyi_media_payload(downloaded_media)
        # =================================================================

        # 將最近一次可回發的小藝身份寫入 config.yaml，供 cron 推送時使用
        try:
            from jiuwenclaw.common.config import update_channel_in_config

            rpc_id = message.get("id")
            update_channel_in_config(
                "xiaoyi",
                {
                    "last_session_id": session_id or "",
                    "last_task_id": task_id or "",
                    "last_message_id": str(rpc_id) if rpc_id is not None else "",
                },
            )
        except Exception as config_error:
            logger.warning(f"XiaoyiChannel 更新配置失敗: {config_error}")

        # ==================== BUILD MESSAGE AND ROUTE ====================
        # 平臺身份寫入 metadata，供回發時使用（與 session_id 解耦，\new_session 後仍可正確回發）
        metadata = {
            "method": "message/stream",
            "xiaoyi_session_id": session_id,
            "xiaoyi_task_id": task_id,
        }
        # Add media payload to metadata
        params = {"query": text, "task_id": task_id}
        if media_payload:
            params["files"] = media_payload

        user_message = Message(
            id=message.get("id", ""),
            type="req",
            channel_id=self.channel_id,
            session_id=session_id,
            params=params,
            timestamp=time.time(),
            is_stream=self.config.enable_streaming,
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            chat_id=session_id,
            metadata=metadata,
        )

        # ==================== START TASK TIMEOUT PROTECTION ====================
        # Start 1-hour task timeout timer
        task_timeout_ms = self.config.task_timeout_ms
        logger.info(f"[TASK TIMEOUT] Starting {task_timeout_ms}ms task timeout protection for session {session_id}")

        async def task_timeout_handler():
            """1-hour task timeout handler."""
            try:
                await asyncio.sleep(task_timeout_ms / 1000)
                logger.info(f"[TASK TIMEOUT] 1-hour timeout triggered for session {session_id}")
                # Send default message with is_final=true
                for url_key in list(self._ws_connections.keys()):
                    await self._send_text_response(session_id, task_id, "任務還在處理中~", url_key, is_final=True)
                # Mark session as waiting for push state
                self._mark_session_waiting_for_push(session_id, task_id)
            except asyncio.CancelledError:
                pass

        self._task_timeout_tasks[session_id] = asyncio.create_task(task_timeout_handler())

        # Start 60-second periodic timeout for status updates
        async def periodic_timeout_handler():
            """60-second periodic timeout for status updates."""
            try:
                while session_id in self._session_active:
                    await asyncio.sleep(60)
                    # Skip if already waiting for push (1-hour timeout triggered)
                    if self._is_session_waiting_for_push(session_id, task_id):
                        break
                    # Send status update
                    await self._send_status_update(task_id, session_id, "任務正在處理中，請稍後~")
            except asyncio.CancelledError:
                pass

        self._session_timeout_tasks[session_id] = asyncio.create_task(periodic_timeout_handler())
        # =================================================================

        handled = False
        if self._on_message_cb is not None:
            result = self._on_message_cb(user_message)
            if inspect.isawaitable(result):
                result = await result
            handled = bool(result)

        if not handled:
            await self.bus.route_user_message(user_message)

        # Start session heartbeat to prevent xiaoyi client timeout
        if not self.config.enable_streaming and session_id:
            await self._start_session_heartbeat(session_id, task_id)

    async def _start_session_heartbeat(self, session_id: str, task_id: str) -> None:
        """啟動會話心跳任務，每隔5秒傳送空訊息直到final訊息發出."""
        await self._stop_session_heartbeat(session_id)

        async def heartbeat_loop():
            try:
                while self._running:
                    await asyncio.sleep(5)
                    # Send empty heartbeat message (non-final)
                    for url_key, ws in self._ws_connections.items():
                        if ws:
                            try:
                                await self._send_text_response(
                                    session_id,
                                    task_id,
                                    "",
                                    url_key,
                                    append=True,
                                    is_final=False,
                                )
                            except Exception as e:
                                logger.warning(f"XiaoyiChannel 傳送心跳訊息失敗 ({url_key}): {e}")
            except asyncio.CancelledError:
                logger.info(f"XiaoyiChannel 會話心跳已停止: {session_id}")
            except Exception as e:
                logger.warning(f"XiaoyiChannel 會話心跳異常 ({session_id}): {e}")

        self._session_heartbeat_tasks[session_id] = asyncio.create_task(heartbeat_loop())
        logger.info(f"XiaoyiChannel 會話心跳已啟動: {session_id}")

    async def _stop_session_heartbeat(self, session_id: str) -> None:
        """停止會話心跳任務."""
        if session_id in self._session_heartbeat_tasks:
            task = self._session_heartbeat_tasks[session_id]
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._session_heartbeat_tasks.pop(session_id, None)
            logger.info(f"XiaoyiChannel 會話心跳已停止: {session_id}")

    async def _send_status_update(self, task_id: str, session_id: str, message: str) -> None:
        """傳送狀態更新訊息（A2A 格式）."""
        response = {
            "jsonrpc": "2.0",
            "id": f"msg_{int(time.time() * 1000)}",
            "result": {
                "taskId": task_id,
                "kind": "status-update",
                "final": False,
                "status": {
                    "message": {
                        "role": "agent",
                        "parts": [{"kind": "text", "text": message}],
                    },
                    "state": "working",
                },
            },
        }
        # Send to all active connections
        for url_key in list(self._ws_connections.keys()):
            await self._send_agent_response(session_id, task_id, response, url_key)

    async def _send_status_update_with_state(
            self, task_id: str, session_id: str, message: str, state: str, url_key: str
    ) -> None:
        """傳送狀態更新訊息（A2A 格式），支援自定義狀態."""
        response = {
            "jsonrpc": "2.0",
            "id": f"msg_{int(time.time() * 1000)}",
            "result": {
                "taskId": task_id,
                "kind": "status-update",
                "final": False,
                "status": {
                    "message": {
                        "role": "agent",
                        "parts": [{"kind": "text", "text": message}],
                    },
                    "state": state,
                },
            },
        }
        await self._send_agent_response(session_id, task_id, response, url_key)

    def _is_session_active(self, session_id: str) -> bool:
        """檢查會話是否有活躍任務."""
        return session_id in self._session_active

    def _mark_session_active(self, session_id: str) -> None:
        """標記會話為活躍狀態."""
        self._session_active.add(session_id)

    def _mark_session_completed(self, session_id: str) -> None:
        """標記會話已完成."""
        self._session_active.discard(session_id)

    def _is_session_waiting_for_push(self, session_id: str, task_id: str) -> bool:
        """檢查會話是否正在等待推送."""
        return self._sessions_waiting_for_push.get(session_id) == task_id

    def _mark_session_waiting_for_push(self, session_id: str, task_id: str) -> None:
        """標記會話正在等待推送."""
        self._sessions_waiting_for_push[session_id] = task_id

    def _clear_session_waiting_for_push(self, session_id: str, task_id: str) -> None:
        """清除會話的推送等待狀態."""
        if self._sessions_waiting_for_push.get(session_id) == task_id:
            self._sessions_waiting_for_push.pop(session_id, None)

    def _is_session_pending_cleanup(self, session_id: str) -> bool:
        """檢查會話是否待清理."""
        return session_id in self._sessions_marked_for_cleanup

    def _mark_session_for_cleanup(self, session_id: str, reason: str = "unknown") -> None:
        """標記會話待清理."""
        self._sessions_marked_for_cleanup[session_id] = {
            "reason": reason,
            "marked_at": time.time(),
        }

    def _force_cleanup_session(self, session_id: str) -> None:
        """強制清理會話."""
        self._sessions_marked_for_cleanup.pop(session_id, None)
        self._session_task_map.pop(session_id, None)

    async def _handle_clear_context(self, message: dict[str, Any]) -> None:
        """處理清空上下文請求."""
        session_id = message.get("sessionId", "")
        logger.info(f"XiaoyiChannel 清空上下文: {session_id}")

        # Check if there's an active task for this session
        if self._is_session_active(session_id):
            logger.info(f"[CLEAR] Active task exists for session {session_id}, will continue in background")
            # Mark session for cleanup (delayed cleanup)
            self._mark_session_for_cleanup(session_id, "user_cleared")
        else:
            logger.info(f"[CLEAR] No active task for session {session_id}, clean up immediately")
            self._force_cleanup_session(session_id)

        response = {
            "jsonrpc": "2.0",
            "id": message.get("id", ""),
            "result": {"status": {"state": "cleared"}},
        }
        # Send response to all active connections
        for url_key in list(self._ws_connections.keys()):
            await self._send_agent_response(session_id, session_id, response, url_key)

    async def _handle_tasks_cancel(self, message: dict[str, Any]) -> None:
        """處理取消任務請求."""
        session_id = message.get("sessionId", "")
        task_id = message.get("params", {}).get("id") or message.get("taskId", "")
        logger.info(f"XiaoyiChannel 取消任務: {session_id} {task_id}")
        if session_id:
            await self._stop_session_heartbeat(session_id)

        response = {
            "jsonrpc": "2.0",
            "id": message.get("id", ""),
            "result": {"id": message.get("id", ""), "status": {"state": "canceled"}},
        }
        # Send response to all active connections
        for url_key in list(self._ws_connections.keys()):
            await self._send_agent_response(session_id, task_id, response, url_key)

        # 清理超時任務和推送狀態
        self._clear_task_timeout(session_id)
        self._clear_session_timeout(session_id)
        self._clear_session_waiting_for_push(session_id, task_id)
        self._mark_session_completed(session_id)

    async def _send_text_response(
            self,
            session_id: str,
            task_id: str,
            text: str,
            url_key: str,
            *,
            append: bool = False,
            last_chunk: bool = True,
            is_final: bool = True,
    ) -> None:
        """傳送文字響應（A2A 格式）到指定通道."""
        if last_chunk:
            data = {"kind": "text", "text": text}
        else:
            data = {"kind": "reasoningText", "reasoningText": text}
        response = {
            "jsonrpc": "2.0",
            "id": f"msg_{int(time.time() * 1000)}",
            "result": {
                "taskId": task_id,
                "kind": "artifact-update",
                "append": append,
                "lastChunk": last_chunk,
                "final": is_final,
                "artifact": {
                    "artifactId": f"artifact_{int(time.time() * 1000)}",
                    "parts": [data],
                },
            },
        }
        await self._send_agent_response(session_id, task_id, response, url_key)

    async def _send_agent_response(self, session_id: str, task_id: str, response: dict[str, Any], url_key: str) -> None:
        """傳送 agent_response 包裝的訊息（A2A 格式）到指定通道."""
        wrapper = {
            "msgType": "agent_response",
            "agentId": self.config.agent_id,
            "sessionId": session_id,
            "taskId": task_id,
            "msgDetail": json.dumps(response),
        }
        try:
            await self._safe_ws_send(url_key, wrapper)
        except Exception as e:
            logger.warning(f"XiaoyiChannel 傳送響應失敗 ({url_key}): {e}")

    async def _send_file_response_base64(self, session_id: str, task_id: str, file_info: dict, url_key: str) -> None:
        """傳送檔案響應（Base64 格式）到指定通道."""
        try:
            file_path = file_info.get("fullPath", "")
            if not file_path or not os.path.exists(file_path):
                logger.error(f"send file failed, caused by file not exist. file path: {file_path}")
                return
            file_name = os.path.basename(file_info.get("fileName", ""))
            file_name = file_name if file_name else os.path.basename(file_path)

            # Check file size (limit to 20MB for Base64)
            base_url = self.file_upload_config.get("baseUrl")
            api_key = self.file_upload_config.get("apiKey")
            uid = self.file_upload_config.get("uid")

            if not all([base_url, api_key, uid]):
                logger.error("XiaoyiChannel OSMS配置不完整，無法上傳大檔案")
                return

            object_id = ""
            mime_type = FILE_TYPE_TO_MIME_TYPE.get(file_name.split(".")[-1], "text/plain")
            async with XYFileUploadService(base_url, api_key, uid) as upload_service:
                object_id = await upload_service.upload_file(file_path)
                logger.info(f"file upload success: {object_id}")
                if object_id:
                    # Send file reference response
                    payload = {
                        "jsonrpc": "2.0",
                        "id": task_id,
                        "result": {
                            "kind": "artifact-update",
                            "append": True,
                            "lastChunk": False,
                            "isFinal": False,
                            "artifact": {
                                "artifactId": task_id,
                                "parts": [
                                    {
                                        "kind": "file",
                                        "file": {
                                            "fileId": object_id,
                                            "name": file_name,
                                            "mimeType": mime_type
                                        }
                                    }
                                ],
                            },
                        },
                        "error": {
                            "code": 0
                        }
                    }
                    response = {
                        "msgType": "agent_response",
                        "agentId": self.config.agent_id,
                        "sessionId": session_id,
                        "taskId": task_id,
                        "msgDetail": json.dumps(payload)
                    }
                    await self._safe_ws_send(url_key, response)
            return object_id
        except Exception as e:
            logger.error(f"XiaoyiChannel 傳送檔案響應失敗: {e}")

    async def _send_file_response(self, session_id: str, task_id: str, file_info: dict, url_key: str) -> None:
        """傳送檔案響應到指定通道."""
        try:
            # If file is available locally, send as Base64
            if file_info.get("fullPath"):
                await self._send_file_response_base64(session_id, task_id, file_info, url_key)
                return
        except Exception as e:
            logger.error(f"XiaoyiChannel 傳送檔案響應失敗: {e}")

    async def _safe_ws_send(self, url_key: str, payload: dict[str, Any]) -> None:
        ws = self._ws_connections.get(url_key)
        if not ws:
            raise RuntimeError(f"ws connection not available: {url_key}")
        lock = self._send_locks.get(url_key)
        if lock is None:
            lock = asyncio.Lock()
            self._send_locks[url_key] = lock
        data = json.dumps(payload, ensure_ascii=False)
        async with lock:
            await ws.send(data)

    async def send_agent_response_to_all(
            self, session_id: str, task_id: str, response: dict[str, Any]
    ) -> None:
        """向所有活躍 WebSocket 連線傳送預構建的 agent_response 訊息.

        Args:
            session_id: 會話 ID
            task_id: 任務 ID
            response: 已包含 msgType、agentId 等欄位的完整訊息體
        """
        sent = False
        for url_key in list(self._ws_connections.keys()):
            try:
                await self._safe_ws_send(url_key, response)
                sent = True
            except Exception as e:
                logger.warning(
                    "XiaoyiChannel send_agent_response_to_all 失敗 (%s): %s",
                    url_key,
                    e,
                )
        if not sent:
            raise RuntimeError("傳送檔案訊息失敗，WebSocket 未連線")

    def _clear_task_timeout(self, session_id: str) -> None:
        """清除任務超時任務."""
        if session_id in self._task_timeout_tasks:
            task = self._task_timeout_tasks[session_id]
            if task and not task.done():
                task.cancel()
            self._task_timeout_tasks.pop(session_id, None)

    def _clear_session_timeout(self, session_id: str) -> None:
        """清除會話超時任務."""
        if session_id in self._session_timeout_tasks:
            task = self._session_timeout_tasks[session_id]
            if task and not task.done():
                task.cancel()
            self._session_timeout_tasks.pop(session_id, None)

    async def _send_push_notification(self, text: str, push_text: str) -> bool:
        """傳送推送通知."""
        if not (self.config.api_id):
            logger.info("[PUSH] Push not configured, skipping")
            return False

        try:
            push_config = PushConfig(
                mode=self.config.mode,
                api_id=self.config.api_id,
                push_id=self.config.push_id,
                push_url=self.config.push_url,
                ak=self.config.ak,
                sk=self.config.sk,
                uid=self.config.uid,
                api_key=self.config.api_key
            )
            push_service = XiaoYiPushService(push_config)
            result = await push_service.send_push(text, push_text)
            logger.info(f"[PUSH] Push notification sent: {result}")
            return result
        except Exception as e:
            logger.error(f"[PUSH] Error sending push: {e}")
            return False

    async def send_xiaoyi_phone_tools_command(
            self,
            session_id: str,
            task_id: str,
            message_id: str,
            command: dict[str, Any],
    ) -> bool:
        """傳送 Command 指令到手機端（A2A artifact-update 格式）.

        Args:
            session_id: 會話 ID
            task_id: 任務 ID
            message_id: 訊息 ID（用於 JSON-RPC id）
            command: Command 資料結構，包含 header 和 payload

        Returns:
            是否傳送成功
        """
        response = {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "taskId": task_id,
                "kind": "artifact-update",
                "append": False,
                "lastChunk": True,
                "final": False,
                "artifact": {
                    "artifactId": str(uuid.uuid4()),
                    "parts": [{"kind": "data", "data": {"commands": [command]}}],
                },
            },
        }

        # OutboundWebSocketMessage：msgType/agentId/sessionId/taskId/msgDetail（msgDetail 為 JSON 字串）
        wrapper = {
            "msgType": "agent_response",
            "agentId": self.config.agent_id,
            "sessionId": session_id,
            "taskId": task_id,
            "msgDetail": json.dumps(response, ensure_ascii=False),
        }

        # 傳送到所有活躍連線
        sent = False
        for url_key, ws in self._ws_connections.items():
            if ws:
                try:
                    await self._safe_ws_send(url_key, wrapper)
                    intent_name = command.get("payload", {}).get("executeParam", {}).get("intentName") or command.get(
                        "header", {}
                    ).get("name", "unknown")
                    logger.info(f"XiaoyiChannel 傳送 command 成功 ({url_key}):intent={intent_name}")
                    sent = True
                except Exception as e:
                    logger.warning(f"XiaoyiChannel 傳送 command 失敗 ({url_key}): {e}")

        return sent

    def _get_a2a_parts(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """從直連或 Wrapped A2A 訊息中取出 message.parts."""
        msg_type = message.get("msgType")
        if msg_type == "data":
            try:
                a2a_request = json.loads(message.get("msgDetail", "{}"))
            except json.JSONDecodeError:
                return []
            params = a2a_request.get("params", {})
        else:
            params = message.get("params", {})
        msg = params.get("message", {})
        parts = msg.get("parts", [])
        return parts if isinstance(parts, list) else []

    async def _dispatch_gui_agent_events(self, message: dict[str, Any]) -> None:
        """分發 InvokeJarvisGUIAgentResponse（data.events 內）.

        各 handler 獨立 try/except，單個工具回撥異常不影響同幀其他 handler 及後續 data-event。
        """
        if len(self._gui_agent_handlers) > 1:
            logger.warning(
                "XiaoyiChannel GUI handler 數量=%s，可能存在併發未序列化",
                len(self._gui_agent_handlers),
            )
        for part in self._get_a2a_parts(message):
            if part.get("kind") != "data":
                continue
            events = part.get("data", {}).get("events", [])
            if not isinstance(events, list):
                continue
            for item in events:
                if (
                        item.get("header", {}).get("namespace") == "ClawAgent"
                        and item.get("header", {}).get("name") == "InvokeJarvisGUIAgentResponse"
                ):
                    for h in list(self._gui_agent_handlers):
                        try:
                            if asyncio.iscoroutinefunction(h):
                                await h(item)
                            else:
                                h(item)
                        except Exception as e:
                            logger.warning(
                                "XiaoyiChannel GUI agent 處理器異常（已隔離）: %s",
                                e,
                                exc_info=True,
                            )

    def register_gui_agent_handler(self, handler: Callable[[dict[str, Any]], Any]) -> None:
        """註冊 InvokeJarvisGUIAgentResponse 處理器."""
        if handler not in self._gui_agent_handlers:
            self._gui_agent_handlers.append(handler)
            logger.info("XiaoyiChannel 註冊 GUI agent 處理器")

    def unregister_gui_agent_handler(self, handler: Callable[[dict[str, Any]], Any]) -> None:
        """登出 GUI agent 處理器."""
        try:
            self._gui_agent_handlers.remove(handler)
            logger.info("XiaoyiChannel 登出 GUI agent 處理器")
        except ValueError:
            pass

    def register_data_event_handler(
            self, intent_name: str, handler: Callable[[DataEvent], Any]
    ) -> None:
        """註冊 data-event 處理器.

        Args:
            intent_name: 要監聽的 intent 名稱（如 "GetCurrentLocation"）
            handler: 處理函式，接收 DataEvent 引數
        """
        if intent_name not in self._data_event_handlers:
            self._data_event_handlers[intent_name] = []
        if handler not in self._data_event_handlers[intent_name]:
            self._data_event_handlers[intent_name].append(handler)
            logger.info(f"XiaoyiChannel 註冊 data-event 處理器: {intent_name}")

    def unregister_data_event_handler(
            self, intent_name: str, handler: Callable[[DataEvent], Any]
    ) -> None:
        """登出 data-event 處理器.

        Args:
            intent_name: intent 名稱
            handler: 要移除的處理函式
        """
        if intent_name in self._data_event_handlers:
            try:
                self._data_event_handlers[intent_name].remove(handler)
                logger.info(f"XiaoyiChannel 登出 data-event 處理器: {intent_name}")
            except ValueError:
                pass

    async def _handle_data_event(self, event: DataEvent) -> None:
        """分發 data-event 到註冊的處理器."""
        logger.info(f"[XiaoyiChannel] 分發 data-event: intent={event.intent_name}, status={event.status}")
        logger.info(f"[XiaoyiChannel] 已註冊處理器: {list(self._data_event_handlers.keys())}")

        handlers = self._data_event_handlers.get(event.intent_name, [])
        if not handlers:
            logger.warning(f"[XiaoyiChannel] 無處理器處理 data-event: {event.intent_name}")
            return

        logger.info(f"[XiaoyiChannel] 找到 {len(handlers)} 個處理器 for {event.intent_name}")

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                logger.warning(f"XiaoyiChannel data-event 處理器異常 ({event.intent_name}): {e}")

    def _extract_data_event(self, message: dict[str, Any]) -> DataEvent | None:
        """從 A2A 訊息中提取 data-event（如果是 data-only 訊息）.

        支援三種訊息格式：
        1. Direct A2A format: 直接包含 params.message.parts
        2. Wrapped format (msgType="data"): A2A 內容在 msgDetail 中
        3. UploadExeResult 格式: header.name="UploadExeResult" + payload.intentName + payload.outputs

        Args:
            message: 解析後的 A2A 訊息

        Returns:
            DataEvent 或 None（如果不是 data-only 訊息）
        """
        # Wrapped format：msgType="data"，msgDetail 為巢狀的 A2A JSON-RPC 字串
        msg_type = message.get("msgType")
        method = message.get("method")
        if msg_type == "data":
            try:
                # 從 msgDetail 解析 A2A JSON-RPC 請求
                a2a_request = json.loads(message.get("msgDetail", "{}"))
                params = a2a_request.get("params", {})
                msg = params.get("message", {})
                parts = msg.get("parts", [])
                session_id = message.get("sessionId", "")
            except json.JSONDecodeError as e:
                logger.info(
                    f"[XiaoyiChannel] _extract_data_event: msgDetail JSON 解析失敗: {e}"
                )
                return None
            except KeyError as e:
                logger.info(
                    f"[XiaoyiChannel] _extract_data_event: Wrapped A2A 缺少欄位: {e}"
                )
                return None
        else:
            # Direct A2A format
            params = message.get("params", {})
            msg = params.get("message", {})
            parts = msg.get("parts", [])
            session_id = message.get("sessionId", "")

        if not parts:
            return None

        # 檢查是否所有 parts 都是 data 型別
        data_parts = [p for p in parts if p.get("kind") == "data"]
        if not data_parts or len(data_parts) != len(parts):
            return None

        # 提取 data 內容
        for part in data_parts:
            data = part.get("data", {})
            events = data.get("events", [])
            if not isinstance(events, list):
                continue

            for event in events:
                intent_name = ""
                outputs = {}
                status = "success"  # 未顯式給出時與直接格式預設一致

                # 格式 1: 直接格式 (events[].intentName)
                if event.get("intentName"):
                    intent_name = event.get("intentName", "")
                    outputs = event.get("outputs", {})
                    status = event.get("status", "success")

                # 格式 2: UploadExeResult 包裝格式 (header.name + payload)
                elif event.get("header", {}).get("name") == "UploadExeResult":
                    payload = event.get("payload", {})
                    intent_name = payload.get("intentName", "")
                    outputs = payload.get("outputs", {})
                    # UploadExeResult 格式預設 status 為 success
                    status = payload.get("status", "success") or "success"

                # 格式 3: InvokeJarvisGUIAgentResponse（GUI 工具響應，跳過）
                elif event.get("header", {}).get("namespace") == "ClawAgent" and \
                        event.get("header", {}).get("name") == "InvokeJarvisGUIAgentResponse":
                    # GUI 響應不處理，繼續檢查下一個 event
                    continue

                if intent_name:
                    outputs_keys = list(outputs.keys())
                    logger.info(f"[XiaoyiChannel] Extracted data-event: intent={intent_name}, "
                                f"status={status}, outputs_keys={outputs_keys}")
                    return DataEvent(
                        intent_name=intent_name,
                        outputs=outputs,
                        status=status,
                        session_id=message.get("sessionId", ""),
                        task_id=params.get("id", ""),
                    )

        return None
