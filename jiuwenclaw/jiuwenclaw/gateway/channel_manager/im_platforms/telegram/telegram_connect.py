# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""TelegramChannel - Telegram Bot 通道實現."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import logging

from jiuwenclaw.gateway.channel_manager.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.common.schema.message import Message, ReqMethod

logger = logging.getLogger(__name__)

try:
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        filters,
        ContextTypes,
    )

    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = None
    Application = None
    ContextTypes = None


@dataclass
class TelegramChannelConfig:
    """Telegram 通道配置."""

    enabled: bool = False
    bot_token: str = ""  # Telegram Bot Token from @BotFather
    allow_from: list[str] = field(default_factory=list)  # 允許的 Telegram user_id 列表
    parse_mode: str = "Markdown"  # 訊息解析模式: Markdown, HTML, None
    group_chat_mode: str = "mention"  # 群聊模式: all, mention, reply, off


class TelegramChannel(BaseChannel):
    """
    Telegram Bot 通道.

    使用 Telegram Bot API 接收和傳送訊息.
    需要:
    - 來自 @BotFather 的 Bot Token
    - 可選: 配置允許訪問的使用者白名單
    """

    name = "telegram"

    def __init__(self, config: TelegramChannelConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: TelegramChannelConfig = config
        self._application: Any = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_message_cb: Callable[[Message], Any] | None = None
        self._chat_sessions: dict[int, str] = {}  # chat_id -> session_id 對映

    @property
    def channel_id(self) -> str:
        """ChannelManager 按 channel_id 註冊與派發."""
        return self.name

    @property
    def clients(self) -> set[Any]:
        """相容 BaseChannel 介面."""
        return set()

    def on_message(self, callback: Callable[[Message], None]) -> None:
        """ChannelManager 註冊: 收到訊息時呼叫 callback."""
        self._on_message_cb = callback

    async def start(self) -> None:
        """啟動 Telegram Bot."""
        if not TELEGRAM_AVAILABLE:
            logger.error(
                "Telegram SDK not installed. Run: pip install python-telegram-bot"
            )
            return

        if not self.config.enabled:
            logger.warning("TelegramChannel 未啟用（enabled=False）")
            return

        if not self.config.bot_token:
            logger.error("Telegram bot_token not configured")
            return

        if self._running:
            logger.warning("TelegramChannel 已在執行")
            return

        self._running = True
        self._loop = asyncio.get_running_loop()

        try:
            # 建立 Telegram Application
            self._application = (
                Application.builder().token(self.config.bot_token).build()
            )

            # 註冊命令處理器
            self._application.add_handler(CommandHandler("start", self._start_command))
            self._application.add_handler(CommandHandler("help", self._help_command))

            # 註冊訊息處理器 (文字訊息)
            self._application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
            )

            # 初始化並啟動 bot
            await self._application.initialize()
            await self._application.start()

            # 在後臺執行 polling
            await self._application.updater.start_polling(
                allowed_updates=Update.ALL_TYPES, drop_pending_updates=True
            )

            logger.info("Telegram Bot 已啟動")

            # 持續執行直到停止
            while self._running:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error("Telegram Bot 啟動失敗: %s", e)
            self._running = False
            raise

    async def stop(self) -> None:
        """停止 Telegram Bot."""
        self._running = False

        if self._application:
            try:
                if self._application.updater.running:
                    await self._application.updater.stop()
                await self._application.stop()
                await self._application.shutdown()
            except Exception as e:
                logger.warning("Error stopping Telegram Bot: %s", e)

        logger.info("Telegram Bot 已停止")

    async def send(self, msg: Message) -> None:
        """透過 Telegram 傳送訊息."""
        if not self._application or not self._running:
            logger.warning("Telegram Bot not initialized or not running")
            return

        try:
            # 從 session_id 或 metadata 獲取 chat_id
            chat_id = self._get_chat_id_from_message(msg)
            if not chat_id:
                logger.warning("Telegram send: 無法確定 chat_id")
                return

            # 提取訊息內容
            content = self._extract_content(msg)
            if not content:
                logger.warning("Telegram send: content 為空，跳過傳送")
                return

            # 傳送訊息
            parse_mode = (
                self.config.parse_mode if self.config.parse_mode != "None" else None
            )

            try:
                await self._application.bot.send_message(
                    chat_id=chat_id, text=content, parse_mode=parse_mode
                )
            except Exception as send_error:
                # 僅在 parse_mode 非空且錯誤涉及解析時重試
                error_str = str(send_error)
                if parse_mode and (
                        "parse" in error_str.lower() or "entity" in error_str.lower()
                ):
                    logger.warning(
                        f"Telegram Markdown parse error, retrying without parse_mode: {send_error}"
                    )
                    await self._application.bot.send_message(
                        chat_id=chat_id, text=content, parse_mode=None
                    )
                else:
                    raise

            logger.debug("Telegram message sent to chat_id=%s", chat_id)

        except Exception as e:
            logger.error(f"Error sending Telegram message: {type(e).__name__}: {e}")

    def _get_chat_id_from_message(self, msg: Message) -> int | None:
        """從 Message 中提取 chat_id."""
        # 優先從 metadata 獲取
        if msg.metadata and "chat_id" in msg.metadata:
            return int(msg.metadata["chat_id"])

        # 從 session_id 解析 (格式: "telegram_{chat_id}")
        if msg.session_id and msg.session_id.startswith("telegram_"):
            try:
                return int(msg.session_id.split("_")[1])
            except (IndexError, ValueError):
                pass

        return None

    def _extract_content(self, msg: Message) -> str:
        """從 Message 中提取文字內容."""
        # Gateway/Agent 響應在 payload.content
        content = (
                (msg.params or {}).get("content")
                or (getattr(msg, "payload") or {}).get("content")
                or ""
        )

        # 處理字典格式
        if isinstance(content, dict):
            content = content.get("output", str(content))

        return str(content).strip()

    async def _start_command(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """處理 /start 命令."""
        if not update.effective_user or not update.effective_chat:
            return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        # 檢查許可權
        if not self.is_allowed(str(user_id)):
            await update.message.reply_text("抱歉，您沒有許可權使用此機器人。")
            return

        welcome_msg = (
            "歡迎使用 JiuWenClaw 機器人! 🤖\n\n"
            "您可以直接傳送訊息與我對話。\n"
            "使用 /help 檢視幫助資訊。"
        )
        await update.message.reply_text(welcome_msg)
        logger.info(f"Telegram /start from user_id={user_id} chat_id={chat_id}")

    async def _help_command(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """處理 /help 命令."""
        help_msg = (
            "JiuWenClaw 機器人幫助 📚\n\n"
            "命令:\n"
            "/start - 開始對話\n"
            "/help - 顯示幫助\n\n"
            "您可以直接傳送文字訊息與我對話。"
        )
        await update.message.reply_text(help_msg)

    async def _handle_message(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """處理接收到的訊息."""
        try:
            if (
                    not update.message
                    or not update.effective_user
                    or not update.effective_chat
            ):
                return

            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            message_id = update.message.message_id
            text = update.message.text or ""

            # 檢查許可權
            if not self.is_allowed(str(user_id)):
                logger.warning(f"Telegram message from unauthorized user: {user_id}")
                return

            # 檢查是否為群聊
            is_group_chat = update.effective_chat.type in ["group", "supergroup"]

            # 群聊模式檢查
            if is_group_chat:
                group_mode = self.config.group_chat_mode

                # off 模式: 不響應群聊訊息
                if group_mode == "off":
                    logger.debug(
                        "Telegram group chat mode is 'off', ignoring message from chat_id=%s",
                        chat_id
                    )
                    return

                # mention 模式: 只響應 @機器人 的訊息
                if group_mode == "mention":
                    bot_username = context.bot.username
                    if not bot_username:
                        logger.warning(
                            "Cannot check mentions: bot username not available"
                        )
                        return

                    # 檢查是否 @ 了機器人
                    mention_text = f"@{bot_username}"
                    if mention_text not in text:
                        logger.debug(
                            f"Telegram group chat mode is 'mention', message doesn't mention bot, ignoring"
                        )
                        return

                    # 移除 @mention 從文字中
                    text = text.replace(mention_text, "").strip()

                # reply 模式: 只響應回覆機器人的訊息
                elif group_mode == "reply":
                    if not update.message.reply_to_message:
                        logger.debug(
                            f"Telegram group chat mode is 'reply', message is not a reply, ignoring"
                        )
                        return

                    # 檢查是否回覆的是機器人的訊息
                    if update.message.reply_to_message.from_user.id != context.bot.id:
                        logger.debug(
                            f"Telegram group chat mode is 'reply', not replying to bot, ignoring"
                        )
                        return

                # all 模式: 響應所有訊息（預設行為）

            # 對原訊息回應一個表情，表示正在處理
            try:
                await update.message.set_reaction("👀")  # 使用眼睛表情表示"正在檢視"
            except Exception as e:
                logger.debug("Failed to set reaction: %s", e)

            # 生成或獲取 session_id
            session_id = self._chat_sessions.get(chat_id)
            if not session_id:
                session_id = f"telegram_{chat_id}"
                self._chat_sessions[chat_id] = session_id

            # 建立 Message 物件
            user_message = Message(
                id=str(message_id),
                type="req",
                channel_id=self.channel_id,
                session_id=session_id,
                params={"content": text, "query": text},
                timestamp=time.time(),
                ok=True,
                req_method=ReqMethod.CHAT_SEND,
                metadata={
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "message_id": message_id,
                    "username": update.effective_user.username,
                    "is_group_chat": is_group_chat,
                },
            )

            # 傳送到 Gateway 或 Router
            if self._on_message_cb:
                result = self._on_message_cb(user_message)
                if asyncio.iscoroutine(result):
                    await result
            else:
                await self.bus.route_user_message(user_message)

            logger.info(
                f"Telegram message received: "
                f"user_id={user_id} chat_id={chat_id} is_group={is_group_chat} text={text[:50]}"
            )

        except Exception as e:
            logger.error(f"Error processing Telegram message: {e}")

    def get_metadata(self) -> ChannelMetadata:
        """獲取 Channel 後設資料."""
        return ChannelMetadata(
            channel_id=self.channel_id,
            source="telegram",
            extra={
                "bot_token_configured": bool(
                    self.config.bot_token and self.config.bot_token.strip()
                ),
                "parse_mode": self.config.parse_mode,
            },
        )
