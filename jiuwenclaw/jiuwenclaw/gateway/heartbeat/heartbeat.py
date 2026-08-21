# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Heartbeat - Gateway 內週期性向 AgentServer 傳送探活請求."""

from __future__ import annotations

import logging
import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING
import secrets

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from jiuwenclaw.gateway.routing.agent_client import AgentServerClient

# 心跳請求使用的預設標識，AgentServer 可據此識別探活請求
HEARTBEAT_CHANNEL_ID = "__heartbeat__"

HEARTBEAT_OK = "HEARTBEAT_OK"

# 探活請求傳送的 content，AgentServer 可識別為心跳
HEARTBEAT_PROMPT = "如果你的workspace目錄存在HEARTBEAT.md檔案, 讀取檔案內容並且根據檔案內容執行任務. 如果沒有HEARTBEAT.md檔案, 僅回覆HEARTBEAT_OK"


def normalize_active_hours(active_hours: dict[str, str] | None) -> dict[str, str] | None:
    """將 active_hours 的 start/end 規範為 "HH:MM" 字串。

    YAML 中未加引號的 22:00 會被解析為 1320（60 進位制），此處將數字轉回 "HH:MM"。
    """
    if not active_hours or not isinstance(active_hours, dict):
        return active_hours
    result: dict[str, str] = {}
    for k, v in active_hours.items():
        if k in ("start", "end") and isinstance(v, (int, float)):
            minutes = int(v)
            h, m = divmod(minutes, 60)
            result[k] = f"{h:02d}:{m:02d}"
        elif isinstance(v, str):
            result[k] = v
        else:
            result[k] = str(v) if v is not None else ""
    return result


__all__ = [
    "HEARTBEAT_CHANNEL_ID",
    "HEARTBEAT_PROMPT",
    "HeartbeatConfig",
    "IHeartbeat",
    "GatewayHeartbeatService",
    "normalize_active_hours",
]


@dataclass
class HeartbeatConfig:
    """Heartbeat 配置.

    interval_seconds: 心跳間隔（秒），MUST > 0。
    timeout_seconds: 單次心跳請求超時（秒），可選；若提供則 MUST > 0。
    channel_id: 心跳請求使用的 channel_id，預設 __heartbeat__。
    session_id: 心跳請求使用的 session_id，預設 __heartbeat__。
    relay_channel_id: 將心跳響應內容回傳的 channel_id（如 "web" 對應 WebChannel），
        從 .env 的 HEARTBEAT_RELAY_CHANNEL_ID 讀取；為 None 則不回傳。
    """

    interval_seconds: float
    timeout_seconds: float | None = None
    channel_id: str = HEARTBEAT_CHANNEL_ID
    relay_channel_id: str | None = None
    # 心跳生效時間段，格式為 {"start": "HH:MM", "end": "HH:MM"}；為 None 表示始終生效
    active_hours: dict[str, str] | None = None


class IHeartbeat(ABC):
    """Heartbeat 介面.

    按配置週期定時向 AgentServer 傳送探活請求；
    不向任何 Channel 下發訊息，成功/失敗僅用於內部狀態或回撥。
    """

    @abstractmethod
    async def start(self) -> None:
        """啟動週期任務；之後每隔 interval_seconds 執行一次心跳."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """停止週期任務，不再傳送心跳."""
        ...

    @abstractmethod
    def is_running(self) -> bool:
        """返回週期任務是否正在執行."""
        ...


class GatewayHeartbeatService(IHeartbeat):
    """
    週期性向 AgentServer 傳送探活請求的 IHeartbeat 實現。

    固定間隔執行迴圈，每次 _tick 傳送一次請求；
    請求使用 HeartbeatConfig 中的 channel_id/session_id，不向任何 Channel 下發響應。

    判斷是否成功：① 看日誌：成功會打 INFO「Gateway heartbeat OK」，失敗會打 WARNING；
    ② 程式碼檢查：用 last_tick_ok（True/False/None）、last_tick_at（最近一次執行時間）判斷。
    """

    def __init__(
            self,
            agent_client: "AgentServerClient",
            config: HeartbeatConfig,
            message_handler: "MessageHandler | None" = None,
    ) -> None:
        self._agent_client = agent_client
        self._config = config
        self._message_handler = message_handler
        self._running = False
        self._task: asyncio.Task | None = None
        # 最近一次心跳結果，便於呼叫方判斷是否成功
        self._last_tick_ok: bool | None = None
        self._last_tick_at: float | None = None

    async def start(self) -> None:
        """啟動週期任務；之後每隔 interval_seconds 執行一次心跳."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Gateway heartbeat started (every %.1fs)",
            self._config.interval_seconds,
        )

    async def stop(self) -> None:
        """停止週期任務，不再傳送心跳."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Gateway heartbeat stopped")

    def is_running(self) -> bool:
        """返回週期任務是否正在執行."""
        return self._running

    async def _run_loop(self) -> None:
        """主迴圈：每隔 interval_seconds 執行一次 _tick."""
        while self._running:
            try:
                await asyncio.sleep(self._config.interval_seconds)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Gateway heartbeat loop error: %s", e)

    async def _tick(self) -> None:
        """執行一次探活：構造 E2A 發往 AgentServer，不向 Channel 下發."""
        from jiuwenclaw.common.e2a.gateway_normalize import e2a_from_agent_fields

        # 若當前時間不在 active_hours 配置範圍內，則跳過本次心跳
        if not self._is_active_now():
            logger.debug(
                "Gateway heartbeat skipped due to inactive hours: %r",
                self._config.active_hours,
            )
            return

        ts = format(int(time.time() * 1000), "x")
        suffix = secrets.token_hex(3)
        request_id = f"heartbeat-{ts}_{suffix}"
        session_id = f"heartbeat_{ts}_{suffix}"
        envelope = e2a_from_agent_fields(
            request_id=request_id,
            channel_id=self._config.channel_id,
            session_id=session_id,
            params={
                "heartbeat": HEARTBEAT_PROMPT,
                "run": {
                    "kind": "heartbeat",
                    "context": {
                        "reason": "interval",
                        "session_id": session_id,
                    },
                },
            },
        )
        try:
            if self._config.timeout_seconds is not None and self._config.timeout_seconds > 0:
                resp = await asyncio.wait_for(
                    self._agent_client.send_request(envelope),
                    timeout=self._config.timeout_seconds,
                )
            else:
                resp = await self._agent_client.send_request(envelope)
            self._last_tick_at = time.time()
            self._last_tick_ok = True
            payload = resp.payload if isinstance(resp.payload, dict) else {}
            heartbeat_raw = payload.get("heartbeat")
            heartbeat_content = heartbeat_raw if isinstance(heartbeat_raw, str) else ""
            if not heartbeat_content:
                # 相容 Agent 在執行 HEARTBEAT.md 任務時返回的 chat 結構：
                # payload = {"content": {"output": "...", "result_type": "answer"}}
                content = payload.get("content")
                if isinstance(content, dict):
                    output = content.get("output")
                    if isinstance(output, str):
                        heartbeat_content = output
                elif isinstance(content, str):
                    heartbeat_content = content
            logger.info("Gateway heartbeat content: %s", heartbeat_content)
            if HEARTBEAT_OK in (heartbeat_content if isinstance(heartbeat_content, str) else "").upper():
                logger.info("Gateway heartbeat OK: request_id=%s (last_tick_at=%.0f)", request_id, self._last_tick_at)
            else:
                logger.info("Gateway heartbeat complete: request_id=%s (last_tick_at=%.0f)", request_id,
                            self._last_tick_at)

            # 將 resp.payload["heartbeat"] 作為 event 型別 Message 回傳到配置的 channel（如 WebChannel）
            if self._config.relay_channel_id and self._message_handler:
                from jiuwenclaw.common.schema.message import Message, EventType
                relay_msg = Message(
                    id=f"heartbeat-relay-{request_id}",
                    type="event",
                    channel_id=self._config.relay_channel_id,
                    session_id=session_id,
                    params={},
                    timestamp=time.time(),
                    ok=True,
                    payload={"heartbeat": heartbeat_content},
                    event_type=EventType.HEARTBEAT_RELAY,
                )
                await self._message_handler.publish_robot_messages(relay_msg)
                logger.debug("Gateway heartbeat relay to channel %s", self._config.relay_channel_id)

        except asyncio.TimeoutError:
            self._last_tick_ok = False
            self._last_tick_at = time.time()
            logger.warning(
                "Gateway heartbeat timeout (request_id=%s, timeout=%.1fs)",
                request_id,
                self._config.timeout_seconds or 0,
            )
        except Exception as e:
            self._last_tick_ok = False
            self._last_tick_at = time.time()
            logger.warning("Gateway heartbeat request failed: %s", e)

    @property
    def last_tick_ok(self) -> bool | None:
        """最近一次心跳是否成功。None 表示尚未執行過任何一次 tick."""
        return self._last_tick_ok

    @property
    def last_tick_at(self) -> float | None:
        """最近一次心跳執行時間（Unix 時間戳）。None 表示尚未執行過."""
        return self._last_tick_at

    def _is_active_now(self) -> bool:
        """根據 active_hours 判斷當前時間心跳是否應當生效."""
        active_hours = normalize_active_hours(self._config.active_hours)
        if not active_hours:
            return True
        try:
            start_str = active_hours.get("start")
            end_str = active_hours.get("end")
            if not (isinstance(start_str, str) and isinstance(end_str, str)):
                return True

            def _parse_hm(s: str) -> int:
                parts = s.split(":", 1)
                if len(parts) != 2:
                    raise ValueError(f"invalid time format: {s!r}")
                h = int(parts[0])
                m = int(parts[1])
                return h * 60 + m

            start_minutes = _parse_hm(start_str)
            end_minutes = _parse_hm(end_str)

            now_struct = time.localtime()
            now_minutes = now_struct.tm_hour * 60 + now_struct.tm_min

            if start_minutes <= end_minutes:
                # 普通區間：如 08:00-22:00
                return start_minutes <= now_minutes < end_minutes
            # 跨午夜區間：如 22:00-06:00
            return now_minutes >= start_minutes or now_minutes < end_minutes
        except Exception as e:  # noqa: BLE001
            logger.warning("Invalid heartbeat active_hours config %r: %s", active_hours, e)
            # 配置非法時，為避免誤停心跳，按“始終生效”處理
            return True

    def get_heartbeat_conf(self) -> dict[str, object]:
        """返回當前心跳配置摘要（every/target/active_hours）。active_hours 的 start/end 統一為 "HH:MM" 字串。"""
        return {
            "every": self._config.interval_seconds,
            "target": self._config.relay_channel_id,
            "active_hours": normalize_active_hours(self._config.active_hours),
        }

    async def set_heartbeat_conf(
            self,
            *,
            every: float | None = None,
            target: str | None = None,
            active_hours: dict[str, str] | None = None,
    ) -> None:
        """更新心跳配置並在需要時重啟 Heartbeat 服務."""
        updated = False

        if every is not None:
            if every <= 0:
                raise ValueError("heartbeat 'every' must be > 0")
            self._config.interval_seconds = float(every)
            updated = True

        if target is not None:
            self._config.relay_channel_id = target
            updated = True

        if active_hours is not None:
            self._config.active_hours = active_hours
            updated = True

        if not updated:
            return

        was_running = self._running
        if was_running:
            await self.stop()

        # 重置最近一次心跳狀態
        self._last_tick_ok = None
        self._last_tick_at = None

        if was_running:
            await self.start()

        logger.info(
            "Gateway heartbeat config updated: every=%s, target=%s, active_hours=%s",
            self._config.interval_seconds,
            self._config.relay_channel_id,
            self._config.active_hours,
        )
