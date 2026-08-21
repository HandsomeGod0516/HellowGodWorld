# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""IM 輸入管道，負責處理收到的 IM 訊息，包括解析、驗證、路由等."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

from jiuwenclaw.common.config import _parse_custom_headers
from jiuwenclaw.gateway.routing.interaction_context import PendingInteraction
from jiuwenclaw.common.schema.message import Message, ReqMethod
from jiuwenclaw.gateway.message_handler.command_parser.slash_command import CONTROL_MESSAGE_TEXTS
from jiuwenclaw.common.utils import get_deepagent_user_md_path, logger
SYSTEM_PROMPT_TEMPLATE = """
你是{principal_name}的數字分身，活躍在即時通訊群聊中。當群裡有其他使用者傳送與{principal_name}相關的訊息時，你的任務是改寫這條訊息，使其更清晰、更完整，以便後續幫助{principal_name}生成恰當的回覆。

## 訊息格式說明

收到的訊息格式為：`[時間戳] [傳送者]: 訊息內容`
例如：`[2026-03-20 14:44:34] [某人]: {bot_mention_hint} @{principal_name} 111`

## 判斷是否需要處理（按優先順序順序）

### 必須回覆的情況（不能輸出[無需處理]）：

1. **訊息中@了機器人**：如果訊息內容中出現機器人 mention（例如 {bot_mention_hint}），無論傳送者是誰，都必須回覆
2. **其他人@了使用者本人**：如果傳送者不是{principal_name}本人，且訊息中@了{principal_name}，必須回覆

### 由模型判斷的情況：

如果以上條件都不滿足，則根據以下標準判斷：
- 當前訊息中提到了{principal_name}的名字
- 當前訊息是對{principal_name}之前發言的回覆或延續
- 當前訊息是群聊中需要{principal_name}參與討論的話題
- 當前訊息是否與群裡其他人所發歷史訊息有關

如果判斷為無關，輸出：[無需處理]

## 改寫原則

1. 明確傳送者意圖：對方想表達什麼？是提問、請求、討論還是閒聊？
2. 補充上下文：如果訊息涉及歷史對話（如"那個檔案"、"剛才說的"），補充具體資訊
3. 明確回覆期望：對方期望什麼樣的回應？是解答問題、確認資訊、還是參與討論？
4. 保留原意：不要改變訊息的核心意圖

## 輸出要求

直接輸出改寫後的訊息，不要新增任何解釋或額外內容。
""".strip()


@dataclass
class IMHistoryMessage:
    user_id: str
    user_name: str
    content: str
    timestamp_ms: int


class IMPlatformAdapter(Protocol):
    """統一的平臺介面卡介面，同時服務入站和出站管線。

    每個 IM 平臺（飛書 / 企微）實現一個介面卡，註冊後供
    IMInboundPipeline 和 IMOutboundPipeline 共享使用。
    """

    channel_id: str

    # --- 入站能力 ---

    def get_principal_user_id(self) -> str:
        ...

    def get_principal_display_name(self) -> str:
        ...

    def resolve_user_display_name(self, user_id: str) -> str:
        ...

    def get_bot_mention_tokens(self) -> list[str]:
        ...

    def load_recent_messages(
        self, thread_id: str, limit: int = 500
    ) -> list[IMHistoryMessage]:
        ...

    def build_relevance_metadata(
        self,
        metadata: dict[str, Any],
        *,
        sender_user_id: str,
        relevant: bool,
    ) -> dict[str, Any]:
        ...

    # --- 出站能力 ---

    @property
    def platform_name(self) -> str:
        """平臺顯示名，用於 LLM prompt（如 "飛書"、"企業微信"）。"""
        ...

    @property
    def reply_user_id_key(self) -> str:
        """metadata 中設定回覆目標使用者 ID 的 key。

        飛書: "reply_feishu_open_id"
        企微: "reply_wecom_user_id"
        """
        ...

    @property
    def use_keyword_override(self) -> bool:
        """LLM 判斷為 CHAT 但關鍵詞命中時，是否覆蓋為 DM。"""
        ...

    def get_candidate_user_id(self, metadata: dict[str, Any]) -> str:
        """從 metadata 中提取候選私發目標使用者 ID；無候選返回空字串。"""
        ...


@dataclass
class InboundProcessResult:
    should_forward: bool = True
    rewritten_content: str | None = None
    metadata_patch: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


class IMConversationProcessor:
    def __init__(
        self,
        *,
        user_profile_path: Path | None = None,
        model_name: str | None = None,
    ) -> None:
        self._user_profile_path = (
            user_profile_path
            if user_profile_path is not None
            else get_deepagent_user_md_path()
        )
        self._model_name, self._model_client_raw = self._load_model_config(model_name)
        self._llm: Model | None = None

    @staticmethod
    def _load_model_config(model_name_override: str | None = None) -> tuple[str, dict]:
        """與 react agent 一致的模型配置讀取：config.yaml → 環境變數 → 預設值。"""
        try:
            from jiuwenclaw.common.config import get_config
            cfg = get_config() or {}
        except Exception:
            cfg = {}
        react = cfg.get("react") or {}
        mcc = react.get("model_client_config") or {}
        name = (
            (model_name_override or "").strip()
            or react.get("model_name", "")
            or os.getenv("MODEL_NAME", "").strip()
            or "gpt-4o"
        )
        return name, mcc

    async def process(
        self,
        msg: Message,
        adapter: IMPlatformAdapter,
        *,
        pending_context: str | None = None,
    ) -> InboundProcessResult:
        if msg.req_method != ReqMethod.CHAT_SEND:
            return InboundProcessResult(reason="non-chat-send")

        text = self._extract_text(msg)
        if not text:
            return InboundProcessResult(reason="empty-content")
        if text.strip() in CONTROL_MESSAGE_TEXTS:
            return InboundProcessResult(reason="control-message")

        metadata = dict(msg.metadata or {})
        chat_type = str(
            metadata.get("im_chat_type") or metadata.get("chat_type") or ""
        ).strip().lower()
        if chat_type != "group":
            return InboundProcessResult(reason="non-group-chat")

        sender_user_id = str(
            metadata.get("im_sender_user_id")
            or metadata.get("open_id")
            or metadata.get("sender_id")
            or ""
        ).strip()
        thread_id = str(
            metadata.get("im_thread_id")
            or metadata.get("feishu_chat_id")
            or msg.session_id
            or ""
        ).strip()

        principal_user_id = adapter.get_principal_user_id().strip()
        principal_name = adapter.get_principal_display_name().strip() or "使用者"
        if not principal_user_id:
            return InboundProcessResult(reason="missing-principal-user")

        if self._should_always_reply(
            text=text,
            metadata=metadata,
            sender_user_id=sender_user_id,
            principal_user_id=principal_user_id,
            principal_name=principal_name,
            bot_mentions=adapter.get_bot_mention_tokens(),
        ):
            return InboundProcessResult(
                metadata_patch=adapter.build_relevance_metadata(
                    metadata,
                    sender_user_id=sender_user_id,
                    relevant=True,
                ),
                reason="always-reply",
            )

        prompt = self._build_prompt(
            thread_id=thread_id,
            sender_user_id=sender_user_id,
            text=text,
            timestamp_ms=self._resolve_timestamp_ms(msg.timestamp, metadata),
            principal_name=principal_name,
            adapter=adapter,
            pending_context=pending_context,
        )
        rewritten_content = await self._rewrite_query(prompt, principal_name, adapter)
        if not rewritten_content:
            return InboundProcessResult(reason="rewrite-failed")

        normalized_content = rewritten_content.strip()
        if normalized_content == "[無需處理]":
            if self._has_image_context(metadata):
                return InboundProcessResult(reason="image-fallback")
            return InboundProcessResult(should_forward=False, reason="irrelevant")

        return InboundProcessResult(
            rewritten_content=normalized_content,
            metadata_patch=adapter.build_relevance_metadata(
                metadata,
                sender_user_id=sender_user_id,
                relevant=True,
            ),
            reason="rewritten",
        )

    @staticmethod
    def _extract_text(msg: Message) -> str:
        if not isinstance(msg.params, dict):
            return ""
        query = msg.params.get("query")
        if isinstance(query, str) and query.strip():
            return query
        content = msg.params.get("content")
        if isinstance(content, str):
            return content
        return ""

    @staticmethod
    def _resolve_timestamp_ms(timestamp: float, metadata: dict[str, Any]) -> int:
        raw_ts = metadata.get("timestamp_ms")
        if isinstance(raw_ts, int):
            return raw_ts
        if isinstance(raw_ts, str) and raw_ts.strip().isdigit():
            return int(raw_ts.strip())
        return int(timestamp * 1000)

    @staticmethod
    def _has_image_context(metadata: dict[str, Any]) -> bool:
        if str(metadata.get("msg_type") or "").strip() == "image":
            return True
        merged_types = metadata.get("merged_msg_types") or []
        if isinstance(merged_types, list):
            return any(str(item).strip() == "image" for item in merged_types)
        return False

    @staticmethod
    def _should_always_reply(
        *,
        text: str,
        metadata: dict[str, Any],
        sender_user_id: str,
        principal_user_id: str,
        principal_name: str,
        bot_mentions: list[str],
    ) -> bool:
        mentioned_user_ids = metadata.get("im_mentioned_user_ids") or metadata.get(
            "mentioned_open_ids"
        ) or []
        if isinstance(mentioned_user_ids, list):
            if principal_user_id in [str(item).strip() for item in mentioned_user_ids]:
                return True

        for token in bot_mentions:
            if token and token in text:
                return True
            # 同時檢查機器人名稱（從配置讀取）是否在文字中
            try:
                from jiuwenclaw.common.config import get_config
                cfg = get_config()
                bot_name = str(
                    cfg.get("bot_name") or
                    cfg.get("channels", {}).get("wecom", {}).get("bot_name") or
                    ""
                ).strip()
                if bot_name and bot_name in text:
                    return True
            except Exception as e:
                logger.debug(f"Failed to get bot_name from config: {e}")

        is_not_me = sender_user_id and sender_user_id != principal_user_id
        if is_not_me and principal_name and f"@{principal_name}" in text:
            return True
        return False

    def _build_prompt(
        self,
        *,
        thread_id: str,
        sender_user_id: str,
        text: str,
        timestamp_ms: int,
        principal_name: str,
        adapter: IMPlatformAdapter,
        pending_context: str | None = None,
    ) -> str:
        prompt_parts: list[str] = []
        prompt_parts.append("=== 群聊歷史訊息 ===")
        history = adapter.load_recent_messages(thread_id, limit=500)
        if history:
            prompt_parts.append(f"最近 {len(history)} 條訊息：\n")
            for msg in history:
                dt = datetime.fromtimestamp(int(msg.timestamp_ms) / 1000)
                prompt_parts.append(
                    f"[{dt.strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"[{msg.user_name or '未知使用者'}]: {msg.content}"
                )
            prompt_parts.append("")
        else:
            prompt_parts.append("暫無歷史訊息\n")

        prompt_parts.append("=== 使用者畫像 ===")
        user_profile = self._load_user_profile()
        prompt_parts.append(user_profile if user_profile else "暫無使用者畫像資訊")
        prompt_parts.append("")

        if pending_context:
            prompt_parts.append("=== 待回答的追問 ===")
            prompt_parts.append(pending_context)
            prompt_parts.append("")

        prompt_parts.append("=== 當前訊息 ===")
        sender_name = adapter.resolve_user_display_name(sender_user_id) or "未知使用者"
        dt = datetime.fromtimestamp(int(timestamp_ms) / 1000)
        prompt_parts.append(
            f"[{dt.strftime('%Y-%m-%d %H:%M:%S')}] [{sender_name}]: {text}"
        )
        prompt_parts.append("")

        return "\n".join(prompt_parts)

    def _load_user_profile(self) -> str:
        try:
            if not self._user_profile_path.exists():
                return ""
            return self._user_profile_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("[IMConversationProcessor] 讀取 USER.md 失敗: %s", exc)
            return ""

    def _ensure_llm(self) -> Model | None:
        if self._llm is not None:
            return self._llm
        try:
            model_cfg = ModelRequestConfig(
                model=self._model_name,
                temperature=0.2,
                top_p=0.7,
            )
            mcc = self._model_client_raw
            api_key = (mcc.get("api_key") or os.getenv("API_KEY") or "").strip()
            api_base = (mcc.get("api_base") or os.getenv("API_BASE") or "").strip()
            if api_base.endswith("/chat/completions"):
                api_base = api_base.rsplit("/chat/completions", 1)[0]
            client_provider = mcc.get("client_provider") or os.getenv("MODEL_PROVIDER", "OpenAI")
            custom_headers = _parse_custom_headers(mcc.get("custom_headers") or os.getenv("CUSTOM_HEADERS"))
            model_client_cfg = ModelClientConfig(
                client_id="im_conversation_processor_client",
                client_provider=client_provider,
                api_key=api_key,
                api_base=api_base,
                verify_ssl=False,
                timeout=180.0,
                custom_headers=custom_headers,
            )
            self._llm = Model(
                model_config=model_cfg,
                model_client_config=model_client_cfg,
            )
        except Exception as exc:
            logger.warning(
                "[IMConversationProcessor] 初始化 LLM 失敗，將回退原始訊息: %s",
                exc,
            )
            self._llm = None
        return self._llm

    async def _rewrite_query(
        self,
        prompt: str,
        principal_name: str,
        adapter: IMPlatformAdapter,
    ) -> str | None:
        llm = self._ensure_llm()
        if llm is None:
            return None

        bot_mentions = adapter.get_bot_mention_tokens()
        bot_mention_hint = " / ".join(bot_mentions) if bot_mentions else "@機器人"
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            principal_name=principal_name,
            bot_mention_hint=bot_mention_hint,
        )
        try:
            response = await llm.invoke(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            )
            if response and isinstance(response.content, str):
                return response.content.strip() or None
            
        except Exception as exc:
            logger.warning(
                "[IMConversationProcessor] 呼叫 LLM 改寫失敗，將回退原始訊息: %s",
                exc,
            )
        return None


class IMInboundPipeline:
    def __init__(
        self,
        *,
        processor: IMConversationProcessor | None = None,
        adapters: dict[str, IMPlatformAdapter] | None = None,
    ) -> None:
        self._processor = processor or IMConversationProcessor()
        self._adapters: dict[str, IMPlatformAdapter] = dict(adapters or {})

    def register_adapter(self, channel_id: str, adapter: IMPlatformAdapter) -> None:
        self._adapters[channel_id] = adapter

    def unregister_adapter(self, channel_id: str) -> None:
        self._adapters.pop(channel_id, None)

    async def apply(self, msg: Message) -> bool:
        if not msg.group_digital_avatar:
            return True

        adapter = self._adapters.get(msg.channel_id)
        if adapter is None:
            return True

        metadata = dict(msg.metadata or {})
        if bool(metadata.get("is_resume_message")):
            dm_pending_id = str(metadata.get("dm_pending_interaction_id") or "").strip()
            if dm_pending_id:
                pi = PendingInteraction.load(dm_pending_id)
                if pi is not None:
                    answer = ""
                    if isinstance(msg.params, dict):
                        answer = str(
                            msg.params.get("query") or msg.params.get("content") or ""
                        ).strip()
                    resume_content = pi.build_resume_content(answer)
                    logger.info(
                        "[IMInboundPipeline][DEBUG] dm_resume: id=%s answer=%s",
                        dm_pending_id,
                        answer[:80],
                    )
                    logger.info(
                        "[IMInboundPipeline][DEBUG] resume_content=\n%s",
                        resume_content,
                    )
                    if not isinstance(msg.params, dict):
                        msg.params = {}
                    msg.params["query"] = resume_content
                    if "content" in msg.params:
                        msg.params["content"] = resume_content
                    merged = dict(msg.metadata or {})
                    merged["interaction_context"] = resume_content
                    msg.metadata = merged
                    pi.status = "completed"
                    pi.save()
                    pi.remove()
            logger.info(
                "[IMInboundPipeline] resume 訊息直接放行: channel=%s id=%s",
                msg.channel_id, msg.id,
            )
            return True

        original_query = ""
        if isinstance(msg.params, dict):
            query = msg.params.get("query")
            content = msg.params.get("content")
            if isinstance(query, str) and query.strip():
                original_query = query
            elif isinstance(content, str):
                original_query = content

        pending_context = self._peek_pending(msg)
        if pending_context:
            logger.info(
                "[IMInboundPipeline][DEBUG] pending_context=\n%s",
                pending_context,
            )

        result = await self._processor.process(msg, adapter, pending_context=pending_context)

        if result.metadata_patch:
            merged_metadata = dict(msg.metadata or {})
            merged_metadata.update(result.metadata_patch)
            msg.metadata = merged_metadata

        if result.rewritten_content is not None:
            if not isinstance(msg.params, dict):
                msg.params = {}
            msg.params["query"] = result.rewritten_content
            if "content" in msg.params:
                msg.params["content"] = result.rewritten_content

        if original_query:
            merged = dict(msg.metadata or {})
            merged["avatar_original_query"] = original_query
            msg.metadata = merged

        if pending_context and result.should_forward:
            merged = dict(msg.metadata or {})
            merged["interaction_context"] = pending_context
            sender_user_id = str(
                metadata.get("im_sender_user_id")
                or metadata.get("open_id")
                or metadata.get("sender_id")
                or ""
            ).strip()
            if sender_user_id:
                merged["interaction_answered_user_id"] = sender_user_id
            msg.metadata = merged

            session_id = str(msg.session_id or "").strip()
            if session_id and sender_user_id:
                pi = PendingInteraction.find_group_pending(session_id, sender_user_id)
                logger.info(
                    "[IMInboundPipeline][DEBUG] find_group_pending=%s",
                    pi,
                )
                if pi is not None:
                    answer = result.rewritten_content or original_query
                    resume_content = pi.build_resume_content(answer)
                    logger.info(
                        "[IMInboundPipeline][DEBUG] resume_content=\n%s",
                        resume_content,
                    )
                    if not isinstance(msg.params, dict):
                        msg.params = {}
                    msg.params["query"] = resume_content
                    if "content" in msg.params:
                        msg.params["content"] = resume_content

        if result.should_forward and result.reason != "non-group-chat":
            principal_name = adapter.get_principal_display_name().strip()
            avatar_detail: dict[str, Any] = {
                "avatar_mode": True,
                "avatar_channel_type": adapter.platform_name,
            }
            if principal_name:
                avatar_detail["avatar_principal_name"] = principal_name
            principal_id = adapter.get_principal_user_id().strip()
            if principal_id:
                avatar_detail["principal_user_id"] = principal_id
            merged = dict(msg.metadata or {})
            merged.update(avatar_detail)
            msg.metadata = merged

        rewritten_preview = (result.rewritten_content or "").replace("\n", "\\n")[:120]
        original_preview = (original_query or "").replace("\n", "\\n")[:120]
        metadata_keys = sorted(list((result.metadata_patch or {}).keys()))

        logger.info(
            "[IMInboundPipeline] channel=%s request=%s should_forward=%s reason=%s rewritten=%s "
            "original_preview=%r rewritten_preview=%r metadata_keys=%s pending=%s",
            msg.channel_id,
            msg.id,
            result.should_forward,
            result.reason,
            result.rewritten_content is not None,
            original_preview,
            rewritten_preview,
            metadata_keys,
            bool(pending_context),
        )
        return result.should_forward

    @staticmethod
    def _peek_pending(msg: Message) -> str | None:
        metadata = dict(msg.metadata or {})
        session_id = str(msg.session_id or "").strip()
        sender_user_id = str(
            metadata.get("im_sender_user_id")
            or metadata.get("open_id")
            or metadata.get("sender_id")
            or ""
        ).strip()
        if not session_id or not sender_user_id:
            return None
        pi = PendingInteraction.find_group_pending(session_id, sender_user_id)
        if pi is None:
            return None
        return (
            f"【追問上下文】你之前在處理以下任務時向 {pi.target_user_name or '使用者'} 追問了資訊：\n"
            f"- 原始請求：{pi.origin_content}\n"
            f"- 你的追問：{pi.question}\n"
            f"現在 {pi.target_user_name or '使用者'} 已回覆，請綜合「原始請求」和「使用者回覆」中的所有資訊繼續完成任務，"
            f"原始請求中已明確提供的資訊（如時間、地點等）直接使用即可，不要再次追問。"
            f"不要與群聊歷史中的其他任務混淆。"
        )