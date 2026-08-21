# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Session Manager - 管理 session 任務佇列和併發控制.

提供：
- Session 任務佇列管理（先進後出，新任務優先）
- Session 任務執行器
- Session 任務取消
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class SessionManager:
    """Session 工作管理員.

    管理多 session 併發執行，同 session 內任務按先進後出順序執行.
    """

    def __init__(self) -> None:
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._session_priorities: dict[str, int] = {}
        self._session_queues: dict[str, asyncio.PriorityQueue] = {}
        self._session_processors: dict[str, asyncio.Task] = {}

    @staticmethod
    def get_session_id(session_id: str | None) -> str:
        """獲取 session_id，預設為 'default'."""
        return session_id or "default"

    async def cancel_session_task(self, session_id: str, log_msg_prefix: str = "") -> None:
        """取消指定 session 的非流式任務."""
        task = self._session_tasks.get(session_id)
        if task is not None and not task.done():
            logger.info(
                "[SessionManager] %s取消 session 非流式任務: session_id=%s",
                log_msg_prefix, session_id,
            )
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            self._session_tasks[session_id] = None
            logger.info(
                "[SessionManager] %ssession task terminated: session_id=%s",
                log_msg_prefix,
                session_id,
            )

    async def cancel_all_session_tasks(self, log_msg_prefix: str = "") -> None:
        """取消所有 session 的非流式任務."""
        for session_id in list(self._session_tasks.keys()):
            await self.cancel_session_task(session_id, log_msg_prefix)

    async def ensure_session_processor(self, session_id: str) -> None:
        """確保 session 的任務處理器在執行."""
        if session_id not in self._session_processors or self._session_processors[session_id].done():
            self._session_queues[session_id] = asyncio.PriorityQueue()
            self._session_priorities[session_id] = 0

            async def process_session_queue():
                """處理 session 任務佇列（先進後出執行，新任務優先）."""
                queue = self._session_queues[session_id]
                while True:
                    try:
                        priority, task_func = await queue.get()
                        if task_func is None:
                            break

                        self._session_tasks[session_id] = asyncio.create_task(task_func())
                        try:
                            await self._session_tasks[session_id]
                        finally:
                            self._session_tasks[session_id] = None
                            queue.task_done()

                    except asyncio.CancelledError:
                        logger.info("[SessionManager] Session 任務處理器被取消: session_id=%s", session_id)
                        break
                    except Exception as e:
                        logger.error("[SessionManager] Session 任務處理器異常: %s", e)

                self._session_queues.pop(session_id, None)
                self._session_priorities.pop(session_id, None)
                self._session_tasks.pop(session_id, None)
                self._session_processors.pop(session_id, None)
                logger.info("[SessionManager] Session 任務處理器已關閉: session_id=%s", session_id)

            self._session_processors[session_id] = asyncio.create_task(process_session_queue())

    async def submit_task(
        self,
        session_id: str,
        task_func: Callable[[], Awaitable[Any]],
    ) -> None:
        """提交任務到 session 佇列.

        Args:
            session_id: Session ID.
            task_func: 非同步任務函式.
        """
        await self.ensure_session_processor(session_id)
        self._session_priorities[session_id] -= 1
        priority = self._session_priorities[session_id]
        await self._session_queues[session_id].put((priority, task_func))

    async def submit_and_wait(
        self,
        session_id: str,
        task_func: Callable[[], Awaitable[Any]],
    ) -> Any:
        """提交任務到 session 佇列並等待結果.

        Args:
            session_id: Session ID.
            task_func: 非同步任務函式.

        Returns:
            任務執行結果.
        """
        await self.ensure_session_processor(session_id)
        result_future = asyncio.get_event_loop().create_future()

        async def wrapped_task():
            try:
                result = await task_func()
                result_future.set_result(result)
            except Exception as e:
                result_future.set_exception(e)

        self._session_priorities[session_id] -= 1
        priority = self._session_priorities[session_id]
        await self._session_queues[session_id].put((priority, wrapped_task))

        return await result_future

    def get_current_task(self, session_id: str) -> asyncio.Task | None:
        """獲取當前 session 正在執行的任務."""
        return self._session_tasks.get(session_id)

    def has_active_processor(self, session_id: str) -> bool:
        """檢查 session 是否有活躍的處理器."""
        return (
            session_id in self._session_processors
            and not self._session_processors[session_id].done()
        )