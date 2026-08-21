# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from jiuwenclaw.common.schema.message import Message


logger = logging.getLogger(__name__)
if TYPE_CHECKING:
    pass


class ChannelType(str, Enum):
    """Channel 型別列舉."""
    ACP = "acp"
    WEB = "web"
    FEISHU = "feishu"
    XIAOYI = "xiaoyi"
    DINGTALK = "dingtalk"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WHATSAPP = "whatsapp"
    WECOM = "wecom"
    WECHAT = "wechat"
    CLI = "tui"


@dataclass
class ChannelMetadata:
    """Channel 後設資料."""

    channel_id: str
    source: str
    user_id: str | None = None
    extra: dict[str, Any] | None = None


class RobotMessageRouter:
    """管理整個系統的入站（從通道到機器人）和出站（從機器人到通道）訊息佇列，並提供出站訊息的訂閱/分發機制。"""
    def __init__(self):
        self._user_messages: asyncio.Queue[Message] = asyncio.Queue()
        self._robot_messages: asyncio.Queue[Message] = asyncio.Queue()
        self._channel_subscriptions: dict[str, list[Callable[[Message], Awaitable[None]]]] = {}
        self._is_active = False

    async def route_user_message(self, msg: Message) -> None:
        """將接收到的訊息放入user_messages佇列，等待機器人處理。"""
        await self._user_messages.put(msg)

    async def wait_for_user_message(self) -> Message:
        """阻塞地從user_messages佇列中取出一條訊息進行處理。"""
        return await self._user_messages.get()

    async def queue_robot_message(self, msg: Message) -> None:
        """將生成的回覆訊息放入robot_messages佇列。"""
        await self._robot_messages.put(msg)

    async def wait_for_robot_message(self) -> Message:
        """阻塞地從robot_messages佇列取訊息，主要用於除錯或直接消費（但框架通常使用訂閱分發機制）。"""
        return await self._robot_messages.get()

    def register_channel_subscription(
        self,
        channel: str,
        callback: Callable[[Message], Awaitable[None]]
    ) -> None:
        """允許通道（或其他元件）註冊一個非同步回撥函式，專門接收目標為特定通道ID的出站訊息。"""
        if channel not in self._channel_subscriptions:
            self._channel_subscriptions[channel] = []
        self._channel_subscriptions[channel].append(callback)

    async def dispatch_robot_messages(self) -> None:
        """
        持續監聽robot_messages佇列，將每條訊息分發給對應通道的訂閱回撥。
        """
        self._is_active = True
        while self._is_active:
            try:
                msg = await asyncio.wait_for(self._robot_messages.get(), timeout=1.0)
                subscribers = self._channel_subscriptions.get(msg.channel_id, [])
                for callback in subscribers:
                    try:
                        await callback(msg)
                    except Exception as e:
                        logger.error(f"Error dispatching to {msg.channel_id}: {e}")
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Stop the dispatcher loop."""
        self._is_active = False

    @property
    def pending_incoming_count(self) -> int:
        """待處理的入站訊息數量"""
        return self._user_messages.qsize()

    @property
    def pending_outgoing_count(self) -> int:
        """待傳送的出站訊息數量"""
        return self._robot_messages.qsize()


class BaseChannel(ABC):
    """
    Channel實現的抽象基類。

    每個Channel都應該實現這個介面
    以整合到奈米機器人訊息匯流排中。
    """

    name: str = "base"

    def __init__(self, config: Any, router: RobotMessageRouter):
        """
        初始化Channel
        """
        self.config = config
        self.bus = router
        self._running = False

    @abstractmethod
    async def start(self) -> None:
        """
        啟動Channel並開始監聽訊息

        一個長期執行的非同步任務，需要：
        1. 連線到聊天平臺
        2. 監聽傳入訊息
        3. 透過_handle_message()將訊息轉發到匯流排
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """停止Channel並清理資源"""
        pass

    @abstractmethod
    async def send(self, msg: Message) -> None:
        """
        透過Channel傳送訊息
        """
        pass

    def is_allowed(self, sender_id: str) -> bool:
        """
        檢查傳送者是否被允許使用此機器人
        """
        allow_list = getattr(self.config, "allow_from", [])

        # If no allow list, allow everyone
        if not allow_list:
            return True

        sender_str = str(sender_id)
        if sender_str in allow_list:
            return True
        if "|" in sender_str:
            for part in sender_str.split("|"):
                if part and part in allow_list:
                    return True
        return False

    async def _handle_message(
            self,
            chat_id: str,
            content: str,
            metadata: dict[str, Any] | None = None
    ) -> None:

        msg = Message(
            id=chat_id,
            type="req",
            channel_id=self.name,
            session_id=str(chat_id),
            params={'content': content},
            timestamp=time.time(),
            ok=True,
            metadata=metadata
        )

        await self.bus.route_user_message(msg)

    @property
    def is_running(self) -> bool:
        """Check if the channel_id is running."""
        return self._running

