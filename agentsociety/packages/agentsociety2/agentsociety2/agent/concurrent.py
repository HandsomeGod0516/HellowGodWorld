"""併發控制模組。

提供Agent的併發執行、限流和任務排程功能。

模組結構
========
- :class:`Priority`: 任務優先順序列舉
- :class:`PrioritizedTask`: 帶優先順序的任務封裝
- :class:`PriorityScheduler`: 優先順序排程器
- :class:`ParallelExecutor`: 並行工具執行器
- :class:`RateLimiter`: 令牌桶限流器
- :class:`TaskManager`: 後臺工作管理員
- :class:`DeadlockDetector`: 死鎖檢測器

設計原則
========
1. 無全域性單例：每個 Agent 擁有獨立的併發控制例項
2. 優先順序排程：高優先順序任務優先執行
3. 死鎖檢測：基於超時的簡單死鎖檢測
4. 結構化併發：任務生命週期清晰可控
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Coroutine, Optional, TypeVar

from .config import AgentConfig

T = TypeVar("T")


class Priority(IntEnum):
    """任務優先順序，數值越大優先順序越高。"""

    LOW = 0
    NORMAL = 10
    HIGH = 20
    CRITICAL = 30


@dataclass(order=True)
class PrioritizedTask:
    """帶優先順序的任務封裝。"""

    priority: int
    task_id: str = field(compare=False)
    coro: Coroutine = field(compare=False)
    created_at: float = field(default_factory=time.monotonic, compare=False)


class PriorityScheduler:
    """優先順序排程器。

    按優先順序順序執行任務，支援併發限制。

    Example::

        scheduler = PriorityScheduler(max_concurrent=5)
        await scheduler.submit("task1", my_coro(), Priority.HIGH)
        result = await scheduler.get_result("task1")
    """

    def __init__(self, max_concurrent: int = 10):
        self._max_concurrent = max_concurrent
        self._pending: list[PrioritizedTask] = []
        self._running: dict[str, asyncio.Task] = {}
        self._results: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def submit(
        self,
        task_id: str,
        coro: Coroutine,
        priority: Priority = Priority.NORMAL,
    ) -> None:
        """提交任務到排程佇列。

        :param task_id: 任務唯一標識。
        :param coro: 協程。
        :param priority: 優先順序。
        """
        task = PrioritizedTask(priority=priority.value, task_id=task_id, coro=coro)
        async with self._lock:
            self._pending.append(task)
            self._pending.sort(reverse=True)
            asyncio.create_task(self._run_next())

    async def _run_next(self) -> None:
        """執行下一個待處理任務。"""
        async with self._lock:
            if not self._pending:
                return
            if len(self._running) >= self._max_concurrent:
                return
            ptask = self._pending.pop(0)

        async with self._semaphore:
            task = asyncio.create_task(ptask.coro)
            async with self._lock:
                self._running[ptask.task_id] = task

            try:
                result = await task
                async with self._lock:
                    self._results[ptask.task_id] = {"ok": True, "result": result}
            except Exception as e:
                async with self._lock:
                    self._results[ptask.task_id] = {"ok": False, "error": str(e)}
            finally:
                async with self._lock:
                    self._running.pop(ptask.task_id, None)

    async def get_result(self, task_id: str, timeout: float = 30.0) -> dict[str, Any]:
        """獲取任務結果。

        :param task_id: 任務ID。
        :param timeout: 超時時間（秒）。
        :return: 執行結果。
        :raises asyncio.TimeoutError: 超時。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            async with self._lock:
                if task_id in self._results:
                    return self._results.pop(task_id)
                if task_id not in self._running and task_id not in [
                    t.task_id for t in self._pending
                ]:
                    return {"ok": False, "error": "Task not found"}
            await asyncio.sleep(0.1)
        raise asyncio.TimeoutError(f"Task {task_id} timed out")

    @property
    def pending_count(self) -> int:
        """待處理任務數量。"""
        return len(self._pending)

    @property
    def running_count(self) -> int:
        """執行中任務數量。"""
        return len(self._running)


class ParallelExecutor:
    """並行工具執行器。

    自動識別可安全並行執行的工具，最佳化執行效率。

    可安全並行的工具：
        - workspace_read
        - glob
        - grep
        - workspace_list
        - read_skill

    Example::

        executor = ParallelExecutor(config)
        results = await executor.execute(tools, my_executor)
    """

    PARALLEL_SAFE = {"workspace_read", "glob", "grep", "workspace_list", "read_skill"}

    def __init__(self, config: AgentConfig):
        self.config = config
        self._semaphore = asyncio.Semaphore(config.concurrency.max_parallel_tools)

    def is_safe(self, tool: str) -> bool:
        """檢查工具是否可安全並行。"""
        return tool in self.PARALLEL_SAFE

    async def execute(
        self,
        tools: list[tuple[str, dict[str, Any]]],
        executor: Callable[[str, dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """執行工具列表。

        可安全並行的工具會並行執行，其他順序執行。

        :param tools: (工具名, 引數) 元組列表。
        :param executor: 單個工具執行函式。
        :return: 結果列表，與輸入順序一致。
        """
        if not tools:
            return []

        parallel = [(i, t, a) for i, (t, a) in enumerate(tools) if self.is_safe(t)]
        sequential = [
            (i, t, a) for i, (t, a) in enumerate(tools) if not self.is_safe(t)
        ]

        results: list[dict[str, Any]] = [{}] * len(tools)

        # 並行執行
        if parallel:
            tasks = [self._exec(executor, t, a) for _, t, a in parallel]
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            for (idx, _, _), result in zip(parallel, outcomes):
                results[idx] = (
                    {"ok": False, "error": str(result)}
                    if isinstance(result, Exception)
                    else result
                )

        # 順序執行
        for idx, tool, args in sequential:
            try:
                results[idx] = await executor(tool, args)
            except Exception as e:
                results[idx] = {"ok": False, "error": str(e)}

        return results

    async def _exec(
        self,
        executor: Callable,
        tool: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """帶訊號量保護的執行。"""
        async with self._semaphore:
            return await executor(tool, args)


class RateLimiter:
    """令牌桶限流器。

    控制操作速率，防止過載。

    Example::

        limiter = RateLimiter(rps=10.0)
        await limiter.acquire()
    """

    def __init__(self, rps: float, burst: int = 10):
        self.rate = rps
        self.burst = burst
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """等待可用令牌。

        使用非阻塞方式計算等待時間，避免持鎖 sleep。
        """
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.burst, self._tokens + (now - self._last) * self.rate
                )
                self._last = now

                if self._tokens >= 1:
                    self._tokens -= 1
                    return

                # 計算需要等待的時間
                wait_time = (1 - self._tokens) / self.rate

            # 釋放鎖後再 sleep，避免阻塞其他請求
            await asyncio.sleep(wait_time)


class TaskManager:
    """後臺工作管理員。

    管理後臺非同步任務，支援啟動、取消和等待。

    Example::

        manager = TaskManager()
        await manager.start("task1", my_coroutine())
        await manager.cancel("task1")
    """

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def start(self, task_id: str, coro: Coroutine) -> None:
        """啟動後臺任務。

        :param task_id: 任務ID。
        :param coro: 協程。
        :raises ValueError: 任務已存在。
        """
        async with self._lock:
            if task_id in self._tasks and not self._tasks[task_id].done():
                raise ValueError(f"Task {task_id} already running")
            self._tasks[task_id] = asyncio.create_task(coro)

    async def wait(self, task_id: str, timeout: Optional[float] = None) -> Any:
        """等待任務完成。

        :param task_id: 任務ID。
        :param timeout: 超時時間。
        :return: 任務結果。
        :raises asyncio.TimeoutError: 超時。
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task {task_id} not found")

        return await asyncio.wait_for(task, timeout=timeout)

    async def cancel(self, task_id: str) -> bool:
        """取消後臺任務。

        :param task_id: 任務ID。
        :return: 是否成功取消。
        """
        async with self._lock:
            if task_id not in self._tasks:
                return False
            task = self._tasks[task_id]
            if task.done():
                return False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return True

    async def cancel_all(self) -> None:
        """取消所有後臺任務。"""
        async with self._lock:
            for task in self._tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
            self._tasks.clear()

    def list(self) -> list[str]:
        """列出所有任務ID。"""
        return list(self._tasks.keys())

    @property
    def running_count(self) -> int:
        """執行中任務數量。"""
        return sum(1 for t in self._tasks.values() if not t.done())


class DeadlockDetector:
    """簡單死鎖檢測器。

    基於超時檢測潛在死鎖，適用於長時間執行的任務監控。

    Example::

        detector = DeadlockDetector(timeout=60.0)
        detector.register("operation1")
        # ... 操作完成後
        detector.complete("operation1")
        # 檢查是否有超時操作
        deadlocked = detector.check()
    """

    def __init__(self, timeout: float = 60.0):
        self._timeout = timeout
        self._operations: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def register(self, op_id: str) -> None:
        """註冊操作開始。

        :param op_id: 操作ID。
        """
        self._operations[op_id] = time.monotonic()

    def complete(self, op_id: str) -> None:
        """標記操作完成。

        :param op_id: 操作ID。
        """
        self._operations.pop(op_id, None)

    def check(self) -> list[str]:
        """檢查超時操作。

        :return: 超時操作ID列表。
        """
        now = time.monotonic()
        return [
            op_id
            for op_id, start in self._operations.items()
            if now - start > self._timeout
        ]

    @property
    def active_count(self) -> int:
        """活躍運算元量。"""
        return len(self._operations)
