# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
import asyncio
import concurrent.futures
import os
import types
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

import requests
from pydantic import BaseModel, Field

from jiuwenclaw.gateway.channel_manager.base import RobotMessageRouter, BaseChannel
from jiuwenclaw.gateway.channel_manager.im_platforms.platform_adapter.message import MessageStore
from jiuwenclaw.common.schema.message import Message, ReqMethod, EventType
from jiuwenclaw.gateway.channel_manager.im_platforms.feishu.feishu_file_service import (
    FeishuFileService,
    is_image_file,
    is_audio_file,
    is_video_file,
)



logger = logging.getLogger(__name__)


class FeishuConfig(BaseModel):
    """飛書通道配置模型，使用WebSocket長連線接收訊息。"""

    enabled: bool = False  # 是否啟用飛書通道
    app_id: str = ""  # 飛書開放平臺的應用ID
    app_secret: str = ""  # 飛書開放平臺的應用金鑰
    encrypt_key: str = ""  # 事件訂閱的加密金鑰（可選）
    verification_token: str = ""  # 事件訂閱的驗證令牌（可選）
    allow_from: list[str] = Field(default_factory=list)  # 允許的使用者的open_id列表
    enable_streaming: bool = True  # 是否開啟流式/過程訊息下發
    chat_id: str = ""  # 可選：固定推送目標 chat_id（群聊 oc_xxx 或個人 open_id）
    channel_id: str = "feishu"  # ChannelManager 路由鍵，支援多例項
    bot_key: str = ""  # 企業飛書多 bot 配置鍵（僅 feishu_enterprise 使用）
    # 收訊息時寫入 config.yaml，用於無 metadata 時的回發兜底（與 session_id 解耦）
    last_chat_id: str = ""
    last_open_id: str = ""

    # 檔案處理配置
    max_download_size: int = 100 * 1024 * 1024  # 最大下載檔案大小（預設100MB）
    download_timeout: int = 60  # 下載超時時間（秒）
    enable_file_upload: bool = True  # 是否啟用檔案上傳功能
    temp_file_dir: str = ""  # 臨時檔案儲存目錄，預設使用工作空間

    # 數字分身配置
    my_user_id: str = ""  # 可選：當前數字分身對應的使用者open_id
    bot_name: str = ""  # 可選：機器人在群聊中的名稱，用於@識別
    group_digital_avatar: bool = False  # 是否啟用群聊數字分身功能
    enable_memory: bool = False  # 是否啟用群聊記憶功能
    message_merge_window_ms: int = 15000  # 連續訊息合併視窗（毫秒）


try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateImageRequest,
        CreateImageRequestBody,
        CreateFileRequest,
        CreateFileRequestBody,
        CreateMessageRequest,
        CreateMessageRequestBody,
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        Emoji,
        P2ImMessageReceiveV1,
    )
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTrigger,
        P2CardActionTriggerResponse,
    )

    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    lark = None
    Emoji = None

# 非文字訊息型別的顯示佔位符對映
MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}


class _ThreadLocalLoopProxy:
    """Provide a thread-local event loop proxy for lark_oapi ws client."""

    @staticmethod
    def _get_loop() -> asyncio.AbstractEventLoop:
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    def run_until_complete(self, coro):
        return self._get_loop().run_until_complete(coro)

    def create_task(self, coro):
        return self._get_loop().create_task(coro)


@dataclass
class FeishuInboundMessage:
    """Feishu 入站訊息載體，避免引數列表持續膨脹。"""

    message_id: str
    chat_id: str
    content: str
    user_id: str | None = None
    bot_id: str | None = None
    metadata: dict[str, Any] | None = None
    params: dict[str, Any] | None = None


@dataclass
class FeishuMessageSendRequest:
    """飛書訊息傳送請求引數封裝。"""

    receive_id: str
    id_type: str
    msg_type: str
    content: str
    log_label: str = ""
    max_retries: int = 3


class FeishuChannel(BaseChannel):
    """
    飛書/飛書IM通道實現，基於WebSocket長連線。

    特性：
    - 使用WebSocket接收事件，無需公網IP或webhook
    - 支援群聊和私聊訊息
    - 自動新增"已讀"反應表情
    - 支援Markdown表格渲染為飛書表格元素

    依賴：
    - 飛書開放平臺的應用ID和應用金鑰
    - 機器人功能已啟用
    - 事件訂閱已啟用（im.message.receive_v1）
    """

    name = "feishu"
    _ws_loop_proxy_lock = threading.Lock()
    _ws_loop_proxy_installed = False

    def __init__(
        self,
        config: FeishuConfig,
        router: RobotMessageRouter,
        im_platform_adapter: Any | None = None,
    ):
        """
        初始化飛書通道例項。

        Args:
            config: 飛書配置物件
            router: 訊息路由器例項
            im_platform_adapter: 平臺介面卡例項（可選，用於數字分身功能）
        """
        super().__init__(config, router)
        self.config: FeishuConfig = config
        self._channel_id = str(getattr(config, "channel_id", "") or self.name).strip() or self.name
        self._api_client: Any = None  # 飛書API客戶端（用於傳送訊息）
        self._websocket_client: Any = None  # WebSocket客戶端（用於接收訊息）
        self._websocket_thread: threading.Thread | None = None  # WebSocket執行執行緒
        self._message_dedup_cache: OrderedDict[str, None] = OrderedDict()  # 訊息去重快取
        self._main_loop: asyncio.AbstractEventLoop | None = None  # 主執行緒事件迴圈
        self._ws_thread_loop: asyncio.AbstractEventLoop | None = None  # WebSocket執行緒事件迴圈
        self._message_callback: Callable[[Message], None] | None = None  # 閘道器模式回撥
        self._im_platform_adapter = im_platform_adapter
        self._message_storage = MessageStore(api_client=None, platform_adapter=self._im_platform_adapter)
        self._pending_message_batches: dict[tuple[str, str], dict[str, Any]] = {}
        self._pending_message_lock = asyncio.Lock()
        self._pending_group_progress_tasks: dict[str, asyncio.Task[None]] = {}
        self._sent_group_progress_requests: set[str] = set()
        self._stopping = False
        # 按 request_id 聚合 chat.delta，避免同一任務被拆分成多條訊息傳送到飛書。
        self._stream_text_buffers: dict[str, str] = {}
        # 檔案服務（延遲初始化）
        self._file_service: FeishuFileService | None = None
        # 按 request_id 記錄已透過 chat.file 傳送的檔案路徑，用於兜底去重
        # key=request_id, value=set of absolute file paths
        self._sent_file_paths_by_req: dict[str, set[str]] = {}
        # 自演進使用者確認卡片, key=request_id, value=query card
        self._user_question_card: dict[str, dict] = {}

    @property
    def channel_id(self) -> str:
        """返回通道唯一識別符號，用於ChannelManager註冊與訊息派發。"""
        return self._channel_id

    def on_message(self, callback: Callable[[Message], None]) -> None:
        """
        註冊訊息回撥函式，用於Gateway模式。

        當收到訊息時呼叫此回撥函式，而非透過router路由。

        Args:
            callback: 訊息回撥函式
        """
        self._message_callback = callback

    async def _handle_message(
        self,
        inbound: FeishuInboundMessage,
    ) -> None:
        """
        處理接收到的訊息並分發。

        若已透過on_message註冊閘道器回撥，則直接回撥；否則透過router路由訊息。

        Args:
            inbound: Feishu 入站訊息
        """
        from jiuwenclaw.gateway.routing.interaction_context import PendingInteraction

        msg_id = f"{self.channel_id}:{inbound.message_id}"
        params = {"content": inbound.content, "query": inbound.content} if inbound.params is None else inbound.params
        _meta = inbound.metadata or {}
        _chat_type = _meta.get("chat_type", "")
        _is_group = _chat_type == "group"
        _msg_enable_streaming = self.config.enable_streaming
        _is_stream = self.config.enable_streaming
        if self.config.group_digital_avatar and _is_group:
            _msg_enable_streaming = False
            _is_stream = False
            _meta = dict(_meta)
            _meta["avatar_mode"] = True
            _meta["principal_user_id"] = self._get_target_user_open_id()
            _meta["triggering_user_id"] = str(inbound.user_id or "")

        if not _is_group and self.config.group_digital_avatar:
            sender_open_id = str(inbound.user_id or "").strip()
            principal_id = self._get_target_user_open_id()
            if sender_open_id and sender_open_id == principal_id:
                pi = PendingInteraction.find_pending(self.channel_id, principal_id)
                if pi is not None:
                    _meta = dict(_meta)
                    _meta["avatar_mode"] = True
                    _meta["is_resume_message"] = True
                    _meta["principal_user_id"] = principal_id
                    _meta["dm_pending_interaction_id"] = pi.interaction_id

                    resume_msg = Message(
                        id=f"{self.channel_id}:resume:{inbound.message_id}",
                        type="req",
                        channel_id=self.channel_id,
                        session_id=pi.origin_session_id,
                        params={"content": inbound.content or "", "query": inbound.content or ""},
                        timestamp=time.time(), ok=True,
                        provider=self.name,
                        chat_id=pi.origin_session_id,
                        user_id=principal_id,
                        bot_id=str(inbound.bot_id or self.config.app_id or ""),
                        req_method=ReqMethod.CHAT_SEND,
                        is_stream=False,
                        metadata=_meta,
                        group_digital_avatar=True,
                        enable_memory=self.config.enable_memory,
                        enable_streaming=False,
                    )
                    if self._message_callback:
                        self._message_callback(resume_msg)
                    else:
                        await self.bus.route_user_message(resume_msg)
                    return

        _effective_group_digital_avatar = self.config.group_digital_avatar and _is_group
        msg = Message(
            id=msg_id,
            type="req",
            channel_id=self.channel_id,
            session_id=str(inbound.chat_id),
            params=params,
            timestamp=time.time(), ok=True,
            provider=self.name,
            chat_id=str(inbound.chat_id or ""),
            user_id=str(inbound.user_id or ""),
            bot_id=str(inbound.bot_id or self.config.app_id or ""),
            req_method=ReqMethod.CHAT_SEND,
            is_stream=_is_stream,
            metadata=_meta,
            group_digital_avatar=_effective_group_digital_avatar,
            enable_memory=self.config.enable_memory,
            enable_streaming=_msg_enable_streaming,
        )
        if self._message_callback:
            self._message_callback(msg)
        else:
            await self.bus.route_user_message(msg)

    async def start(self) -> None:
        """啟動飛書機器人，使用WebSocket長連線接收訊息。"""
        if not self._validate_start_conditions():
            return

        self._running = True
        self._main_loop = asyncio.get_running_loop()
        self._initialize_api_client()
        self._start_websocket_in_thread()

        logger.info("飛書機器人已啟動，使用WebSocket長連線接收訊息")
        logger.info("無需公網IP - 透過WebSocket接收事件")

        # 持續執行直到停止
        while self._running:
            await asyncio.sleep(1)

    def _validate_start_conditions(self) -> bool:
        """驗證啟動所需的條件是否滿足。"""
        if not FEISHU_AVAILABLE:
            logger.error("飛書SDK未安裝，請先安裝 lark_oapi")
            return False

        if not self.config.app_id or not self.config.app_secret:
            logger.error("飛書應用ID或應用金鑰未配置")
            return False

        return True

    def _initialize_api_client(self) -> None:
        """初始化飛書API客戶端，用於傳送訊息。"""
        self._api_client = (
            lark.Client.builder()
            .app_id(self.config.app_id)
            .app_secret(self.config.app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )
        self._message_storage.set_api_client(self._api_client)
        if self._im_platform_adapter and hasattr(self._im_platform_adapter, "set_api_client"):
            self._im_platform_adapter.set_api_client(self._api_client)
        if hasattr(self._message_storage, 'set_platform_adapter'):
            self._message_storage.set_platform_adapter(self._im_platform_adapter)
        # 初始化檔案服務
        from jiuwenclaw.common.utils import get_agent_workspace_dir
        workspace_dir = self.config.temp_file_dir or str(get_agent_workspace_dir())
        self._file_service = FeishuFileService(
            api_client=self._api_client,
            config=self.config,
            workspace_dir=workspace_dir,
        )

    def _start_websocket_in_thread(self) -> None:
        """在獨立執行緒中啟動WebSocket客戶端，避免事件迴圈衝突。"""
        config = {
            "app_id": self.config.app_id,
            "app_secret": self.config.app_secret,
            "encrypt_key": self.config.encrypt_key or "",
            "verification_token": self.config.verification_token or "",
        }

        self._websocket_thread = threading.Thread(
            target=self._run_websocket_client,
            args=(config,),
            daemon=True,
        )
        self._websocket_thread.start()

        # 等待WebSocket客戶端建立完成
        self._wait_for_websocket_client_ready()

    def _run_websocket_client(self, config: dict) -> None:
        """
        在子執行緒中執行WebSocket客戶端。

        Args:
            config: WebSocket配置引數
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._ws_thread_loop = loop

        # 將 SDK 的模組級 loop 替換為執行緒內代理，避免多例項時相互覆蓋。
        self._ensure_thread_local_ws_loop_proxy()

        ws_client = None
        try:
            event_handler = (
                lark.EventDispatcherHandler.builder(
                    config["encrypt_key"],
                    config["verification_token"],
                )
                .register_p2_im_message_receive_v1(self._on_message_sync)
                .register_p2_im_message_message_read_v1(self._on_message_read_event)
                .register_p2_card_action_trigger(self._on_card_action_trigger_sync)
                .build()
            )

            ws_client = lark.ws.Client(
                config["app_id"],
                config["app_secret"],
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
            )
            self._patch_ws_client_shutdown(ws_client)
            self._websocket_client = ws_client
            ws_client.start()
        except Exception as e:
            if self._stopping or not self._running:
                logger.info("飛書WebSocket執行緒退出: %s", e)
            else:
                logger.error("飛書WebSocket連線建立失敗: %s", e)
        finally:
            self._cleanup_websocket_thread(ws_client, loop)

    @classmethod
    def _ensure_thread_local_ws_loop_proxy(cls) -> None:
        with cls._ws_loop_proxy_lock:
            if cls._ws_loop_proxy_installed:
                return
            import lark_oapi.ws.client as _ws_client_mod
            _ws_client_mod.loop = _ThreadLocalLoopProxy()
            cls._ws_loop_proxy_installed = True

    def _cleanup_websocket_thread(self, ws_client: Any, loop: asyncio.AbstractEventLoop) -> None:
        """清理WebSocket執行緒資源。"""

        if ws_client is None:
            self._websocket_client = None

        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(asyncio.sleep(0))
        except Exception:
            pass

        try:
            loop.close()
        except Exception:
            pass

        self._ws_thread_loop = None

    def _wait_for_websocket_client_ready(self) -> None:
        """等待WebSocket客戶端建立完成。"""
        for _ in range(50):
            if self._websocket_client is not None:
                break
            time.sleep(0.1)

    async def stop(self) -> None:
        """停止飛書機器人。"""
        self._running = False
        self._stopping = True
        self._stream_text_buffers.clear()

        if self._websocket_client and self._ws_thread_loop and self._ws_thread_loop.is_running():
            try:
                await self._shutdown_ws_client()
            except Exception as e:
                logger.warning("停止WebSocket客戶端時發生異常: {}", e)

        if self._ws_thread_loop and self._ws_thread_loop.is_running():
            self._ws_thread_loop.call_soon_threadsafe(self._ws_thread_loop.stop)

        if self._websocket_thread and self._websocket_thread.is_alive():
            self._websocket_thread.join(timeout=2.0)

        logger.info("飛書機器人已停止")
        self._stopping = False

    async def _shutdown_ws_client(self) -> None:
        """在飛書 websocket 執行緒中執行斷連與任務清理."""
        loop = self._ws_thread_loop
        ws_client = self._websocket_client
        if loop is None or ws_client is None or not loop.is_running():
            return

        async def _shutdown() -> None:
            try:
                setattr(ws_client, "_auto_reconnect", False)
            except Exception:
                pass

            conn = getattr(ws_client, "_conn", None)
            if conn is not None:
                try:
                    await conn.close(code=1000, reason="bye")
                except Exception as e:
                    logger.debug("飛書連線關閉時出現異常: {}", e)

            await asyncio.sleep(0.05)

        fut = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
        try:
            await asyncio.wait_for(asyncio.wrap_future(fut), timeout=2.0)
        except concurrent.futures.CancelledError:
            pass
        except asyncio.TimeoutError:
            logger.debug("飛書客戶端清理超時，繼續停止事件迴圈")
        except Exception as e:
            logger.debug("飛書客戶端清理任務異常: {}", e)

    @staticmethod
    def _patch_ws_client_shutdown(ws_client: Any) -> None:
        """修復 lark_oapi 在併發關閉時可能觸發的 Lock release 異常."""
        original_disconnect = getattr(ws_client, "_disconnect", None)
        if not callable(original_disconnect):
            return
        if getattr(ws_client, "_disconnect_patched", False):
            return

        async def _safe_disconnect(self):
            try:
                return await original_disconnect()
            except RuntimeError as e:
                if "Lock is not acquired" in str(e):
                    logger.debug("忽略 lark_oapi 斷連併發異常: {}", e)
                    return None
                raise

        ws_client._disconnect = types.MethodType(_safe_disconnect, ws_client)
        ws_client._disconnect_patched = True

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> None:
        """
        新增訊息反應的同步方法（線上程池中執行）。

        Args:
            message_id: 訊息ID
            emoji_type: 表情型別
        """
        try:
            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )

            response = self._api_client.im.v1.message_reaction.create(request)

            if not response.success():
                logger.warning(
                    f"新增訊息反應失敗: 錯誤碼={response.code}, 訊息={response.msg}"
                )
            else:
                logger.debug("已為訊息 %s 新增 %s 表情", message_id, emoji_type)
        except Exception as e:
            logger.warning(f"新增訊息反應時發生異常: {e}")

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """
        為訊息新增反應表情符號（非阻塞）。

        常見表情符號型別：
        - THUMBSUP: 點贊
        - OK: 確認
        - EYES: 檢視
        - DONE: 完成
        - OnIt: 處理中
        - HEART: 愛心

        Args:
            message_id: 訊息ID
            emoji_type: 表情型別
        """
        if not self._api_client or not Emoji:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type)

    def _get_target_user_open_id(self) -> str:
        """返回當前數字分身對應的使用者 open_id。"""
        return str(
            (self.config.my_user_id or "").strip()
            or os.getenv("MY_USER_ID", "").strip()
        )

    @staticmethod
    def _extract_mentioned_users(message: Any) -> list[dict[str, str]]:
        """從飛書訊息事件中提取被 @ 使用者的 open_id 和姓名。"""
        mentions = getattr(message, "mentions", None) or []
        users: list[dict[str, str]] = []
        for mention in mentions:
            open_id = ""
            name = str(getattr(mention, "name", "") or "").strip()
            mention_id = getattr(mention, "id", None)
            if isinstance(mention_id, dict):
                open_id = str(mention_id.get("open_id") or mention_id.get("id") or "").strip()
            elif isinstance(mention_id, str):
                open_id = mention_id.strip()
            elif mention_id is not None:
                nested_open_id = getattr(mention_id, "open_id", None)
                if nested_open_id:
                    open_id = str(nested_open_id).strip()
            else:
                open_id = str(getattr(mention, "open_id", "") or "").strip()
            if open_id and all(user["open_id"] != open_id for user in users):
                users.append({"open_id": open_id, "name": name})
        return users

    def _resolve_user_display_name(self, open_id: str, fallback_name: str = "") -> str:
        """優先使用已知姓名，必要時再查詢飛書聯絡人資訊。"""
        if fallback_name.strip():
            return fallback_name.strip()
        if self._im_platform_adapter and hasattr(self._im_platform_adapter, 'get_user_name_by_open_id'):
            return self._im_platform_adapter.get_user_name_by_open_id(open_id).strip()
        else:
            return ""

    def _replace_mentions_with_names(self, message: Any, text: str) -> str:
        """
        將訊息中的 @mentions 佔位符（如 @_user_1）替換為真實使用者名稱。
        當 @all 時，替換為數字分身使用者本人的名字。

        Args:
            message: 飛書訊息物件
            text: 原始文字內容

        Returns:
            str: 替換後的文字內容
        """
        mentions = getattr(message, "mentions", None) or []

        result = text
        target_user_open_id = self._get_target_user_open_id()
        target_user_name = ""
        if target_user_open_id:
            target_user_name = self._resolve_user_display_name(target_user_open_id, "")

        if "@_all" in result and target_user_name:
            result = result.replace("@_all", f"@{target_user_name}")

        if not mentions:
            return result

        for mention in mentions:
            mention_key = getattr(mention, "key", None)

            if not mention_key:
                mention_id = getattr(mention, "id", None)
                if isinstance(mention_id, dict):
                    mention_key = mention_id.get("key", "")
                elif isinstance(mention_id, str):
                    if "_" in mention_id:
                        mention_key = mention_id.split("_")[-1]

            if not mention_key:
                continue

            name = str(getattr(mention, "name", "") or "").strip()
            if not name:
                bot_name = getattr(message, "bot_name", "") or ""
                name = bot_name or mention_key

            if mention_key.startswith("@"):
                old_pattern = mention_key
            else:
                old_pattern = f"@{mention_key}"
            new_pattern = f"@{name}"
            result = result.replace(old_pattern, new_pattern)

        return result

    _GROUP_PROGRESS_HINT_DELAY_SECONDS = 6.0
    _GROUP_PROGRESS_HINT_TEXTS: tuple[str, ...] = (
        "收到，我先看一下。",
        "我先確認下，馬上回復。",
        "這個我在處理，稍等我一下。",
    )

    @staticmethod
    def _should_send_group_ack(metadata: dict[str, Any]) -> bool:
        """僅在待辦/提醒類私發場景下，才補發群內短確認。"""
        if bool(metadata.get("is_cron_job")):
            return False
        if str(metadata.get("reply_scope") or "").strip().lower() != "dm":
            return False
        if str(metadata.get("reply_reason") or "").strip() not in {
            "mentioned_target_user",
            "processor_target_user",
        }:
            return False
        if not str(metadata.get("feishu_chat_id") or "").strip():
            return False
        return bool(metadata.get("reply_personal_action"))

    @staticmethod
    def _should_send_interaction_ack(metadata: dict[str, Any]) -> bool:
        """追問場景下，群內補發簡短確認。"""
        if bool(metadata.get("is_cron_job")):
            return False
        has_interaction = bool(metadata.get("interaction_mention_user")) or bool(
            metadata.get("interaction_question")
        )
        if not has_interaction:
            return False
        if not str(metadata.get("feishu_chat_id") or "").strip():
            return False
        return True

    async def _send_interaction_ack(self, metadata: dict[str, Any], content: str) -> None:
        """追問場景下在群內補發簡短確認。"""
        try:
            group_chat_id = str(metadata.get("feishu_chat_id") or "").strip()
            if not group_chat_id:
                return

            mention_name = str(metadata.get("interaction_mention_user") or "").strip()
            if mention_name:
                ack_text = f"已向 {mention_name} 追問，等回覆後繼續處理。"
            else:
                ack_text = "已私聊確認，等回覆後繼續處理。"

            card = self._build_card_content(ack_text)
            await self._send_feishu_message(group_chat_id, "chat_id", card, "interaction_ack")
            logger.info("[FeishuChannel] 追問確認已傳送: chat_id=%s", group_chat_id)
        except Exception as e:
            logger.warning("[FeishuChannel] 追問確認傳送失敗: %s", e)

    @staticmethod
    def _fallback_group_ack() -> str:
        """群內短回覆兜底文案，保持第一人稱口吻。"""
        return "好的，我知道了，會跟進處理。"

    @classmethod
    def _normalize_group_ack_text(cls, target_name: str, text: str) -> str:
        """過濾掉機器人旁白式短回覆，儘量保留像使用者本人說的話。"""
        normalized = re.split(r"[\r\n]+", (text or "").strip(), maxsplit=1)[0]
        normalized = re.sub(r"\s+", " ", normalized).strip(' "\'""''「」')
        if not normalized:
            return ""

        forbidden_phrases = (
            "已提醒",
            "已通知",
            "私下通知",
            "私聊",
            "轉告",
            "提醒了",
            "通知了",
            "告訴了",
            "幫你",
            "幫您",
            "代為",
        )
        if target_name and target_name in normalized:
            return ""
        if any(phrase in normalized for phrase in forbidden_phrases):
            return ""
        return normalized

    def _generate_group_ack_sync(self, target_name: str, content: str) -> str:
        """呼叫輕量 LLM 生成群內簡短確認文案。"""
        api_key = os.getenv("API_KEY", "").strip()
        api_base = os.getenv("API_BASE", "").strip()
        model_name = os.getenv("MODEL_NAME", "").strip() or "GLM-4.7"
        if not api_key or not api_base:
            return self._fallback_group_ack()

        prompt = (
            "你是一個飛書群聊機器人。群裡有一條需要{name}關注的訊息,"
            "你已經把詳細回覆私發給了{name}。"
            "現在群裡需要補一句很短的話，但這句話必須像{name}本人在群裡的直接回復,"
            "而不是機器人旁白或轉述。\n\n"
            "要求：\n"
            "- 一句話，不超過30個字\n"
            "- 用第一人稱口吻，像當事人本人在說話\n"
            "- 不要提到{name}的名字\n"
            "- 不要寫成'我已經提醒了{name}''已通知{name}''我幫{name}處理了'這類機器人/第三人稱表達\n"
            "- 更像'好的，我知道了，會準時參加會議''收到，我會跟進這件事'\n"
            "- 不要照搬原文，保留核心動作即可\n\n"
            "你私發給{name}的內容是：\n{content}"
        ).format(   
            name=target_name,
            content=content[:500],
        )
        try:
            resp = requests.post(
                f"{api_base.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 80,
                },
                timeout=30,
            )
            resp.raise_for_status()
            choices = resp.json().get("choices") or []
            if choices:
                text = (choices[0].get("message") or {}).get("content", "").strip()
                text = self._normalize_group_ack_text(target_name, text)
                if text:
                    return text
        except Exception as e:
            logger.warning("[FeishuChannel] 生成群確認文案失敗，使用回退: %s", e)

        return self._fallback_group_ack()

    async def _send_group_ack(self, metadata: dict[str, Any], content: str) -> None:
        """後臺生成併傳送群內簡短確認，不阻塞主回覆。"""
        try:
            target_name = str(metadata.get("reply_target_name") or "").strip() or "對方"
            group_chat_id = str(metadata.get("feishu_chat_id") or "").strip()
            if not group_chat_id:
                return

            ack_text = await asyncio.to_thread(
                self._generate_group_ack_sync, target_name, content
            )
            if ack_text:
                card = self._build_card_content(ack_text)
                await self._send_feishu_message(group_chat_id, "chat_id", card, "group_ack")
                logger.info("[FeishuChannel] 群確認已傳送: chat_id=%s text=%s", group_chat_id, ack_text[:50])
        except Exception as e:
            logger.warning("[FeishuChannel] 後臺群確認傳送失敗: %s", e)

    @staticmethod
    def _should_send_group_progress_hint(metadata: dict[str, Any]) -> bool:
        """僅對群聊訊息展示輕量處理進度。"""
        return (
            str(metadata.get("chat_type") or "").strip() == "group"
            and bool(str(metadata.get("feishu_chat_id") or "").strip())
        )

    def _build_group_progress_hint_text(self, metadata: dict[str, Any]) -> str:
        """根據場景挑選一條儘量自然的群內處理提示。"""
        try:
            merged_count = int(metadata.get("merged_count", 1) or 1)
        except (TypeError, ValueError):
            merged_count = 1

        if str(metadata.get("reply_scope") or "").strip().lower() == "dm":
            return self._GROUP_PROGRESS_HINT_TEXTS[1]
        if merged_count > 1:
            return self._GROUP_PROGRESS_HINT_TEXTS[2]
        return self._GROUP_PROGRESS_HINT_TEXTS[0]

    def _clear_group_progress_state(self, request_id: str) -> None:
        """清理指定請求的延遲提示任務和已傳送標記。"""
        pending_task = self._pending_group_progress_tasks.pop(request_id, None)
        if pending_task and not pending_task.done():
            pending_task.cancel()
        self._sent_group_progress_requests.discard(request_id)

    def _should_skip_group_progress_scheduling(self, request_id: str, metadata: dict[str, Any]) -> bool:
        """判斷是否應該跳過群內處理提示的排程。"""
        if not request_id:
            return True
        if request_id in self._pending_group_progress_tasks:
            return True
        if request_id in self._sent_group_progress_requests:
            return True
        if not self._should_send_group_progress_hint(metadata):
            return True
        return False

    def _schedule_group_progress_hint(self, request_id: str, metadata: dict[str, Any]) -> None:
        """為慢請求安排一條延遲傳送的群內處理提示。"""
        if self._should_skip_group_progress_scheduling(request_id, metadata):
            return

        task = asyncio.create_task(
            self._send_group_progress_hint_after_delay(request_id, dict(metadata)),
            name=f"feishu-progress-{request_id}",
        )
        self._pending_group_progress_tasks[request_id] = task

    async def _send_group_progress_hint_after_delay(
        self, request_id: str, metadata: dict[str, Any]
    ) -> None:
        """僅在請求持續較久時，補發一條極短群內處理提示。"""
        try:
            await asyncio.sleep(self._GROUP_PROGRESS_HINT_DELAY_SECONDS)
            if self._pending_group_progress_tasks.get(request_id) is not asyncio.current_task():
                return
            if request_id in self._sent_group_progress_requests:
                return
            if not self._should_send_group_progress_hint(metadata):
                return

            group_chat_id = str(metadata.get("feishu_chat_id") or "").strip()
            hint_text = self._build_group_progress_hint_text(metadata)
            if not group_chat_id or not hint_text:
                return

            card = self._build_card_content(hint_text)
            await self._send_feishu_message(
                group_chat_id,
                "chat_id",
                card,
                f"group_progress:{request_id}",
            )
            self._sent_group_progress_requests.add(request_id)
            logger.info(
                "[FeishuChannel] 群進度提示已傳送: request_id=%s chat_id=%s text=%s",
                request_id,
                group_chat_id,
                hint_text,
            )
        except Exception as e:
            logger.warning("[FeishuChannel] 群進度提示傳送失敗: %s", e)
        finally:
            if self._pending_group_progress_tasks.get(request_id) is asyncio.current_task():
                self._pending_group_progress_tasks.pop(request_id, None)

    async def _handle_processing_status_event(
        self, msg: Message, metadata: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        """處理閘道器發出的 processing_status 事件。"""
        request_id = str(msg.id or "").strip()
        if not request_id or not self._should_send_group_progress_hint(metadata):
            return

        if bool(payload.get("is_processing")):
            self._schedule_group_progress_hint(request_id, metadata)
            return

        self._clear_group_progress_state(request_id)

    def _build_reply_metadata(
        self, *, message: Any, sender_open_id: str, base_metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """根據群聊上下文補充預設的回覆投遞意圖。"""
        if not self.config.group_digital_avatar:
            return dict(base_metadata)
        metadata = dict(base_metadata)
        target_user_open_id = self._get_target_user_open_id()
        mentioned_users = self._extract_mentioned_users(message)
        mentioned_open_ids = [user["open_id"] for user in mentioned_users]

        message_content = ""
        try:
            content_obj = json.loads(message.content or "{}")
            message_content = content_obj.get("text", "") or ""
        except Exception:
            message_content = str(message.content or "")

        mention_all = "@_all" in message_content
        if mention_all and target_user_open_id and target_user_open_id not in mentioned_open_ids:
            mentioned_open_ids.append(target_user_open_id)

        if mentioned_open_ids:
            metadata["mentioned_open_ids"] = mentioned_open_ids
            metadata["im_mentioned_user_ids"] = mentioned_open_ids

        is_group_chat = str(getattr(message, "chat_type", "") or "").strip() == "group"
        if not is_group_chat or not target_user_open_id:
            return metadata

        if target_user_open_id in mentioned_open_ids:
            target_user_name = ""
            for user in mentioned_users:
                if user["open_id"] == target_user_open_id:
                    target_user_name = self._resolve_user_display_name(
                        target_user_open_id, user.get("name", "")
                    )
                    break
            metadata["reply_candidate_feishu_open_id"] = target_user_open_id
            metadata["reply_candidate_reason"] = "mentioned_target_user"
            if target_user_name:
                metadata["reply_target_name"] = target_user_name
            return metadata

        if sender_open_id == target_user_open_id:
            metadata["reply_scope"] = "dm"
            metadata["reply_feishu_open_id"] = target_user_open_id
            metadata["reply_reason"] = "sender_is_target_user"
            return metadata

        return metadata

    @staticmethod
    def _is_control_message(content: str) -> bool:
        from jiuwenclaw.gateway.message_handler.command_parser.slash_command import is_control_like_for_im_batching

        return is_control_like_for_im_batching(content)

    async def _enqueue_message_batch(
        self,
        *,
        chat_id: str,
        open_id: str,
        content: str,
        timestamp_ms: int,
        metadata: dict[str, Any],
    ) -> None:
        """將同一使用者的連續訊息做短暫聚合，再統一交給 gateway 入站鏈路。"""
        if self._is_control_message(content):
            if content == "/mode team":
                await self._process_batched_message(
                chat_id=chat_id,
                open_id=open_id,
                merged_content="/new_session",
                timestamp_ms=timestamp_ms,
                metadata=metadata,
                )
            await self._process_batched_message(
                chat_id=chat_id,
                open_id=open_id,
                merged_content=content,
                timestamp_ms=timestamp_ms,
                metadata=metadata,
            )
            return

        merge_window_ms = max(int(self.config.message_merge_window_ms or 0), 0)
        if merge_window_ms <= 0:
            await self._process_batched_message(
                chat_id=chat_id,
                open_id=open_id,
                merged_content=content,
                timestamp_ms=timestamp_ms,
                metadata=metadata,
            )
            return

        batch_key = (chat_id, open_id or "")
        async with self._pending_message_lock:
            batch = self._pending_message_batches.get(batch_key)
            if batch is None:
                batch = {
                    "chat_id": chat_id,
                    "open_id": open_id,
                    "items": [],
                    "flush_task": None,
                }
                self._pending_message_batches[batch_key] = batch

            batch["items"].append(
                {
                    "content": content,
                    "timestamp_ms": timestamp_ms,
                    "metadata": dict(metadata),
                }
            )

            old_task = batch.get("flush_task")
            if old_task and not old_task.done():
                old_task.cancel()

            delay_seconds = merge_window_ms / 1000
            batch["flush_task"] = asyncio.create_task(
                self._flush_message_batch_after_delay(batch_key, delay_seconds)
            )

    async def _flush_message_batch_after_delay(
        self, batch_key: tuple[str, str], delay_seconds: float
    ) -> None:
        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            return

        async with self._pending_message_lock:
            batch = self._pending_message_batches.get(batch_key)
            if not batch or batch.get("flush_task") is not asyncio.current_task():
                return
            self._pending_message_batches.pop(batch_key, None)

        items = batch.get("items") or []
        if not items:
            return

        merged_content = "\n".join(
            item["content"].strip() for item in items if item.get("content", "").strip()
        ).strip()
        if not merged_content:
            return

        last_item = items[-1]
        merged_metadata = dict(last_item["metadata"])
        merged_metadata["merged_message_ids"] = [
            item["metadata"].get("message_id", "") for item in items
        ]
        merged_metadata["merged_msg_types"] = [
            item["metadata"].get("msg_type", "") for item in items
        ]
        merged_metadata["merged_count"] = len(items)
        for key in (
            "reply_scope",
            "reply_feishu_open_id",
            "reply_feishu_chat_id",
            "reply_reason",
            "reply_target_name",
        ):
            for item in reversed(items):
                value = item["metadata"].get(key)
                if isinstance(value, str) and value.strip():
                    merged_metadata[key] = value
                    break

        mentioned_open_ids: list[str] = []
        for item in items:
            for open_id in item["metadata"].get("mentioned_open_ids", []) or []:
                if open_id and open_id not in mentioned_open_ids:
                    mentioned_open_ids.append(open_id)
        if mentioned_open_ids:
            merged_metadata["mentioned_open_ids"] = mentioned_open_ids

        if len(items) > 1:
            logger.info(
                "[FeishuChannel] 合併連續訊息: chat_id=%s open_id=%s count=%s",
                batch.get("chat_id", ""),
                batch.get("open_id", ""),
                len(items),
            )

        await self._process_batched_message(
            chat_id=batch["chat_id"],
            open_id=batch["open_id"],
            merged_content=merged_content,
            timestamp_ms=int(last_item["timestamp_ms"]),
            metadata=merged_metadata,
        )

    async def _process_batched_message(
        self,
        *,
        chat_id: str,
        open_id: str,
        merged_content: str,
        timestamp_ms: int,
        metadata: dict[str, Any],
    ) -> None:
        """對單條或合併後的訊息做平臺整理後轉發。"""
        chat_type = metadata.get("chat_type", "")
        is_group_chat = chat_type == "group"

        enriched_metadata = dict(metadata)
        enriched_metadata["timestamp_ms"] = timestamp_ms
        enriched_metadata["im_platform"] = "feishu"
        enriched_metadata["im_chat_type"] = "group" if is_group_chat else "direct"
        enriched_metadata["im_sender_user_id"] = open_id
        enriched_metadata["im_thread_id"] = chat_id

        inbound = FeishuInboundMessage(
            message_id=enriched_metadata.get("message_id", ""),
            chat_id=chat_id,
            content=merged_content,
            user_id=open_id,
            bot_id=self.config.app_id or "",
            metadata=enriched_metadata,
        )
        await self._handle_message(inbound)

    # Markdown表格正規表示式（標題行+分隔符行+資料行）
    _TABLE_RE = re.compile(
        r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
        re.MULTILINE,
    )

    @staticmethod
    def _parse_markdown_table(table_text: str) -> dict | None:
        """
        將Markdown表格解析為飛書表格元素。

        Args:
            table_text: Markdown表格文字

        Returns:
            dict: 飛書表格元素，解析失敗返回None
        """
        lines = [
            line.strip() for line in table_text.strip().split("\n") if line.strip()
        ]
        if len(lines) < 3:
            return None

        def split_line(line):
            return [c.strip() for c in line.strip("|").split("|")]

        headers = split_line(lines[0])
        rows = [split_line(line) for line in lines[2:]]

        columns = [
            {"tag": "column", "name": f"c{i}", "display_name": h, "width": "auto"}
            for i, h in enumerate(headers)
        ]

        return {
            "tag": "table",
            "page_size": len(rows) + 1,
            "columns": columns,
            "rows": [
                {f"c{i}": r[i] if i < len(r) else "" for i in range(len(headers))}
                for r in rows
            ],
        }

    def _build_feishu_card_elements(self, content: str) -> list[dict]:
        """
        將內容分割為Markdown和表格元素，用於構建飛書卡片。

        Args:
            content: 要處理的內容

        Returns:
            list[dict]: 飛書卡片元素列表
        """
        elements, last_end = [], 0

        for m in self._TABLE_RE.finditer(content):
            before = content[last_end: m.start()].strip()
            if before:
                # 轉換非表格內容為富文字 div 元素
                elements.extend(self._markdown_to_feishu_elements(before))

            elements.append(
                self._parse_markdown_table(m.group(1))
                or self._markdown_to_feishu_elements(m.group(1))
            )
            last_end = m.end()

        remaining = content[last_end:].strip()
        if remaining:
            elements.extend(self._markdown_to_feishu_elements(remaining))

        return elements or self._markdown_to_feishu_elements(content)

    def _markdown_to_feishu_elements(self, md_content: str) -> list[dict]:
        """
        將 Markdown 內容轉換為飛書卡片元素列表。

        Args:
            md_content: Markdown 內容

        Returns:
            list[dict]: 飛書卡片元素列表
        """
        elements = []
        lines = md_content.split('\n')
        current_text = []

        for line in lines:
            stripped = line.strip()

            # 處理標題
            if stripped.startswith('## '):
                if current_text:
                    elements.append(self._create_div_element('\n'.join(current_text)))
                    current_text = []
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{stripped[3:]}**"
                    }
                })
            elif stripped.startswith('### '):
                if current_text:
                    elements.append(self._create_div_element('\n'.join(current_text)))
                    current_text = []
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{stripped[4:]}**"
                    }
                })
            elif stripped.startswith('# '):
                if current_text:
                    elements.append(self._create_div_element('\n'.join(current_text)))
                    current_text = []
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{stripped[2:]}**"
                    }
                })
            # 處理分隔線
            elif stripped == '---':
                if current_text:
                    elements.append(self._create_div_element('\n'.join(current_text)))
                    current_text = []
                elements.append({"tag": "hr"})
            # 處理引用塊
            elif stripped.startswith('> '):
                current_text.append(stripped[2:])
            # 處理列表項
            elif stripped.startswith('- ') or stripped.startswith('* '):
                current_text.append(f"• {stripped[2:]}")
            elif re.match(r'^\d+\. ', stripped):
                current_text.append(stripped)
            else:
                current_text.append(line)

        if current_text:
            elements.append(self._create_div_element('\n'.join(current_text)))

        return elements if elements else [{"tag": "div", "text": {"tag": "lark_md", "content": md_content}}]

    def _create_div_element(self, content: str) -> dict:
        """
        建立飛書 div 元素。

        Args:
            content: 文字內容

        Returns:
            dict: 飛書 div 元素
        """
        # 處理內聯格式：粗體、斜體、程式碼等
        formatted = content
        # 保留粗體和斜體
        # 處理行內程式碼
        formatted = re.sub(r'`([^`]+)`', r'`\1`', formatted)
        # 處理連結
        formatted = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'[\1](\2)', formatted)

        return {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": formatted
            }
        }

    async def send(self, msg: Message) -> None:
        """
        透過飛書傳送訊息。

        Args:
            msg: 要傳送的訊息物件
        """
        if not self._api_client:
            logger.warning("飛書客戶端未初始化")
            return

        try:
            payload = msg.payload if isinstance(msg.payload, dict) else {}
            event_name = getattr(msg.event_type, "value", None) or payload.get("event_type") or ""
            stream_key = str(getattr(msg, "id", "") or "")
            _msg_streaming = getattr(msg, "enable_streaming", None)
            streaming_enabled = bool(_msg_streaming if _msg_streaming is not None else self.config.enable_streaming)

            meta = dict(getattr(msg, "metadata", None) or {})

            # 處理檔案訊息
            if msg.event_type == EventType.CHAT_FILE or msg.event_type == EventType.CHAT_REASONING:
                if self.config.enable_file_upload and self._file_service:
                    await self._send_file_message(msg)
                return

            # 處理媒體訊息
            if msg.event_type == EventType.CHAT_MEDIA:
                if self.config.enable_file_upload and self._file_service:
                    await self._send_media_message(msg)
                return

            # 處理team message
            if msg.event_type == EventType.TEAM_MESSAGE:
                await self._send_team_message(msg)
                return

            # 處理使用者詢問訊息（傳送確認卡片）
            if msg.event_type == EventType.CHAT_ASK_USER_QUESTION:
                await self._send_ask_user_question_card(msg)
                return

            # 流式增量：先快取；若開啟流式則實時傳送，否則僅快取不傳送。
            if event_name == "chat.delta":
                delta = self._extract_message_content(msg)
                if delta and stream_key:
                    self._stream_text_buffers[stream_key] = (
                        self._stream_text_buffers.get(stream_key, "") + delta
                    )
                return

            # 非 streaming 模式下僅下發最終結果，遮蔽執行過程類事件。
            if (not streaming_enabled) and event_name in {"chat.tool_call", "chat.tool_result", "todo.updated"}:
                return

            # 流式結束兜底：有些場景不會攜帶非空 chat.final，使用 processing_status=false 沖刷快取。
            if event_name == "chat.processing_status":
                is_processing = payload.get("is_processing")
                if is_processing is not False:
                    if not streaming_enabled:
                        await self._handle_processing_status_event(msg, meta, payload)
                        return
                    content_str = self._extract_message_content(msg)
                    if not content_str.strip():
                        return
                else:
                    content_str = self._stream_text_buffers.pop(stream_key, "")
                    if not content_str.strip():
                        if not streaming_enabled:
                            await self._handle_processing_status_event(msg, meta, payload)
                            return
                        content_str = self._extract_message_content(msg)
                        if not content_str.strip():
                            return
            else:
                buffered_text = ""
                if event_name == "chat.final":
                    buffered_text = self._stream_text_buffers.pop(stream_key, "")
                elif event_name in {"chat.error", "chat.interrupt_result"}:
                    self._stream_text_buffers.pop(stream_key, None)
                content_str = self._extract_message_content(msg)
                is_complete = msg.payload.get("is_complete", False)
                if is_complete:
                    content_str = self._merge_stream_and_final_content(
                        buffered_text,
                        content_str,
                    )

            receive_id, id_type = self._extract_receive_info(msg)
            payload = getattr(msg, "payload", None) or {}
            skills_card_content = self._build_skills_list_card_content(payload, event_name)
            if skills_card_content:
                request_id = str(msg.id or "").strip()
                if request_id and msg.event_type != EventType.HEARTBEAT_RELAY:
                    self._clear_group_progress_state(request_id)
                await self._send_feishu_message(receive_id, id_type, skills_card_content, msg.id)
                return
            if (
                msg.event_type == EventType.HEARTBEAT_RELAY
                and isinstance(payload, dict)
                and payload.get("heartbeat")
            ):
                content_str = str(payload.get("heartbeat"))

            if not content_str.strip():
                logger.warning("飛書傳送：訊息內容為空，跳過傳送")
                return

            request_id = str(msg.id or "").strip()
            if request_id and msg.event_type != EventType.HEARTBEAT_RELAY:
                self._clear_group_progress_state(request_id)

            # 過濾群聊訊息中的使用者敏感資訊
            content_str = self._filter_user_info_for_group(content_str, meta)

            # 兜底：chat.final 中提到了 workspace 檔案但 LLM 未呼叫 send_file_to_user 時，
            # 自動提取檔案路徑併傳送，避免使用者收不到檔案。
            if (
                event_name == "chat.final"
                and self.config.enable_file_upload
                and self._file_service
            ):
                req_id = str(getattr(msg, "id", "") or "")
                already_sent = self._sent_file_paths_by_req.get(req_id, set())
                detected_files = [
                    fp for fp in self._detect_workspace_files(content_str)
                    if os.path.abspath(fp) not in already_sent
                ]
                if detected_files:
                    logger.info(
                        "飛書兜底檔案傳送：從 chat.final 中檢測到 %d 個未傳送檔案: %s",
                        len(detected_files),
                        detected_files,
                    )
                    for fp in detected_files:
                        try:
                            if is_image_file(fp):
                                await self._send_image_message(receive_id, id_type, fp)
                            elif is_audio_file(fp):
                                await self._send_audio_message(receive_id, id_type, fp, os.path.basename(fp))
                            elif is_video_file(fp):
                                await self._send_video_message(receive_id, id_type, fp, os.path.basename(fp))
                            else:
                                await self._send_file_card(receive_id, id_type, fp, os.path.basename(fp))
                        except Exception as file_err:
                            logger.error("飛書兜底檔案傳送失敗: %s %s", fp, file_err)
            
            # 群聊數字分身回覆到群聊時，@傳送人
            if msg.group_digital_avatar and id_type == "chat_id":
                mention_user_id = str(
                    meta.get("interaction_mention_user_id") or ""
                ).strip()
                sender_open_id = str(
                    meta.get("im_sender_user_id")
                    or meta.get("open_id")
                    or ""
                ).strip()
                at_user_id = mention_user_id or sender_open_id
                if at_user_id and not at_user_id.startswith("bot"):
                    content_str = f"<at id={at_user_id}></at>\n{content_str}"

            card_content = self._build_card_content(content_str)
            await self._send_feishu_message(receive_id, id_type, card_content, msg.id)

            if msg.group_digital_avatar and self._should_send_interaction_ack(meta):
                asyncio.create_task(self._send_interaction_ack(meta, content_str))
            elif msg.group_digital_avatar and self._should_send_group_ack(meta):
                group_chat_id = str(meta.get("feishu_chat_id") or "").strip()
                if group_chat_id and group_chat_id != receive_id:
                    asyncio.create_task(self._send_group_ack(meta, content_str))

            try:
                chat_id = ""

                if not chat_id:
                    meta = getattr(msg, "metadata", None) or {}
                    reply_scope = str(meta.get("reply_scope") or "").strip().lower()

                    if reply_scope == "dm" and id_type == "open_id":
                        chat_id = receive_id
                    elif id_type == "chat_id":
                        chat_id = receive_id
                    else:
                        chat_id = meta.get("feishu_chat_id") or ""

                # 記錄機器人回覆訊息到群聊歷史中去（僅數字分身模式）
                if chat_id and self.config.group_digital_avatar:
                    self._message_storage.add_message_to_memory(
                            chat_id=chat_id,
                            message={
                            "message_id": f"bot_{int(time.time() * 1000)}_{msg.id}",
                            "content": content_str,
                            "timestamp": int(time.time() * 1000),
                            "msg_type": "interactive",
                            "open_id": f"bot_{self.config.app_id}",
                            "chat_type": "bot_reply",
                        }
                    )
            except Exception as e:
                logger.warning(f"記錄機器人回覆訊息失敗: {e}")

        except Exception as e:
            logger.error(f"傳送飛書訊息時發生異常: {e}")

    def _detect_workspace_files(self, text: str) -> list[str]:
        """從文字中提取 workspace 下實際存在的檔案路徑。

        用於兜底檢測 LLM 提到但未透過 send_file_to_user 傳送的檔案。
        支援兩種模式：
        1. 完整絕對路徑：/home/xxx/.jiuwenclaw/agent/workspace/xxx.docx
        2. 僅檔名：'xxx.docx' 或 "xxx.docx"——在 workspace 目錄下查詢
        """
        from jiuwenclaw.common.utils import get_agent_workspace_dir
        workspace_dir = str(get_agent_workspace_dir())

        seen: set[str] = set()
        result: list[str] = []

        # 模式1：完整路徑 - 動態匹配當前 workspace 目錄
        # 支援 Linux (/home/xxx/.jiuwenclaw/...) 和 Windows (C:\Users\xxx\.jiuwenclaw\...)
        workspace_pattern = re.escape(workspace_dir) + r"[^\s\[\]\"']+\.\w+"
        for m in re.findall(workspace_pattern, text):
            m = m.rstrip(".,;:!?)")
            if m not in seen and os.path.isfile(m):
                seen.add(m)
                result.append(m)

        # 模式2：從引號或書名號中提取檔名，在 workspace 下查詢
        # 匹配 '檔名.ext'、"檔名.ext"、《檔名.ext》
        name_pattern = (
            r"""['"'《]([^'"'》\n]+\."""
            r"""(?:docx?|xlsx?|pptx?|pdf|csv|txt|md|zip|tar|gz|"""
            r"""png|jpg|jpeg|gif|mp3|mp4|wav|opus))['"'》]"""
        )  # noqa: E501
        for fname in re.findall(name_pattern, text, re.IGNORECASE):
            fpath = os.path.join(workspace_dir, fname)
            if fpath not in seen and os.path.isfile(fpath):
                seen.add(fpath)
                result.append(fpath)

        return result

    @staticmethod
    def _merge_stream_and_final_content(stream_text: str, final_text: str) -> str:
        """合併流式累積文字和 final 文字，優先保留資訊更完整的一側。"""
        stream_text = stream_text or ""
        final_text = final_text or ""
        if not stream_text.strip():
            return final_text
        if not final_text.strip():
            return stream_text
        if stream_text == final_text:
            return final_text
        if final_text.startswith(stream_text):
            return final_text
        if stream_text.startswith(final_text):
            return stream_text
        return final_text if len(final_text) >= len(stream_text) else stream_text

    def _extract_receive_info(self, msg: Message) -> tuple[str, str]:
        """
        從訊息物件中提取接收者ID和ID型別。
        優先使用 metadata 中的平臺身份（feishu_chat_id / feishu_open_id），
        避免 \new_session 覆蓋 session_id 後導致 Invalid ids。

        Args:
            msg: 訊息物件

        Returns:
            tuple: (接收者ID, ID型別)
        """
        meta = getattr(msg, "metadata", None) or {}
        receive_id = ""
        id_type = "open_id"

        # 0) 群聊數字分身場景：僅當 outbound pipeline 確認 reply_scope=dm 時，
        #    才用 reply_candidate_feishu_open_id 作為私發目標；
        #    否則 candidate 僅作為候選，不影響實際傳送路由。
        reply_scope = str(meta.get("reply_scope") or "").strip().lower()
        if reply_scope == "dm":
            # 優先使用 reply_candidate_feishu_open_id（由 outbound pipeline 設定）
            # 相容 reply_feishu_open_id（由入站階段 sender_is_target_user 場景設定）
            reply_candidate_open_id = (
                meta.get("reply_candidate_feishu_open_id")
                or meta.get("reply_feishu_open_id")
                or ""
            ).strip()
            if reply_candidate_open_id:
                receive_id = reply_candidate_open_id
                id_type = "open_id"

        # 1) 優先用 metadata 中的平臺身份
        if not receive_id:
            feishu_chat_id = (meta.get("feishu_chat_id") or "").strip()
            feishu_open_id = (meta.get("feishu_open_id") or "").strip()

            if feishu_chat_id:
                receive_id = feishu_chat_id
                id_type = "chat_id" if feishu_chat_id.startswith("oc_") else "open_id"
            elif feishu_open_id:
                receive_id = feishu_open_id
                id_type = "open_id"

        # 2) 若 metadata 中沒有平臺身份，則使用配置中的 chat_id 作為固定推送目標
        if not receive_id:
            cfg_chat_id = getattr(self.config, "chat_id", "") or ""
            cfg_chat_id = cfg_chat_id.strip()
            if cfg_chat_id:
                receive_id = cfg_chat_id
                id_type = "chat_id" if cfg_chat_id.startswith("oc_") else "open_id"

        # 2b) 最近一次會話（收訊息時寫入 config / 記憶體），避免 supplement 等路徑丟 metadata 後誤用 session_id
        if not receive_id:
            last_cid = str(getattr(self.config, "last_chat_id", "") or "").strip()
            last_oid = str(getattr(self.config, "last_open_id", "") or "").strip()
            if last_cid:
                receive_id = last_cid
                id_type = "chat_id" if last_cid.startswith("oc_") else "open_id"
            elif last_oid:
                receive_id = last_oid
                id_type = "open_id"

        # 3) 仍然沒有，則回退到 session_id / id（相容舊邏輯）
        if not receive_id:
            receive_id = getattr(msg, "session_id", None) or msg.id or ""
            if receive_id.startswith("oc_"):
                id_type = "chat_id"
            else:
                id_type = "open_id"
            logger.warning(f"飛書回發未找到有效平臺身份，使用回退值: {receive_id}")

        return receive_id, id_type

    def _extract_message_content(self, msg: Message) -> str:
        """
        從訊息物件中提取內容字串。

        Args:
            msg: 訊息物件

        Returns:
            str: 訊息內容字串
        """
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        event_name = getattr(msg.event_type, "value", None) or payload.get("event_type") or ""

        if event_name == "chat.tool_call":
            tool_info = payload.get("tool_call", payload)
            if isinstance(tool_info, dict):
                tool_name = tool_info.get("tool_name") or tool_info.get("name") or "unknown_tool"
                args = (
                    tool_info.get("arguments")
                    or tool_info.get("args")
                    or tool_info.get("input")
                    or tool_info.get("params")
                )
                args_text = self._truncate_text(self._extract_preferred_text(args), max_len=240)
                return f"[工具呼叫] {tool_name}" if not args_text else f"[工具呼叫] {tool_name}\n引數: {args_text}"
            tool_text = self._extract_preferred_text(tool_info)
            tool_text = self._truncate_text(tool_text, max_len=160)
            return f"[工具呼叫] {tool_text}" if tool_text else "[工具呼叫]"

        if event_name == "chat.tool_result":
            tool_name = payload.get("tool_name") or "unknown_tool"
            result_text = self._extract_tool_result_text(payload.get("result"))
            return f"[工具結果] {tool_name}" if not result_text else f"[工具結果] {tool_name}\n{result_text}"

        if event_name == "todo.updated":
            todos = payload.get("todos")
            if not isinstance(todos, list) or not todos:
                return "[待辦更新]"
            total = len(todos)
            completed = 0
            running = 0
            pending = 0
            cancelled = 0
            for item in todos:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status", "")).strip().lower()
                if status == "completed":
                    completed += 1
                elif status == "running":
                    running += 1
                elif status in ("cancelled", "canceled"):
                    cancelled += 1
                else:
                    # waiting/pending/unknown 統一歸為待處理
                    pending += 1
            return (
                f"[待辦更新] 已完成 {completed}/{total}"
                f"｜進行中 {running}"
                f"｜待處理 {pending}"
                f"｜已取消 {cancelled}"
            )

        if event_name == "chat.error":
            error_text = self._extract_preferred_text(payload.get("error"))
            return f"[錯誤] {error_text}" if error_text else "[錯誤] 未知錯誤"

        if event_name == "chat.processing_status":
            is_processing = payload.get("is_processing")
            if is_processing is True:
                return "[狀態] 處理中"
            if is_processing is False:
                return "[狀態] 已完成"
            return ""

        if event_name == "chat.interrupt_result":
            return self._extract_preferred_text(payload.get("message")) or "[狀態] 任務已中斷"

        if event_name == "heartbeat.relay":
            return self._extract_preferred_text(payload.get("heartbeat"))

        # Gateway/Agent 響應在 payload.content，直接傳送可能在 params.content
        content_str = (msg.params or {}).get("content") or payload.get("content") or ""
        if isinstance(content_str, dict):
            content_str = content_str.get("output", content_str)
        text = self._truncate_text(self._extract_preferred_text(content_str), max_len=4000)
        if text:
            return text

        # 最後僅嘗試提取可讀欄位，不再整包透傳 JSON，避免渠道側出現原始結構化噪音。
        return self._extract_preferred_text(payload if payload else msg.payload)

    def _extract_tool_result_text(self, value: Any) -> str:
        """提取工具結果可讀摘要，限制長度，避免飛書訊息過載。"""
        if isinstance(value, dict):
            for key in ("summary", "message", "output", "result", "content", "text", "error"):
                if key in value:
                    text = self._extract_preferred_text(value.get(key))
                    if text:
                        return self._truncate_text(text, max_len=600)
        text = self._extract_preferred_text(value)
        return self._truncate_text(text, max_len=600)

    @staticmethod
    def _extract_preferred_text(value: Any) -> str:
        """從結構化資料中提取可讀文字，避免直接傳送 JSON."""
        if value is None:
            return ""
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return ""
            if (
                (text.startswith("{") and text.endswith("}"))
                or (text.startswith("[") and text.endswith("]"))
            ):
                try:
                    parsed = json.loads(text)
                except Exception:
                    # 相容 Python dict 字串
                    match = re.search(
                        r"['\"](output|content|text|message|result|error|summary)['\"]\s*:\s*['\"](.+?)['\"]",
                        text,
                        flags=re.DOTALL,
                    )
                    return match.group(2).strip() if match else ""
                extracted = FeishuChannel._extract_preferred_text(parsed)
                return extracted or ""
            return text

        if isinstance(value, dict):
            for key in ("output", "content", "text", "message", "result", "error", "summary"):
                if key in value:
                    extracted = FeishuChannel._extract_preferred_text(value.get(key))
                    if extracted:
                        return extracted
            return ""

        if isinstance(value, list):
            parts: list[str] = []
            for item in value[:3]:
                extracted = FeishuChannel._extract_preferred_text(item)
                if extracted:
                    parts.append(extracted)
            return "\n".join(parts).strip()

        return str(value).strip()

    @staticmethod
    def _truncate_text(text: str, max_len: int = 240) -> str:
        text = (text or "").strip()
        if len(text) <= max_len:
            return text
        return text[:max_len].rstrip() + "..."

    @staticmethod
    def _stringify_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        return str(value)

    _IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp"})

    @staticmethod
    def _is_image_file(path: str) -> bool:
        """Check if file path has an image extension supported by Feishu."""
        ext = os.path.splitext(path)[1].lower()
        return ext in FeishuChannel._IMAGE_EXTENSIONS

    def _upload_image(self, abs_path: str) -> str | None:
        """Upload an image to Feishu and return the image_key, or None on failure."""
        if not self._api_client:
            return None
        try:
            with open(abs_path, "rb") as f:
                request = (
                    CreateImageRequest.builder()
                    .request_body(
                        CreateImageRequestBody.builder()
                        .image_type("message")
                        .image(f)
                        .build()
                    )
                    .build()
                )
                response = self._api_client.im.v1.image.create(request)

            if not response.success():
                logger.warning(
                    "飛書圖片上傳失敗: code=%s msg=%s path=%s",
                    response.code, response.msg, abs_path,
                )
                return None

            image_key = getattr(response.data, "image_key", None)
            if image_key:
                logger.info("飛書圖片上傳成功: image_key=%s", image_key)
            return image_key
        except FileNotFoundError:
            logger.warning("飛書圖片上傳失敗: 檔案不存在 path=%s", abs_path)
            return None
        except Exception as e:
            logger.error("飛書圖片上傳異常: %s path=%s", e, abs_path)
            return None

    def _upload_file(self, abs_path: str) -> str | None:
        """Upload a file to Feishu and return the file_key, or None on failure."""
        if not self._api_client:
            return None
        try:
            file_name = os.path.basename(abs_path)
            with open(abs_path, "rb") as f:
                request = (
                    CreateFileRequest.builder()
                    .request_body(
                        CreateFileRequestBody.builder()
                        .file_type("stream")
                        .file_name(file_name)
                        .file(f)
                        .build()
                    )
                    .build()
                )
                response = self._api_client.im.v1.file.create(request)

            if not response.success():
                logger.warning(
                    "飛書檔案上傳失敗: code=%s msg=%s path=%s",
                    response.code, response.msg, abs_path,
                )
                return None

            file_key = getattr(response.data, "file_key", None)
            if file_key:
                logger.info("飛書檔案上傳成功: file_key=%s", file_key)
            return file_key
        except FileNotFoundError:
            logger.warning("飛書檔案上傳失敗: 檔案不存在 path=%s", abs_path)
            return None
        except Exception as e:
            logger.error("飛書檔案上傳異常: %s path=%s", e, abs_path)
            return None

    def _build_card_content(self, content_str: str) -> str:
        """
        構建飛書卡片內容。

        Args:
            content_str: 訊息內容字串

        Returns:
            str: JSON格式的卡片內容
        """
        elements = self._build_feishu_card_elements(content_str)
        card = {
            "config": {"wide_screen_mode": True},
            "elements": elements,
        }
        return json.dumps(card, ensure_ascii=False)

    def _build_skills_list_card_content(self, payload: Any, event_name: str) -> str | None:
        """將 skills.list 返回渲染為飛書卡片；非 skills.list 資料返回 None。"""
        if event_name != "chat.final":
            return None
        if not isinstance(payload, dict):
            return None
        if "skills" not in payload and not self._looks_like_skills_error_payload(payload):
            return None

        skills = payload.get("skills")
        error_text = str(payload.get("error") or "").strip()
        header_title = "技能列表"
        elements: list[dict[str, Any]] = []

        if error_text:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"獲取技能列表失敗：{error_text}"},
            })
        elif not isinstance(skills, list) or not skills:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": "當前無可用技能。"},
            })
        else:
            source_counter: dict[str, int] = {}
            for item in skills:
                if not isinstance(item, dict):
                    continue
                src = str(item.get("source") or "unknown").strip() or "unknown"
                source_counter[src] = source_counter.get(src, 0) + 1

            source_parts = [f"{k}: {v}" for k, v in sorted(source_counter.items(), key=lambda kv: kv[0])]
            if source_parts:
                source_summary = " | ".join(source_parts[:4])
                if len(source_parts) > 4:
                    source_summary += " | ..."
                elements.append({
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": f"總數: {len(skills)}"},
                        {"tag": "plain_text", "content": f"來源: {source_summary}"},
                    ],
                })
                elements.append({"tag": "hr"})

            limit = 20
            for i, item in enumerate(skills[:limit], 1):
                if isinstance(item, dict):
                    name = str(item.get("name") or item.get("title") or "?").strip()
                    desc = str(item.get("description") or "").strip()
                    source = str(item.get("source") or "").strip()
                    path = str(item.get("path") or "").strip()
                    title = f"{i}. {name}" + (f" ({source})" if source else "")
                    body = title
                    if desc:
                        short_desc = desc if len(desc) <= 180 else f"{desc[:180]}..."
                        body = f"{title}\n{short_desc}"
                    if path:
                        short_path = path if len(path) <= 120 else f"...{path[-117:]}"
                        body = f"{body}\n`{short_path}`"
                    elements.append({
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": body},
                    })
                else:
                    elements.append({
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": f"{i}. {item}"},
                    })
            if len(skills) > limit:
                elements.append({
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": f"共 {len(skills)} 項，僅顯示前 {limit} 項"}],
                })

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": header_title},
            },
            "elements": elements,
        }
        return json.dumps(card, ensure_ascii=False)

    @staticmethod
    def _looks_like_skills_error_payload(payload: dict[str, Any]) -> bool:
        """判定是否為 skills.list 的錯誤透傳載荷。"""
        if "error" not in payload:
            return False
        # 避免誤判 chat.error/chat.final 的普通錯誤訊息
        noisy_keys = {"content", "output", "result", "text", "event_type"}
        return not any(k in payload for k in noisy_keys)

    async def _send_ask_user_question_card(self, msg: Message) -> None:
        """
        傳送使用者詢問卡片（帶選項按鈕的確認卡片）。

        Args:
            msg: 包含 ask_user_question payload 的訊息物件
        """
        try:
            payload = msg.payload if isinstance(msg.payload, dict) else {}
            questions = payload.get("questions", [])
            request_id = payload.get("request_id", "")

            if not questions:
                logger.warning("傳送使用者詢問卡片：沒有問題資料")
                return

            # 構建 card 元素，每次只顯示一個問題
            elements = []

            # 新增問題文字
            question = questions[0]
            question_text = question.get("question", "")
            if question_text:
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": question_text
                    }
                })

            # 新增分隔線
            elements.append({"tag": "hr"})

            # 新增按鈕
            options = question.get("options", [])
            if options:
                actions = []
                for option in options:
                    label = option.get("label", "")
                    # value = json.dumps({"label": label}, ensure_ascii=False)
                    button_element = {
                        "tag": "button",
                        "text": {
                            "tag": "plain_text",
                            "content": label
                        },
                        "type": "primary",
                        "value": {"label": label, "request_id": request_id}
                    }
                    actions.append(button_element)

                elements.append({
                    "tag": "action",
                    "actions": actions
                })

            # 構建卡片
            card = {
                "config": {"wide_screen_mode": True},
                "elements": elements,
            }

            # 新增 header 用於儲存 request_id
            header_text = question.get("header", "")
            if header_text:
                card["header"] = {
                    "title": {
                        "tag": "plain_text",
                        "content": header_text
                    },
                    "template": request_id  # 使用 template 欄位儲存 request_id
                }

            self._user_question_card[request_id] = card
            # 傳送卡片
            receive_id, id_type = self._extract_receive_info(msg)
            card_json = json.dumps(card, ensure_ascii=False)

            await self._create_and_send_message(
                FeishuMessageSendRequest(
                    receive_id=receive_id,
                    id_type=id_type,
                    msg_type="interactive",
                    content=card_json,
                    log_label=f"ask_user_question:{request_id}",
                )
            )

            logger.info(
                "[FeishuChannel] 傳送使用者詢問卡片: request_id=%s, chat_id=%s",
                request_id,
                receive_id,
            )

        except Exception as e:
            logger.error(f"傳送使用者詢問卡片時發生異常: {e}", exc_info=True)

    async def _send_feishu_message(
        self, receive_id: str, id_type: str, content: str, msg_id: str,
    ) -> Any:
        """
        傳送飛書卡片訊息（非同步，線上程池中執行同步 SDK 呼叫）。

        Args:
            receive_id: 接收者ID
            id_type: ID型別
            content: 訊息內容（JSON字串）
            msg_id: 傳送訊息ID（用於日誌）
        """
        await self._create_and_send_message(
            FeishuMessageSendRequest(
                receive_id=receive_id,
                id_type=id_type,
                msg_type="interactive",
                content=content,
                log_label=msg_id,
            )
        )

    def _on_message_sync(self, data: "P2ImMessageReceiveV1") -> None:
        """
        傳入訊息的同步處理器（從WebSocket執行緒呼叫）。

        在主事件迴圈中排程非同步處理。

        Args:
            data: 飛書訊息事件資料
        """
        if self._main_loop and self._main_loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._main_loop)

    async def _on_message(self, data: "P2ImMessageReceiveV1") -> None:
        """
        處理來自飛書的傳入訊息。

        Args:
            data: 飛書訊息事件資料
        """
        try:
            event = data.event
            message = event.message
            sender = event.sender

            # 訊息去重檢查
            if self._is_duplicate_message(message.message_id):
                return

            # 跳過機器人傳送的訊息
            if sender.sender_type == "bot":
                return

            # 群聊數字分身模式下不自動點贊
            if not self.config.group_digital_avatar:
                asyncio.create_task(self._add_reaction(message.message_id, "THUMBSUP"))

            # 解析訊息內容（支援檔案型別）
            content, file_info = await self._parse_message_content_with_file(message)
            if content == "/mode team" and self.config.enable_streaming == False:
                # 非流式情況下不支援team模式，向使用者傳送提示
                try:
                    # 提取傳送者open_id
                    open_id = (
                        getattr(getattr(sender, "sender_id", None), "open_id", None) or ""
                    )
                    # 獲取chat_id和判斷ID型別
                    chat_id = getattr(message, "chat_id", None) or ""
                    if chat_id.startswith("oc_"):
                        receive_id = chat_id
                        id_type = "chat_id"
                    else:
                        # 私聊場景使用open_id
                        receive_id = open_id
                        id_type = "open_id"
                    hint_text = "⚠️ 非流式模式下不支援 Team 模式，已保持原有模式。\n\n" \
                        "如需使用 Team 模式，請在配置中開啟流式輸出 (enable_streaming: true)。"
                    card = self._build_card_content(hint_text)
                    await self._send_feishu_message(receive_id, id_type, card, message.message_id)
                except Exception as e:
                    logger.warning(f"[FeishuChannel] 傳送Team模式提示失敗: {e}")
                return
            if not content and not file_info:
                return

            # 提取傳送者open_id
            open_id = (
                getattr(getattr(sender, "sender_id", None), "open_id", None) or ""
            )

            # 將最近一次可回發的飛書身份寫入 config.yaml，供 cron 推送時使用
            if self.channel_id == self.name:
                try:
                    from jiuwenclaw.common.config import update_channel_in_config

                    update_channel_in_config(
                        "feishu",
                        {
                            "last_chat_id": getattr(message, "chat_id", None) or "",
                            "last_open_id": open_id or "",
                            "last_message_id": getattr(message, "message_id", None) or "",
                        },
                    )
                except Exception:
                    # 不影響正常收訊息
                    pass
            elif self.channel_id.startswith("feishu_enterprise:") and self.config.bot_key:
                try:
                    from jiuwenclaw.common.config import update_channel_subsection_in_config

                    update_channel_subsection_in_config(
                        "feishu_enterprise",
                        self.config.bot_key,
                        {
                            "last_chat_id": getattr(message, "chat_id", None) or "",
                            "last_open_id": open_id or "",
                            "last_message_id": getattr(message, "message_id", None) or "",
                        },
                    )
                except Exception:
                    # 不影響正常收訊息
                    pass

            # 記憶體同步 last_*：無 metadata 的回發兜底立即生效，不必等通道重啟從 yaml 載入
            lc = str(getattr(message, "chat_id", None) or "").strip()
            lo = str(open_id or "").strip()
            try:
                self.config = self.config.model_copy(
                    update={"last_chat_id": lc, "last_open_id": lo},
                )
            except Exception:
                pass

            # 構建訊息引數
            params = {"content": content, "query": content}
            if file_info:
                params["files"] = [file_info]

            # 構建基礎 metadata
            base_metadata = {
                "message_id": message.message_id,
                "chat_type": message.chat_type,
                "msg_type": message.message_type,
                "open_id": open_id,
                "feishu_open_id": open_id,
                "feishu_chat_id": getattr(message, "chat_id", None) or "",
                **({"file_info": file_info} if file_info else {}),
            }

            # 記錄訊息到本地儲存（僅數字分身模式）
            if self.config.group_digital_avatar:
                try:
                    self._message_storage.add_message_to_memory(
                        chat_id=message.chat_id,
                        message={
                            "message_id": message.message_id,
                            "content": content,
                            "timestamp": getattr(message, "create_time", int(time.time() * 1000)),
                            "msg_type": message.message_type,
                            "open_id": open_id,
                            "chat_type": message.chat_type,
                        }
                    )
                except Exception as e:
                    logger.warning(f"記錄訊息到本地儲存失敗: {e}")

            # 數字分身模式：走訊息批次合併；否則直接處理
            _ts = int(getattr(message, "create_time", int(time.time() * 1000)))
            _meta = self._build_reply_metadata(
                message=message,
                sender_open_id=open_id,
                base_metadata=base_metadata,
            )
            if self.config.group_digital_avatar:
                await self._enqueue_message_batch(
                    chat_id=message.chat_id,
                    open_id=open_id or "",
                    content=content,
                    timestamp_ms=_ts,
                    metadata=_meta,
                )
            else:
                await self._process_batched_message(
                    chat_id=message.chat_id,
                    open_id=open_id or "",
                    merged_content=content,
                    timestamp_ms=_ts,
                    metadata=_meta,
                )

        except Exception as e:
            logger.error(f"處理飛書訊息時發生異常: {e}")

    def _on_message_read_event(self, data: Any) -> None:
        """
        處理飛書訊息已讀事件（空處理器，僅用於消除 SDK 日誌警告）。

        飛書會在使用者閱讀訊息後推送 im.message.message_read_v1 事件，
        但當前場景不需要處理此事件，註冊空處理器避免 SDK 列印 ERROR 日誌。

        Args:
            data: 飛書訊息已讀事件資料（忽略）
        """
        pass  # 不處理，僅用於消除日誌警告

    def _on_card_action_trigger_sync(self, data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        """
        處理飛書卡片按鈕點選事件的同步處理器（從WebSocket執行緒呼叫）。

        根據飛書SDK規範，需要返回 P2CardActionTriggerResponse 物件。

        Args:
            data: 飛書卡片回撥事件資料 (P2CardActionTrigger)

        Returns:
            Any: P2CardActionTriggerResponse 響應物件
        """
        try:
            event = data.event
            action = event.action

            # 提取回撥資訊
            token = event.token or ""
            action_value = action.value or ""

            # 返回成功的響應：更新卡片去除按鈕，顯示已選擇的內容
            selected_label = action_value.get("label", "已選擇")
            request_id = action_value.get("request_id", "")
            if not request_id or request_id not in self._user_question_card:
                card_data = {
                        "elements": [
                            {
                                "tag": "div",
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"您已選擇：**{selected_label}**"
                                }
                            }
                        ]
                    }
                header = {}
            else:
                card_data = self._user_question_card[request_id]
                content = card_data["elements"][0].get("text", {}).get("content", "") \
                    if "elements" in card_data and len(card_data["elements"]) > 0 else ""
                header = card_data.get("header", {})
                card_data = {
                    "elements": [{
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"{content} <br> <br> 您已選擇：**{selected_label}**"
                            }
                        }]
                }
                self._user_question_card.pop(request_id)
            # 獲取使用者和上下文資訊
            operator_id = event.operator.open_id if event.operator and event.operator.open_id else ""

            logger.info(
                "[FeishuChannel] 收到卡片回撥: token=%s, action=%s, user=%s",
                token,
                action_value,
                operator_id,
            )

            # 在主事件迴圈中排程非同步處理
            if self._main_loop and self._main_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._handle_card_callback_async(token, action_value, operator_id),
                    self._main_loop
                )

            # 返回成功的響應：更新卡片去除按鈕，提示已接受
            response = {
                "toast": {
                    "type": "info",
                    "content": "已收到您的選擇"
                },
                "card": {
                    "type": "raw",
                    "data": {
                         "schema": "2.0",
                         "config": {
                             "update_multi": True,
                         },
                         "body": card_data,
                         "header": header
                    }
                }
            }
            return P2CardActionTriggerResponse(response)
        except Exception as e:
            logger.error(f"處理飛書卡片回撥時發生異常: {e}", exc_info=True)
            # 返回錯誤響應
            error_response = {
                "toast": {
                    "type": "error",
                    "content": "處理失敗"
                }
            }
            if P2CardActionTriggerResponse:
                return P2CardActionTriggerResponse(error_response)
            return error_response

    async def _handle_card_callback_async(
        self,
        token: str,
        action_value: str,
        operator_id: str,
    ) -> None:
        """
        非同步處理卡片回撥，將回撥轉換為 chat.user_answer 事件。

        Args:
            token: 卡片 token
            action_value: 使用者選擇的值
            operator_id: 操作者 ID
        """
        try:
            request_id = action_value.pop("request_id", "")
            if not request_id:
                raise ValueError("missing request_id")
            # 構建使用者響應（符合 evolution service 期望的格式）
            answers = [{"selected_options": list(action_value.values())}] if action_value else []

            logger.info(
                "[FeishuChannel] 傳送使用者答案: token=%s, answer=%s",
                token,
                action_value,
            )
            msg = Message(
                    id=token,
                    type="event",
                    channel_id=self.channel_id,
                    session_id=operator_id,
                    params={"answers": answers, "request_id": request_id},
                    timestamp=time.time(),
                    ok=True,
                    req_method=ReqMethod.CHAT_ANSWER,
                    provider=self.name,
                    chat_id="",  # 卡片回撥不直接關聯特定chat_id
                    user_id=operator_id,
                    bot_id=self.config.app_id,
                    event_type=None,  # 不使用特定事件型別，透過 params 傳遞答案
                    metadata={
                        "request_id": token,
                        "answers": answers,
                    },
                )
            # 傳送 chat.user_answer 事件到 gateway
            if self._message_callback:
                # 構建 Message 物件
                self._message_callback(msg)
            else:
                # 透過 bus 路由
                await self.bus.publish_user_message(msg)

        except Exception as e:
            logger.error(f"非同步處理卡片回撥時發生異常: {e}", exc_info=True)

    def _is_duplicate_message(self, message_id: str) -> bool:
        """
        檢查訊息是否重複。

        Args:
            message_id: 訊息ID

        Returns:
            bool: True表示訊息重複，False表示新訊息
        """
        if message_id in self._message_dedup_cache:
            return True

        self._message_dedup_cache[message_id] = None

        # 修剪快取：當超過1000時保留最近的500條
        while len(self._message_dedup_cache) > 1000:
            self._message_dedup_cache.popitem(last=False)

        return False

    def _parse_message_content(self, message: Any) -> str:
        """
        解析訊息內容。

        Args:
            message: 飛書訊息物件

        Returns:
            str: 解析後的訊息內容
        """
        msg_type = message.message_type

        if msg_type == "text":
            try:
                text = json.loads(message.content).get("text", "")
                # 替換 @mentions 佔位符為真實使用者名稱
                text = self._replace_mentions_with_names(message, text)
                return text
            except json.JSONDecodeError:
                return message.content or ""
        else:
            return MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")

    async def _parse_message_content_with_file(
        self,
        message: Any,
    ) -> tuple[str, dict | None]:
        """
        解析訊息內容，支援檔案型別（非同步版本）。

        Args:
            message: 飛書訊息物件

        Returns:
            tuple: (文字內容, 檔案資訊字典或None)
        """
        if not self._file_service:
            return self._parse_message_content(message), None

        msg_type = message.message_type

        # 文字訊息
        if msg_type == "text":
            try:
                text = json.loads(message.content).get("text", "")
                # 替換 @mentions 佔位符為真實使用者名稱
                text = self._replace_mentions_with_names(message, text)
                return text, None
            except json.JSONDecodeError:
                return message.content or "", None

        # 圖片訊息
        if msg_type == "image":
            return await self._handle_image_message(message)

        # 檔案訊息
        if msg_type == "file":
            return await self._handle_file_message(message)

        # 音訊訊息
        if msg_type == "audio":
            return await self._handle_audio_message(message)

        # 影片/媒體訊息
        if msg_type == "media":
            return await self._handle_media_message(message)

        # 貼紙訊息
        if msg_type == "sticker":
            return "[貼紙]", None

        # 其他型別
        return MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]"), None

    async def _handle_image_message(self, message: Any) -> tuple[str, dict | None]:
        """處理圖片訊息。"""
        try:
            content = json.loads(message.content)
            image_key = content.get("image_key")

            if not image_key:
                return "[圖片]", None

            # 下載圖片
            file_info = await self._file_service.download_image(
                file_key=image_key,
                message_id=message.message_id,
            )

            if file_info:
                return "[圖片]", file_info

            return "[圖片下載失敗]", None

        except Exception as e:
            logger.error(f"處理圖片訊息失敗: {e}")
            return "[圖片]", None

    async def _handle_file_message(self, message: Any) -> tuple[str, dict | None]:
        """處理檔案訊息。"""
        try:
            content = json.loads(message.content)
            file_key = content.get("file_key")
            file_name = content.get("file_name", "unknown")
            file_size = content.get("file_size", 0)

            logger.info(f"飛書檔案訊息: file_name={file_name}, file_size={file_size}, file_key={file_key}")

            if not file_key:
                return f"[檔案: {file_name}]", None

            # 檢查檔案大小限制
            max_size = self.config.max_download_size
            if file_size > max_size:
                logger.warning(f"檔案過大，跳過下載: {file_name} ({file_size} > {max_size})")
                return f"[檔案過大: {file_name}]", None

            # 下載檔案
            logger.info(f"開始下載飛書檔案: {file_name}")
            file_info = await self._file_service.download_file_resource(
                file_key=file_key,
                message_id=message.message_id,
                extra_info={"file_name": file_name, "file_size": file_size},
            )

            if file_info:
                file_info["name"] = file_name
                file_info["size"] = file_size
                logger.info(f"飛書檔案下載成功: {file_name}, path={file_info.get('path')}")
                return f"[檔案: {file_name}]", file_info

            logger.warning(f"飛書檔案下載失敗: {file_name}")
            return f"[檔案下載失敗: {file_name}]", None

        except Exception as e:
            logger.error(f"處理檔案訊息失敗: {e}")
            return "[檔案]", None

    async def _handle_audio_message(self, message: Any) -> tuple[str, dict | None]:
        """處理音訊訊息。"""
        try:
            content = json.loads(message.content)
            file_key = content.get("file_key")
            duration = content.get("duration", 0)

            if not file_key:
                return "[音訊]", None

            # 下載音訊
            file_info = await self._file_service.download_audio(
                file_key=file_key,
                message_id=message.message_id,
            )

            if file_info:
                duration_sec = duration / 1000
                return f"[音訊: {duration_sec:.1f}秒]", file_info

            return "[音訊下載失敗]", None

        except Exception as e:
            logger.error(f"處理音訊訊息失敗: {e}")
            return "[音訊]", None

    async def _handle_media_message(self, message: Any) -> tuple[str, dict | None]:
        """處理影片/媒體訊息。"""
        try:
            content = json.loads(message.content)
            file_key = content.get("file_key")
            duration = content.get("duration", 0)

            if not file_key:
                return "[影片]", None

            # 下載影片
            file_info = await self._file_service.download_media(
                file_key=file_key,
                message_id=message.message_id,
            )

            if file_info:
                duration_sec = duration / 1000
                return f"[影片: {duration_sec:.1f}秒]", file_info

            return "[影片下載失敗]", None

        except Exception as e:
            logger.error(f"處理影片訊息失敗: {e}")
            return "[影片]", None

    def _extract_text_from_file(self, file_path: str, max_len: int = 50000) -> str | None:
        """從文字檔案中提取內容。"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read(max_len + 1)
            if len(content) > max_len:
                content = content[:max_len] + "\n... [內容已截斷]"
            return content
        except UnicodeDecodeError:
            # 嘗試其他編碼
            try:
                with open(file_path, "r", encoding="gbk") as f:
                    content = f.read(max_len + 1)
                if len(content) > max_len:
                    content = content[:max_len] + "\n... [內容已截斷]"
                return content
            except Exception:
                return None
        except Exception as e:
            logger.warning(f"提取檔案內容失敗: {e}")
            return None

    # ==================== 檔案訊息傳送 ====================

    async def _send_file_message(self, msg: Message) -> None:
        """
        傳送檔案訊息（支援圖片/音訊/影片/普通檔案）。

        Args:
            msg: 包含檔案資訊的訊息物件
        """
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        files = payload.get("files", [])

        if not files:
            logger.warning("飛書傳送檔案訊息：無檔案資訊")
            return

        receive_id, id_type = self._extract_receive_info(msg)
        logger.info(f"飛書傳送檔案訊息: receive_id={receive_id}, id_type={id_type}")

        for file_info in files:
            # 支援兩種格式：路徑字串 或 包含 path 欄位的字典
            if isinstance(file_info, dict):
                file_path = file_info.get("path", "")
                file_name = file_info.get("name", os.path.basename(file_path))
            else:
                file_path = str(file_info)
                file_name = os.path.basename(file_path)

            if not file_path or not os.path.exists(file_path):
                logger.warning(f"飛書傳送檔案：檔案不存在 {file_path}")
                continue

            req_id = str(getattr(msg, "id", "") or "")
            if req_id:
                self._sent_file_paths_by_req.setdefault(req_id, set()).add(
                    os.path.abspath(file_path)
                )

            if is_image_file(file_path):
                await self._send_image_message(receive_id, id_type, file_path)
            elif is_audio_file(file_path):
                await self._send_audio_message(receive_id, id_type, file_path, file_name)
            elif is_video_file(file_path):
                await self._send_video_message(receive_id, id_type, file_path, file_name)
            else:
                await self._send_file_card(receive_id, id_type, file_path, file_name)

    async def _create_and_send_message(
        self,
        request: FeishuMessageSendRequest,
    ) -> None:
        """構建併傳送飛書訊息（線上程池中執行同步 SDK 呼叫，支援重試）。"""
        loop = asyncio.get_running_loop()
        
        for attempt in range(request.max_retries):
            try:
                def _do_send():
                    req = (
                        CreateMessageRequest.builder()
                        .receive_id_type(request.id_type)
                        .request_body(
                            CreateMessageRequestBody.builder()
                            .receive_id(request.receive_id)
                            .msg_type(request.msg_type)
                            .content(request.content)
                            .build()
                        )
                        .build()
                    )
                    return self._api_client.im.v1.message.create(req)

                response = await loop.run_in_executor(None, _do_send)

                if not response.success():
                    logger.warning(
                        "飛書傳送訊息失敗 (嘗試 %d/%d, msg_type=%s%s): code=%s msg=%s",
                        attempt + 1, request.max_retries,
                        request.msg_type,
                        f" {request.log_label}" if request.log_label else "",
                        response.code,
                        response.msg,
                    )
                    if attempt < request.max_retries - 1:
                        await asyncio.sleep(1 * (attempt + 1))
                        continue
                else:
                    logger.info("飛書訊息傳送成功: msg_type=%s%s", request.msg_type,
                                f" {request.log_label}" if request.log_label else "")
                    return
                    
            except Exception as e:
                logger.warning(
                    "飛書傳送訊息異常 (嘗試 %d/%d, msg_type=%s%s): %s",
                    attempt + 1, request.max_retries,
                    request.msg_type,
                    f" {request.log_label}" if request.log_label else "",
                    e,
                )
                if attempt < request.max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                else:
                    logger.error("傳送飛書訊息異常: %s", e)

    async def _send_image_message(
        self,
        receive_id: str,
        id_type: str,
        file_path: str,
    ) -> None:
        """
        傳送圖片訊息。

        優先使用 image.create API（返回 image_key，傳送 image 訊息），
        失敗時回退到 file.create API（傳送 file 訊息）。
        """
        try:
            upload_result = await self._file_service.upload_image(file_path)
            if not upload_result:
                logger.error(f"飛書上傳圖片失敗: {file_path}")
                return

            file_type = upload_result.get("file_type", "image")
            image_key = upload_result.get("image_key")
            file_key = upload_result.get("file_key")

            if file_type == "image" and image_key:
                content = json.dumps({"image_key": image_key}, ensure_ascii=False)
                msg_type = "image"
            elif file_key:
                content = json.dumps({"file_key": file_key}, ensure_ascii=False)
                msg_type = "file"
            else:
                logger.error(f"飛書上傳圖片返回無效: {upload_result}")
                return

            await self._create_and_send_message(
                FeishuMessageSendRequest(
                    receive_id=receive_id,
                    id_type=id_type,
                    msg_type=msg_type,
                    content=content,
                    log_label=os.path.basename(file_path),
                )
            )

        except Exception as e:
            logger.error(f"傳送飛書圖片訊息異常: {e}")

    async def _send_audio_message(
        self,
        receive_id: str,
        id_type: str,
        file_path: str,
        file_name: str,
    ) -> None:
        """
        傳送音訊訊息。

        若檔案為 .opus 格式（飛書原生），使用 msg_type=audio 傳送（可線上播放）；
        其他格式降級為普通檔案訊息。
        """
        try:
            upload_result = await self._file_service.upload_file_resource(file_path)
            if not upload_result:
                logger.error(f"飛書上傳音訊失敗: {file_path}")
                return

            file_key = upload_result.get("file_key")
            if not file_key:
                logger.error(f"飛書上傳音訊返回無效: {file_path}")
                return

            feishu_file_type = upload_result.get("file_type", "stream")

            if feishu_file_type == "opus":
                # opus 格式支援線上播放
                content = json.dumps({"file_key": file_key}, ensure_ascii=False)
                msg_type = "audio"
            else:
                # 其他音訊格式作為普通檔案傳送
                content = json.dumps({"file_key": file_key}, ensure_ascii=False)
                msg_type = "file"

            await self._create_and_send_message(
                FeishuMessageSendRequest(
                    receive_id=receive_id,
                    id_type=id_type,
                    msg_type=msg_type,
                    content=content,
                    log_label=file_name,
                )
            )

        except Exception as e:
            logger.error(f"傳送飛書音訊訊息異常: {e}")

    async def _send_video_message(
        self,
        receive_id: str,
        id_type: str,
        file_path: str,
        file_name: str,
    ) -> None:
        """
        傳送影片訊息（msg_type=media，支援線上播放）。

        飛書 media 訊息內容：{"file_key": "..."}（thumbnail 可選，此處省略）
        """
        try:
            upload_result = await self._file_service.upload_file_resource(file_path)
            if not upload_result:
                logger.error(f"飛書上傳影片失敗: {file_path}")
                return

            file_key = upload_result.get("file_key")
            if not file_key:
                logger.error(f"飛書上傳影片返回無效: {file_path}")
                return

            content = json.dumps({"file_key": file_key}, ensure_ascii=False)

            await self._create_and_send_message(
                FeishuMessageSendRequest(
                    receive_id=receive_id,
                    id_type=id_type,
                    msg_type="media",
                    content=content,
                    log_label=file_name,
                )
            )

        except Exception as e:
            logger.error(f"傳送飛書影片訊息異常: {e}")

    async def _send_file_card(
        self,
        receive_id: str,
        id_type: str,
        file_path: str,
        file_name: str,
    ) -> None:
        """傳送普通檔案訊息（msg_type=file）。"""
        try:
            upload_result = await self._file_service.upload_file_resource(file_path)
            if not upload_result:
                logger.error(f"飛書上傳檔案失敗: {file_path}")
                return

            file_key = upload_result.get("file_key")
            if not file_key:
                logger.error(f"飛書上傳檔案返回無效: {file_path}")
                return

            content = json.dumps({"file_key": file_key}, ensure_ascii=False)

            await self._create_and_send_message(
                FeishuMessageSendRequest(
                    receive_id=receive_id,
                    id_type=id_type,
                    msg_type="file",
                    content=content,
                    log_label=file_name,
                )
            )

        except Exception as e:
            logger.error(f"傳送飛書檔案訊息異常: {e}")

    async def _send_team_message(self, msg: Message) -> None:
        """
        傳送Agent Team訊息卡片。

        Args:
            msg: 包含 team.message payload 的訊息物件
        """
        try:
            payload = msg.payload if isinstance(msg.payload, dict) else {}
            event = payload.get("event", {})
            message_type = event.get("type", "")
            from_member = event.get("from_member", "")
            to_member = event.get("to_member", "")
            content = event.get("content", "")

            if not from_member or not content:
                logger.warning("傳送Team訊息卡片：缺少必要欄位 from_member 或 content")
                return

            # 構建 card 元素
            elements = []

            # 接收者資訊
            if message_type == "team.message.broadcast":
                receiver_text = "📢 **廣播訊息**"
            elif message_type == "team.message.p2p":
                receiver_text = f"👤 **發給: {to_member}**"
            else:
                receiver_text = f"📋 **型別: {message_type}**"

            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": receiver_text
                }
            })

            # 新增分隔線
            elements.append({"tag": "hr"})

            # 訊息內容
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": content
                }
            })

            # 構建卡片
            card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"🤖 {from_member}"
                    },
                    "template": "teal"
                },
                "elements": elements,
            }

            # 傳送卡片
            receive_id, id_type = self._extract_receive_info(msg)
            card_json = json.dumps(card, ensure_ascii=False)

            await self._create_and_send_message(
                FeishuMessageSendRequest(
                    receive_id=receive_id,
                    id_type=id_type,
                    msg_type="interactive",
                    content=card_json,
                    log_label=f"team_message:{message_type}",
                )
            )

            logger.info(
                "[FeishuChannel] 傳送Team訊息卡片: type=%s, from=%s, to=%s",
                message_type,
                from_member,
                to_member if message_type == "team.message.p2p" else "broadcast",
            )

        except Exception as e:
            logger.error(f"傳送Team訊息卡片時發生異常: {e}", exc_info=True)

    async def _send_media_message(self, msg: Message) -> None:
        """
        傳送媒體訊息（video/audio）入口，與檔案訊息統一處理。
        """
        await self._send_file_message(msg)

    def _filter_user_info_for_group(self, content: str, metadata: dict[str, Any]) -> str:
        """
        過濾群聊訊息中的使用者實際資訊。

        在群聊中回覆時，移除可能包含使用者隱私資訊的內容。
        私聊時不過濾。

        Args:
            content: 原始訊息內容
            metadata: 訊息後設資料

        Returns:
            str: 過濾後的訊息內容
        """
        chat_type = str(metadata.get("chat_type") or "").strip()
        im_chat_type = str(metadata.get("im_chat_type") or "").strip()
        is_group = chat_type == "group" or im_chat_type == "group"

        if not is_group:
            return content

        filtered_content = content

        user_sensitive_patterns = [
            r'\b[\w\.-]+@[\w\.-]+\.\w+\b',
            r'\b(?:電話|手機|聯絡方式|手機號|電話號碼)[:：]?\s*[\d\s-]{7,15}\b',
            r'\b1[3-9]\d{9}\b',
            r'\b(?:身份證|身份證號|ID)[:：]?\s*\d{15,18}\b',
            r'\b\d{15,18}\b',
            r'ou_[a-zA-Z0-9]{20,}',
            r'on_[a-zA-Z0-9]{20,}',
            r'\b(?:密碼|password|pwd)[:：]?\s*\S+\b',
            r'\b(?:地址|住址|家庭住址)[:：]?\s*[^\n]{10,50}\b',
        ]

        for pattern in user_sensitive_patterns:
            filtered_content = re.sub(pattern, '[已過濾]', filtered_content, flags=re.IGNORECASE)

        return filtered_content
