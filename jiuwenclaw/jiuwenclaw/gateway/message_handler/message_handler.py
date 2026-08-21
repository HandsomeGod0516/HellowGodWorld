# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""MessageHandler - 訊息處理抽象與雙佇列實現（入隊經 AgentServerClient 發往 AgentServer）."""

from __future__ import annotations

import logging
import asyncio
import os
import re
import secrets
import time
from abc import ABC
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict
from jiuwenclaw.gateway.channel_manager.base import ChannelType
from jiuwenclaw.common.e2a.constants import E2A_WIRE_INTERNAL_METADATA_KEYS
from jiuwenclaw.gateway.routing.session_map import SessionMap
from jiuwenclaw.gateway.message_handler.command_parser.slash_command import (
    ParsedControlAction,
    parse_channel_control_text,
)
from jiuwenclaw.extensions.hook_event import GatewayHookEvents
from jiuwenclaw.extensions.hooks_context import GatewayChatHookContext

logger = logging.getLogger(__name__)

_ACP_CHANNEL_ID = "acp"
_ACP_ORIGINAL_SESSION_ID_KEY = "acp_original_session_id"
_DEFAULT_INLINE_FILE_SIZE_LIMIT = 128 * 1024
_KNOWN_JIUWENCLAW_SESSION_PREFIXES = (
    "sess_",
    "tui_",
    "acp_",
    "cron_",
    "feishu_",
    "wechat_",
    "xiaoyi_",
    "dingtalk_",
    "wecom_",
    "telegram_",
    "discord_",
    "whatsapp_",
)



class ChannelMode(str, Enum):
    AGENT_PLAN = "agent.plan"
    AGENT_FAST = "agent.fast"
    CODE_PLAN = "code.plan"
    CODE_NORMAL = "code.normal"
    TEAM = "team"


@dataclass
class ChannelControlState:
    session_id: str | None = None
    mode: ChannelMode = ChannelMode.AGENT_PLAN


@dataclass
class NewSessionCancelParams:
    """\\new_session 時取消舊會話併發通知所需的具名引數（避免過長形參列表）。"""

    user_infos: dict[str, Any]
    channel_id: str
    reply_session_id: str | None
    new_sid: str
    old_sid: str | None


@dataclass
class ModeChangeCancelParams:
    """\\mode 切換時取消舊會話併發通知所需的具名引數。"""

    user_infos: dict[str, Any]
    channel_id: str
    reply_session_id: str | None
    old_sid: str | None
    new_mode_label: str


if TYPE_CHECKING:
    from jiuwenclaw.common.e2a.models import E2AEnvelope
    from jiuwenclaw.gateway.routing.agent_client import AgentServerClient
    from jiuwenclaw.common.schema.agent import AgentResponse, AgentResponseChunk
    from jiuwenclaw.common.schema.message import Message


# ---------- 雙佇列實現：入隊經 AgentServerClient 發往 AgentServer ----------
class MessageHandler(ABC):
    """
    維護兩個非同步訊息佇列，入隊訊息透過 AgentServerClient 傳送給 AgentServer：

    - _user_messages：Channel 發來的訊息，由內部轉發迴圈消費並呼叫 agent_client.send_request
    - _robot_messages：AgentServer 的響應，由 ChannelManager 消費並派發到對應 Channel

    AgentServer 經 WebSocket 下行 **E2AResponse** 線 JSON；``WebSocketAgentServerClient`` 內
    （``jiuwenclaw.e2a.wire_codec``）解析並還原為 ``AgentResponse`` / ``AgentResponseChunk``，
    本類仍透過 ``_response_to_message`` / ``_chunk_to_message`` 轉為 ``Message`` 供 Channel 消費。

    單例模式：全域性僅存在一個 MessageHandler 例項，可透過 MessageHandler(client) 或
    MessageHandler.get_instance(client) 獲取。
    """

    _instance: "MessageHandler | None" = None

    def __new__(cls, agent_client: "AgentServerClient", *args: Any, **kwargs: Any) -> "MessageHandler":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, agent_client: "AgentServerClient") -> None:
        if getattr(self, "_singleton_initialized", False):
            return
        self._singleton_initialized = True
        self._agent_client = agent_client
        self._user_messages: asyncio.Queue["Message"] = asyncio.Queue()
        self._robot_messages: asyncio.Queue["Message"] = asyncio.Queue()
        self._running = False
        self._forward_task: asyncio.Task | None = None
        self._stream_tasks: dict[str, asyncio.Task] = {}  # request_id -> task
        self._stream_sessions: dict[str, str | None] = {}  # request_id -> session_id
        self._stream_metadata: dict[str, dict[str, Any] | None] = {}  # request_id -> request metadata
        self._stream_modes: dict[str, str] = {}  # request_id -> mode
        self._pending_evolution_approval: dict[str, str] = {}  # session_id -> approval_request_id
        self._queued_supplement_input: dict[str, dict[str, Any]] = {}  # session_id -> queued supplement payload
        self._session_evolution_in_progress: set[str] = set()
        self._acp_session_aliases: dict[str, str] = {}  # external_session_id -> internal_session_id
        self._acp_session_alias_lock = asyncio.Lock()

        # per-channel 控制狀態：支援 \new_session / \mode 指令。
        # 使用 ChannelType 的 value 作為標準鍵，避免散落的硬編碼字串。
        self._control_channel_types = {
            ChannelType.FEISHU.value,
            ChannelType.XIAOYI.value,
            ChannelType.DINGTALK.value,
            ChannelType.WHATSAPP.value,
            ChannelType.WECOM.value,
            ChannelType.WECHAT.value,
        }
        # 使用 SessionMap 的 channel 族（由 config 中 gateway.session_map_scope 決定是否在 key 中含 user）
        self._session_map_channel_types = frozenset({
            "feishu_enterprise",
        })
        self._channel_states: Dict[str, ChannelControlState] = {}
        self._session_map = SessionMap()
        self._cron_controller = None

        # IM Pipeline（數字分身）— None 時不執行，不影響原有邏輯
        self._inbound_pipeline = None   # type: Any  # IMInboundPipeline | None
        self._outbound_pipeline = None  # type: Any  # IMOutboundPipeline | None

    def set_inbound_pipeline(self, pipeline: Any) -> None:
        self._inbound_pipeline = pipeline

    def set_outbound_pipeline(self, pipeline: Any) -> None:
        self._outbound_pipeline = pipeline

        # 直接使用 jiuwenclaw.config 的 get_config_raw/set_config/update_channel_in_config
        # 避免在此處重複實現 config 模組載入邏輯。
        from jiuwenclaw.common.config import get_config_raw, update_channel_in_config

        self._get_config_raw = get_config_raw
        self._update_channel_in_config = update_channel_in_config

        from jiuwenclaw.gateway.routing.agent_client import WebSocketAgentServerClient

        if isinstance(self._agent_client, WebSocketAgentServerClient):
            self._agent_client.set_server_push_handler(self._handle_agent_server_push)

    @classmethod
    def get_instance(cls, agent_client: "AgentServerClient | None" = None) -> "MessageHandler":
        """獲取單例例項。

        - 若例項已存在：可直接呼叫 get_instance() 或 get_instance(None)，無需傳入 client。
        - 若尚未建立：需傳入 agent_client，即 get_instance(client) 或 MessageHandler(client)。
        """
        if cls._instance is not None:
            return cls._instance
        if agent_client is None:
            raise RuntimeError(
                "MessageHandler 尚未初始化，請先使用 MessageHandler(client) 或 get_instance(client) 建立"
            )
        return cls(agent_client)

    def handle_message(self, msg: "Message") -> None:
        """Channel 同步回撥：將訊息放入 user_messages 佇列，由轉發迴圈發給 AgentServer."""
        self._user_messages.put_nowait(msg)
        logger.info(
            "[MessageHandler] _user_messages 入隊: id=%s channel_id=%s session_id=%s",
            msg.id, msg.channel_id, msg.session_id,
        )

    # ---------- Channel 控制狀態：\new_session / \mode ----------

    def _get_channel_default_state(self, channel_id: str) -> ChannelControlState:
        """從 config.yaml 讀取 Channel 的預設 session_id / mode."""
        try:
            cfg: Dict[str, Any] = self._get_config_raw()
        except Exception:  # noqa: BLE001
            cfg = {}
        channels_cfg = cfg.get("channels") or {}
        ch_cfg = channels_cfg.get(channel_id) or {}
        sid_raw = ch_cfg.get("default_session_id") or ""
        sid = str(sid_raw).strip() or None
        # 若未在 config 中指定預設 session_id，為該 channel 生成一個帶時間戳的新 session_id
        if not sid:
            sid = self._generate_channel_session_id(channel_id)
        mode_raw = str(ch_cfg.get("default_mode") or "agent.plan").strip().lower()
        mode_map = {
            "agent.plan": ChannelMode.AGENT_PLAN,
            "agent.fast": ChannelMode.AGENT_FAST,
            "code.plan": ChannelMode.CODE_PLAN,
            "code.normal": ChannelMode.CODE_NORMAL,
            "team": ChannelMode.TEAM,
        }
        mode = mode_map.get(mode_raw, ChannelMode.AGENT_PLAN)
        return ChannelControlState(session_id=sid, mode=mode)

    def _get_channel_state_key(self, channel_id: str, conversation_id: str | None) -> str:
        """生成 channel 狀態的複合鍵：channel_id:conversation_id."""
        if conversation_id:
            return f"{channel_id}:{conversation_id}"
        return channel_id

    def _get_or_create_channel_state(self, msg: "Message") -> ChannelControlState:
        """獲取或建立訊息對應 channel 狀態（使用複合鍵）。

        conversation_id 從 msg.metadata 獲取，如 feishu 的 feishu_chat_id。
        """
        ch = msg.channel_id
        # 獲取 conversation_id：從不同平臺的 metadata 中提取會話標識
        # feishu: feishu_chat_id, xiaoyi: xiaoyi_session_id, 其他用 session_id
        key = self._get_channel_state_key(ch, msg.session_id)

        # 如果狀態已存在，直接返回
        state = self._channel_states.get(key)
        if state is not None:
            return state

        # 否則從 config 載入預設值，並快取
        state = self._get_channel_default_state(ch)
        identity_key = self._extract_identity_tuple(msg)
        if identity_key and self._channel_id_matches_session_map_types(str(ch or "")):
            state.session_id = self._session_map.get_session_id(*identity_key)
        self._channel_states[key] = state
        return state

    def _save_channel_state_to_config(self, channel_id: str) -> None:
        """將指定 Channel 的預設 session_id / mode 寫回 config.yaml."""
        state = self._channel_states.get(channel_id)
        if not state:
            return
        self._update_channel_in_config(
            channel_id,
            {
                "default_session_id": state.session_id or "",
                "default_mode": state.mode.value if hasattr(state.mode, 'value') else str(state.mode),
            },
        )

    def _generate_channel_session_id(self, channel_id: str) -> str:
        """為指定 channel 生成新的 session_id."""
        ts = format(int(time.time() * 1000), "x")
        suffix = secrets.token_hex(3)
        return f"{channel_id}_{ts}_{suffix}"

    @staticmethod
    def _extract_identity_tuple(msg: "Message") -> tuple[str, str, str, str] | None:
        provider = str(getattr(msg, "provider", None) or "").strip()
        chat_id = str(getattr(msg, "chat_id", None) or "").strip()
        bot_id = str(getattr(msg, "bot_id", None) or "").strip()
        user_id = str(getattr(msg, "user_id", None) or "").strip()
        identity_parts = (provider, chat_id, bot_id, user_id)
        if all(identity_parts):
            return (provider, chat_id, bot_id, user_id)
        return None

    def _channel_id_matches_session_map_types(self, channel_id: str) -> bool:
        """channel_id 是否屬於 _session_map_channel_types 中某一族（精確匹配或 base: 字首）."""
        cid = str(channel_id or "").strip()
        for base in self._session_map_channel_types:
            if cid == base or cid.startswith(f"{base}:"):
                return True
        return False

    def _resolve_control_channel_type(self, msg: "Message") -> str:
        """Resolve control channel type key: prefer provider, fallback to channel_id."""
        provider_raw = getattr(msg, "provider", None)
        provider = str(getattr(provider_raw, "value", provider_raw) or "").strip()
        if provider:
            return provider
        return str(getattr(msg, "channel_id", "") or "")

    async def _send_channel_notice(
        self,
        user_infos: dict,
        channel_id: str,
        session_id: str | None,
        text_or_payload: str | dict[str, Any],
    ) -> None:
        """向指定 channel 傳送一條系統提示訊息.

        - str: 相容歷史行為，封裝為 {"content": text, "is_complete": True}
        - dict: 透傳給 channel（僅確保 is_complete=True）
        """
        from jiuwenclaw.common.schema.message import Message, EventType

        if isinstance(text_or_payload, dict):
            payload = dict(text_or_payload)
            payload.setdefault("is_complete", True)
        else:
            payload = {"content": text_or_payload, "is_complete": True}

        msg = Message(
            id=user_infos['id'],
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload=payload,
            event_type=EventType.CHAT_FINAL,
            metadata=user_infos['meta_data']
        )
        await self.publish_robot_messages(msg)

    async def _cancel_agent_work_for_session(self, msg: "Message", old_sid: str | None) -> None:
        """取消指定 session 的閘道器流式任務，並向 AgentServer 傳送 CHAT_CANCEL（與 Web chat.interrupt intent=cancel 對齊）。

        閘道器側僅取消 ``_stream_sessions[rid] == old_sid`` 的流式任務，並向 AgentServer 傳送同 session 的
        ``intent=cancel``，由 AgentServer 繼續取消該 session 上的實際執行任務。
        """
        from jiuwenclaw.common.schema.message import Message, ReqMethod

        self._clear_session_evolution_states(old_sid)

        tasks_to_cancel: list[asyncio.Task] = []
        rids_cancelled: list[str] = []

        for rid, task in list(self._stream_tasks.items()):
            if self._stream_sessions.get(rid) != old_sid:
                continue
            if not task.done():
                logger.info(
                    "[MessageHandler] 取消流式任務: request_id=%s session_id=%s",
                    rid,
                    old_sid,
                )
                task.cancel()
                tasks_to_cancel.append(task)
                rids_cancelled.append(rid)

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
            logger.info(
                "[MessageHandler] 當前 session 流式任務已終止: session_id=%s request_ids=%s",
                old_sid,
                rids_cancelled,
            )

        if old_sid is None and not rids_cancelled:
            return

        sid_for_agent = (old_sid or "").strip()
        if not sid_for_agent:
            return

        # 即使閘道器側已無活躍流式拉取任務（例如 Agent 正在執行 shell/工具），也必須通知 AgentServer，
        # 否則僅斷開 CLI WebSocket 無法停止已派發的工作。

        cancel_req = Message(
            id=f"interrupt_{int(time.time() * 1000):x}_{secrets.token_hex(3)}",
            type="req",
            channel_id=msg.channel_id,
            session_id=sid_for_agent,
            params={
                "intent": "cancel",
                "session_id": sid_for_agent,
            },
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_CANCEL,
            metadata=msg.metadata,
            provider=getattr(msg, "provider", None),
            chat_id=getattr(msg, "chat_id", None),
            user_id=getattr(msg, "user_id", None),
            bot_id=getattr(msg, "bot_id", None),
        )
        agent_msg = await self._prepare_agent_dispatch_message(cancel_req)
        env_interrupt = self.message_to_e2a(agent_msg)
        try:
            resp = await self._agent_client.send_request(env_interrupt)
        except Exception as exc:
            logger.warning("[MessageHandler] AgentServer 中斷請求失敗: %s", exc)
            await self._send_interrupt_result_notification(
                msg.id,
                msg.channel_id,
                sid_for_agent,
                "cancel",
                message=f"任務終止失敗: {exc}",
                success=False,
            )
            return

        payload = resp.payload if isinstance(resp.payload, dict) else {}
        if payload.get("event_type") == "chat.interrupt_result":
            out = self._response_to_message(
                resp,
                sid_for_agent,
                request_metadata=msg.metadata,
            )
            await self.publish_robot_messages(out)
            logger.info(
                "[MessageHandler] 已轉發 AgentServer 中斷結果: request_id=%s ok=%s",
                resp.request_id,
                resp.ok,
            )
            return

        error_message = "任務終止失敗"
        if isinstance(payload, dict):
            raw_error = payload.get("error") or payload.get("message")
            if isinstance(raw_error, str) and raw_error.strip():
                error_message = raw_error.strip()
        elif not resp.ok:
            error_message = "任務終止失敗"

        await self._send_interrupt_result_notification(
            msg.id,
            msg.channel_id,
            sid_for_agent,
            "cancel",
            message=error_message,
            success=False,
        )

    async def cancel_agent_sessions_on_disconnect(
        self,
        session_keys: list[tuple[str, str]],
    ) -> None:
        """TUI/WebSocket 異常斷開時，取消仍繫結在該連線上的會話（與顯式 chat.interrupt 對齊）。"""
        from jiuwenclaw.common.schema.message import Message, ReqMethod

        seen: set[str] = set()
        for _channel_id, session_id in session_keys:
            sid = (session_id or "").strip()
            if not sid or sid in seen:
                continue
            seen.add(sid)
            stub = Message(
                id=f"ws_drop_{int(time.time() * 1000):x}_{secrets.token_hex(4)}",
                type="req",
                channel_id=_channel_id,
                session_id=sid,
                params={"intent": "cancel", "session_id": sid},
                timestamp=time.time(),
                ok=True,
                req_method=ReqMethod.CHAT_CANCEL,
                is_stream=False,
            )
            try:
                await self._cancel_agent_work_for_session(stub, sid)
            except Exception:
                logger.warning(
                    "[MessageHandler] disconnect cancel failed: channel_id=%s session_id=%s",
                    _channel_id,
                    sid,
                    exc_info=True,
                )

    async def _new_session_cancel_and_notice(
        self,
        params: NewSessionCancelParams,
        msg: "Message",
    ) -> None:
        """先完成舊會話取消與 AgentServer 中斷，再下發 session 已變更提示。"""
        await self._cancel_agent_work_for_session(msg, params.old_sid)
        await self._send_channel_notice(
            params.user_infos,
            params.channel_id,
            params.reply_session_id,
            f"[收到 CLI 指令], session_id 已變更為 {params.new_sid}",
        )

    async def _mode_change_cancel_and_notice(
        self,
        params: ModeChangeCancelParams,
        msg: "Message",
    ) -> None:
        """與 /new_session 一致：先取消當前會話在閘道器與 Agent 側的任務，再下發 mode 已變更提示。"""
        await self._cancel_agent_work_for_session(msg, params.old_sid)
        await self._send_channel_notice(
            params.user_infos,
            params.channel_id,
            params.reply_session_id,
            self._build_mode_change_notice_text(params.new_mode_label),
        )

    @staticmethod
    def _build_mode_change_notice_text(mode_label: str) -> str:
        return f"[收到 CLI 指令], mode 已變更為 {mode_label}"

    def _handle_channel_control(self, msg: "Message") -> bool:
        r"""處理 \new_session / \mode / \skills 指令.

        Returns:
            True: 該訊息是控制指令，已處理完畢，不需要轉發給 Agent。
            False: 非控制指令，繼續正常處理。
        """
        user_infos = {"id": msg.id, "meta_data": msg.metadata}

        ch = msg.channel_id
        channel_type = self._resolve_control_channel_type(msg)
        if channel_type not in self._control_channel_types:
            return False

        params = msg.params or {}
        text = str(params.get("query") or params.get("content") or "").strip()
        if not text:
            return False

        parsed = parse_channel_control_text(text)
        if parsed.action is ParsedControlAction.NONE:
            return False

        logger.info(
            "[MessageHandler] _handle_channel_control channel=%s text=%s action=%s",
            channel_type,
            text,
            parsed.action.value,
        )

        if parsed.action is ParsedControlAction.SKILLS_OK:
            asyncio.create_task(
                self._skills_slash_notice(user_infos, ch, msg.session_id, msg)
            )
            return True

        # 獲取當前會話的狀態（使用複合鍵）
        state = self._get_or_create_channel_state(msg)

        if parsed.action is ParsedControlAction.NEW_SESSION_OK:
            old_sid = state.session_id
            cid = str(getattr(msg, "channel_id", "") or "")
            identity_key = self._extract_identity_tuple(msg)
            if identity_key and self._channel_id_matches_session_map_types(cid):
                new_sid = self._session_map.get_session_id(*identity_key, rotate=True)
            else:
                new_sid = self._generate_channel_session_id(channel_type)
            state.session_id = new_sid
            asyncio.create_task(
                self._new_session_cancel_and_notice(
                    NewSessionCancelParams(
                        user_infos=user_infos,
                        channel_id=ch,
                        reply_session_id=msg.session_id,
                        new_sid=new_sid,
                        old_sid=old_sid,
                    ),
                    msg,
                )
            )
            return True
        if parsed.action is ParsedControlAction.NEW_SESSION_BAD:
            asyncio.create_task(
                self._send_channel_notice(
                    user_infos,
                    ch,
                    msg.session_id,
                    "非法指令",
                )
            )
            return True

        if parsed.action is ParsedControlAction.MODE_OK:
            mode_str = parsed.mode_subcommand or ""
            if mode_str not in (
                "agent",
                "code",
                "team",
                "agent.plan",
                "agent.fast",
                "code.plan",
                "code.normal",
            ):
                asyncio.create_task(
                    self._send_channel_notice(
                        user_infos,
                        ch,
                        msg.session_id,
                        "非法指令",
                    )
                )
                return True
            old_mode = state.mode
            old_sid = state.session_id
            if mode_str == "agent":
                state.mode = ChannelMode.AGENT_PLAN
            elif mode_str == "code":
                state.mode = ChannelMode.CODE_NORMAL
            elif mode_str == "team":
                state.mode = ChannelMode.TEAM
            elif mode_str == "agent.plan":
                state.mode = ChannelMode.AGENT_PLAN
            elif mode_str == "agent.fast":
                state.mode = ChannelMode.AGENT_FAST
            elif mode_str == "code.plan":
                state.mode = ChannelMode.CODE_PLAN
            elif mode_str == "code.normal":
                state.mode = ChannelMode.CODE_NORMAL
            new_label = state.mode.value
            if old_mode != state.mode:
                asyncio.create_task(
                    self._mode_change_cancel_and_notice(
                        ModeChangeCancelParams(
                            user_infos=user_infos,
                            channel_id=ch,
                            reply_session_id=msg.session_id,
                            old_sid=old_sid,
                            new_mode_label=new_label,
                        ),
                        msg,
                    )
                )
            else:
                asyncio.create_task(
                    self._send_channel_notice(
                        user_infos,
                        ch,
                        msg.session_id,
                        self._build_mode_change_notice_text(new_label),
                    )
                )
            return True
        if parsed.action is ParsedControlAction.SWITCH_OK:
            switch_str = parsed.switch_subcommand or ""
            target_mode: ChannelMode | None = None
            if switch_str == "plan":
                if state.mode in (ChannelMode.AGENT_PLAN, ChannelMode.AGENT_FAST):
                    target_mode = ChannelMode.AGENT_PLAN
                elif state.mode in (ChannelMode.CODE_PLAN, ChannelMode.CODE_NORMAL):
                    target_mode = ChannelMode.CODE_PLAN
            elif switch_str == "fast":
                if state.mode in (ChannelMode.AGENT_PLAN, ChannelMode.AGENT_FAST):
                    target_mode = ChannelMode.AGENT_FAST
            elif switch_str == "normal":
                if state.mode in (ChannelMode.CODE_PLAN, ChannelMode.CODE_NORMAL):
                    target_mode = ChannelMode.CODE_NORMAL
            if target_mode is None:
                asyncio.create_task(
                    self._send_channel_notice(
                        user_infos,
                        ch,
                        msg.session_id,
                        "非法指令",
                    )
                )
                return True
            old_mode = state.mode
            old_sid = state.session_id
            state.mode = target_mode
            new_label = state.mode.value
            if old_mode != state.mode:
                asyncio.create_task(
                    self._mode_change_cancel_and_notice(
                        ModeChangeCancelParams(
                            user_infos=user_infos,
                            channel_id=ch,
                            reply_session_id=msg.session_id,
                            old_sid=old_sid,
                            new_mode_label=new_label,
                        ),
                        msg,
                    )
                )
            else:
                asyncio.create_task(
                    self._send_channel_notice(
                        user_infos,
                        ch,
                        msg.session_id,
                        self._build_mode_change_notice_text(new_label),
                    )
                )
            return True
        if parsed.action in (ParsedControlAction.MODE_BAD, ParsedControlAction.SWITCH_BAD):
            asyncio.create_task(
                self._send_channel_notice(
                    user_infos,
                    ch,
                    msg.session_id,
                    "非法指令",
                )
            )
            return True

        return False

    async def _skills_slash_notice(
        self,
        user_infos: dict[str, Any],
        channel_id: str,
        reply_session_id: str | None,
        msg: "Message",
    ) -> None:
        """受控通道整行 /skills list：請求 skills.list 並以 CHAT_FINAL 通知透傳。"""
        from jiuwenclaw.common.schema.message import Message, ReqMethod

        req_id = f"skills_slash_{int(time.time() * 1000):x}_{secrets.token_hex(3)}"
        skills_req = Message(
            id=req_id,
            type="req",
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.SKILLS_LIST,
            is_stream=False,
            metadata=msg.metadata,
            provider=getattr(msg, "provider", None),
            chat_id=getattr(msg, "chat_id", None),
            user_id=getattr(msg, "user_id", None),
            bot_id=getattr(msg, "bot_id", None),
        )
        try:
            env = self.message_to_e2a(skills_req)
            resp = await self._agent_client.send_request(env)
            if resp.ok:
                if isinstance(resp.payload, dict):
                    notice_payload: dict[str, Any] = dict(resp.payload)
                else:
                    notice_payload = {"data": resp.payload}
            else:
                err = ""
                if isinstance(resp.payload, dict):
                    err = str(resp.payload.get("error") or "").strip()
                notice_payload = {
                    "error": f"獲取技能列表失敗{(': ' + err) if err else ''}",
                }
            await self._send_channel_notice(
                user_infos, channel_id, reply_session_id, notice_payload
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[MessageHandler] /skills list 請求失敗: %s", exc)
            await self._send_channel_notice(
                user_infos,
                channel_id,
                reply_session_id,
                {"error": f"獲取技能列表失敗：{exc}"},
            )

    def _apply_channel_state(self, msg: "Message") -> None:
        """將當前 Channel 的控制狀態應用到訊息上（session_id / mode）."""
        channel_type = self._resolve_control_channel_type(msg)
        if channel_type not in self._control_channel_types:
            return
        state = self._get_or_create_channel_state(msg)

        # 僅 _session_map_channel_types 中的通道族使用 SessionMap；其它受控通道仍按 config/state 與入站 session_id。
        cid = str(getattr(msg, "channel_id", "") or "")
        identity_key = self._extract_identity_tuple(msg)
        if identity_key and self._channel_id_matches_session_map_types(cid):
            sid = self._session_map.get_session_id(*identity_key)
            state.session_id = sid
            msg.session_id = sid
        elif state.session_id:
            msg.session_id = state.session_id

        # 將 mode 寫入 params，後續 E2A / Agent 側從 params["mode"] 讀取
        if msg.params is None:
            msg.params = {}
        if isinstance(msg.params, dict):
            msg.params.setdefault("mode", state.mode.value)

    # ---------- user_messages ----------

    async def publish_user_messages(self, msg: "Message") -> None:
        """將訊息放入 user_messages 佇列（非同步）."""
        await self._user_messages.put(msg)

    def publish_user_messages_nowait(self, msg: "Message") -> None:
        """將訊息放入 user_messages 佇列（同步）."""
        self._user_messages.put_nowait(msg)

    async def consume_user_messages(self, timeout: float | None = None) -> "Message | None":
        """消費一條 user_messages；timeout 為 None 則阻塞，否則超時返回 None."""
        if timeout is not None and timeout <= 0:
            try:
                return self._user_messages.get_nowait()
            except asyncio.QueueEmpty:
                return None
        try:
            if timeout is None:
                return await self._user_messages.get()
            return await asyncio.wait_for(self._user_messages.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ---------- robot_messages ----------

    async def publish_robot_messages(self, msg: "Message") -> None:
        """將 Agent 響應放入 robot_messages 佇列."""
        # Outbound Pipeline（數字分身出站路由）— 在入隊前執行
        if self._outbound_pipeline is not None:
            try:
                await self._outbound_pipeline.apply(msg)
            except Exception:
                logger.exception("Outbound pipeline error, message queued without routing")
        await self._robot_messages.put(msg)

    def publish_robot_messages_nowait(self, msg: "Message") -> None:
        """將 Agent 響應放入 robot_messages 佇列（同步）."""
        self._robot_messages.put_nowait(msg)

    async def consume_robot_messages(self, timeout: float | None = None) -> "Message | None":
        """消費一條 robot_messages；timeout 為 None 則阻塞，否則超時返回 None."""
        if timeout is not None and timeout <= 0:
            try:
                return self._robot_messages.get_nowait()
            except asyncio.QueueEmpty:
                return None
        try:
            if timeout is None:
                return await self._robot_messages.get()
            return await asyncio.wait_for(self._robot_messages.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    @staticmethod
    def _is_session_map_style_session_id(session_id: str) -> bool:
        parts = [part.strip() for part in str(session_id or "").split("::")]
        if len(parts) not in (5, 6):
            return False
        return all(parts)

    @classmethod
    def _is_known_jiuwenclaw_session_id(cls, session_id: str | None) -> bool:
        sid = str(session_id or "").strip()
        if not sid:
            return False
        if sid.startswith(_KNOWN_JIUWENCLAW_SESSION_PREFIXES):
            return True
        return cls._is_session_map_style_session_id(sid)

    async def _ensure_acp_agent_session(self, session_id: str) -> str:
        from jiuwenclaw.common.e2a.gateway_normalize import e2a_from_agent_fields
        from jiuwenclaw.common.schema.message import ReqMethod

        env = e2a_from_agent_fields(
            request_id=f"acp-session-create-{int(time.time() * 1000):x}-{secrets.token_hex(3)}",
            channel_id=_ACP_CHANNEL_ID,
            session_id=session_id,
            req_method=ReqMethod.SESSION_CREATE,
            params={"session_id": session_id},
            is_stream=False,
            timestamp=time.time(),
        )
        resp = await self._agent_client.send_request(env)
        if not resp.ok:
            payload = dict(resp.payload or {}) if isinstance(resp.payload, dict) else {}
            raise RuntimeError(str(payload.get("error") or "acp session.create failed"))
        payload = dict(resp.payload or {}) if isinstance(resp.payload, dict) else {}
        resolved = payload.get("sessionId") or payload.get("session_id") or session_id
        resolved_str = str(resolved or "").strip()
        if not resolved_str:
            raise RuntimeError("acp session.create returned empty session_id")
        return resolved_str

    async def _resolve_acp_internal_session_id(
        self,
        external_session_id: str | None,
    ) -> tuple[str | None, bool]:
        external = str(external_session_id or "").strip()
        if not external:
            return None, False

        cached = self._acp_session_aliases.get(external)
        if cached:
            return cached, cached != external

        async with self._acp_session_alias_lock:
            cached = self._acp_session_aliases.get(external)
            if cached:
                return cached, cached != external

            desired = (
                external
                if self._is_known_jiuwenclaw_session_id(external)
                else self._generate_channel_session_id(_ACP_CHANNEL_ID)
            )
            ensured = await self._ensure_acp_agent_session(desired)
            self._acp_session_aliases[external] = ensured
            return ensured, ensured != external

    async def _prepare_agent_dispatch_message(self, msg: "Message") -> "Message":
        from jiuwenclaw.common.schema.message import ReqMethod

        if msg.channel_id != _ACP_CHANNEL_ID:
            return msg
        if msg.req_method in (ReqMethod.INITIALIZE, ReqMethod.SESSION_CREATE):
            return msg

        internal_session_id, aliased = await self._resolve_acp_internal_session_id(msg.session_id)
        if not internal_session_id:
            return msg

        params = dict(msg.params or {})
        params["session_id"] = internal_session_id

        metadata = dict(msg.metadata or {})
        if aliased:
            metadata.setdefault(_ACP_ORIGINAL_SESSION_ID_KEY, str(msg.session_id or ""))

        return replace(
            msg,
            session_id=internal_session_id,
            params=params,
            metadata=metadata or None,
        )

    def _resolve_acp_external_session_id(
        self,
        session_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None

        original = ""
        if isinstance(metadata, dict):
            original = str(metadata.get(_ACP_ORIGINAL_SESSION_ID_KEY) or "").strip()
        if original:
            return original

        for external, internal in self._acp_session_aliases.items():
            if internal == sid:
                return external
        return sid

    @staticmethod
    def _resolve_at_file_references(
        content: str,
        cwd: str | None = None,
        max_file_size: int | None = _DEFAULT_INLINE_FILE_SIZE_LIMIT,
    ) -> str:
        """Parse ``@path`` references in *content* and inline the file text.

        Supported forms:
        - ``@relative/path`` / ``@/absolute/path`` — resolved against *cwd*
        - ``@"path with spaces"`` — quoted paths
        - ``@path#L10-20`` — line-range suffix (ignored for now, whole file read)

        Returns content with ``@path`` replaced by a ``<file-content>`` block
        containing the actual text.  If a file cannot be read the original
        ``@path`` is kept unchanged.
        """
        if not content:
            return content

        working_dir = cwd or os.getcwd()

        # Match @path or @"quoted path", optionally followed by #L... line range
        pattern = re.compile(
            r'(?P<prefix>(?:^|(?<=\s)))@(?:"(?P<quoted>[^"]+)"|(?P<plain>[^\s#]+))(?:#[^#\s]*)?'
        )

        def _replacer(m: re.Match[str]) -> str:
            raw = m.group("quoted") or m.group("plain") or ""
            if not raw:
                return m.group(0)

            # Resolve path
            if raw.startswith("~/"):
                home = os.path.expanduser("~")
                resolved = os.path.join(home, raw[2:])
            elif MessageHandler._is_absolute_reference_path(raw):
                resolved = raw
            else:
                resolved = os.path.join(working_dir, raw)

            try:
                path = Path(resolved)
                if not path.is_file():
                    return m.group(0)
                size = path.stat().st_size
                truncated = False
                if max_file_size is None:
                    text = path.read_text(encoding="utf-8", errors="replace")
                else:
                    with path.open("r", encoding="utf-8", errors="replace") as handle:
                        text = handle.read(max_file_size + 1)
                    if size > max_file_size or len(text) > max_file_size:
                        truncated = True
                    if len(text) > max_file_size:
                        text = text[:max_file_size]
                    if truncated:
                        suffix = f"\n... (truncated, original_size={size} bytes)"
                        text = f"{text}{suffix}"
                return (
                    f'\n<file-content path="{raw}">\n{text}\n</file-content>\n'
                )
            except (OSError, UnicodeDecodeError):
                return m.group(0)

        return pattern.sub(_replacer, content)

    @staticmethod
    def _is_absolute_reference_path(raw: str) -> bool:
        return raw.startswith("/") or (len(raw) >= 3 and raw[1] == ":" and raw[2] == "\\")

    @staticmethod
    def _resolve_reference_path(raw: str, cwd: str | None = None) -> str:
        working_dir = cwd or os.getcwd()
        if raw.startswith("~/"):
            return os.path.join(os.path.expanduser("~"), raw[2:])
        if MessageHandler._is_absolute_reference_path(raw):
            return raw
        return os.path.join(working_dir, raw)

    @classmethod
    def _normalize_structured_attachments(
        cls,
        attachments: Any,
        cwd: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(attachments, list):
            return []

        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in attachments:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                continue
            resolved_path = cls._resolve_reference_path(raw_path, cwd)
            if resolved_path in seen:
                continue
            seen.add(resolved_path)
            normalized.append(
                {
                    "path": resolved_path,
                    "type": str(item.get("type") or "file").strip() or "file",
                    "filename": str(item.get("filename") or Path(resolved_path).name).strip(),
                }
            )
        return normalized

    @classmethod
    def _strip_attached_mentions(
        cls,
        content: str,
        attachments: list[dict[str, Any]],
        cwd: str | None = None,
    ) -> str:
        if not content or not attachments:
            return content

        attached_paths = {
            cls._resolve_reference_path(str(item.get("path") or ""), cwd)
            for item in attachments
            if str(item.get("path") or "").strip()
        }
        if not attached_paths:
            return content

        pattern = re.compile(
            r'(?P<prefix>(?:^|(?<=\s)))@(?:"(?P<quoted>[^"]+)"|(?P<plain>[^\s#]+))(?:#[^#\s]*)?'
        )

        def _replacer(match: re.Match[str]) -> str:
            raw = match.group("quoted") or match.group("plain") or ""
            if not raw:
                return match.group(0)
            resolved = cls._resolve_reference_path(raw, cwd)
            if resolved not in attached_paths:
                return match.group(0)
            return f"{match.group('prefix')}{raw}"

        return pattern.sub(_replacer, content)

    @classmethod
    def _resolve_structured_attachments(
        cls,
        content: str,
        attachments: Any,
        cwd: str | None = None,
    ) -> str:
        normalized = cls._normalize_structured_attachments(attachments, cwd)
        if not normalized:
            return content

        prefix = " ".join(f'@"{item["path"]}"' for item in normalized)
        cleaned_content = cls._strip_attached_mentions(content, normalized, cwd)
        merged_content = f"{prefix} {cleaned_content}".strip()
        return cls._resolve_at_file_references(merged_content, cwd=cwd)

    @staticmethod
    def message_to_e2a(msg: "Message") -> "E2AEnvelope":
        from jiuwenclaw.common.e2a.gateway_normalize import message_to_e2a_or_fallback

        return message_to_e2a_or_fallback(msg)


    @staticmethod
    def _merge_agent_metadata(
        request_metadata: dict[str, Any] | None,
        response_metadata: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """合併 Agent 響應 metadata 與閘道器請求 metadata。

        send_push / 工具鏈返回的響應常不帶 metadata，通道（如釘釘 batchSend）需要
        請求側的 dingtalk_sender_id、conversation_type 等；響應中有同名欄位時優先響應。
        """
        req_md = request_metadata or {}
        resp_md = response_metadata or {}
        if not req_md and not resp_md:
            return None
        merged: dict[str, Any] = {**req_md, **resp_md}
        return merged

    @staticmethod
    def _response_to_message(
        resp: "AgentResponse",
        session_id: str | None,
        *,
        request_metadata: dict[str, Any] | None = None,
    ) -> "Message":
        from jiuwenclaw.common.schema.message import Message, EventType

        metadata = MessageHandler._merge_agent_metadata(request_metadata, resp.metadata)

        # 從 metadata 中提取 group_digital_avatar 和 enable_memory 欄位
        # 這些欄位在 message_to_e2a 中被放入 metadata，需要在這裡提取出來
        group_digital_avatar = bool(metadata.get("group_digital_avatar", False)) if metadata else False
        enable_memory = bool(metadata.get("enable_memory", True)) if metadata else True

        # 檢查 payload 中是否包含 event_type，如果包含則建立事件訊息
        event_type = None
        if resp.payload and isinstance(resp.payload, dict):
            event_type_str = resp.payload.get("event_type")
            if isinstance(event_type_str, str):
                try:
                    event_type = EventType(event_type_str)
                    # 如果是事件型別，建立事件訊息而不是響應訊息
                    return Message(
                        id=resp.request_id,
                        type="event",
                        channel_id=resp.channel_id,
                        session_id=session_id,
                        params={},
                        timestamp=time.time(),
                        ok=True,
                        payload=resp.payload,
                        event_type=event_type,
                        metadata=metadata,
                        group_digital_avatar=group_digital_avatar,
                        enable_memory=enable_memory,
                    )
                except ValueError:
                    # 不是有效的 EventType，繼續作為普通響應處理
                    pass

        # 普通響應訊息
        return Message(
            id=resp.request_id,
            type="res",
            channel_id=resp.channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=resp.ok,
            payload=resp.payload,
            event_type=EventType.CHAT_FINAL,
            metadata=metadata,
            group_digital_avatar=group_digital_avatar,
            enable_memory=enable_memory,
        )

    async def _handle_agent_server_push(self, wire: dict[str, Any]) -> None:
        """AgentServer ``send_push`` 下行：與 RPC 共用連線但不得佔用 unary/stream 等待佇列。"""
        from jiuwenclaw.common.e2a.wire_codec import parse_agent_server_wire_chunk

        try:
            chunk = parse_agent_server_wire_chunk(wire)
        except Exception as e:
            logger.exception("[MessageHandler] server_push 解析失敗: %s", e)
            return
        rid = str(chunk.request_id or "")
        sid_raw = wire.get("session_id")
        if sid_raw is not None and str(sid_raw).strip():
            session_id: str | None = str(sid_raw)
        else:
            session_id = self._stream_sessions.get(rid)
        
        # 獲取原始請求的 metadata，用於合併
        request_metadata = self._stream_metadata.get(rid)
        
        # 獲取 AgentServer 返回的 metadata
        wmd = wire.get("metadata")
        if isinstance(wmd, dict):
            resp_md = {
                k: v
                for k, v in wmd.items()
                if k not in E2A_WIRE_INTERNAL_METADATA_KEYS
            }
        else:
            resp_md = None

        # 合併 metadata：請求 metadata 在前，響應 metadata 在後（響應優先）
        bus_metadata = MessageHandler._merge_agent_metadata(request_metadata, resp_md)

        if chunk.channel_id == _ACP_CHANNEL_ID:
            session_id = self._resolve_acp_external_session_id(session_id, bus_metadata)
        if isinstance(chunk.payload, dict) and chunk.payload.get("event_type") == "cron.response":
            await self._handle_cron_push_payload(
                payload=dict(chunk.payload),
                request_id=rid,
                channel_id=chunk.channel_id,
                session_id=session_id,
                metadata=bus_metadata,
            )
            return
        if self._is_terminal_stream_chunk(chunk):
            logger.debug(
                "[MessageHandler] 忽略 server_push 終止 chunk: request_id=%s",
                chunk.request_id,
            )
            return

        # Track evolution state on the server_push path as well.
        await self._handle_evolution_chunk(chunk, session_id, bus_metadata)

        out = self._chunk_to_message(
            chunk, session_id=session_id, metadata=bus_metadata
        )
        await self.publish_robot_messages(out)
        logger.info(
            "[MessageHandler] server_push 已寫入 robot_messages: request_id=%s channel_id=%s",
            rid,
            chunk.channel_id,
        )

    def set_cron_controller(self, controller: Any) -> None:
        self._cron_controller = controller

    async def _handle_cron_push_payload(
        self,
        *,
        payload: dict[str, Any],
        request_id: str,
        channel_id: str,
        session_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> None:
        cc = self._cron_controller
        if cc is None:
            return
        action = str(payload.get("action") or "").strip()
        params = payload.get("data") or {}
        if not isinstance(params, dict):
            params = {}
        try:
            if action == "list":
                data = await cc.list_jobs()
            elif action == "get":
                data = await cc.get_job(str(params.get("job_id") or ""))
            elif action == "create":
                # 從原始請求中獲取 mode，覆蓋 LLM 工具呼叫的預設值
                request_mode = self._stream_modes.get(request_id)
                if request_mode:
                    params["mode"] = request_mode
                data = await cc.create_job(params)
            elif action == "update":
                data = await cc.update_job(str(params.get("job_id") or ""), dict(params.get("patch") or {}))
            elif action == "delete":
                data = {"deleted": await cc.delete_job(str(params.get("job_id") or ""))}
            elif action == "toggle":
                data = await cc.toggle_job(str(params.get("job_id") or ""), bool(params.get("enabled")))
            elif action == "preview":
                data = await cc.preview_job(str(params.get("job_id") or ""), int(params.get("count", 5)))
            elif action == "run_now":
                data = {"run_id": await cc.run_now(str(params.get("job_id") or ""))}
            else:
                data = {"error": f"unknown cron action: {action}"}
        except Exception as exc:  # noqa: BLE001
            data = {"error": str(exc)}

        from jiuwenclaw.common.schema.message import EventType, Message
        out = Message(
            id=request_id,
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "event_type": "chat.tool_result",
                "tool_name": "cron",
                "result": data,
            },
            event_type=EventType.CHAT_TOOL_RESULT,
            metadata=metadata,
            enable_streaming=False,  # 工具結果不開啟流式，避免被髮送到群聊
        )
        await self.publish_robot_messages(out)

    @staticmethod
    def _chunk_to_message(
        chunk: AgentResponseChunk,
        session_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """將 AgentResponseChunk 轉換為 Message（用於流式處理）。
        metadata 傳入 request 的 metadata，供 Feishu/Xiaoyi 等通道回發時使用平臺身份。
        """
        from jiuwenclaw.common.schema.message import Message, EventType

        # 從 metadata 中提取 group_digital_avatar 和 enable_memory 欄位
        # 這些欄位在 message_to_e2a 中被放入 metadata，需要在這裡提取出來
        group_digital_avatar = bool(metadata.get("group_digital_avatar", False)) if metadata else False
        enable_memory = bool(metadata.get("enable_memory", True)) if metadata else True

        # 從 payload 中提取 event_type（如果存在）
        event_type = None
        if chunk.payload and isinstance(chunk.payload, dict):
            event_type_str = chunk.payload.get("event_type")
            if isinstance(event_type_str, str):
                try:
                    event_type = EventType(event_type_str)
                except ValueError:
                    logger.debug("未知的 event_type: %s", event_type_str)

        return Message(
            id=chunk.request_id,
            type="event",
            channel_id=chunk.channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload=chunk.payload,
            event_type=event_type,
            metadata=metadata,
            group_digital_avatar=group_digital_avatar,
            enable_memory=enable_memory,
        )

    @staticmethod
    def _is_terminal_stream_chunk(chunk: AgentResponseChunk) -> bool:
        """識別僅用於結束流的哨兵 chunk，避免被當作業務事件繼續下發。"""
        if not bool(getattr(chunk, "is_complete", False)):
            return False
        payload = getattr(chunk, "payload", None)
        if not payload:
            return True
        if not isinstance(payload, dict):
            return False
        if payload.get("event_type"):
            return False
        if payload.get("content") not in (None, ""):
            return False
        if payload.get("error") not in (None, ""):
            return False
        return payload.get("is_complete") is True and set(payload.keys()) <= {"is_complete"}

    async def _publish_stream_cancelled_final(
        self,
        request_id: str,
        channel_id: str,
        session_id: str | None,
        request_metadata: dict[str, Any] | None,
    ) -> None:
        """流式任務被閘道器取消時補發 chat.final，帶 is_complete（供飛書等通道合併緩衝）。"""
        from jiuwenclaw.common.schema.message import Message, EventType

        group_digital_avatar = bool(request_metadata.get("group_digital_avatar", False)) if request_metadata else False
        enable_memory = bool(request_metadata.get("enable_memory", True)) if request_metadata else True

        out = Message(
            id=request_id,
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "event_type": EventType.CHAT_FINAL.value,
                "content": "",
                "is_complete": True,
            },
            event_type=EventType.CHAT_FINAL,
            metadata=request_metadata,
            group_digital_avatar=group_digital_avatar,
            enable_memory=enable_memory,
        )
        await self.publish_robot_messages(out)
        logger.info(
            "[MessageHandler] 已傳送流式取消結束幀: request_id=%s session_id=%s",
            request_id,
            session_id,
        )

    @staticmethod
    def _non_stream_rpc_may_run_parallel(env: "E2AEnvelope") -> bool:
        """可與其它非流式 RPC 併發，不阻塞 _forward_loop。

        閘道器佇列否則序列 await Agent，慢請求（如 SkillNet 搜尋）會堵住後續的 skills.list 重新整理。
        聊天相關必須按入隊順序與流式任務協調，不得後臺併發。
        """
        from jiuwenclaw.common.schema.message import ReqMethod

        m = env.method
        if not m:
            return False
        return m not in (
            ReqMethod.CHAT_SEND.value,
            ReqMethod.CHAT_RESUME.value,
            ReqMethod.CHAT_CANCEL.value,
            ReqMethod.CHAT_ANSWER.value,
        )

    @staticmethod
    def _should_trigger_before_chat_request_hook(msg: "Message") -> bool:
        from jiuwenclaw.common.schema.message import ReqMethod

        return msg.req_method in (
            ReqMethod.CHAT_SEND,
            ReqMethod.CHAT_RESUME,
            ReqMethod.CHAT_ANSWER,
        )

    async def _trigger_before_chat_request_hook(self, msg: "Message") -> None:
        if not self._should_trigger_before_chat_request_hook(msg):
            return

        params = msg.params if isinstance(msg.params, dict) else {}
        if not isinstance(msg.params, dict):
            msg.params = params

        ctx = GatewayChatHookContext(
            request_id=msg.id,
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            req_method=msg.req_method.value if msg.req_method is not None else None,
            params=params,
        )
        from jiuwenclaw.extensions.registry import ExtensionRegistry

        await ExtensionRegistry.get_instance().trigger(GatewayHookEvents.BEFORE_CHAT_REQUEST, ctx)

    @staticmethod
    def _is_evolution_approval_request_id(request_id: Any) -> bool:
        # Support skill evolution (skill_evolve_*) and team skill evolution (team_skill_evolve_*).
        # Note: skill creation (SkillCreateRail/TeamSkillCreateRail) uses ask_user + skill-creator
        # flow, not the approval-based routing.
        return isinstance(request_id, str) and (
            request_id.startswith("skill_evolve_") or
            request_id.startswith("team_skill_evolve_")
        )

    def _queue_supplement_input(
        self,
        session_id: str | None,
        new_input: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        if not session_id:
            return
        payload: dict[str, Any] = {"new_input": new_input}
        if attachments:
            payload["attachments"] = attachments
        self._queued_supplement_input[session_id] = payload

    def _pop_queued_supplement_input(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        return self._queued_supplement_input.pop(session_id, None)

    def _mark_pending_evolution_approval(self, session_id: str | None, request_id: Any) -> None:
        if not session_id:
            return
        if self._is_evolution_approval_request_id(request_id):
            self._pending_evolution_approval[session_id] = str(request_id)

    def _build_auto_accept_evolution_answer(
        self,
        *,
        channel_id: str,
        session_id: str,
        request_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> "Message":
        from jiuwenclaw.common.schema.message import Message, ReqMethod

        return Message(
            id=f"auto_evolve_answer_{int(time.time() * 1000):x}_{secrets.token_hex(3)}",
            type="req",
            channel_id=channel_id,
            session_id=session_id,
            params={
                "request_id": request_id,
                "answers": [{"selected_options": ["接收"]}],
                "source": "auto_accept",
            },
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_ANSWER,
            is_stream=False,
            metadata=metadata,
        )

    def _maybe_auto_accept_replaced_evolution_approval(
        self,
        *,
        session_id: str | None,
        incoming_request_id: str,
        channel_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not session_id or not incoming_request_id:
            return

        previous_request_id = self._pending_evolution_approval.get(session_id)
        if not previous_request_id or previous_request_id == incoming_request_id:
            return

        auto_answer = self._build_auto_accept_evolution_answer(
            channel_id=channel_id,
            session_id=session_id,
            request_id=previous_request_id,
            metadata=metadata,
        )
        self._user_messages.put_nowait(auto_answer)
        logger.info(
            "[MessageHandler] auto-accept superseded evolution approval: session_id=%s old=%s new=%s",
            session_id,
            previous_request_id,
            incoming_request_id,
        )

    def _clear_pending_evolution_approval(self, session_id: str | None) -> None:
        if not session_id:
            return
        self._pending_evolution_approval.pop(session_id, None)

    def _mark_session_evolution_in_progress(self, session_id: str | None) -> None:
        if not session_id:
            return
        self._session_evolution_in_progress.add(session_id)

    def _clear_session_evolution_in_progress(self, session_id: str | None) -> None:
        if not session_id:
            return
        self._session_evolution_in_progress.discard(session_id)

    def _is_session_evolution_in_progress(self, session_id: str | None) -> bool:
        return isinstance(session_id, str) and session_id in self._session_evolution_in_progress

    def _finish_evolution_approval_if_current(
        self,
        session_id: str | None,
        answered_request_id: str | None,
    ) -> dict[str, Any] | None:
        if not session_id or not answered_request_id:
            return None

        current_request_id = self._pending_evolution_approval.get(session_id)
        if current_request_id != answered_request_id:
            logger.info(
                "[MessageHandler] stale evolution approval resolved, "
                "keep current pending: session_id=%s answered=%s current=%s",
                session_id,
                answered_request_id,
                current_request_id,
            )
            return None

        self._clear_pending_evolution_approval(session_id)
        self._clear_session_evolution_in_progress(session_id)
        return self._pop_queued_supplement_input(session_id)

    async def _handle_evolution_chunk(
        self,
        chunk,
        session_id: str | None,
        request_metadata: dict[str, Any] | None = None,
    ) -> None:
        """處理 chunk 中的演進狀態和審批事件，更新 Gateway 狀態機。

        在 process_stream 和 _handle_agent_server_push 兩條路徑中複用。
        """
        if not isinstance(chunk.payload, dict):
            return
        event_type = chunk.payload.get("event_type")
        if event_type == "chat.evolution_status":
            status = str(chunk.payload.get("status", "")).strip().lower()
            if status == "start":
                self._mark_session_evolution_in_progress(session_id)
                rid = getattr(chunk, "request_id", "")
                logger.info(
                    "[MessageHandler] evolution status start: session_id=%s request_id=%s",
                    session_id,
                    rid,
                )
            elif status == "end":
                self._clear_session_evolution_in_progress(session_id)
                rid = getattr(chunk, "request_id", "")
                logger.info(
                    "[MessageHandler] evolution status end: session_id=%s request_id=%s",
                    session_id,
                    rid,
                )
        approval_request_id = chunk.payload.get("request_id")
        if (
            event_type == "chat.ask_user_question"
            and self._is_evolution_approval_request_id(approval_request_id)
        ):
            self._maybe_auto_accept_replaced_evolution_approval(
                session_id=session_id,
                incoming_request_id=str(approval_request_id),
                channel_id=str(getattr(chunk, "channel_id", "") or ""),
                metadata=request_metadata,
            )
            self._mark_pending_evolution_approval(session_id, approval_request_id)
            logger.info(
                "[MessageHandler] evolution approval detected: session_id=%s request_id=%s",
                session_id,
                approval_request_id,
            )

    def _clear_session_evolution_states(self, session_id: str | None) -> None:
        self._clear_session_evolution_in_progress(session_id)
        self._clear_pending_evolution_approval(session_id)
        self._pop_queued_supplement_input(session_id)

    @staticmethod
    def _build_queued_chat_send_message(
        msg: "Message",
        new_input: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> "Message":
        from jiuwenclaw.common.schema.message import Message, ReqMethod

        new_req_id = f"req_{int(time.time() * 1000):x}_{msg.id}"
        params: dict[str, Any] = {
            "query": new_input,
            "session_id": msg.session_id,
            "is_supplement": True,
        }
        if attachments:
            params["attachments"] = attachments
        return Message(
            id=new_req_id,
            type="req",
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            params=params,
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            is_stream=True,
        )

    async def _process_non_stream_request(self, msg: "Message", env: "E2AEnvelope") -> Any:
        """執行單次非流式 Agent 請求並將結果寫入 robot_messages（供序列或後臺任務複用）。"""
        try:
            resp = await self._agent_client.send_request(env)
            out = self._response_to_message(
                resp,
                session_id=msg.session_id,
                request_metadata=msg.metadata,
            )
            await self.publish_robot_messages(out)
            logger.info(
                "[MessageHandler] Agent 響應已寫入 robot_messages: request_id=%s channel_id=%s",
                resp.request_id,
                resp.channel_id,
            )
            return resp
        except Exception as e:
            logger.exception("AgentServer send_request failed for %s: %s", msg.id, e)
            err_msg = self._build_error_out_message(msg, e)
            await self.publish_robot_messages(err_msg)
            logger.info(
                "[MessageHandler] 錯誤響應已寫入 robot_messages: id=%s channel_id=%s",
                msg.id,
                msg.channel_id,
            )
            return None

    # ---------- 入隊 -> AgentServer -> 出隊 轉發迴圈 ----------

    async def _forward_loop(self) -> None:
        """迴圈：從 user_messages 取訊息，經 AgentServerClient 發往 AgentServer，將響應寫入 robot_messages.
        支援流式和非流式兩種模式。使用 timeout=None 阻塞等待，保證有訊息時第一時間被喚醒處理；
        stop 時 task 被 cancel 會打斷 get() 並退出。

        支援中斷機制：當收到 CHAT_CANCEL 請求時，會立即取消正在執行的流式任務。
        """
        from jiuwenclaw.common.schema.message import ReqMethod

        while self._running:
            try:
                msg = await self.consume_user_messages(timeout=None)
                if msg is None:
                    continue
                
         
                # 先處理受控通道的 Channel 控制指令（如 /new_session、/mode、/skills list）
                if self._handle_channel_control(msg):
                    # 該訊息僅用於修改 session/mode，已給 Channel 回覆提示，不再轉發給 Agent
                    continue

                # 將當前 Channel 的控制狀態應用到訊息上
                self._apply_channel_state(msg)

                # 檢查是否是中斷請求
                if msg.req_method == ReqMethod.CHAT_ANSWER:
                    agent_msg = await self._prepare_agent_dispatch_message(msg)
                    env = self.message_to_e2a(agent_msg)
                    resp = await self._process_non_stream_request(msg, env)
                    answer_request_id = (msg.params or {}).get("request_id")
                    if self._is_evolution_approval_request_id(answer_request_id):
                        # Check whether the response indicates the approval was actually resolved.
                        resolved = False
                        if resp is not None and hasattr(resp, "payload") and isinstance(resp.payload, dict):
                            resolved = resp.payload.get("resolved", False) is True
                        if resolved:
                            queued_payload = self._finish_evolution_approval_if_current(
                                msg.session_id,
                                str(answer_request_id or ""),
                            )
                            queued_input = str((queued_payload or {}).get("new_input") or "").strip()
                            queued_attachments = (queued_payload or {}).get("attachments")
                            if queued_input:
                                queued_msg = self._build_queued_chat_send_message(
                                    msg,
                                    queued_input,
                                    queued_attachments if isinstance(queued_attachments, list) else None,
                                )
                                self._user_messages.put_nowait(queued_msg)
                                logger.info(
                                    "[MessageHandler] evolution approval answered (resolved), "
                                    "queued supplement dispatched: id=%s session_id=%s",
                                    queued_msg.id,
                                    msg.session_id,
                                )
                        else:
                            logger.info(
                                "[MessageHandler] evolution approval answered but not resolved: "
                                "id=%s session_id=%s request_id=%s",
                                msg.id,
                                msg.session_id,
                                answer_request_id,
                            )
                    continue

                if msg.req_method == ReqMethod.CHAT_CANCEL:
                    logger.info(
                        "[MessageHandler] 收到中斷請求: id=%s channel_id=%s",
                        msg.id, msg.channel_id,
                    )
                    new_input = (msg.params or {}).get("new_input")
                    has_new_input = isinstance(new_input, str) and new_input.strip()
                    raw_attachments = (msg.params or {}).get("attachments")
                    supplement_attachments = (
                        raw_attachments if isinstance(raw_attachments, list) else None
                    )
                    intent = (msg.params or {}).get("intent", "cancel")

                    if has_new_input:
                        if (
                            self._is_session_evolution_in_progress(msg.session_id)
                            or (
                                isinstance(msg.session_id, str)
                                and msg.session_id in self._pending_evolution_approval
                            )
                        ):
                            queued_input = new_input.strip()
                            self._queue_supplement_input(
                                msg.session_id,
                                queued_input,
                                supplement_attachments,
                            )
                            logger.info(
                                "[MessageHandler] evolution phase pending, queue supplement input: session_id=%s",
                                msg.session_id,
                            )
                            await self._send_interrupt_result_notification(
                                msg.id,
                                msg.channel_id,
                                msg.session_id,
                                "supplement",
                                message="已加入佇列，等待演進完成",
                            )
                            continue

                        # 有新輸入：取消舊任務 → 保留 todo → 啟動新任務（非併發）

                        # 1. 取消 gateway 側當前 session 相關的流式任務（而非所有任務）
                        tasks_to_cancel = []
                        rids_cancelled = []
                        current_sid = msg.session_id
                        for rid, task in list(self._stream_tasks.items()):
                            # 只取消與當前 session_id 關聯的任務
                            if self._stream_sessions.get(rid) != current_sid:
                                continue
                            if not task.done():
                                logger.info(
                                    "[MessageHandler] supplement: 取消流式任務 request_id=%s session_id=%s",
                                    rid, current_sid,
                                )
                                task.cancel()
                                tasks_to_cancel.append(task)
                                rids_cancelled.append(rid)
                        if tasks_to_cancel:
                            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

                        # 2. 通知前端 supplement（前端據此判斷 is_processing 狀態）
                        await self._send_interrupt_result_notification(
                            msg.id, msg.channel_id, msg.session_id, "supplement",
                        )

                        # 3. 傳送 supplement intent 到 AgentServer（取消任務但保留 todo）
                        #    用 await 確保 agent 側先完成取消再啟動新任務
                        from jiuwenclaw.common.e2a.gateway_normalize import e2a_from_agent_fields

                        agent_msg = await self._prepare_agent_dispatch_message(msg)
                        supplement_env = e2a_from_agent_fields(
                            request_id=f"supplement_{int(time.time() * 1000):x}",
                            channel_id=msg.channel_id,
                            session_id=agent_msg.session_id,
                            req_method=ReqMethod.CHAT_CANCEL,
                            params={"intent": "supplement", "session_id": agent_msg.session_id},
                            is_stream=False,
                            timestamp=time.time(),
                        )
                        try:
                            await self._send_interrupt_to_agent(supplement_env)
                        except Exception:
                            pass  # 即使失敗也繼續啟動新任務

                        # 4. 入隊新任務（單一任務，不併發）
                        from jiuwenclaw.common.schema.message import Message

                        new_req_id = f"req_{int(time.time() * 1000):x}_{msg.id}"
                        sup_meta = dict(msg.metadata) if msg.metadata else None
                        new_msg = Message(
                            id=new_req_id,
                            type="req",
                            channel_id=msg.channel_id,
                            session_id=msg.session_id,
                            params={
                                "query": new_input.strip(),
                                "session_id": msg.session_id,
                                "is_supplement": True,
                                **(
                                    {"model_name": (msg.params or {}).get("model_name")}
                                    if (msg.params or {}).get("model_name")
                                    else {}
                                ),
                                **(
                                    {"attachments": supplement_attachments}
                                    if supplement_attachments
                                    else {}
                                ),
                            },
                            timestamp=time.time(),
                            ok=True,
                            req_method=ReqMethod.CHAT_SEND,
                            is_stream=True,
                            provider=msg.provider,
                            chat_id=msg.chat_id,
                            user_id=msg.user_id,
                            bot_id=msg.bot_id,
                            metadata=sup_meta,
                        )
                        self._user_messages.put_nowait(new_msg)
                        logger.info(
                            "[MessageHandler] supplement: 舊任務已取消，新任務已入隊: id=%s session_id=%s",
                            new_msg.id, msg.session_id,
                        )

                    elif intent == "cancel":
                        await self._cancel_agent_work_for_session(msg, msg.session_id)

                    elif intent in ("pause", "resume"):
                        # 暫停/恢復：不取消流式任務，轉發給 AgentServer 處理 ReAct 迴圈
                        agent_msg = await self._prepare_agent_dispatch_message(msg)
                        env_interrupt = self.message_to_e2a(agent_msg)
                        asyncio.create_task(self._send_interrupt_to_agent(env_interrupt))
                        # 通知前端狀態變更
                        await self._send_interrupt_result_notification(
                            msg.id, msg.channel_id, msg.session_id, intent,
                        )

                    continue

                # ---- Inbound Pipeline（數字分身入站過濾）----
                if self._inbound_pipeline is not None and msg.req_method == ReqMethod.CHAT_SEND:
                    try:
                        should_forward = await self._inbound_pipeline.apply(msg)
                    except Exception:
                        logger.exception("Inbound pipeline error, fallback to forwarding")
                    else:
                        if not should_forward:
                            continue  # 不相關訊息，跳過

                # ---- Resolve @file references in chat.send content ----
                if msg.req_method == ReqMethod.CHAT_SEND and msg.params:
                    content = msg.params.get("query") or msg.params.get("content") or ""
                    attachments = msg.params.get("attachments")
                    cwd = None
                    if isinstance(msg.metadata, dict):
                        cwd = msg.metadata.get("cwd")
                    enriched = content
                    if attachments:
                        enriched = self._resolve_structured_attachments(
                            content,
                            attachments,
                            cwd=cwd,
                        )
                    elif content and "@" in content:
                        enriched = self._resolve_at_file_references(content, cwd=cwd)
                    if enriched != content:
                        msg.params = dict(msg.params)
                        msg.params["query"] = enriched
                        if "content" in msg.params:
                            msg.params["content"] = enriched
                        logger.info(
                            "[MessageHandler] attachments resolved in chat.send: id=%s",
                            msg.id,
                        )

                logger.info(
                    "[MessageHandler] 從 user_messages 取出，發往 AgentServer: id=%s channel_id=%s is_stream=%s",
                    msg.id, msg.channel_id, msg.is_stream,
                )
                agent_msg = await self._prepare_agent_dispatch_message(msg)
                await self._trigger_before_chat_request_hook(agent_msg)
                env = self.message_to_e2a(agent_msg)
                stream_rid = env.request_id or msg.id
                try:
                    if env.is_stream:
                        # 流式處理：啟動後臺任務，支援多工併發
                        # 通知前端新任務開始處理
                        await self._send_processing_status(
                            stream_rid, msg.session_id, msg.channel_id, is_processing=True,
                        )
                        task = asyncio.create_task(
                            self.process_stream(env, msg.session_id, msg.metadata)
                        )
                        self._stream_tasks[stream_rid] = task
                        self._stream_sessions[stream_rid] = msg.session_id
                        self._stream_metadata[stream_rid] = msg.metadata
                        self._stream_modes[stream_rid] = (
                            msg.params.get("mode", "plan") if isinstance(msg.params, dict) else "plan"
                        )
                        logger.info(
                            "[MessageHandler] Stream 任務已啟動（後臺執行）: request_id=%s channel_id=%s 當前併發=%d",
                            stream_rid, msg.channel_id, len(self._stream_tasks),
                        )
                        # 不 await，讓流式任務在後臺執行，_forward_loop 繼續處理下一個訊息
                    elif self._non_stream_rpc_may_run_parallel(env):
                        # 非流式且非聊天：後臺執行，避免慢 RPC（如 SkillNet）阻塞佇列中的其它請求
                        method_label = env.method or "none"
                        asyncio.create_task(
                            self._process_non_stream_request(msg, env),
                            name=f"gw-nonstr-{method_label}-{stream_rid[:24]}",
                        )
                        logger.info(
                            "[MessageHandler] 非流式 RPC 已後臺執行: id=%s method=%s",
                            msg.id,
                            method_label,
                        )
                    else:
                        await self._process_non_stream_request(msg, env)
                except Exception as e:
                    logger.exception("AgentServer send_request failed for %s: %s", msg.id, e)
                    err_msg = self._build_error_out_message(msg, e)
                    await self.publish_robot_messages(err_msg)
                    logger.info(
                            "[MessageHandler] 錯誤響應已寫入 robot_messages: id=%s channel_id=%s",
                        msg.id, msg.channel_id,
                    )
            except asyncio.CancelledError:
                break

    async def process_stream(
        self,
        env: "E2AEnvelope",
        session_id: str | None,
        request_metadata: dict[str, Any] | None,
    ) -> None:
        """處理流式請求，逐個 chunk 寫入 robot_messages.

        這個方法被包裝為 Task，在後臺執行，可以被隨時取消。
        遙測可透過替換類上的 ``process_stream`` 進行打點。
        """
        rid = env.request_id or ""
        channel_id = env.channel or ""
        cancelled = False
        has_processing_status_false = False  # 追蹤 AgentServer 是否已傳送 processing_status=false
        try:
            async for chunk in self._agent_client.send_request_stream(env):
                # 跳過終止 chunk（僅作為流結束訊號，不含實際資料）
                if self._is_terminal_stream_chunk(chunk):
                    logger.debug(
                        "[MessageHandler] 跳過終止 chunk: request_id=%s",
                        chunk.request_id,
                    )
                    continue
                await self._handle_evolution_chunk(chunk, session_id, request_metadata)
                # 攜帶 request metadata，供 Feishu/Xiaoyi 用平臺身份回發
                # 檢查是否是 processing_status=false 事件
                payload = chunk.payload or {}
                if isinstance(payload, dict):
                    if payload.get("event_type") == "chat.processing_status":
                        if payload.get("is_processing") is False:
                            has_processing_status_false = True

                out = self._chunk_to_message(
                    chunk,
                    session_id=session_id,
                    metadata=request_metadata,
                )
                await self.publish_robot_messages(out)
                logger.debug(
                    "[MessageHandler] Stream chunk 已寫入 robot_messages: request_id=%s event_type=%s",
                    chunk.request_id, out.event_type,
                )
            logger.info(
                "[MessageHandler] Stream 正常完成: request_id=%s",
                rid,
            )
        except asyncio.CancelledError:
            cancelled = True
            logger.info(
                "[MessageHandler] Stream 被取消: request_id=%s",
                rid,
            )
            await self._publish_stream_cancelled_final(
                rid, channel_id, session_id, request_metadata,
            )
            raise  # 重新丟擲，讓呼叫者知道任務被取消
        finally:
            # 清理狀態
            self._stream_tasks.pop(rid, None)
            self._stream_sessions.pop(rid, None)
            self._stream_metadata.pop(rid, None)
            self._stream_modes.pop(rid, None)
            if session_id is not None and session_id not in self._stream_sessions.values():
                # Fallback cleanup when stream exits unexpectedly without evolution end signal.
                self._clear_session_evolution_in_progress(session_id)
            logger.debug(
                "[MessageHandler] Stream 任務狀態已清理: request_id=%s",
                rid,
            )
            # 所有流式任務正常結束後，通知前端全部處理完成
            # 只有當 AgentServer 沒有傳送過 processing_status=false 時才傳送
            if not cancelled and not self._stream_tasks and not has_processing_status_false:
                await self._send_processing_status(
                    rid, session_id, channel_id, is_processing=False,
                )
                logger.info(
                    "[MessageHandler] 所有流式任務已完成，已傳送 is_processing=false: session_id=%s",
                    session_id,
                )

    async def _send_stream_cancelled_notification(
        self, request_id: str | None, channel_id: str, session_id: str | None
    ) -> None:
        """傳送流式任務被取消的通知到客戶端."""
        if not request_id:
            return

        from jiuwenclaw.common.schema.message import Message, EventType

        cancel_msg = Message(
            id=request_id,
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "event_type": "chat.interrupt_result",
                "intent": "cancel",
                "success": True,
                "message": "任務已取消",
            },
            event_type=EventType.CHAT_INTERRUPT_RESULT,
            metadata=None,
        )
        await self.publish_robot_messages(cancel_msg)
        logger.info(
            "[MessageHandler] 已傳送流式任務取消通知: request_id=%s",
            request_id,
        )

    async def _send_interrupt_to_agent(self, env: "E2AEnvelope") -> None:
        """Fire-and-forget: 傳送中斷請求到 AgentServer，不阻塞轉發迴圈."""
        try:
            resp = await self._agent_client.send_request(env)
            logger.info(
                "[MessageHandler] AgentServer 中斷響應(已丟棄): request_id=%s ok=%s",
                resp.request_id, resp.ok,
            )
        except Exception as e:
            logger.warning("[MessageHandler] AgentServer 中斷請求失敗(忽略): %s", e)

    async def _send_interrupt_result_notification(
        self,
        request_id: str,
        channel_id: str,
        session_id: str | None,
        intent: str,
        message: str | None = None,
        success: bool = True,
    ) -> None:
        """傳送 interrupt_result 事件到前端（pause / resume 等）."""
        from jiuwenclaw.common.schema.message import Message, EventType

        success_messages_map = {
            "pause": "任務已暫停",
            "resume": "任務已恢復",
            "cancel": "任務已取消",
            "supplement": "任務已切換",
        }
        failure_messages_map = {
            "pause": "任務暫停失敗",
            "resume": "任務恢復失敗",
            "cancel": "任務終止失敗",
            "supplement": "任務切換失敗",
        }
        notify_msg = Message(
            id=request_id,
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "event_type": "chat.interrupt_result",
                "intent": intent,
                "success": success,
                "message": message
                or (
                    success_messages_map.get(intent, "任務已中斷")
                    if success
                    else failure_messages_map.get(intent, "任務中斷失敗")
                ),
            },
            event_type=EventType.CHAT_INTERRUPT_RESULT,
            metadata=None,
        )
        await self.publish_robot_messages(notify_msg)
        logger.info(
            "[MessageHandler] 已傳送 interrupt_result 通知: intent=%s request_id=%s",
            intent, request_id,
        )

    async def _send_processing_status(
        self, request_id: str, session_id: str | None, channel_id: str, *, is_processing: bool,
    ) -> None:
        """傳送 chat.processing_status 事件到客戶端."""
        from jiuwenclaw.common.schema.message import Message, EventType

        status_msg = Message(
            id=request_id,
            type="event",
            channel_id=channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "event_type": "chat.processing_status",
                "session_id": session_id,
                "is_processing": is_processing,
                "is_complete": not is_processing
            },
            event_type=EventType.CHAT_PROCESSING_STATUS,
            metadata=None,
        )
        await self.publish_robot_messages(status_msg)

    def _build_error_out_message(self, msg: "Message", error: Exception) -> "Message":
        from jiuwenclaw.common.schema.message import Message

        return Message(
            id=msg.id,
            type="res",
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            params={},
            timestamp=time.time(),
            ok=False,
            payload={"error": str(error)},
            metadata=msg.metadata,
        )

    async def start_forwarding(self) -> None:
        """啟動入隊 -> AgentServer -> 出隊 的轉發任務."""
        if self._forward_task is not None:
            return
        self._running = True
        self._forward_task = asyncio.create_task(self._forward_loop())
        logger.info("[MessageHandler] 轉發迴圈已啟動 (_user_messages -> AgentServer -> _robot_messages)")

    async def stop_forwarding(self) -> None:
        """停止轉發任務."""
        self._running = False

        # 取消所有流式任務
        for rid, task in list(self._stream_tasks.items()):
            if not task.done():
                logger.info("[MessageHandler] 停止時取消流式任務: request_id=%s", rid)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._stream_tasks.clear()
        self._stream_sessions.clear()
        self._stream_metadata.clear()
        self._stream_modes.clear()
        self._session_evolution_in_progress.clear()
        self._pending_evolution_approval.clear()
        self._queued_supplement_input.clear()

        # 取消轉發迴圈
        if self._forward_task is not None:
            self._forward_task.cancel()
            try:
                await self._forward_task
            except asyncio.CancelledError:
                pass
            self._forward_task = None

        logger.info("[MessageHandler] 轉發迴圈已停止")

    # ---------- 狀態 ----------

    @property
    def user_messages_size(self) -> int:
        return self._user_messages.qsize()

    @property
    def robot_messages_size(self) -> int:
        return self._robot_messages.qsize()
