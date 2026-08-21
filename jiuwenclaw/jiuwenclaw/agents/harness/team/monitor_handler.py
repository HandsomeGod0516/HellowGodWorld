# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team Monitor 處理器.

處理 Team Monitor 的事件流和狀態查詢，將團隊狀態轉換為前端可消費的格式.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from openjiuwen.agent_teams.monitor import create_monitor, TeamMonitor
from openjiuwen.agent_teams.monitor.models import MonitorEvent, MonitorEventType
from openjiuwen.agent_teams.agent.team_agent import TeamAgent

from jiuwenclaw.agents.harness.team.event_types import (
    get_team_event_type,
    get_event_category,
)

logger = logging.getLogger(__name__)


class TeamMonitorHandler:
    """Team Monitor 處理器.

    封裝 Monitor 的建立、事件處理和狀態查詢，提供簡化的介面給前端.
    """

    def __init__(
        self,
        team_agent: TeamAgent,
        session_id: str,
    ):
        """初始化處理器.

        Args:
            team_agent: TeamAgent 例項
            session_id: 會話 ID
        """
        self._team_agent = team_agent
        self._session_id = session_id
        self._monitor: TeamMonitor | None = None
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._event_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """啟動 Monitor."""
        if self._running:
            return

        try:
            from openjiuwen.agent_teams.spawn.context import set_session_id, reset_session_id
            
            token = set_session_id(self._session_id)
            try:
                self._monitor = create_monitor(self._team_agent)
                await self._monitor.start()
            finally:
                reset_session_id(token)
            
            self._running = True

            # 啟動事件收集任務
            self._event_task = asyncio.create_task(self._collect_events())

            logger.info(
                "[TeamMonitorHandler] Monitor 啟動成功: session_id=%s",
                self._session_id,
            )

        except Exception as e:
            logger.error(
                "[TeamMonitorHandler] Monitor 啟動失敗: session_id=%s, error=%s",
                self._session_id,
                e,
            )
            raise

    async def stop(self) -> None:
        """停止 Monitor."""
        self._running = False

        if self._event_task is not None:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
            self._event_task = None

        if self._monitor is not None:
            try:
                await self._monitor.stop()
            except Exception as e:
                logger.warning(
                    "[TeamMonitorHandler] Monitor 停止失敗: session_id=%s, error=%s",
                    self._session_id,
                    e,
                )
            self._monitor = None

        logger.info(
            "[TeamMonitorHandler] Monitor 已停止: session_id=%s",
            self._session_id,
        )

    async def _collect_events(self) -> None:
        """後臺任務：收集 Monitor 事件."""
        if self._monitor is None:
            return

        try:
            async for event in self._monitor.events():
                if not self._running:
                    break

                event_dict = await self._convert_event_to_dict(event)
                if event_dict:
                    await self._event_queue.put(event_dict)

        except Exception as e:
            logger.error(
                "[TeamMonitorHandler] 事件收集失敗: session_id=%s, error=%s",
                self._session_id,
                e,
            )

    @staticmethod
    def _handle_member_spawned(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        """處理成員建立事件."""
        base["member_id"] = event.member_id
        return base

    @staticmethod
    def _handle_member_status_changed(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        """處理成員狀態變更事件."""
        base.update({
            "member_id": event.member_id,
            "old_status": event.old_status,
            "new_status": event.new_status,
        })
        return base

    @staticmethod
    def _handle_member_execution_changed(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        """處理成員執行狀態變更事件."""
        base.update({
            "member_id": event.member_id,
            "old_status": event.old_status,
            "new_status": event.new_status,
        })
        return base

    @staticmethod
    def _handle_member_restarted(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        """處理成員重啟事件."""
        base.update({
            "member_id": event.member_id,
            "reason": event.reason,
            "restart_count": event.restart_count,
        })
        return base

    @staticmethod
    def _handle_member_shutdown(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        """處理成員關閉事件."""
        base.update({
            "member_id": event.member_id,
            "force": event.force,
        })
        return base

    @staticmethod
    def _handle_task_created(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        """處理任務建立事件."""
        base.update({
            "task_id": event.task_id,
            "status": event.status,
        })
        return base

    @staticmethod
    def _handle_task_claimed(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        """處理任務認領事件."""
        base["task_id"] = event.task_id
        return base

    @staticmethod
    def _handle_task_completed(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        """處理任務完成事件."""
        base["task_id"] = event.task_id
        return base

    @staticmethod
    def _handle_task_cancelled(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        """處理任務取消事件."""
        base["task_id"] = event.task_id
        return base

    @staticmethod
    def _handle_task_unblocked(base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        """處理任務解除阻塞事件."""
        base["task_id"] = event.task_id
        return base

    async def _handle_message(self, base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        """處理點對點訊息事件."""
        message_content = await self._get_message_content(event.message_id)
        base.update({
            "message_id": event.message_id,
            "from_member": event.from_member,
            "to_member": event.to_member,
            "content": message_content,
        })
        return base

    async def _handle_broadcast(self, base: dict[str, Any], event: MonitorEvent) -> dict[str, Any]:
        """處理廣播訊息事件."""
        message_content = await self._get_message_content(event.message_id)
        base.update({
            "message_id": event.message_id,
            "from_member": event.from_member,
            "content": message_content,
        })
        return base

    async def _get_message_content(self, message_id: str | None) -> str:
        """獲取訊息內容.

        Args:
            message_id: 訊息 ID

        Returns:
            訊息內容，如果獲取失敗返回空字串
        """
        if not message_id or not self._monitor:
            return ""

        try:
            from openjiuwen.agent_teams.spawn.context import set_session_id, reset_session_id
            
            token = set_session_id(self._session_id)
            try:
                messages = await self._monitor.get_messages()
                for message in messages:
                    if message.message_id == message_id:
                        return message.content or ""
                return ""
            finally:
                reset_session_id(token)
        except Exception as e:
            logger.warning(
                "[TeamMonitorHandler] 查詢訊息內容失敗: message_id=%s, error=%s",
                message_id,
                e,
            )
            return ""

    async def _convert_event_to_dict(self, event: MonitorEvent) -> dict[str, Any] | None:
        """將 MonitorEvent 轉換為字典格式.

        Args:
            event: MonitorEvent 例項

        Returns:
            事件字典，如果事件型別不需要處理返回 None
        """
        team_event_type = get_team_event_type(event.event_type)
        if team_event_type is None:
            return None

        event_category = get_event_category(team_event_type)

        event_data: dict[str, Any] = {
            "type": team_event_type.value,
            "team_id": event.team_id,
        }

        if event.member_id:
            event_data["member_id"] = event.member_id

        event_handlers = {
            MonitorEventType.MEMBER_SPAWNED: self._handle_member_spawned,
            MonitorEventType.MEMBER_STATUS_CHANGED: self._handle_member_status_changed,
            MonitorEventType.MEMBER_EXECUTION_CHANGED: self._handle_member_execution_changed,
            MonitorEventType.MEMBER_RESTARTED: self._handle_member_restarted,
            MonitorEventType.MEMBER_SHUTDOWN: self._handle_member_shutdown,
            MonitorEventType.TASK_CREATED: self._handle_task_created,
            MonitorEventType.TASK_CLAIMED: self._handle_task_claimed,
            MonitorEventType.TASK_COMPLETED: self._handle_task_completed,
            MonitorEventType.TASK_CANCELLED: self._handle_task_cancelled,
            MonitorEventType.TASK_UNBLOCKED: self._handle_task_unblocked,
            MonitorEventType.MESSAGE: self._handle_message,
            MonitorEventType.BROADCAST: self._handle_broadcast,
        }

        handler = event_handlers.get(event.event_type)
        if handler is None:
            return None

        if asyncio.iscoroutinefunction(handler):
            event_data = await handler(event_data, event)
        else:
            event_data = handler(event_data, event)

        return {
            "event_type": event_category.value,
            "event": event_data,
        }

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """獲取事件流.

        Yields:
            事件字典
        """
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=0.1)
                yield event
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(
                    "[TeamMonitorHandler] 事件流錯誤: session_id=%s, error=%s",
                    self._session_id,
                    e,
                )
                break

    @property
    def is_running(self) -> bool:
        """Monitor 是否正在執行."""
        return self._running

    @property
    def team_id(self) -> str | None:
        """團隊 ID."""
        return self._monitor.team_id if self._monitor else None