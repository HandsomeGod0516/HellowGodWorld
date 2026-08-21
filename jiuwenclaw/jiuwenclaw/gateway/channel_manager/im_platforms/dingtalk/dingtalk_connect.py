# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, Field
import httpx

from jiuwenclaw.gateway.channel_manager.base import RobotMessageRouter, BaseChannel
from jiuwenclaw.gateway.channel_manager.im_platforms.dingtalk.dingtalk_file_service import DingTalkFileService
from jiuwenclaw.common.schema.message import Message, ReqMethod
from jiuwenclaw.common.utils import get_agent_workspace_dir

logger = logging.getLogger(__name__)


class DingTalkConfig(BaseModel):
    """釘釘通道配置（使用Stream模式）"""
    enabled: bool = False
    client_id: str = ""  # 應用ID
    client_secret: str = ""  # 應用金鑰
    allow_from: list[str] = Field(default_factory=list)  # 允許的員工ID
    # 檔案處理配置
    max_download_size: int = 100 * 1024 * 1024  # 最大下載檔案大小（預設 100MB）
    download_timeout: int = 60  # 下載超時時間（秒）
    send_file_allowed: bool = True  # 是否啟用檔案上傳功能
    enable_file_download: bool = True  # 是否啟用檔案下載功能
    workspace_dir: str = ""  # 工作空間目錄


@dataclass
class DingTalkInboundMessage:
    """釘釘入站訊息載體，避免引數列表持續膨脹。"""

    content: str
    sender_id: str
    sender_name: str
    conversation_id: str
    conversation_type: str
    files: list[dict] | None = None


@dataclass
class DingTalkMessageSendRequest:
    """釘釘訊息傳送請求引數封裝。"""

    token: str
    chat_id: str
    conversation_type: str
    open_conversation_id: str
    file_path: str
    file_name: str


@dataclass
class DingTalkFileNotificationRequest:
    """釘釘檔案通知請求引數封裝。"""

    token: str
    chat_id: str
    conversation_type: str
    open_conversation_id: str
    file_path: str
    file_name: str
    error_msg: str = ""


@dataclass
class DingTalkMediaMessageRequest:
    """釘釘媒體訊息傳送請求引數封裝。"""

    token: str
    chat_id: str
    conversation_type: str
    open_conversation_id: str
    msg_key: str
    msg_param: str


@dataclass
class DingTalkImageSendRequest:
    """釘釘圖片傳送請求引數封裝。"""

    token: str
    chat_id: str
    conversation_type: str
    open_conversation_id: str
    file_path: str


try:
    from dingtalk_stream import (
        DingTalkStreamClient,
        Credential,
        CallbackHandler,
        CallbackMessage,
        AckMessage,
    )
    from dingtalk_stream.chatbot import ChatbotMessage

    DINGTALK_AVAILABLE = True
except ImportError:
    DINGTALK_AVAILABLE = False
    CallbackHandler = object
    CallbackMessage = None
    AckMessage = None
    ChatbotMessage = None


class DingTalkHandler(CallbackHandler):
    """
    釘釘Stream SDK標準回撥處理器。
    解析傳入訊息並轉發到通道。
    """

    def __init__(self, channel: "DingTalkChannel"):
        super().__init__()
        self.channel = channel

    def _extract_text_content(self, chatbot_msg: ChatbotMessage, raw_data: dict) -> str:
        """從訊息物件中提取文字內容"""
        content = ""
        if chatbot_msg.text:
            content = chatbot_msg.text.content.strip()
        if not content:
            content = raw_data.get("text", {}).get("content", "").strip()
        return content

    def _extract_sender_info(self, chatbot_msg: ChatbotMessage) -> tuple[str, str]:
        """提取傳送者資訊"""
        sender_id = chatbot_msg.sender_staff_id or chatbot_msg.sender_id
        sender_name = chatbot_msg.sender_nick or "Unknown"
        return sender_id, sender_name

    def _extract_conversation_info(self, chatbot_msg: ChatbotMessage) -> tuple[str, str]:
        """提取會話資訊"""
        conversation_id = chatbot_msg.conversation_id or ""
        conversation_type = chatbot_msg.conversation_type or "1"  # 1: 單聊；2：群聊
        return conversation_id, conversation_type

    def _create_message_task(self, message: DingTalkInboundMessage) -> None:
        """建立非同步任務處理訊息"""
        task = asyncio.create_task(
            self.channel.handle_incoming_message(message)
        )
        self.channel._background_tasks.add(task)
        task.add_done_callback(self.channel._background_tasks.discard)

    async def process(self, message: CallbackMessage):
        """處理傳入的流訊息"""
        try:
            # 使用SDK的ChatbotMessage進行健壯解析
            chatbot_msg = ChatbotMessage.from_dict(message.data)
            raw_data = message.data
            msg_type = raw_data.get("msgtype", "text")

            # 提取傳送者資訊
            sender_id, sender_name = self._extract_sender_info(chatbot_msg)

            # 提取會話資訊
            conversation_id, conversation_type = self._extract_conversation_info(chatbot_msg)

            # 許可權檢查（所有訊息型別）
            if not self.channel.is_allowed(sender_id):
                logger.warning(f"傳送者 {sender_id} 未被允許使用此機器人")
                return AckMessage.STATUS_OK, "OK"

            # 根據訊息型別處理
            content = ""
            files = None

            if msg_type == "text":
                content = self._extract_text_content(chatbot_msg, raw_data)
            elif msg_type == "picture":
                content, files = await self.channel.handle_picture_message(
                    raw_data, sender_id, conversation_id, conversation_type
                )
            elif msg_type == "file":
                content, files = await self.channel.handle_file_message(
                    raw_data, sender_id, conversation_id, conversation_type
                )
            elif msg_type == "audio" or msg_type == "voice":
                content, files = await self.channel.handle_audio_message(
                    raw_data, sender_id, conversation_id, conversation_type
                )
            elif msg_type == "video":
                content, files = await self.channel.handle_video_message(
                    raw_data, sender_id, conversation_id, conversation_type
                )
            else:
                content = f"[不支援的訊息型別: {msg_type}]"
                logger.warning(f"收到不支援的訊息型別: {msg_type}")

            if not content and not files:
                logger.warning(f"收到空訊息: {msg_type}")
                return AckMessage.STATUS_OK, "OK"

            logger.info(
                f"收到來自 {sender_name} ({sender_id}) 的釘釘訊息: {content[:50]}... (會話ID: {conversation_id})"
            )

            # 轉發到通道（非阻塞）
            self._create_message_task(
                DingTalkInboundMessage(
                    content=content,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    conversation_id=conversation_id,
                    conversation_type=conversation_type,
                    files=files,
                )
            )

            return AckMessage.STATUS_OK, "OK"

        except Exception as e:
            logger.error(f"處理釘釘訊息時出錯: {e}")
            # 返回OK以避免釘釘伺服器重試迴圈
            return AckMessage.STATUS_OK, "Error"


class DingTalkChannel(BaseChannel):
    """
    使用Stream模式的釘釘通道。

    透過 `dingtalk-stream` SDK 使用 WebSocket 接收事件。
    使用直接 HTTP API 傳送訊息（SDK主要用於接收）。
    """

    name = "dingtalk"

    def __init__(self, config: DingTalkConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: DingTalkConfig = config
        self._client: Any = None
        self._http: httpx.AsyncClient | None = None

        self._access_token: str | None = None
        self._token_expiry: float = 0
        self._background_tasks: set[asyncio.Task] = set()

        self._gateway_callback: Callable[[Message], None] | None = None
        self._stream_task: asyncio.Task | None = None  # 用於跟蹤 SDK start() 任務

        # 檔案服務
        self._file_service: DingTalkFileService | None = None
        # 按 request_id 記錄已傳送檔案路徑，避免重複傳送
        self._sent_file_paths_by_req: dict[str, set[str]] = {}

    @property
    def channel_id(self) -> str:
        """返回通道的唯一標識"""
        return self.name

    def on_message(self, callback: Callable[[Message], None]) -> None:
        """註冊釘釘通道的回撥函式"""
        self._gateway_callback = callback

    async def _handle_message(
            self,
            chat_id: str,
            content: str,
            metadata: dict[str, Any] | None = None
    ) -> None:
        """處理來自釘釘通道的傳入訊息（符合基類介面）"""
        # 檢查傳送者許可權
        if not self.is_allowed(chat_id):
            logger.warning(f"傳送者 {chat_id} 未被允許使用此機器人")
            return

        # 呼叫內部處理方法
        await self._process_incoming_message(
            chat_id=chat_id,
            sender_id=chat_id,
            content=content,
            conversation_id="",
            conversation_type="1",
            metadata=metadata,
        )

    def _build_user_message(self, chat_id: str, sender_id: str, content: str,
                            conversation_id: str, conversation_type: str,
                            metadata: dict[str, Any] | None = None,
                            files: list[dict] | None = None) -> Message:
        """構建使用者訊息物件"""
        metadata = metadata or {}
        metadata.update({"conversation_id": conversation_id, "conversation_type": conversation_type})
        params = {"content": content, "query": content}
        if files:
            params["files"] = files
        return Message(
            id=chat_id,
            type="req",
            channel_id=self.name,
            session_id=str(chat_id),
            params=params,
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            chat_id=conversation_id,
            metadata=metadata,
        )

    async def _process_incoming_message(self, chat_id: str, sender_id: str, content: str, conversation_id: str,
                                        conversation_type: str, metadata: dict[str, Any] | None = None,
                                        files: list[dict] | None = None) -> None:
        """處理來自釘釘通道的傳入訊息"""
        msg = self._build_user_message(chat_id, sender_id, content, conversation_id, conversation_type, metadata, files)

        if self._gateway_callback:
            self._gateway_callback(msg)
        else:
            await self.bus.route_user_message(msg)

    def _validate_config(self) -> bool:
        """驗證配置是否有效"""
        if not DINGTALK_AVAILABLE:
            logger.error(
                "釘釘Stream SDK未安裝。請執行: pip install dingtalk-stream"
            )
            return False

        if not self.config.client_id or not self.config.client_secret:
            logger.error("釘釘 client_id 和 client_secret 未配置")
            return False

        return True

    def _initialize_stream_client(self) -> None:
        """初始化釘釘Stream客戶端"""
        logger.info("正在初始化釘釘Stream客戶端")
        credential = Credential(self.config.client_id, self.config.client_secret)
        self._client = DingTalkStreamClient(credential)

        # 註冊標準處理器
        handler = DingTalkHandler(self)
        self._client.register_callback_handler(ChatbotMessage.TOPIC, handler)

        logger.info("釘釘機器人已啟動（Stream模式）")

    async def start(self) -> None:
        """啟動釘釘機器人（Stream模式）"""
        try:
            if not self._validate_config():
                return

            self._running = True
            self._http = httpx.AsyncClient()

            # 初始化檔案服務
            workspace_dir = self.config.workspace_dir or str(get_agent_workspace_dir())
            self._file_service = DingTalkFileService(
                client_id=self.config.client_id,
                get_token_func=self._get_access_token,
                http_client=self._http,
                max_download_size=self.config.max_download_size,
                download_timeout=self.config.download_timeout,
                workspace_dir=workspace_dir,
            )

            self._initialize_stream_client()

            # 將 SDK start() 作為獨立任務執行，便於在 stop() 時取消
            self._stream_task = asyncio.create_task(self._client.start(), name="dingtalk-sdk-start")

            # 等待任務完成（當 _running=False 時，任務會被取消）
            try:
                await self._stream_task
            except asyncio.CancelledError:
                logger.info("釘釘 Stream 任務已被取消")
            except Exception as e:
                logger.warning(f"釘釘 Stream 任務異常退出: {e}")

        except Exception as e:
            logger.exception(f"啟動釘釘通道失敗: {e}")

    async def stop(self) -> None:
        """停止釘釘機器人"""
        self._running = False

        # 取消 SDK start() 任務
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await asyncio.wait_for(self._stream_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("等待釘釘 Stream 任務取消超時")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"等待釘釘 Stream 任務取消時出錯: {e}")
        self._stream_task = None

        # 關閉 WebSocket 連線
        if self._client and hasattr(self._client, 'websocket') and self._client.websocket:
            try:
                await self._client.websocket.close()
            except Exception as e:
                logger.warning(f"關閉 WebSocket 連線時出錯: {e}")

        # 清理客戶端
        if self._client:
            try:
                # 檢查 SDK 是否提供 stop 方法
                if hasattr(self._client, 'stop'):
                    await self._client.stop()
                # 檢查 SDK 是否提供 close 方法
                elif hasattr(self._client, 'close'):
                    await self._client.close()
                # 檢查 SDK 是否提供 shutdown 方法
                elif hasattr(self._client, 'shutdown'):
                    await self._client.shutdown()
            except Exception as e:
                logger.warning(f"停止 DingTalkStreamClient 時出錯: {e}")
            finally:
                self._client = None

        # 關閉共享HTTP客戶端
        if self._http:
            await self._http.aclose()
            self._http = None

        # 取消未完成的後臺任務
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()

    def _is_token_valid(self) -> bool:
        """檢查當前令牌是否有效"""
        return self._access_token is not None and time.time() < self._token_expiry

    def _build_token_request_data(self) -> dict:
        """構建令牌請求資料"""
        return {
            "appKey": self.config.client_id,
            "appSecret": self.config.client_secret,
        }

    def _parse_token_response(self, res_data: dict) -> None:
        """解析令牌響應"""
        self._access_token = res_data.get("accessToken")
        # 提前60秒過期以確保安全
        self._token_expiry = time.time() + int(res_data.get("expireIn", 7200)) - 60

    async def _request_new_token(self) -> str | None:
        """請求新的訪問令牌"""
        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        data = self._build_token_request_data()

        if not self._http:
            logger.warning("釘釘HTTP客戶端未初始化，無法重新整理令牌")
            return None

        try:
            resp = await self._http.post(url, json=data)
            resp.raise_for_status()
            res_data = resp.json()
            self._parse_token_response(res_data)
            return self._access_token
        except Exception as e:
            logger.error(f"獲取釘釘訪問令牌失敗: {e}")
            return None

    async def _get_access_token(self) -> str | None:
        """獲取或重新整理訪問令牌"""
        if self._is_token_valid():
            return self._access_token

        return await self._request_new_token()

    def _extract_message_content(self, msg: Message) -> str | None:
        """從訊息物件中提取內容"""
        if msg.params and "content" in msg.params:
            return str(msg.params["content"])
        elif msg.payload and "content" in msg.payload:
            content_ = msg.payload["content"]
            if isinstance(content_, dict) and "output" in content_:
                return str(content_["output"])
            return str(content_)
        elif msg.payload and "text" in msg.payload:
            return str(msg.payload["text"])
        return None

    def _extract_chat_id(self, msg: Message) -> str | None:
        """從訊息物件中提取聊天ID"""
        chat_id = msg.id if msg.id else None
        if not chat_id:
            chat_id = msg.session_id
        return chat_id

    def _build_group_message_payload(self, content: str, open_conversation_id: str) -> dict:
        """構建群聊訊息負載"""
        return {
            "robotCode": self.config.client_id,
            "openConversationId": open_conversation_id,
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps({
                "text": content,
                "title": "JiuClaw Reply",
            }),
        }

    def _build_private_message_payload(self, chat_id: str, content: str) -> dict:
        """構建私聊訊息負載"""
        return {
            "robotCode": self.config.client_id,
            "userIds": [chat_id],
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps({
                "text": content,
                "title": "JiuClaw Reply",
            }),
        }

    def _get_send_api_url(self, conversation_type: str) -> str:
        """根據會話型別獲取傳送API URL"""
        if conversation_type == "2":
            return "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
        else:
            return "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"

    def _build_send_request(self, chat_id: str, content: str, conversation_type: str, open_conversation_id: str) -> \
    tuple[str, dict]:
        """構建傳送請求"""
        url = self._get_send_api_url(conversation_type)

        if conversation_type == "2":
            data = self._build_group_message_payload(content, open_conversation_id)
        else:
            data = self._build_private_message_payload(chat_id, content)

        return url, data

    async def _send_http_request(self, url: str, data: dict, token: str, chat_id: str) -> None:
        """傳送HTTP請求"""
        headers = {"x-acs-dingtalk-access-token": token}

        if not self._http:
            logger.warning("釘釘HTTP客戶端未初始化，無法傳送訊息")
            return

        try:
            resp = await self._http.post(url, json=data, headers=headers)
            if resp.status_code != 200:
                logger.error(f"釘釘訊息傳送失敗: {resp.text}")
            else:
                logger.debug("釘釘訊息已傳送至 %s", chat_id)
        except Exception as e:
            logger.error(f"傳送釘釘訊息時出錯: {e}")

    async def send(self, msg: Message) -> None:
        """透過釘釘傳送訊息"""
        token = await self._get_access_token()
        if not token:
            return

        # 提取事件型別
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        event_type = getattr(msg.event_type, "value", None) or payload.get("event_type") or ""

        # 處理檔案傳送事件（chat.media 與 chat.file 統一走檔案傳送路徑）
        if event_type in ("chat.file", "chat.media"):
            await self._send_file_message(msg)
            return

        # 提取內容
        content = self._extract_message_content(msg)
        if not content:
            logger.warning("釘釘傳送: 在 msg.params 或 msg.payload 中未找到內容")
            return

        # 提取聊天ID
        chat_id = self._extract_chat_id(msg)
        if not chat_id:
            logger.warning("釘釘傳送: 在訊息中未找到 chat_id 或 session_id")
            return

        # 構建請求
        metadata = msg.metadata or {}
        conversation_type = metadata.get("conversation_type", "")
        open_conversation_id = metadata.get("conversation_id", "")
        url, data = self._build_send_request(chat_id, content, conversation_type, open_conversation_id)

        # 傳送HTTP請求
        await self._send_http_request(url, data, token, chat_id)

        # chat.final 兜底檔案傳送
        if event_type == "chat.final":
            await self._send_fallback_files(msg, content)

    # ==================== 檔案下載處理方法 ====================

    async def handle_picture_message(
            self, raw_data: dict, sender_id: str, conversation_id: str, conversation_type: str
    ) -> tuple[str, list[dict] | None]:
        """處理圖片訊息"""
        if not self.config.enable_file_download:
            return "[圖片: 檔案下載功能已禁用]", None

        if not self._file_service:
            return "[圖片: 檔案服務未初始化]", None

        content = raw_data.get("content", {})
        download_code = content.get("downloadCode", "")
        message_id = raw_data.get("msgId", sender_id)

        if not download_code:
            return "[圖片: 缺少下載碼]", None

        file_info = await self._file_service.download_image(download_code, message_id)
        if not file_info:
            return "[圖片: 下載失敗]", None

        return "[圖片]", [file_info]

    async def handle_file_message(
            self, raw_data: dict, sender_id: str, conversation_id: str, conversation_type: str
    ) -> tuple[str, list[dict] | None]:
        """處理檔案訊息"""
        if not self.config.enable_file_download:
            return "[檔案: 檔案下載功能已禁用]", None

        if not self._file_service:
            return "[檔案: 檔案服務未初始化]", None

        content = raw_data.get("content", {})
        download_code = content.get("downloadCode", "")
        file_name = content.get("fileName", "unknown_file")
        file_size = content.get("fileSize", 0)
        message_id = raw_data.get("msgId", sender_id)

        if not download_code:
            return "[檔案: 缺少下載碼]", None

        # 檢查檔案大小（僅當檔案大小已知且過大時跳過）
        # 注意：釘釘訊息可能不包含 fileSize 欄位，所以只在已知大小時檢查
        if file_size > 0 and file_size > self.config.max_download_size:
            return f"[檔案過大: {file_name}]", None

        file_info = await self._file_service.download_file(download_code, message_id, file_name)
        if not file_info:
            return f"[檔案: {file_name} 下載失敗]", None

        # 下載後檢查實際檔案大小
        if file_info.get("size", 0) == 0:
            logger.warning(f"下載的檔案為空: {file_name}")
            return "[空檔案]", None

        return f"[檔案: {file_name}]", [file_info]

    async def handle_audio_message(
            self, raw_data: dict, sender_id: str, conversation_id: str, conversation_type: str
    ) -> tuple[str, list[dict] | None]:
        """處理音訊訊息"""
        if not self.config.enable_file_download:
            return "[音訊: 檔案下載功能已禁用]", None

        if not self._file_service:
            return "[音訊: 檔案服務未初始化]", None

        content = raw_data.get("content", {})
        download_code = content.get("downloadCode", "")
        duration = content.get("duration", 0)
        message_id = raw_data.get("msgId", sender_id)

        if not download_code:
            return "[音訊: 缺少下載碼]", None

        file_info = await self._file_service.download_audio(download_code, message_id)
        if not file_info:
            return "[音訊: 下載失敗]", None

        duration_str = f" {duration / 1000:.1f}s" if duration else ""
        return f"[音訊{duration_str}]", [file_info]

    async def handle_video_message(
            self, raw_data: dict, sender_id: str, conversation_id: str, conversation_type: str
    ) -> tuple[str, list[dict] | None]:
        """處理影片訊息"""
        if not self.config.enable_file_download:
            return "[影片: 檔案下載功能已禁用]", None

        if not self._file_service:
            return "[影片: 檔案服務未初始化]", None

        content = raw_data.get("content", {})
        download_code = content.get("downloadCode", "")
        duration = content.get("duration", 0)
        message_id = raw_data.get("msgId", sender_id)

        if not download_code:
            return "[影片: 缺少下載碼]", None

        file_info = await self._file_service.download_video(download_code, message_id)
        if not file_info:
            return "[影片: 下載失敗]", None

        duration_str = f" {duration / 1000:.1f}s" if duration else ""
        return f"[影片{duration_str}]", [file_info]

    async def handle_incoming_message(self, message: DingTalkInboundMessage) -> None:
        """處理傳入訊息（由DingTalkHandler呼叫）

        委託給 _process_incoming_message()，該方法在釋出到匯流排之前執行 allow_from
        許可權檢查。
        """
        try:
            logger.info(f"釘釘入站訊息: {message.content} 來自 {message.sender_name}")
            await self._process_incoming_message(
                chat_id=message.sender_id,
                sender_id=message.sender_id,
                content=str(message.content),
                conversation_id=message.conversation_id,
                conversation_type=message.conversation_type,
                metadata={
                    "sender_name": message.sender_name,
                    "platform": "dingtalk",
                    "dingtalk_chat_id": message.conversation_id,
                    "dingtalk_sender_id": message.sender_id,
                },
                files=message.files,
            )
        except Exception as e:
            logger.error(f"釋出釘釘訊息時出錯: {e}")

    # ==================== 檔案傳送方法 ====================

    def _extract_receive_info(self, msg: Message) -> tuple[str, str, str]:
        """從訊息中提取接收者資訊。

        Returns:
            (chat_id, conversation_type, open_conversation_id)
        """
        metadata = msg.metadata or {}
        conversation_type = metadata.get("conversation_type", "1")
        open_conversation_id = metadata.get("conversation_id", "")

        # 根據會話型別選擇正確的 chat_id：
        # - 私聊 (type=1): Robot API userIds 需要員工ID (sender_staff_id)
        # - 群聊 (type=2): Robot API openConversationId 需要 conversation_id
        if conversation_type == "2":
            # 群聊：使用 conversation_id
            chat_id = metadata.get("dingtalk_chat_id") or metadata.get("dingtalk_sender_id") or ""
        else:
            # 私聊：使用 sender_staff_id（員工ID）
            chat_id = metadata.get("dingtalk_sender_id") or metadata.get("dingtalk_chat_id") or ""

        # 回退到 session_id
        if not chat_id:
            chat_id = getattr(msg, "session_id", "") or msg.id or ""

        return chat_id, conversation_type, open_conversation_id

    async def _send_file_message(self, msg: Message) -> None:
        """傳送檔案訊息"""
        if not self._file_service or not self.config.send_file_allowed:
            logger.warning("釘釘檔案傳送功能未啟用")
            return

        payload = msg.payload if isinstance(msg.payload, dict) else {}
        files = payload.get("files", [])
        if not files:
            return

        chat_id, conversation_type, open_conversation_id = self._extract_receive_info(msg)
        if not chat_id:
            logger.warning("釘釘檔案傳送: 未找到接收者")
            return

        # 獲取當前 request_id 用於去重
        request_id = getattr(msg, "id", "") or ""
        if request_id not in self._sent_file_paths_by_req:
            self._sent_file_paths_by_req[request_id] = set()

        token = await self._get_access_token()
        if not token:
            logger.error("釘釘檔案傳送: 無法獲取 access_token")
            return

        for file_info in files:
            file_path = file_info.get("path", "")
            if not file_path or not os.path.isfile(file_path):
                logger.warning(f"釘釘檔案傳送: 檔案不存在 {file_path}")
                continue

            # 檢查是否已傳送
            if file_path in self._sent_file_paths_by_req[request_id]:
                continue

            # 根據檔案型別選擇傳送方法
            ext = os.path.splitext(file_path)[1].lower()
            file_name = os.path.basename(file_path)

            try:
                if ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}:
                    await self._send_image(
                        DingTalkImageSendRequest(
                            token=token,
                            chat_id=chat_id,
                            conversation_type=conversation_type,
                            open_conversation_id=open_conversation_id,
                            file_path=file_path,
                        )
                    )
                elif ext in {'.mp3', '.wav', '.aac', '.ogg', '.flac', '.m4a'}:
                    await self._send_audio(
                        DingTalkMessageSendRequest(
                            token=token,
                            chat_id=chat_id,
                            conversation_type=conversation_type,
                            open_conversation_id=open_conversation_id,
                            file_path=file_path,
                            file_name=file_name,
                        )
                    )
                elif ext == '.mp4':
                    await self._send_video(
                        DingTalkMessageSendRequest(
                            token=token,
                            chat_id=chat_id,
                            conversation_type=conversation_type,
                            open_conversation_id=open_conversation_id,
                            file_path=file_path,
                            file_name=file_name,
                        )
                    )
                else:
                    await self._send_file(
                        DingTalkMessageSendRequest(
                            token=token,
                            chat_id=chat_id,
                            conversation_type=conversation_type,
                            open_conversation_id=open_conversation_id,
                            file_path=file_path,
                            file_name=file_name,
                        )
                    )

                self._sent_file_paths_by_req[request_id].add(file_path)
                logger.info(f"釘釘檔案已傳送: {file_name}")

            except Exception as e:
                logger.error(f"釘釘檔案傳送失敗: {file_name} - {e}")
                # 傳送失敗時通知使用者
                await self._send_file_notification(
                    DingTalkFileNotificationRequest(
                        token=token,
                        chat_id=chat_id,
                        conversation_type=conversation_type,
                        open_conversation_id=open_conversation_id,
                        file_path=file_path,
                        file_name=file_name,
                        error_msg=str(e),
                    )
                )

        # 清理過期的傳送記錄
        if len(self._sent_file_paths_by_req) > 100:
            keys_to_remove = list(self._sent_file_paths_by_req.keys())[:50]
            for key in keys_to_remove:
                del self._sent_file_paths_by_req[key]

    async def _send_image(self, request: DingTalkImageSendRequest) -> None:
        """傳送圖片訊息"""
        media_id = await self._file_service.upload_media(request.file_path, "image")
        if not media_id:
            raise Exception("圖片上傳失敗")

        msg_param = json.dumps({"photoURL": media_id})
        await self._send_media_message(
            DingTalkMediaMessageRequest(
                token=request.token,
                chat_id=request.chat_id,
                conversation_type=request.conversation_type,
                open_conversation_id=request.open_conversation_id,
                msg_key="sampleImageMsg",
                msg_param=msg_param,
            )
        )

    async def _send_audio(self, request: DingTalkMessageSendRequest) -> None:
        """傳送音訊訊息"""
        media_id = await self._file_service.upload_media(request.file_path, "voice")
        if not media_id:
            raise Exception("音訊上傳失敗")

        msg_param = json.dumps({"mediaId": media_id})
        await self._send_media_message(
            DingTalkMediaMessageRequest(
                token=request.token,
                chat_id=request.chat_id,
                conversation_type=request.conversation_type,
                open_conversation_id=request.open_conversation_id,
                msg_key="sampleAudio",
                msg_param=msg_param,
            )
        )

    async def _send_video(self, request: DingTalkMessageSendRequest) -> None:
        """傳送影片訊息"""
        media_id = await self._file_service.upload_media(request.file_path, "video")
        if not media_id:
            raise Exception("影片上傳失敗")

        msg_param = json.dumps({
            "videoMediaId": media_id,
            "videoType": "mp4"
        })
        await self._send_media_message(
            DingTalkMediaMessageRequest(
                token=request.token,
                chat_id=request.chat_id,
                conversation_type=request.conversation_type,
                open_conversation_id=request.open_conversation_id,
                msg_key="sampleVideo",
                msg_param=msg_param,
            )
        )

    async def _send_file(self, request: DingTalkMessageSendRequest) -> None:
        """傳送檔案訊息

        使用機器人訊息 API 傳送檔案訊息，msgKey 為 sampleFile。
        支援各種檔案型別：音訊、影片、PPT、Excel、PDF、壓縮包等。

        參考：https://open.dingtalk.com/document/orgapp/chatbots-send-one-on-one-chat-messages-in-batches
        """
        # 上傳檔案獲取 mediaId
        media_id = await self._file_service.upload_media(request.file_path, "file")
        if not media_id:
            raise Exception("檔案上傳失敗")

        # 獲取副檔名用於 fileType
        ext = os.path.splitext(request.file_name)[1].lower().lstrip('.')
        if not ext:
            ext = "stream"

        # 使用機器人 API 傳送檔案訊息
        # msgKey: sampleFile
        # msgParam: {"mediaId": "xxx", "fileName": "xxx", "fileType": "xxx"}
        msg_param = json.dumps({
            "mediaId": media_id,
            "fileName": request.file_name,
            "fileType": ext,
        })

        await self._send_media_message(
            DingTalkMediaMessageRequest(
                token=request.token,
                chat_id=request.chat_id,
                conversation_type=request.conversation_type,
                open_conversation_id=request.open_conversation_id,
                msg_key="sampleFile",
                msg_param=msg_param,
            )
        )
        logger.info(f"[DingTalk] 檔案傳送成功: {request.file_name} -> {request.chat_id}")

    async def _send_file_notification(self, request: DingTalkFileNotificationRequest) -> None:
        """傳送檔案通知（Markdown 訊息），用於檔案傳送失敗時的備用通知。"""
        # 獲取檔案大小
        try:
            file_size = os.path.getsize(request.file_path)
            if file_size < 1024:
                size_str = f"{file_size} B"
            elif file_size < 1024 * 1024:
                size_str = f"{file_size / 1024:.1f} KB"
            else:
                size_str = f"{file_size / (1024 * 1024):.1f} MB"
        except Exception:
            size_str = "未知大小"

        if request.error_msg:
            markdown_content = (
                f"### 檔案傳送失敗\n\n"
                f"**檔名**: {request.file_name}\n\n"
                f"**大小**: {size_str}\n\n"
                f"**錯誤**: {request.error_msg}\n\n"
                f"**路徑**: `{request.file_path}`"
            )
        else:
            markdown_content = (
                f"### 檔案已生成\n\n"
                f"**檔名**: {request.file_name}\n\n"
                f"**大小**: {size_str}\n\n"
                f"**路徑**: `{request.file_path}`"
            )

        msg_param = json.dumps({
            "title": "檔案通知",
            "text": markdown_content,
        })
        await self._send_media_message(
            DingTalkMediaMessageRequest(
                token=request.token,
                chat_id=request.chat_id,
                conversation_type=request.conversation_type,
                open_conversation_id=request.open_conversation_id,
                msg_key="sampleMarkdown",
                msg_param=msg_param,
            )
        )

    async def _send_media_message(self, request: DingTalkMediaMessageRequest) -> None:
        """傳送媒體訊息"""
        if request.conversation_type == "2":
            # 群聊
            url = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
            data = {
                "robotCode": self.config.client_id,
                "openConversationId": request.open_conversation_id,
                "msgKey": request.msg_key,
                "msgParam": request.msg_param,
            }
        else:
            # 私聊
            url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
            data = {
                "robotCode": self.config.client_id,
                "userIds": [request.chat_id],
                "msgKey": request.msg_key,
                "msgParam": request.msg_param,
            }

        headers = {"x-acs-dingtalk-access-token": request.token}

        if not self._http:
            raise Exception("HTTP 客戶端未初始化")

        response = await self._http.post(url, json=data, headers=headers)
        if response.status_code != 200:
            raise Exception(f"傳送失敗: {response.text}")

    # ==================== 兜底檔案傳送 ====================

    def _detect_workspace_files(self, text: str, workspace_dir: str) -> list[str]:
        """檢測文字中提到的 workspace 檔案路徑。

        支援兩種模式：
        1. 完整絕對路徑匹配
        2. 從引號/書名號中提取檔名，在 workspace 下查詢

        Args:
            text: 訊息文字
            workspace_dir: 工作空間目錄

        Returns:
            存在的檔案路徑列表
        """
        if not workspace_dir or not text:
            return []

        found_files = []

        # 模式1：完整絕對路徑匹配
        # 匹配類似 /home/xxx/.jiuwenclaw/agent/workspace/xxx.ext 的路徑
        path_pattern = re.compile(
            r'(?:^|["\'「「【《\s])(' + re.escape(workspace_dir) + r'[^\s"\'」」】》]+\.\w{1,10})(?:$|["\'」」】》\s])',
            re.MULTILINE
        )
        for match in path_pattern.finditer(text):
            path = match.group(1).strip()
            if os.path.isfile(path):
                found_files.append(path)

        # 模式2：從引號/書名號中提取檔名
        # 匹配 "filename.ext"、'filename.ext'、「filename.ext」、《filename.ext》
        filename_pattern = re.compile(
            r'["\'「「【《]([^"\'」」】》]+\.\w{1,10})["\'」」】》]'
        )
        for match in filename_pattern.finditer(text):
            filename = match.group(1).strip()
            # 在 workspace 下查詢同名檔案
            potential_path = os.path.join(workspace_dir, filename)
            if os.path.isfile(potential_path) and potential_path not in found_files:
                found_files.append(potential_path)

        return found_files

    async def _send_fallback_files(self, msg: Message, content: str) -> None:
        """chat.final 兜底檔案傳送。

        當 LLM 未呼叫 send_file_to_user 但在回覆中提到了檔案時，
        自動檢測併傳送這些檔案。
        """
        if not self._file_service or not self.config.send_file_allowed:
            return

        workspace_dir = self.config.workspace_dir or str(get_agent_workspace_dir())
        if not os.path.isdir(workspace_dir):
            return

        # 檢測檔案路徑
        file_paths = self._detect_workspace_files(content, workspace_dir)
        if not file_paths:
            return

        # 獲取當前 request_id 用於去重
        request_id = getattr(msg, "id", "") or ""
        sent_paths = self._sent_file_paths_by_req.get(request_id, set())

        # 過濾已傳送的檔案
        new_files = [p for p in file_paths if p not in sent_paths]
        if not new_files:
            return

        logger.info(f"釘釘兜底檔案傳送: 檢測到 {len(new_files)} 個未傳送檔案")

        # 構造檔案訊息併傳送
        files_payload = [{"path": p, "name": os.path.basename(p)} for p in new_files]
        fallback_msg = Message(
            id=msg.id,
            type=msg.type,
            channel_id=self.name,
            session_id=msg.session_id,
            payload={"event_type": "chat.file", "files": files_payload},
            metadata=msg.metadata,
            chat_id=getattr(msg, "chat_id", None),
        )
        await self._send_file_message(fallback_msg)
