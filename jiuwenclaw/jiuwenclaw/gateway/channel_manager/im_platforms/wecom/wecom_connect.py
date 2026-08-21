# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""WecomChannel - 企業微信 AI 機器人通道（WebSocket 長連線）。"""

from __future__ import annotations

import logging
import asyncio
import concurrent.futures
import json
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

import requests
from pydantic import BaseModel, Field

from jiuwenclaw.gateway.channel_manager.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.gateway.channel_manager.im_platforms.platform_adapter.message import MessageStore
from jiuwenclaw.common.schema.message import Message, ReqMethod, EventType
from jiuwenclaw.common.utils import get_agent_workspace_dir

logger = logging.getLogger(__name__)

try:
    from wecom_aibot_sdk import WSClient
    from wecom_aibot_sdk.utils import generate_req_id

    WECOM_AVAILABLE = True
except ImportError:
    WECOM_AVAILABLE = False
    WSClient = None
    generate_req_id = None


class WecomConfig(BaseModel):
    """企業微信通道配置（WebSocket 長連線）。"""

    enabled: bool = False
    bot_id: str = ""
    secret: str = ""
    ws_url: str = "wss://openws.work.weixin.qq.com"
    allow_from: list[str] = Field(default_factory=list)
    enable_streaming: bool = True
    send_thinking_message: bool = True
    my_user_id: str = ""
    bot_name: str = ""
    message_merge_window_ms: int = 15000
    group_digital_avatar: bool = False  # 是否啟用群聊數字分身功能
    enable_memory: bool = False  # 是否啟用群聊記憶功能
    # 檔案處理配置
    max_download_size: int = 100 * 1024 * 1024  # 最大下載檔案大小（預設 100MB）
    download_timeout: int = 60  # 下載超時時間（秒）
    send_file_allowed: bool = True  # 是否啟用檔案上傳功能
    enable_file_download: bool = True  # 是否啟用檔案下載功能
    workspace_dir: str = ""  # 工作空間目錄


class WecomChannel(BaseChannel):
    """
    企業微信 AI 機器人通道，基於 WebSocket 長連線。

    依賴：
    - 企業微信後臺建立 AI 機器人，獲取 bot_id 和 secret
    """

    name = "wecom"

    def __init__(
        self,
        config: WecomConfig,
        router: RobotMessageRouter,
        im_platform_adapter: Any | None = None,
    ):
        super().__init__(config, router)
        self.config: WecomConfig = config
        self._ws_client: Any = None
        self._message_callback: Callable[[Message], None] | None = None
        self._connect_task: asyncio.Task | None = None
        self._pending_streams: dict[str, dict[str, Any]] = {}
        self._message_dedup_cache: OrderedDict[str, None] = OrderedDict()
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._pending_message_batches: dict[tuple[str, str], dict[str, Any]] = {}
        self._pending_message_lock = asyncio.Lock()
        self._pending_group_progress_tasks: dict[str, asyncio.Task[None]] = {}
        self._sent_group_progress_requests: set[str] = set()
        self._stopping = False
        self._stream_text_buffers: dict[str, str] = {}
        self._stream_completed_requests: set[str] = set()
        self._message_storage = MessageStore(api_client=None)
        self._im_platform_adapter: Any = im_platform_adapter
        # 檔案服務
        self._file_service: Any = None
        # 按 request_id 記錄已傳送檔案路徑，避免重複傳送
        self._sent_file_paths_by_req: dict[str, set[str]] = {}

    @property
    def channel_id(self) -> str:
        return self.name

    def on_message(self, callback: Callable[[Message], None]) -> None:
        """註冊訊息回撥，供 ChannelManager 使用。"""
        self._message_callback = callback

    def set_platform_adapter(self, adapter: Any) -> None:
        """設定平臺介面卡。"""
        self._im_platform_adapter = adapter
        self._message_storage.set_platform_adapter(adapter)

    @staticmethod
    def _looks_like_msgid(val: str) -> bool:
        """過濾 msgid（長數字），避免誤作 chatid 導致 93006。"""
        if not val or not isinstance(val, str):
            return True
        s = val.strip()
        if len(s) < 10:
            return False
        return s.isdigit()

    def _extract_chatid_from_frame(self, frame: dict) -> str:
        """從 SDK frame 提取 chatid。"""
        body = frame.get("body") or {}
        if not isinstance(body, dict):
            body = {}
        if not body and frame.get("msgtype"):
            body = frame

        from_obj = body.get("from") or frame.get("from") or {}
        if not isinstance(from_obj, dict):
            from_obj = {}

        def _pick_chatid(*candidates: str) -> str:
            for c in candidates:
                s = str(c or "").strip()
                if s and not self._looks_like_msgid(s):
                    return s
            return ""

        return _pick_chatid(
            body.get("chatid"),
            body.get("chat_id"),
            from_obj.get("chatid"),
            from_obj.get("chat_id"),
            from_obj.get("userid"),
            from_obj.get("user_id"),
            frame.get("chatid"),
            frame.get("chat_id"),
        )

    def _extract_frame_info(self, frame: dict) -> tuple[str, str, str]:
        """從 SDK frame 提取 chatid、req_id、content。"""
        body = frame.get("body") or {}
        if not isinstance(body, dict):
            body = {}
        # 若 frame 無 body 但含 msgtype，則 frame 本身即訊息體（扁平結構）
        if not body and frame.get("msgtype"):
            body = frame

        text = body.get("text") or {}
        content = (
            (text.get("content", "") if isinstance(text, dict) else str(text) if text else "")
            or body.get("content", "")
            or ""
        )
        headers = frame.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}
        req_id = (
            headers.get("req_id")
            or headers.get("reqId")
            or frame.get("req_id")
            or body.get("msgid")
            or body.get("req_id")
            or ""
        )

        chatid = self._extract_chatid_from_frame(frame)
        return chatid, req_id, str(content or "").strip()

    def _get_target_user_id(self) -> str:
        """返回當前數字分身對應的使用者 ID。"""
        return (self.config.my_user_id or "").strip()

    def _is_duplicate_message(self, message_id: str) -> bool:
        """檢查訊息是否重複。"""
        if message_id in self._message_dedup_cache:
            return True

        self._message_dedup_cache[message_id] = None

        while len(self._message_dedup_cache) > 1000:
            self._message_dedup_cache.popitem(last=False)

        return False

    _GROUP_PROGRESS_HINT_DELAY_SECONDS = 6.0
    _GROUP_PROGRESS_HINT_TEXTS: tuple[str, ...] = (
        "我先確認下，馬上回復。",
        "這個我在處理，稍等我一下。",
    )

    @staticmethod
    def _should_send_group_ack(metadata: dict[str, Any]) -> bool:
        """僅在待辦/提醒類私發場景下，才補發群內短確認。"""
        if bool(metadata.get("is_cron_job")):
            return False
        
        reply_scope = str(metadata.get("reply_scope") or "").strip().lower()
        reply_reason = str(metadata.get("reply_reason") or "").strip()
        wecom_chat_id = str(metadata.get("wecom_chat_id") or "").strip()
        reply_personal_action = bool(metadata.get("reply_personal_action"))
        
        logger.info(
            "[WecomChannel] _should_send_group_ack: reply_scope=%s "
            "reply_reason=%s wecom_chat_id=%s reply_personal_action=%s",
            reply_scope, reply_reason, wecom_chat_id, reply_personal_action
        )
        
        if reply_scope != "dm":
            logger.info("[WecomChannel] _should_send_group_ack: reply_scope不是dm，返回False")
            return False
        if reply_reason not in {
            "mentioned_target_user",
            "processor_target_user",
            "sender_is_target_user",
        }:
            logger.info("[WecomChannel] _should_send_group_ack: reply_reason不符合，返回False")
            return False
        if not wecom_chat_id:
            logger.info("[WecomChannel] _should_send_group_ack: 無wecom_chat_id，返回False")
            return False
        if not reply_personal_action:
            logger.info("[WecomChannel] _should_send_group_ack: reply_personal_action為False，返回False")
            return False
        logger.info("[WecomChannel] _should_send_group_ack: 返回True")
        return True

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
        if not str(metadata.get("wecom_chat_id") or "").strip():
            return False
        return True

    async def _send_interaction_ack(self, metadata: dict[str, Any]) -> None:
        """追問場景下在群內補發簡短確認。"""
        try:
            group_chat_id = str(metadata.get("wecom_chat_id") or "").strip()
            if not group_chat_id or not self._ws_client:
                return

            mention_name = str(metadata.get("interaction_mention_user") or "").strip()
            if mention_name:
                ack_text = f"已向 {mention_name} 追問，等回覆後繼續處理。"
            else:
                ack_text = "已私聊確認，等回覆後繼續處理。"

            body = {"msgtype": "markdown", "markdown": {"content": ack_text}}
            await self._ws_client.send_message(group_chat_id, body)
            logger.info("[WecomChannel] 追問確認已傳送: chat_id=%s", group_chat_id)
        except Exception as e:
            logger.warning("[WecomChannel] 追問確認傳送失敗: %s", e)

    @staticmethod
    def _fallback_group_ack() -> str:
        """群內短回覆兜底文案。"""
        return "好的，我知道了，會跟進處理。"

    @classmethod
    def _normalize_group_ack_text(cls, target_name: str, text: str) -> str:
        """過濾掉機器人旁白式短回覆。"""
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
            "你是一個企業微信群聊機器人。群裡有一條需要{name}關注的訊息,"
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
            logger.warning("[WecomChannel] 生成群確認文案失敗，使用回退: %s", e)

        return self._fallback_group_ack()

    async def _send_group_ack(self, metadata: dict[str, Any], content: str) -> None:
        """後臺生成併傳送群內簡短確認，不阻塞主回覆。"""
        try:
            target_name = str(metadata.get("reply_target_name") or "").strip() or "對方"
            group_chat_id = str(metadata.get("wecom_chat_id") or "").strip()
            if not group_chat_id:
                return

            ack_text = await asyncio.to_thread(
                self._generate_group_ack_sync, target_name, content
            )
            if ack_text and self._ws_client and getattr(self._ws_client, "is_connected", False):
                await self._ws_client.send_message(
                    group_chat_id,
                    {"msgtype": "markdown", "markdown": {"content": ack_text}},
                )
                logger.info("[WecomChannel] 群確認已傳送: chat_id=%s text=%s", group_chat_id, ack_text[:50])
        except Exception as e:
            logger.warning("[WecomChannel] 後臺群確認傳送失敗: %s", e)

    def _should_send_group_progress_hint(self, metadata: dict[str, Any]) -> bool:
        """僅對群聊訊息展示輕量處理進度。僅在數字分身模式下啟用。"""
        if not self.config.group_digital_avatar:
            return False
        return (
            str(metadata.get("chat_type") or "").strip() == "group"
            and bool(str(metadata.get("wecom_chat_id") or "").strip())
        )

    def _build_group_progress_hint_text(self, metadata: dict[str, Any]) -> str:
        """根據場景挑選一條儘量自然的群內處理提示。"""
        try:
            merged_count = int(metadata.get("merged_count", 1) or 1)
        except (TypeError, ValueError):
            merged_count = 1

        if (
            str(metadata.get("reply_scope") or "").strip().lower() == "dm"
            or str(metadata.get("reply_candidate_wecom_user_id") or "").strip()
        ):
            return self._GROUP_PROGRESS_HINT_TEXTS[0]
        if merged_count > 1:
            return self._GROUP_PROGRESS_HINT_TEXTS[1]
        return self._GROUP_PROGRESS_HINT_TEXTS[0]

    def _clear_group_progress_state(self, request_id: str) -> None:
        """清理指定請求的延遲提示任務和已傳送標記。"""
        pending_task = self._pending_group_progress_tasks.pop(request_id, None)
        if pending_task and not pending_task.done():
            pending_task.cancel()
        self._sent_group_progress_requests.discard(request_id)

    def _should_schedule_group_progress_hint(
        self, request_id: str, metadata: dict[str, Any]
    ) -> bool:
        """判斷是否需要安排群內處理提示。

        Args:
            request_id: 請求ID
            metadata: 請求後設資料

        Returns:
            bool: 是否需要安排提示
        """
        if not request_id:
            return False
        if request_id in self._pending_group_progress_tasks:
            return False
        if request_id in self._sent_group_progress_requests:
            return False
        if not self._should_send_group_progress_hint(metadata):
            return False
        return True

    def _schedule_group_progress_hint(self, request_id: str, metadata: dict[str, Any]) -> None:
        """為慢請求安排一條延遲傳送的群內處理提示。"""
        if not self._should_schedule_group_progress_hint(request_id, metadata):
            return

        task = asyncio.create_task(
            self._send_group_progress_hint_after_delay(request_id, dict(metadata)),
            name=f"wecom-progress-{request_id}",
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

            group_chat_id = str(metadata.get("wecom_chat_id") or "").strip()
            hint_text = self._build_group_progress_hint_text(metadata)
            if not group_chat_id or not hint_text:
                return

            if self._ws_client and getattr(self._ws_client, "is_connected", False):
                await self._ws_client.send_message(
                    group_chat_id,
                    {"msgtype": "markdown", "markdown": {"content": hint_text}},
                )
                self._sent_group_progress_requests.add(request_id)
                logger.info(
                    "[WecomChannel] 群進度提示已傳送: request_id=%s chat_id=%s text=%s",
                    request_id,
                    group_chat_id,
                    hint_text,
                )
        except Exception as e:
            logger.warning("[WecomChannel] 群進度提示傳送失敗: %s", e)
        finally:
            if self._pending_group_progress_tasks.get(request_id) is asyncio.current_task():
                self._pending_group_progress_tasks.pop(request_id, None)

    @staticmethod
    def _should_send_private_reply(
        is_dm_intent: bool,
        chat_type: str,
        target_user_id: str,
        event_type: Any,
    ) -> bool:
        """判斷是否應該私發訊息給目標使用者。

        Args:
            is_dm_intent: 是否有私發意圖
            chat_type: 聊天型別
            target_user_id: 目標使用者 ID
            event_type: 訊息事件型別

        Returns:
            bool: 是否應該私發
        """
        return (
            is_dm_intent
            and chat_type == "group"
            and target_user_id
            and event_type == EventType.CHAT_FINAL
        )

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
        self, *, frame: dict, sender_user_id: str, base_metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """根據群聊上下文補充預設的回覆投遞意圖。"""
        if not self.config.group_digital_avatar:
            return dict(base_metadata)
        metadata = dict(base_metadata)
        target_user_id = self._get_target_user_id()

        is_group_chat = str(base_metadata.get("chat_type") or "").strip() == "group"

        logger.info(
            "[WecomChannel] _build_reply_metadata: is_group_chat=%s target_user_id=%s sender_user_id=%s",
            is_group_chat, target_user_id, sender_user_id
        )

        if not is_group_chat or not target_user_id:
            # 非群聊或無目標使用者時，嘗試從介面卡獲取候選使用者（用於processor場景）
            if self._im_platform_adapter and hasattr(self._im_platform_adapter, 'build_relevance_metadata'):
                adapter_metadata = self._im_platform_adapter.build_relevance_metadata(
                    metadata, sender_user_id=sender_user_id, relevant=True
                )
                if adapter_metadata:
                    metadata.update(adapter_metadata)
                    logger.info(
                        "[WecomChannel] 從介面卡獲取候選使用者: %s",
                        adapter_metadata.get("reply_candidate_wecom_user_id")
                    )
            return metadata

        mentioned_user_ids = self._extract_mentioned_user_ids(frame)
        if mentioned_user_ids:
            metadata["mentioned_user_ids"] = mentioned_user_ids
            metadata["im_mentioned_user_ids"] = mentioned_user_ids

        if target_user_id in mentioned_user_ids:
            target_user_name = ""
            if self._im_platform_adapter and hasattr(self._im_platform_adapter, 'resolve_user_display_name'):
                target_user_name = self._im_platform_adapter.resolve_user_display_name(target_user_id)
            metadata["reply_candidate_wecom_user_id"] = target_user_id
            metadata["reply_candidate_reason"] = "mentioned_target_user"
            if target_user_name:
                metadata["reply_target_name"] = target_user_name
            return metadata

        if target_user_id == sender_user_id:
            # 傳送者是目標使用者，設定為候選使用者，讓 IMOutboundPipeline 根據內容決定是否私發
            target_user_name = ""
            if self._im_platform_adapter and hasattr(self._im_platform_adapter, 'resolve_user_display_name'):
                target_user_name = self._im_platform_adapter.resolve_user_display_name(target_user_id)
            metadata["reply_candidate_wecom_user_id"] = target_user_id
            metadata["reply_candidate_reason"] = "sender_is_target_user"
            if target_user_name:
                metadata["reply_target_name"] = target_user_name
            logger.info(
                "[WecomChannel] _build_reply_metadata: 傳送者是目標使用者，設定候選使用者 reply_candidate_wecom_user_id=%s",
                target_user_id
            )
            return metadata

        # 目標使用者不在被@列表中，但設定為候選（後續根據內容決定是否私發）
        # 同時設定 reply_target_name 供 LLM 判斷時使用
        target_user_name = ""
        if self._im_platform_adapter and hasattr(self._im_platform_adapter, 'resolve_user_display_name'):
            target_user_name = self._im_platform_adapter.resolve_user_display_name(target_user_id)
        metadata["reply_candidate_wecom_user_id"] = target_user_id
        metadata["reply_candidate_reason"] = "processor_target_user"
        if target_user_name:
            metadata["reply_target_name"] = target_user_name
        logger.info(
            "[WecomChannel] _build_reply_metadata: 設定候選使用者 reply_candidate_wecom_user_id=%s reply_target_name=%s",
            target_user_id, target_user_name
        )
        return metadata

    def _extract_mentioned_user_ids(self, frame: dict) -> list[str]:
        """從企業微信訊息中提取被 @ 的使用者ID列表。"""
        mentioned_ids: list[str] = []
        try:
            body = frame.get("body") or {}
            if not isinstance(body, dict):
                body = {}
            if not body and frame.get("msgtype"):
                body = frame
            
            text = body.get("text") or {}
            if isinstance(text, dict):
                mentioned_list = text.get("mentioned_list") or []
                if isinstance(mentioned_list, list):
                    for item in mentioned_list:
                        if isinstance(item, dict):
                            user_id = item.get("userid") or item.get("user_id") or ""
                            if user_id and user_id not in mentioned_ids:
                                mentioned_ids.append(user_id)
                        elif isinstance(item, str) and item not in mentioned_ids:
                            mentioned_ids.append(item)
        except Exception as e:
            logger.debug("[WecomChannel] 提取mentioned使用者失敗: %s", e)
        return mentioned_ids

    @staticmethod
    def _is_control_message(content: str) -> bool:
        from jiuwenclaw.gateway.message_handler.command_parser.slash_command import is_control_like_for_im_batching

        return is_control_like_for_im_batching(content)

    async def _enqueue_message_batch(
        self,
        *,
        chat_id: str,
        user_id: str,
        content: str,
        timestamp_ms: int,
        metadata: dict[str, Any],
    ) -> None:
        """將同一使用者的連續訊息做短暫聚合，再統一交給 gateway 入站鏈路。"""
        if self._is_control_message(content):
            await self._process_batched_message(
                chat_id=chat_id,
                user_id=user_id,
                merged_content=content,
                timestamp_ms=timestamp_ms,
                metadata=metadata,
            )
            return

        merge_window_ms = max(int(self.config.message_merge_window_ms or 0), 0)
        if merge_window_ms <= 0:
            await self._process_batched_message(
                chat_id=chat_id,
                user_id=user_id,
                merged_content=content,
                timestamp_ms=timestamp_ms,
                metadata=metadata,
            )
            return

        batch_key = (chat_id, user_id or "")
        async with self._pending_message_lock:
            batch = self._pending_message_batches.get(batch_key)
            if batch is None:
                batch = {
                    "chat_id": chat_id,
                    "user_id": user_id,
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
        merged_metadata["merged_count"] = len(items)
        for key in (
            "reply_scope",
            "reply_wecom_user_id",
            "reply_wecom_chat_id",
            "reply_reason",
            "reply_target_name",
        ):
            for item in reversed(items):
                value = item["metadata"].get(key)
                if isinstance(value, str) and value.strip():
                    merged_metadata[key] = value
                    break

        if len(items) > 1:
            logger.info(
                "[WecomChannel] 合併連續訊息: chat_id=%s user_id=%s count=%s",
                batch.get("chat_id", ""),
                batch.get("user_id", ""),
                len(items),
            )

        await self._process_batched_message(
            chat_id=batch["chat_id"],
            user_id=batch["user_id"],
            merged_content=merged_content,
            timestamp_ms=int(last_item["timestamp_ms"]),
            metadata=merged_metadata,
        )

    async def _process_batched_message(
        self,
        *,
        chat_id: str,
        user_id: str,
        merged_content: str,
        timestamp_ms: int,
        metadata: dict[str, Any],
    ) -> None:
        """對單條或合併後的訊息做平臺整理後轉發。"""
        chat_type = metadata.get("chat_type", "")
        is_group_chat = chat_type == "group"

        enriched_metadata = dict(metadata)
        enriched_metadata["timestamp_ms"] = timestamp_ms
        enriched_metadata["im_platform"] = "wecom"
        enriched_metadata["im_chat_type"] = "group" if is_group_chat else "direct"
        enriched_metadata["im_sender_user_id"] = user_id
        enriched_metadata["im_thread_id"] = chat_id

        await self._handle_incoming_message_internal(
            chat_id=chat_id,
            user_id=user_id,
            content=merged_content,
            metadata=enriched_metadata,
        )

    async def _handle_incoming_message_internal(
        self,
        chat_id: str,
        user_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> None:
        """內部訊息處理入口。"""
        from jiuwenclaw.gateway.routing.interaction_context import PendingInteraction

        chat_type = metadata.get("chat_type", "")
        is_group_chat = chat_type == "group"
        msg_enable_streaming = self.config.enable_streaming
        is_stream = True
        if self.config.group_digital_avatar and is_group_chat:
            msg_enable_streaming = False
            is_stream = False
            metadata = dict(metadata)
            metadata["avatar_mode"] = True
            metadata["principal_user_id"] = self._get_target_user_id()
            metadata["triggering_user_id"] = user_id

        if not is_group_chat and self.config.group_digital_avatar:
            principal_id = self._get_target_user_id()
            if user_id and user_id == principal_id:
                pi = PendingInteraction.find_pending(self.name, principal_id)
                if pi is not None:
                    metadata = dict(metadata)
                    metadata["avatar_mode"] = True
                    metadata["is_resume_message"] = True
                    metadata["principal_user_id"] = principal_id
                    metadata["dm_pending_interaction_id"] = pi.interaction_id

                    resume_msg = Message(
                        id=f"{self.name}:resume:{int(time.time() * 1000)}",
                        type="req",
                        channel_id=self.name,
                        session_id=pi.origin_session_id,
                        params={"content": content, "query": content},
                        timestamp=time.time(),
                        ok=True,
                        req_method=ReqMethod.CHAT_SEND,
                        is_stream=False,
                        metadata=metadata,
                        group_digital_avatar=True,
                        enable_memory=self.config.enable_memory,
                        enable_streaming=False,
                    )
                    if self._message_callback:
                        self._message_callback(resume_msg)
                    else:
                        await self.bus.route_user_message(resume_msg)
                    return

        effective_group_digital_avatar = self.config.group_digital_avatar and is_group_chat
        msg = Message(
            id=metadata.get("message_id") or f"wecom_{int(time.time() * 1000)}",
            type="req",
            channel_id=self.name,
            session_id=chat_id,
            params={"content": content, "query": content},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            is_stream=is_stream,
            chat_id=chat_id,
            metadata=metadata,
            group_digital_avatar=effective_group_digital_avatar,
            enable_memory=self.config.enable_memory,
            enable_streaming=msg_enable_streaming,
        )

        if self._message_callback:
            self._message_callback(msg)
        else:
            await self.bus.route_user_message(msg)

    async def _handle_incoming_message(self, frame: dict) -> None:
        """處理 SDK 收到的訊息，轉換為 jiuwenclaw Message 並分發。"""
        chatid, req_id, content = self._extract_frame_info(frame)
        if not content:
            logger.debug("WecomChannel 收到空內容，跳過")
            return
        if not chatid:
            # 除錯：列印 frame 結構以便排查
            body_preview = frame.get("body") or frame
            if isinstance(body_preview, dict):
                keys = ("msgtype", "chatid", "chat_id", "from", "msgid")
                preview = {k: body_preview.get(k) for k in keys if k in body_preview}
            else:
                preview = str(body_preview)[:200]
            logger.warning(
                "WecomChannel 無法從 frame 提取 chatid，跳過訊息。frame.body 預覽: %s",
                preview,
            )
            return

        if self._is_duplicate_message(req_id or chatid):
            logger.debug("[WecomChannel] 訊息去重: message_id=%s", req_id)
            return

        if not self.is_allowed(chatid):
            logger.warning("[WecomChannel] 傳送者 %s 未被允許", chatid)
            return

        logger.info("[WecomChannel] 收到訊息: chatid=%s req_id=%s content=%s", chatid, req_id, content[:50])

        body = frame.get("body") or {}
        if not isinstance(body, dict):
            body = {}
        if not body and frame.get("msgtype"):
            body = frame

        from_obj = body.get("from") or frame.get("from") or {}
        if not isinstance(from_obj, dict):
            from_obj = {}
        sender_user_id = from_obj.get("userid") or from_obj.get("user_id") or ""

        chat_type = "group" if body.get("chattype") == "group" else "single"

        # 寫入 last_chat_id 和 last_user_id 供 cron/心跳推送使用
        if chatid and not self._looks_like_msgid(chatid):
            try:
                from jiuwenclaw.common.config import update_channel_in_config

                update_data: dict[str, str] = {"last_chat_id": chatid or ""}
                if sender_user_id:
                    update_data["last_user_id"] = sender_user_id
                update_channel_in_config("wecom", update_data)
            except Exception as e:
                logger.warning("WecomChannel 寫入 last_chat_id 失敗: %s", e)

        req_id_final = req_id or f"wecom_{int(time.time() * 1000)}"

        metadata = self._build_reply_metadata(
            frame=frame,
            sender_user_id=sender_user_id,
            base_metadata={
                "message_id": req_id,
                "wecom_req_id": req_id_final,
                "chat_type": chat_type,
                "wecom_chat_id": chatid,
                "wecom_user_id": sender_user_id,
                "im_platform": "wecom",
                "im_chat_type": "group" if chat_type == "group" else "direct",
                "im_sender_user_id": sender_user_id,
                "im_thread_id": chatid,
            },
        )

        if self.config.group_digital_avatar:
            try:
                self._message_storage.add_message_to_memory(
                    chat_id=chatid,
                    message={
                        "message_id": req_id,
                        "content": content,
                        "timestamp": int(time.time() * 1000),
                        "user_id": sender_user_id,
                        "chat_type": chat_type,
                    }
                )
            except Exception as e:
                logger.warning(f"[WecomChannel] 記錄訊息到本地儲存失敗: {e}")
        _effective_streaming = self.config.enable_streaming
        if self.config.group_digital_avatar and chat_type == "group":
            _effective_streaming = False
        if (
            _effective_streaming
            and self._ws_client
            and getattr(self._ws_client, "is_connected", False)
        ):
            stream_id = (
                generate_req_id("stream")
                if generate_req_id
                else f"stream_{int(time.time()*1000)}_{req_id_final}"
            )
            self._pending_streams[req_id_final] = {
                "frame": frame,
                "stream_id": stream_id,
                "accumulated": "",
            }
            if self.config.group_digital_avatar and chat_type == "group":
                pass
            else:
                target_user_id = self._get_target_user_id()
                if chat_type == "group" and target_user_id and target_user_id != sender_user_id:
                    try:
                        ack_text = "我先確認下，馬上回復。"
                        await self._ws_client.send_message(
                            chatid,
                            {"msgtype": "markdown", "markdown": {"content": ack_text}}
                        )
                        logger.info("[WecomChannel] 群聊簡短確認已傳送: chat_id=%s", chatid)
                    except Exception as e:
                        logger.warning("[WecomChannel] 群聊簡短確認傳送失敗: %s", e)
                else:
                    await self._send_stream_placeholder(req_id_final)

        # 數字分身模式：走訊息批次合併；否則直接處理
        _ts = int(time.time() * 1000)
        if self.config.group_digital_avatar:
            await self._enqueue_message_batch(
                chat_id=chatid,
                user_id=sender_user_id,
                content=content,
                timestamp_ms=_ts,
                metadata=metadata,
            )
        else:
            await self._process_batched_message(
                chat_id=chatid,
                user_id=sender_user_id,
                merged_content=content,
                timestamp_ms=_ts,
                metadata=metadata,
            )

    def _extract_chatid(self, msg: Message) -> str | None:
        """從出站訊息提取 chatid；私發模式優先使用 reply_wecom_user_id。"""
        meta = getattr(msg, "metadata", None) or {}

        reply_scope = str(meta.get("reply_scope") or "").strip().lower()
        if reply_scope == "dm":
            dm_user_id = str(meta.get("reply_wecom_user_id") or "").strip()
            if dm_user_id:
                logger.info("[WecomChannel] _extract_chatid: 私發模式，返回使用者ID=%s", dm_user_id)
                return dm_user_id
            else:
                logger.warning("[WecomChannel] _extract_chatid: 私發模式但無使用者ID，嘗試其他方式")

        # cron/系統訊息：優先使用 wecom_user_id 私發給使用者（參考飛書 open_id 優先邏輯）
        msg_id = str(msg.id or "").strip()
        is_system_msg = (
            msg_id.startswith("cron-push")
            or msg_id.startswith("heartbeat-relay")
            or str(getattr(msg, "session_id", "") or "").startswith(("__", "heartbeat_", "cron"))
        )
        if is_system_msg:
            wecom_user_id = str(meta.get("wecom_user_id") or "").strip()
            if wecom_user_id:
                logger.info("[WecomChannel] 系統訊息私發給使用者: user_id=%s, msg.id=%s", wecom_user_id, msg.id)
                return wecom_user_id

        chatid = (meta.get("wecom_chat_id") or "").strip()
        if chatid:
            logger.info("[WecomChannel] _extract_chatid: 返回群聊ID=%s", chatid)
            return chatid

        sid = getattr(msg, "session_id", None) or msg.id
        sid_str = str(sid) if sid else ""
        # 系統會話（心跳、cron 等）無有效 chatid，使用 config 中的 last_chat_id
        system_session_prefixes = ("__", "heartbeat_", "cron")
        if sid_str and not any(sid_str.startswith(p) for p in system_session_prefixes):
            if not self._looks_like_msgid(sid_str):
                logger.info("[WecomChannel] _extract_chatid: 返回session_id=%s", sid_str)
                return sid_str
        try:
            from jiuwenclaw.common.config import get_config
            ch_cfg = (get_config().get("channels") or {}).get("wecom") or {}
            # last_chat_id：使用者聊天時自動寫入；default_chat_id：可手動配置，用於心跳/定時推送
            last = str(ch_cfg.get("last_chat_id") or ch_cfg.get("default_chat_id") or "").strip()
            if last and not self._looks_like_msgid(last):
                logger.info("[WecomChannel] _extract_chatid: 返回config中的chat_id=%s", last)
                return last
            return None
        except Exception:
            return None

    def _extract_content(self, msg: Message) -> str:
        """從出站訊息中提取文字內容。"""
        payload = getattr(msg, "payload", None) or {}
        params = getattr(msg, "params", None) or {}
        content = (
            params.get("content")
            or payload.get("content")
            or ""
        )
        if isinstance(content, dict):
            content = content.get("output", str(content))
        return str(content or "").strip()

    @staticmethod
    def _strip_think_tags(text: str) -> str:
        """移除 <think>...</think> 塊及未閉合的 <think>...，避免將 Agent 的思考過程展示給使用者。"""
        if not text or not isinstance(text, str):
            return text or ""
        # 1. 移除完整的 <think>...</think> 塊
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
        # 2. 移除未閉合的 <think>...（流式場景下可能先收到 <think> 後收到 </think>）
        text = re.sub(r"<think>[\s\S]*$", "", text, flags=re.IGNORECASE | re.DOTALL)
        return text

    @staticmethod
    def _is_thinking_only_content(text: str) -> bool:
        """判斷內容是否為空或僅為佔位符。"""
        if not text or not isinstance(text, str):
            return True
        t = text.strip()
        if not t:
            return True
        # 純省略號、純點
        if re.match(r"^[.．。…\s]+$", t):
            return True
        return False

    def _extract_content_from_payload(self, msg: Message) -> str | None:
        """從 chat.delta / chat.final 的 payload 提取 content，無則返回 None。"""
        payload = getattr(msg, "payload", None) or {}
        if not isinstance(payload, dict):
            return None
        content = payload.get("content")
        if content is None:
            return None
        return str(content)

    @staticmethod
    def _is_reasoning_chunk(msg: Message) -> bool:
        """判斷當前訊息是否為不應展示給企業微信使用者的 reasoning chunk。"""
        payload = getattr(msg, "payload", None) or {}
        if not isinstance(payload, dict):
            return False
        source_chunk_type = str(payload.get("source_chunk_type") or "").strip().lower()
        return source_chunk_type == "llm_reasoning"

    async def _send_stream_placeholder(self, req_id: str) -> None:
        """傳送流式首幀佔位。PHP SDK 用 <think></think> 顯示載入動畫，企業微信 Markdown 可能支援。"""
        entry = self._pending_streams.get(req_id)
        if not entry or not self._ws_client or not getattr(self._ws_client, "is_connected", False):
            return
        try:
            # 嘗試 <think></think>（PHP SDK 用法，可能渲染為載入動畫）；若不支援則顯示為 ...
            placeholder = "<think></think>"
            await self._ws_client.reply_stream(
                entry["frame"],
                entry["stream_id"],
                placeholder,
                finish=False,
            )
            logger.debug("WecomChannel 已傳送流式佔位: req_id=%s", req_id)
        except Exception as e:
            logger.debug("WecomChannel 傳送流式佔位失敗: %s", e)

    def _get_req_id_for_stream(self, msg: Message) -> str | None:
        """從出站訊息中提取 wecom_req_id，用於查詢 pending stream。"""
        meta = getattr(msg, "metadata", None) or {}
        return (meta.get("wecom_req_id") or "").strip() or None

    async def send(self, msg: Message) -> None:
        """透過企業微信傳送訊息。支援流式（reply_stream）與非流式（send_message）。"""
        if not self._ws_client or not getattr(self._ws_client, "is_connected", False):
            logger.warning("WecomChannel 未連線，跳過傳送")
            return

        # 不向企業微信傳送 chat.processing_status（思考狀態事件），避免展示思考過程
        if msg.event_type == EventType.CHAT_PROCESSING_STATUS:
            payload = getattr(msg, "payload", None) or {}
            meta = getattr(msg, "metadata", None) or {}
            if isinstance(payload, dict):
                await self._handle_processing_status_event(msg, meta, payload)
            return

        # 提取事件型別
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        event_type = getattr(msg.event_type, "value", None) or payload.get("event_type") or ""

        # 處理檔案傳送事件（chat.media 與 chat.file 統一走檔案傳送路徑）
        if event_type in ("chat.file", "chat.media"):
            await self._send_file_message(msg)
            return

        # 心跳/系統事件
        if msg.event_type == EventType.HEARTBEAT_RELAY:
            chatid = self._extract_chatid(msg)
            logger.info("[WecomChannel] 心跳/系統事件處理: chatid=%s, msg.id=%s", chatid, msg.id)
            if chatid:
                payload = getattr(msg, "payload", None) or {}
                if isinstance(payload, dict) and payload.get("heartbeat"):
                    try:
                        body = {"msgtype": "markdown", "markdown": {"content": str(payload.get("heartbeat"))}}
                        await self._ws_client.send_message(chatid, body)
                        logger.info("[WecomChannel] 心跳已傳送至 chatid=%s", chatid)
                    except Exception as e:
                        logger.warning("[WecomChannel] 心跳傳送失敗: chatid=%s, error=%s", chatid, e)
            else:
                logger.warning(
                    "[WecomChannel] 心跳未傳送：無有效 chatid。請先在企業微信中與機器人對話一次，以寫入 last_chat_id"
                )
            return

        # 流式回覆：CHAT_DELTA / CHAT_FINAL 透過 reply_stream 傳送，替換首幀「...」
        req_id = self._get_req_id_for_stream(msg)
        _msg_streaming = getattr(msg, "enable_streaming", None)
        _send_streaming = _msg_streaming if _msg_streaming is not None else self.config.enable_streaming
        _meta_tmp = getattr(msg, "metadata", None) or {}
        if self.config.group_digital_avatar and str(_meta_tmp.get("chat_type") or "").strip() == "group":
            _send_streaming = False
        if req_id and _send_streaming:
            entry = self._pending_streams.get(req_id)
            if entry:
                if self._is_reasoning_chunk(msg):
                    logger.debug("WecomChannel 跳過 reasoning chunk: req_id=%s", req_id)
                    return
                content = self._extract_content_from_payload(msg)
                if content is not None:
                    entry["accumulated"] = (entry.get("accumulated") or "") + content
                    # 移除 <think>...</think> 塊，不將 Agent 思考過程展示給使用者
                    to_send = self._strip_think_tags(entry["accumulated"]).strip()
                    if not to_send or self._is_thinking_only_content(to_send):
                        if msg.event_type == EventType.CHAT_FINAL:
                            self._pending_streams.pop(req_id, None)
                            self._stream_completed_requests.add(req_id)
                        return
                    
                    # 群聊場景：出站管線已在 publish_robot_messages 時設定 reply_scope
                    meta = getattr(msg, "metadata", None) or {}
                    chat_type = str(meta.get("chat_type") or "").strip()
                    # 優先使用 IMOutboundPipeline 設定的 reply_wecom_user_id
                    target_user_id = str(meta.get("reply_wecom_user_id") or "").strip()
                    if not target_user_id:
                        # 回退：使用配置中的 my_user_id
                        target_user_id = self._get_target_user_id()
                    is_dm_intent = str(meta.get("reply_scope") or "").strip().lower() == "dm"

                    if self._should_send_private_reply(is_dm_intent, chat_type, target_user_id, msg.event_type):
                        # 群聊場景，私發詳細回覆給目標使用者
                        logger.info("[WecomChannel] 群聊訊息私發給目標使用者: req_id=%s user_id=%s", req_id, target_user_id)
                        self._pending_streams.pop(req_id, None)
                        self._stream_completed_requests.add(req_id)

                        # 私發詳細回覆給目標使用者
                        try:
                            body = {"msgtype": "markdown", "markdown": {"content": to_send}}
                            await self._ws_client.send_message(target_user_id, body)
                            logger.info("[WecomChannel] 私發訊息成功: user_id=%s len=%d", target_user_id, len(to_send))
                        except Exception as e:
                            logger.error("[WecomChannel] 私發訊息失敗: %s", e)
                        return
                    
                    # 非群聊場景或無目標使用者，正常流式傳送
                    try:
                        is_final = msg.event_type == EventType.CHAT_FINAL
                        await self._ws_client.reply_stream(
                            entry["frame"],
                            entry["stream_id"],
                            to_send,
                            finish=is_final,
                        )
                        logger.debug(
                            "WecomChannel 流式傳送: req_id=%s finish=%s len=%d",
                            req_id,
                            is_final,
                            len(entry["accumulated"]),
                        )
                        if is_final:
                            self._pending_streams.pop(req_id, None)
                            self._stream_completed_requests.add(req_id)
                    except Exception as e:
                        logger.error("WecomChannel 流式傳送失敗: %s", e)
                        if msg.event_type == EventType.CHAT_FINAL:
                            self._pending_streams.pop(req_id, None)
                            self._stream_completed_requests.add(req_id)
                    return

        if req_id and req_id in self._stream_completed_requests:
            logger.debug("WecomChannel 跳過已完成的流式請求: req_id=%s", req_id)
            self._stream_completed_requests.discard(req_id)
            return

        if msg.event_type == EventType.CHAT_ERROR:
            if req_id:
                self._pending_streams.pop(req_id, None)
            payload = getattr(msg, "payload", None) or {}
            err_text = payload.get("error", "處理出錯") if isinstance(payload, dict) else "處理出錯"
            chatid = self._extract_chatid(msg)
            if chatid:
                try:
                    await self._ws_client.send_message(
                        chatid,
                        {"msgtype": "markdown", "markdown": {"content": f"⚠️ {err_text}"}},
                    )
                except Exception as e:
                    logger.debug("WecomChannel 傳送錯誤訊息失敗: %s", e)
            return

        # 非流式或無 pending：僅 CHAT_FINAL 用 send_message
        if msg.event_type != EventType.CHAT_FINAL:
            return
        if req_id:
            self._pending_streams.pop(req_id, None)
        if self._is_reasoning_chunk(msg):
            logger.debug("[WecomChannel] 跳過 final reasoning chunk")
            return
        content = self._strip_think_tags(self._extract_content(msg)).strip()
        if not content or self._is_thinking_only_content(content):
            logger.debug("[WecomChannel] 訊息內容為空或僅為思考佔位，跳過傳送")
            return

        meta = getattr(msg, "metadata", None) or {}
        chatid = self._extract_chatid(msg)
        logger.info(
            "[WecomChannel] send: 應用意圖後提取chatid=%s reply_scope=%s reply_wecom_user_id=%s wecom_chat_id=%s",
            chatid,
            meta.get("reply_scope"),
            meta.get("reply_wecom_user_id"),
            meta.get("wecom_chat_id")
        )
        if not chatid:
            logger.warning("[WecomChannel] 無法確定回發目標 chatid, msg.id=%s", msg.id)
            return

        request_id = str(msg.id or "").strip()
        if request_id:
            self._clear_group_progress_state(request_id)

        try:
            is_dm = str(meta.get("reply_scope") or "").strip().lower() == "dm"
            if msg.group_digital_avatar and not is_dm:
                mention_user_id = str(
                    meta.get("interaction_mention_user_id") or ""
                ).strip()
                sender_user_id = str(
                    meta.get("im_sender_user_id") or ""
                ).strip()
                at_user_id = mention_user_id or sender_user_id
                if at_user_id and not at_user_id.startswith("bot"):
                    content = f"<@{at_user_id}>\n{content}"

            body = {"msgtype": "markdown", "markdown": {"content": content}}
            await self._ws_client.send_message(chatid, body)
            
            is_dm = str(meta.get("reply_scope") or "").strip().lower() == "dm"
            if is_dm:
                logger.info("[WecomChannel] 私發訊息: chatid=%s len=%d", chatid, len(content))
            else:
                logger.info("[WecomChannel] 傳送訊息: chatid=%s len=%d", chatid, len(content))

            if msg.group_digital_avatar and self._should_send_interaction_ack(meta):
                asyncio.create_task(self._send_interaction_ack(meta))
            elif msg.group_digital_avatar and self._should_send_group_ack(meta):
                group_chat_id = str(meta.get("wecom_chat_id") or "").strip()
                if group_chat_id and group_chat_id != chatid:
                    asyncio.create_task(self._send_group_ack(meta, content))

            if self.config.group_digital_avatar:
                try:
                    self._message_storage.add_message_to_memory(
                        chat_id=chatid,
                        message={
                            "message_id": f"bot_{int(time.time() * 1000)}_{msg.id}",
                            "content": content,
                            "timestamp": int(time.time() * 1000),
                            "user_id": f"bot_{self.config.bot_id}",
                            "chat_type": "bot_reply",
                        }
                    )
                except Exception as e:
                    logger.warning(f"[WecomChannel] 記錄機器人回覆訊息失敗: {e}")

        except Exception as e:
            logger.error("WecomChannel 傳送失敗: %s", e)

    async def _run_client(self) -> None:
        """在後臺執行 WebSocket 客戶端。"""
        if not WECOM_AVAILABLE or not WSClient:
            logger.error("WecomChannel 依賴未安裝，請執行: pip install wecom-aibot-sdk")
            return

        opts: dict[str, Any] = {
            "bot_id": self.config.bot_id,
            "secret": self.config.secret,
        }
        if self.config.ws_url:
            opts["ws_url"] = self.config.ws_url
        client = WSClient(**opts)

        async def on_text(frame: dict) -> None:
            await self._handle_incoming_message(frame)

        async def on_image(frame: dict) -> None:
            await self._handle_image_message(frame)

        async def on_file(frame: dict) -> None:
            await self._handle_file_message(frame)

        async def on_voice(frame: dict) -> None:
            await self._handle_voice_message(frame)

        async def on_video(frame: dict) -> None:
            await self._handle_video_message(frame)

        async def on_mixed(frame: dict) -> None:
            await self._handle_mixed_message(frame)

        client.on("message.text", on_text)
        client.on("message.image", on_image)
        client.on("message.file", on_file)
        client.on("message.voice", on_voice)
        client.on("message.video", on_video)
        client.on("message.mixed", on_mixed)

        self._ws_client = client

        # 初始化檔案服務
        try:
            from jiuwenclaw.gateway.channel_manager.im_platforms.wecom.wecom_file_service import WecomFileService
            workspace_dir = self.config.workspace_dir or str(get_agent_workspace_dir())
            self._file_service = WecomFileService(
                ws_client=client,
                max_download_size=self.config.max_download_size,
                download_timeout=self.config.download_timeout,
                workspace_dir=workspace_dir,
            )
            logger.info("WecomChannel 檔案服務已初始化")
        except Exception as e:
            logger.warning(f"WecomChannel 檔案服務初始化失敗: {e}")
            self._file_service = None

        try:
            await client.connect()
            logger.info("WecomChannel WebSocket 已連線")
            logger.info("WecomChannel 保活迴圈已啟動（不因短暫斷線退出，不打斷 SDK 重連）")

            # 不要把 is_connected 作為退出條件。
            # wecom-aibot-sdk 在網路抖動/機器休眠喚醒後會先進入 disconnected，
            # 然後在內部自動重連；若這裡因短暫 disconnected 直接退出，會觸發 finally
            # 主動 disconnect，打斷 SDK 的重連流程，導致通道長期停在 disconnected。
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("WecomChannel WebSocket 異常: %s", e)
        finally:
            try:
                await client.disconnect()
            except Exception as e:
                logger.warning("WecomChannel 斷開連線時異常: %s", e)
            self._ws_client = None

    async def start(self) -> None:
        """啟動企業微信通道。"""
        if not WECOM_AVAILABLE:
            logger.error("WecomChannel 依賴未安裝，請執行: pip install wecom-aibot-sdk")
            return

        if not self.config.bot_id or not self.config.secret:
            logger.error("WecomChannel 未配置 bot_id 或 secret")
            return

        if self._running:
            logger.warning("WecomChannel 已在執行")
            return

        self._running = True
        self._main_loop = asyncio.get_running_loop()
        self._connect_task = asyncio.create_task(self._run_client(), name="wecom-channel")
        logger.info("WecomChannel 已啟動（WebSocket 長連線）")

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """停止企業微信通道。"""
        self._running = False
        self._stopping = True
        self._stream_text_buffers.clear()
        self._stream_completed_requests.clear()

        for task in list(self._pending_group_progress_tasks.values()):
            if not task.done():
                task.cancel()
        self._pending_group_progress_tasks.clear()
        self._sent_group_progress_requests.clear()

        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
            self._connect_task = None

        if self._ws_client:
            try:
                await self._ws_client.disconnect()
            except Exception as e:
                logger.warning("WecomChannel 停止時斷開異常: %s", e)
            self._ws_client = None

        logger.info("WecomChannel 已停止")
        self._stopping = False

    # ==================== 檔案訊息處理方法 ====================

    async def _handle_image_message(self, frame: dict) -> None:
        """處理圖片訊息"""
        if not self.config.enable_file_download:
            logger.debug("WecomChannel 檔案下載功能已禁用")
            return

        if not self._file_service:
            logger.warning("WecomChannel 檔案服務未初始化")
            return

        body = frame.get("body", {})
        image_data = body.get("image", {})
        url = image_data.get("url", "")
        aes_key = image_data.get("aeskey", "")

        if not url or not aes_key:
            logger.warning("WecomChannel 圖片訊息缺少 url 或 aeskey")
            return

        # 提取訊息資訊
        chatid, req_id, _ = self._extract_frame_info(frame)
        message_id = req_id or f"img_{int(time.time() * 1000)}"

        # 下載圖片
        file_info = await self._file_service.download_file(
            url=url,
            aes_key=aes_key,
            message_id=message_id,
            file_category="images",
        )

        if not file_info:
            content = "[圖片: 下載失敗]"
        else:
            content = "[圖片]"
            logger.info(f"WecomChannel 圖片下載成功: {file_info['path']}")

        # 構建訊息併傳送
        await self._send_file_message_to_handler(frame, content, [file_info] if file_info else None)

    async def _handle_file_message(self, frame: dict) -> None:
        """處理檔案訊息"""
        if not self.config.enable_file_download:
            logger.debug("WecomChannel 檔案下載功能已禁用")
            return

        if not self._file_service:
            logger.warning("WecomChannel 檔案服務未初始化")
            return

        body = frame.get("body", {})
        file_data = body.get("file", {})
        url = file_data.get("url", "")
        aes_key = file_data.get("aeskey", "")
        filename = file_data.get("filename", "unknown_file")

        if not url or not aes_key:
            logger.warning("WecomChannel 檔案訊息缺少 url 或 aeskey")
            return

        # 提取訊息資訊
        chatid, req_id, _ = self._extract_frame_info(frame)
        message_id = req_id or f"file_{int(time.time() * 1000)}"

        # 下載檔案
        file_info = await self._file_service.download_file(
            url=url,
            aes_key=aes_key,
            message_id=message_id,
            file_category="files",
            filename=filename,
        )

        if not file_info:
            content = f"[檔案: {filename} 下載失敗]"
        else:
            content = f"[檔案: {filename}]"
            logger.info(f"WecomChannel 檔案下載成功: {file_info['path']}")

        # 構建訊息併傳送
        await self._send_file_message_to_handler(frame, content, [file_info] if file_info else None)

    async def _handle_voice_message(self, frame: dict) -> None:
        """處理語音訊息"""
        if not self.config.enable_file_download:
            logger.debug("WecomChannel 檔案下載功能已禁用")
            return

        if not self._file_service:
            logger.warning("WecomChannel 檔案服務未初始化")
            return

        body = frame.get("body", {})
        voice_data = body.get("voice", {})
        url = voice_data.get("url", "")
        aes_key = voice_data.get("aeskey", "")

        if not url or not aes_key:
            logger.warning("WecomChannel 語音訊息缺少 url 或 aeskey")
            return

        # 提取訊息資訊
        chatid, req_id, _ = self._extract_frame_info(frame)
        message_id = req_id or f"voice_{int(time.time() * 1000)}"

        # 下載語音
        file_info = await self._file_service.download_file(
            url=url,
            aes_key=aes_key,
            message_id=message_id,
            file_category="voice",
        )

        if not file_info:
            content = "[語音: 下載失敗]"
        else:
            content = "[語音]"
            logger.info(f"WecomChannel 語音下載成功: {file_info['path']}")

        # 構建訊息併傳送
        await self._send_file_message_to_handler(frame, content, [file_info] if file_info else None)

    async def _handle_video_message(self, frame: dict) -> None:
        """處理影片訊息"""
        if not self.config.enable_file_download:
            logger.debug("WecomChannel 檔案下載功能已禁用")
            return

        if not self._file_service:
            logger.warning("WecomChannel 檔案服務未初始化")
            return

        body = frame.get("body", {})
        video_data = body.get("video", {})
        url = video_data.get("url", "")
        aes_key = video_data.get("aeskey", "")

        if not url or not aes_key:
            logger.warning("WecomChannel 影片訊息缺少 url 或 aeskey")
            return

        # 提取訊息資訊
        chatid, req_id, _ = self._extract_frame_info(frame)
        message_id = req_id or f"video_{int(time.time() * 1000)}"

        # 下載影片
        file_info = await self._file_service.download_file(
            url=url,
            aes_key=aes_key,
            message_id=message_id,
            file_category="video",
        )

        if not file_info:
            content = "[影片: 下載失敗]"
        else:
            content = "[影片]"
            logger.info(f"WecomChannel 影片下載成功: {file_info['path']}")

        # 構建訊息併傳送
        await self._send_file_message_to_handler(frame, content, [file_info] if file_info else None)

    async def _handle_mixed_message(self, frame: dict) -> None:
        """處理圖文混排訊息"""
        if not self.config.enable_file_download:
            logger.debug("WecomChannel 檔案下載功能已禁用")
            # 仍然處理文字部分
            await self._handle_incoming_message(frame)
            return

        if not self._file_service:
            logger.warning("WecomChannel 檔案服務未初始化")
            await self._handle_incoming_message(frame)
            return

        body = frame.get("body", {})
        mixed_data = body.get("mixed", {})
        msg_items = mixed_data.get("msgitem", [])

        if not msg_items:
            await self._handle_incoming_message(frame)
            return

        # 提取文字和圖片
        text_parts = []
        file_infos = []

        chatid, req_id, _ = self._extract_frame_info(frame)
        message_id = req_id or f"mixed_{int(time.time() * 1000)}"

        for idx, item in enumerate(msg_items):
            item_type = item.get("msgtype", "")
            
            if item_type == "text":
                text_content = item.get("text", {}).get("content", "")
                if text_content:
                    text_parts.append(text_content)
            
            elif item_type == "image":
                image_data = item.get("image", {})
                url = image_data.get("url", "")
                aes_key = image_data.get("aeskey", "")
                
                if url and aes_key:
                    file_info = await self._file_service.download_file(
                        url=url,
                        aes_key=aes_key,
                        message_id=f"{message_id}_{idx}",
                        file_category="images",
                    )
                    if file_info:
                        file_infos.append(file_info)

        # 合併文字
        content = " ".join(text_parts) if text_parts else "[圖文混排]"
        
        # 構建訊息併傳送
        await self._send_file_message_to_handler(frame, content, file_infos if file_infos else None)

    async def _send_file_message_to_handler(
        self, frame: dict, content: str, files: list[dict] | None
    ) -> None:
        """將檔案訊息傳送到訊息處理器"""
        chatid, req_id, _ = self._extract_frame_info(frame)
        
        if not chatid:
            logger.warning("WecomChannel 無法從 frame 提取 chatid，跳過檔案訊息")
            return

        # 許可權檢查
        if not self.is_allowed(chatid):
            logger.warning("WecomChannel 傳送者 %s 未被允許", chatid)
            return

        logger.info("WecomChannel 收到檔案訊息: chatid=%s content=%s", chatid, content[:50])

        req_id_final = req_id or f"wecom_{int(time.time() * 1000)}"

        # 構建訊息
        params = {"content": content, "query": content}
        if files:
            params["files"] = files

        msg = Message(
            id=req_id_final,
            type="req",
            channel_id=self.name,
            session_id=chatid,
            params=params,
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            is_stream=True,
            chat_id=chatid,
            metadata={
                "wecom_chat_id": chatid,
                "wecom_req_id": req_id_final,
            },
        )

        if self._message_callback:
            self._message_callback(msg)
        else:
            await self.bus.route_user_message(msg)

    # ==================== 檔案傳送方法 ====================

    async def _send_file_message(self, msg: Message) -> None:
        """傳送檔案訊息"""
        if not self._file_service or not self.config.send_file_allowed:
            logger.warning("WecomChannel 檔案傳送功能未啟用")
            return

        if not self._ws_client or not getattr(self._ws_client, "is_connected", False):
            logger.warning("WecomChannel 未連線，跳過檔案傳送")
            return

        payload = msg.payload if isinstance(msg.payload, dict) else {}
        files = payload.get("files", [])
        if not files:
            return

        # 提取 chatid
        metadata = msg.metadata or {}
        chatid = metadata.get("wecom_chat_id") or ""
        if not chatid:
            chatid = getattr(msg, "session_id", None) or msg.id or ""
        
        if not chatid:
            logger.warning("WecomChannel 檔案傳送: 未找到接收者")
            return

        # 獲取當前 request_id 用於去重
        request_id = getattr(msg, "id", "") or ""
        if request_id not in self._sent_file_paths_by_req:
            self._sent_file_paths_by_req[request_id] = set()

        for file_info in files:
            file_path = file_info if isinstance(file_info, str) else file_info.get("path", "")
            if not file_path or not os.path.isfile(file_path):
                logger.warning(f"WecomChannel 檔案傳送: 檔案不存在 {file_path}")
                continue

            # 檢查是否已傳送
            if file_path in self._sent_file_paths_by_req[request_id]:
                continue

            # 確定媒體型別
            media_type = self._file_service.get_media_type_for_file(file_path)

            try:
                # 上傳檔案
                media_id = await self._file_service.upload_file(file_path, media_type)
                if not media_id:
                    logger.error(f"WecomChannel 檔案上傳失敗: {file_path}")
                    continue

                # 傳送媒體訊息
                await self._ws_client.send_media_message(
                    chatid=chatid,
                    media_type=media_type,
                    media_id=media_id,
                )

                # 記錄已傳送
                self._sent_file_paths_by_req[request_id].add(file_path)
                logger.info(f"WecomChannel 檔案傳送成功: {file_path} -> {chatid}")

            except Exception as e:
                logger.error(f"WecomChannel 檔案傳送失敗: {file_path}, error: {e}")

        # 清理過期的去重記錄
        if len(self._sent_file_paths_by_req) > 100:
            # 刪除最早的 50 個
            keys_to_remove = list(self._sent_file_paths_by_req.keys())[:50]
            for key in keys_to_remove:
                del self._sent_file_paths_by_req[key]

    def get_metadata(self) -> ChannelMetadata:
        return ChannelMetadata(
            channel_id=self.channel_id,
            source="websocket",
            extra={
                "ws_url": self.config.ws_url,
                "bot_id": self.config.bot_id[:8] + "..." if len(self.config.bot_id) > 8 else self.config.bot_id,
            },
        )
