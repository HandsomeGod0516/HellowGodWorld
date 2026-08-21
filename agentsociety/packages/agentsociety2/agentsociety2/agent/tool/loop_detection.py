"""迴圈檢測服務。

防止 Agent 陷入無效迴圈：工具呼叫迴圈、內容迴圈、錯誤重複。

檢測模式：
1. 連續重複檢測 (AAAA)：相同工具+引數連續呼叫
2. 交替模式檢測 (ABAB)：兩個工具交替呼叫
3. 過度使用檢測：同一工具在短時間內呼叫過多

Example:
    from agentsociety2.agent.tool.loop_detection import LoopDetectionService

    detector = LoopDetectionService()
    result = detector.check_tool_loop("bash", {"command": "ls"})
    if result.is_loop:
        print(f"Loop detected: {result.details}")
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass
class LoopDetectionConfig:
    """迴圈檢測配置。

    :ivar max_tool_repeats: 相同工具+引數連續呼叫閾值。
    :ivar max_content_repeats: 相同內容連續輸出閾值。
    :ivar max_error_repeats: 相同錯誤連續出現閾值。
    :ivar history_size: 歷史記錄大小。
    :ivar overuse_threshold: 同一工具在 history_size 呼叫中的過度使用閾值。
    """

    max_tool_repeats: int = 5
    max_content_repeats: int = 10
    max_error_repeats: int = 3
    history_size: int = 20
    overuse_threshold: int = 15


@dataclass
class LoopDetectionResult:
    """迴圈檢測結果。

    :ivar is_loop: 是否檢測到迴圈。
    :ivar loop_type: 迴圈型別 ("tool_repeat", "tool_alternating", "tool_overuse", "content", "error")。
    :ivar details: 詳細描述。
    :ivar root_cause: 根本原因分析。
    :ivar alternative_actions: 建議的替代行動。
    :ivar affected_tools: 受影響的工具列表。
    """

    is_loop: bool = False
    loop_type: str = ""
    details: str = ""
    root_cause: str = ""
    alternative_actions: list[str] = None
    affected_tools: list[str] = None

    def __post_init__(self):
        if self.alternative_actions is None:
            self.alternative_actions = []
        if self.affected_tools is None:
            self.affected_tools = []


class LoopDetectionService:
    """迴圈檢測服務。

    檢測多種迴圈型別：

    1. 工具呼叫迴圈：相同工具+引數連續呼叫
    2. 交替模式：兩個工具交替呼叫
    3. 過度使用：同一工具短時間內呼叫過多
    4. 內容迴圈：相同輸出內容連續出現
    5. 錯誤重複：相同錯誤連續出現
    """

    def __init__(self, config: LoopDetectionConfig | None = None):
        """初始化迴圈檢測服務。

        :param config: 檢測配置，為 None 時使用預設值。
        :type config: LoopDetectionConfig | None
        """
        self._config = config or LoopDetectionConfig()
        self._tool_call_history: deque[str] = deque(maxlen=self._config.history_size)
        self._content_history: deque[str] = deque(maxlen=self._config.history_size)
        self._error_history: deque[str] = deque(maxlen=self._config.history_size)

    def reset(self) -> None:
        """重置歷史記錄。"""
        self._tool_call_history.clear()
        self._content_history.clear()
        self._error_history.clear()

    def check_tool_loop(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> LoopDetectionResult:
        """檢測工具呼叫迴圈。

        檢測三種模式：
        1. 連續重複 (AAAA)
        2. 交替模式 (ABAB)
        3. 過度使用

        :param tool_name: 工具名稱。
        :param arguments: 工具引數。
        :return: 檢測結果。
        :rtype: LoopDetectionResult
        """
        call_fingerprint = f"{tool_name}:{self._hash_arguments(arguments)}"
        self._tool_call_history.append(call_fingerprint)

        history = list(self._tool_call_history)

        # 1. 連續重複檢測
        if len(history) >= self._config.max_tool_repeats:
            recent = history[-self._config.max_tool_repeats :]
            if len(set(recent)) == 1:
                return LoopDetectionResult(
                    is_loop=True,
                    loop_type="tool",
                    details=f"Tool '{tool_name}' called {self._config.max_tool_repeats} times with same arguments",
                    root_cause=f"The tool '{tool_name}' is not producing the expected result, causing repeated attempts.",
                    alternative_actions=[
                        "Check if the tool arguments are correct",
                        "Try a different approach or tool",
                        "Call 'done' to finish this step and try again later",
                        "Read the tool output carefully to understand why it's not working",
                    ],
                    affected_tools=[tool_name],
                )

        # 2. 交替模式檢測 (ABAB)
        if len(history) >= 6:
            last_6 = history[-6:]
            # 檢查 ABABAB 模式
            if (
                len(set(last_6)) == 2
                and last_6[0] == last_6[2] == last_6[4]
                and last_6[1] == last_6[3] == last_6[5]
            ):
                tools = [fp.split(":")[0] for fp in set(last_6)]
                return LoopDetectionResult(
                    is_loop=True,
                    loop_type="tool",
                    details="Alternating pattern detected between tools",
                    root_cause=f"Tools {tools[0]} and {tools[1]} are alternating without progress. This usually means a condition is not being met or a state is not changing as expected.",
                    alternative_actions=[
                        "Check if there's a prerequisite step missing",
                        "Verify the condition you're trying to achieve",
                        "Try a completely different approach",
                        "Consider if the task requires external input",
                    ],
                    affected_tools=tools,
                )

        # 3. 過度使用檢測
        if len(history) >= self._config.history_size:
            tool_counts: dict[str, int] = {}
            for fp in history:
                name = fp.split(":")[0]
                tool_counts[name] = tool_counts.get(name, 0) + 1
            for name, count in tool_counts.items():
                if count >= self._config.overuse_threshold:
                    return LoopDetectionResult(
                        is_loop=True,
                        loop_type="tool",
                        details=f"Tool '{name}' used {count} times in last {self._config.history_size} calls",
                        root_cause=f"Tool '{name}' is being used excessively. This may indicate the task is too complex or the approach is not efficient.",
                        alternative_actions=[
                            "Consider breaking down the task into smaller steps",
                            "Use a different tool or approach",
                            "Activate a relevant skill that might help",
                            "Call 'done' and reassess the overall strategy",
                        ],
                        affected_tools=[name],
                    )

        return LoopDetectionResult(is_loop=False)

    def check_content_loop(self, content: str) -> LoopDetectionResult:
        """檢測內容迴圈。

        :param content: 輸出內容。
        :return: 檢測結果。
        :rtype: LoopDetectionResult
        """
        content_hash = self._hash_content(content)
        self._content_history.append(content_hash)

        if len(self._content_history) >= self._config.max_content_repeats:
            recent = list(self._content_history)[-self._config.max_content_repeats :]
            if len(set(recent)) == 1:
                return LoopDetectionResult(
                    is_loop=True,
                    loop_type="content",
                    details=f"Same content repeated {self._config.max_content_repeats} times",
                )
        return LoopDetectionResult(is_loop=False)

    def check_error_loop(self, error: str) -> LoopDetectionResult:
        """檢測錯誤迴圈。

        :param error: 錯誤資訊。
        :return: 檢測結果。
        :rtype: LoopDetectionResult
        """
        error_hash = self._hash_content(error)
        self._error_history.append(error_hash)

        if len(self._error_history) >= self._config.max_error_repeats:
            recent = list(self._error_history)[-self._config.max_error_repeats :]
            if len(set(recent)) == 1:
                return LoopDetectionResult(
                    is_loop=True,
                    loop_type="error",
                    details=f"Same error repeated {self._config.max_error_repeats} times: {error[:100]}",
                )
        return LoopDetectionResult(is_loop=False)

    @staticmethod
    def _hash_arguments(args: dict[str, Any]) -> str:
        """生成引數雜湊。

        使用 MD5 生成固定長度指紋。

        :param args: 引數字典。
        :return: 雜湊字串（16 字元）。
        :rtype: str
        """
        try:
            data = json.dumps(args, sort_keys=True, default=str)
            return hashlib.md5(data.encode()).hexdigest()[:16]
        except Exception:
            return hashlib.md5(str(args).encode()).hexdigest()[:16]

    @staticmethod
    def _hash_content(content: str) -> str:
        """生成內容雜湊。

        使用 MD5 生成固定長度指紋，避免記憶體佔用過大。

        :param content: 內容字串。
        :return: 雜湊字串（16 字元）。
        :rtype: str
        """
        # 擷取前 500 字元用於雜湊，平衡準確性和效能
        data = content.strip()[:500]
        return hashlib.md5(data.encode()).hexdigest()[:16]
