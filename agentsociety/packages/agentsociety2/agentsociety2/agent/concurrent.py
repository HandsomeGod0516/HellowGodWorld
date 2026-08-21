"""并发控制模块。

提供Agent的并发执行、限流和任务调度功能。

模块结构
========
- :class:`Priority`: 任务优先级枚举
- :class:`PrioritizedTask`: 带优先级的任务封装
- :class:`PriorityScheduler`: 优先级调度器
- :class:`ParallelExecutor`: 并行工具执行器
- :class:`RateLimiter`: 令牌桶限流器
- :class:`TaskManager`: 后台任务管理器
- :class:`DeadlockDetector`: 死锁检测器

设计原则
========
1. 无全局单例：每个 Agent 拥有独立的并发控制实例
2. 优先级调度：高优先级任务优先执行
3. 死锁检测：基于超时的简单死锁检测
4. 结构化并发：任务生命周期清晰可控
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
    """任务优先级，数值越大优先级越高。"""

    LOW = 0
    NORMAL = 10
    HIGH = 20
    CRITICAL = 30


@dataclass(order=True)
class PrioritizedTask:
    """带优先级的任务封装。"""

    priority: int
    task_id: str = field(compare=False)
    coro: Coroutine = field(compare=False)
    created_at: float = field(default_factory=time.monotonic, compare=False)


class PriorityScheduler:
    """优先级调度器。

    按优先级顺序执行任务，支持并发限制。

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
        """提交任务到调度队列。

        :param task_id: 任务唯一标识。
        :param coro: 协程。
        :param priority: 优先级。
        """
        task = PrioritizedTask(priority=priority.value, task_id=task_id, coro=coro)
        async with self._lock:
            self._pending.append(task)
            self._pending.sort(reverse=True)
            asyncio.create_task(self._run_next())

    async def _run_next(self) -> None:
        """执行下一个待处理任务。"""
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
        """获取任务结果。

        :param task_id: 任务ID。
        :param timeout: 超时时间（秒）。
        :return: 执行结果。
        :raises asyncio.TimeoutError: 超时。
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
        """待处理任务数量。"""
        return len(self._pending)

    @property
    def running_count(self) -> int:
        """运行中任务数量。"""
        return len(self._running)


class ParallelExecutor:
    """并行工具执行器。

    自动识别可安全并行执行的工具，优化执行效率。

    可安全并行的工具：
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
        """检查工具是否可安全并行。"""
        return tool in self.PARALLEL_SAFE

    async def execute(
        self,
        tools: list[tuple[str, dict[str, Any]]],
        executor: Callable[[str, dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """执行工具列表。

        可安全并行的工具会并行执行，其他顺序执行。

        :param tools: (工具名, 参数) 元组列表。
        :param executor: 单个工具执行函数。
        :return: 结果列表，与输入顺序一致。
        """
        if not tools:
            return []

        parallel = [(i, t, a) for i, (t, a) in enumerate(tools) if self.is_safe(t)]
        sequential = [
            (i, t, a) for i, (t, a) in enumerate(tools) if not self.is_safe(t)
        ]

        results: list[dict[str, Any]] = [{}] * len(tools)

        # 并行执行
        if parallel:
            tasks = [self._exec(executor, t, a) for _, t, a in parallel]
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            for (idx, _, _), result in zip(parallel, outcomes):
                results[idx] = (
                    {"ok": False, "error": str(result)}
                    if isinstance(result, Exception)
                    else result
                )

        # 顺序执行
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
        """带信号量保护的执行。"""
        async with self._semaphore:
            return await executor(tool, args)


class RateLimiter:
    """令牌桶限流器。

    控制操作速率，防止过载。

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

        使用非阻塞方式计算等待时间，避免持锁 sleep。
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

                # 计算需要等待的时间
                wait_time = (1 - self._tokens) / self.rate

            # 释放锁后再 sleep，避免阻塞其他请求
            await asyncio.sleep(wait_time)


class TaskManager:
    """后台任务管理器。

    管理后台异步任务，支持启动、取消和等待。

    Example::

        manager = TaskManager()
        await manager.start("task1", my_coroutine())
        await manager.cancel("task1")
    """

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def start(self, task_id: str, coro: Coroutine) -> None:
        """启动后台任务。

        :param task_id: 任务ID。
        :param coro: 协程。
        :raises ValueError: 任务已存在。
        """
        async with self._lock:
            if task_id in self._tasks and not self._tasks[task_id].done():
                raise ValueError(f"Task {task_id} already running")
            self._tasks[task_id] = asyncio.create_task(coro)

    async def wait(self, task_id: str, timeout: Optional[float] = None) -> Any:
        """等待任务完成。

        :param task_id: 任务ID。
        :param timeout: 超时时间。
        :return: 任务结果。
        :raises asyncio.TimeoutError: 超时。
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task {task_id} not found")

        return await asyncio.wait_for(task, timeout=timeout)

    async def cancel(self, task_id: str) -> bool:
        """取消后台任务。

        :param task_id: 任务ID。
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
        """取消所有后台任务。"""
        async with self._lock:
            for task in self._tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
            self._tasks.clear()

    def list(self) -> list[str]:
        """列出所有任务ID。"""
        return list(self._tasks.keys())

    @property
    def running_count(self) -> int:
        """运行中任务数量。"""
        return sum(1 for t in self._tasks.values() if not t.done())


class DeadlockDetector:
    """简单死锁检测器。

    基于超时检测潜在死锁，适用于长时间运行的任务监控。

    Example::

        detector = DeadlockDetector(timeout=60.0)
        detector.register("operation1")
        # ... 操作完成后
        detector.complete("operation1")
        # 检查是否有超时操作
        deadlocked = detector.check()
    """

    def __init__(self, timeout: float = 60.0):
        self._timeout = timeout
        self._operations: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def register(self, op_id: str) -> None:
        """注册操作开始。

        :param op_id: 操作ID。
        """
        self._operations[op_id] = time.monotonic()

    def complete(self, op_id: str) -> None:
        """标记操作完成。

        :param op_id: 操作ID。
        """
        self._operations.pop(op_id, None)

    def check(self) -> list[str]:
        """检查超时操作。

        :return: 超时操作ID列表。
        """
        now = time.monotonic()
        return [
            op_id
            for op_id, start in self._operations.items()
            if now - start > self._timeout
        ]

    @property
    def active_count(self) -> int:
        """活跃操作数量。"""
        return len(self._operations)
