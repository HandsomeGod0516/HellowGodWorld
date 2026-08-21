# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""ChannelManager - Channel 生命週期管理抽象與實現."""

from __future__ import annotations

import logging
import asyncio
from abc import ABC
from typing import TYPE_CHECKING, Any, Awaitable, Callable

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from jiuwenclaw.gateway.channel_manager.base import BaseChannel
    from jiuwenclaw.gateway.message_handler import MessageHandler
    from jiuwenclaw.common.schema.message import Message



class ChannelManager(ABC):
    """
    負責：
    1. Channel 的註冊、登出與查詢
    2. 將各 Channel 收到的訊息/事件統一透過 MessageHandler.handle_message 轉發
    3. 執行出隊派發迴圈：從 MessageHandler 取出 AgentServer 響應並投遞到對應 Channel
    """

    def __init__(
        self,
        message_handler: "MessageHandler",
        config: dict[str, Any] | None = None,
        on_config_updated: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._message_handler = message_handler
        self._channels: dict[str, "BaseChannel"] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._running = False
        # 統一管理 Channel 相關配置（例如 FeishuChannel / XiaoyiChannel 等）。
        # 預設僅在閘道器側使用；其他簡單用法可以忽略該欄位。
        self._config: dict[str, Any] = dict(config or {})
        self._on_config_updated = on_config_updated
        # 下一次 on_config_updated 時強制重啟的 channel_id（例如微信解綁：YAML 中 bot_token 本就為空時配置 dict 對比不會變，但記憶體裡仍有舊憑據）
        self._pending_channel_restart: set[str] = set()

    def mark_channel_restart_pending(self, channel_id: str) -> None:
        """請求在下次 set_conf / set_config 觸發配置應用時，無論配置快照是否變化都重啟該 channel。"""
        if channel_id:
            self._pending_channel_restart.add(channel_id)

    def pop_channel_restart_pending(self) -> set[str]:
        """取出並重置待強制重啟集合（由閘道器 _apply_channel_config 呼叫）。"""
        out = set(self._pending_channel_restart)
        self._pending_channel_restart.clear()
        return out

    def _on_channel_message(self, msg: "Message") -> None:
        """Channel 同步 on_message 回撥：交給 MessageHandler 處理（入隊並最終發往 AgentServer）."""
        logger.info(
            "[ChannelManager] Channel 訊息 -> MessageHandler: id=%s channel_id=%s",
            msg.id, msg.channel_id,
        )
        if not self._channels.get(msg.channel_id, None):
            logger.info(f"[ChannelManager] Channel: {msg.channel_id} closed, cancel this user message.")
            return

        self._message_handler.handle_message(msg)

    def register_channel(self, channel: "BaseChannel") -> None:
        """註冊 Channel，併為其註冊「收到訊息時轉發給 MessageHandler」的回撥."""
        cid = channel.channel_id
        self._channels[cid] = channel
        channel.on_message(self._on_channel_message)
        logger.info("[ChannelManager] 已註冊 Channel: channel_id=%s, 當前共 %d 個", cid, len(self._channels))

    def register_channel_with_inbound(
        self,
        channel: "BaseChannel",
        on_message: Callable[["Message"], Any],
    ) -> None:
        """登記 Channel 並使用自定義入站回撥（不替換為預設 _on_channel_message）。"""
        self._channels[channel.channel_id] = channel
        channel.on_message(on_message)

    def register_external_channel(self, channel_id: str, channel: Any) -> None:
        """登記一個已由外部完成入站裝配的 channel 例項。"""
        self._channels[channel_id] = channel

    def deliver_to_message_handler(self, msg: "Message") -> None:
        """將訊息交給 MessageHandler（供自定義入站路徑使用）。"""
        self._message_handler.handle_message(msg)

    def unregister_channel(self, channel_id: str) -> None:
        """登出指定 Channel."""
        self._channels.pop(channel_id, None)
        logger.info("[ChannelManager] 已登出 Channel: channel_id=%s", channel_id)

    def get_channel(self, channel_id: str) -> "BaseChannel | None":
        """根據 channel_id 獲取 Channel."""
        return self._channels.get(channel_id)

    @property
    def enabled_channels(self) -> list[str]:
        """當前已註冊的 Channel 標識列表."""
        return list(self._channels.keys())

    # ----- 配置管理介面 -----

    def get_conf(self, channel_id: str) -> dict[str, Any]:
        """返回指定 channel_id 的配置淺複製；不存在則返回空 dict."""
        conf = self._config.get(channel_id)
        return dict(conf) if isinstance(conf, dict) else {}

    async def set_conf(self, channel_id: str, new_conf: dict[str, Any]) -> None:
        """更新指定 channel_id 的配置，並在必要時觸發重新例項化回撥.

        內部仍維護完整的 Channel 配置字典，並將其整體傳給 on_config_updated，
        以相容現有回撥實現（如根據 channels.feishu 重建 FeishuChannel）。
        """
        merged = dict(self._config)
        merged[channel_id] = dict(new_conf or {})
        self._config = merged
        cb = self._on_config_updated
        if cb is not None:
            await cb(self._config)

    async def set_config(self, new_conf: dict[str, Any]) -> None:
        """相容保留：整體替換配置的舊介面（不推薦新呼叫方使用）."""
        self._config = dict(new_conf or {})
        cb = self._on_config_updated
        if cb is not None:
            await cb(self._config)

    def set_config_callback(
        self,
        callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """設定在配置更新時觸發的回撥，用於由外部實現具體的 Channel 重新例項化邏輯."""
        self._on_config_updated = callback

    async def _dispatch_robot_messages(self) -> None:
        """出隊派發迴圈：從 MessageHandler 消費 robot_messages，按 channel_id 投遞到對應 Channel."""
        # 僅當 MessageHandler 提供 consume_robot_messages 時才能派發
        consume = getattr(self._message_handler, "consume_robot_messages", None)
        if not callable(consume):
            logger.warning("MessageHandler has no consume_robot_messages, robot_messages dispatch skipped")
            return
        while self._running:
            try:
                msg = await consume(timeout=1.0)
                if msg is None:
                    continue
                logger.info(
                    "[ChannelManager] 從 robot_messages 取出，準備派發: id=%s channel_id=%s type=%s",
                    msg.id, msg.channel_id, msg.type,
                )
                channel = self._channels.get(msg.channel_id)
                if channel:
                    try:
                        await channel.send(msg)
                        logger.info(
                            "[ChannelManager] 已派發到 Channel: channel_id=%s id=%s",
                            msg.channel_id, msg.id,
                        )
                    except Exception as e:
                        logger.error("send to channel %s: %s", msg.channel_id, e, exc_info=True)
                else:
                    logger.warning(
                        "[ChannelManager] 未找到 Channel，丟棄 robot_messages: channel_id=%s id=%s",
                        msg.channel_id, msg.id,
                    )
            except asyncio.CancelledError:
                break

    async def start_dispatch(self) -> None:
        """啟動出隊派發任務（消費 MessageHandler.robot_messages 併傳送到各 Channel）."""
        if self._dispatch_task is not None:
            return
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_robot_messages())
        logger.info("[ChannelManager] 出隊派發迴圈已啟動 (robot_messages -> Channel.send)")

    async def stop_dispatch(self) -> None:
        """停止出隊派發任務."""
        self._running = False
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None
        logger.info("[ChannelManager] 出隊派發迴圈已停止")
