"""輕量 ContextConfig（面向測試與閾值計算）。

該模組提供一個與 agent 上下文視窗治理相關的最小配置物件，主要用於 token 估算、
壓縮閾值判斷等純函式邏輯的引數承載。

說明：本倉庫不追求向後相容；此模組的欄位以測試與當前實現需要為準。
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CONTEXT_WINDOW = 200_000


def get_model_context_window(model: str | None) -> int:
    """根據模型名返回上下文視窗大小（tokens）。

    :param model: LiteLLM 路由模型名。
    :returns: 上下文視窗大小（tokens）。
    """
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    m = str(model).lower()
    # 主流：gpt-4o 128k（用於測試）
    if "gpt-4o" in m:
        return 128_000
    return DEFAULT_CONTEXT_WINDOW


@dataclass
class ContextConfig:
    """上下文閾值配置。"""

    model: str = ""

    # window split
    model_context_window: int = DEFAULT_CONTEXT_WINDOW
    output_reserve: int = 16_000
    prompt_overhead: int = 8_000

    # compaction ratios
    compact_warning_ratio: float = 0.60
    compact_trigger_ratio: float = 0.70
    compact_auto_ratio: float = 0.85
    compact_block_ratio: float = 0.95

    # circuit breaker
    max_retries: int = 3
    backoff: float = 2.0

    # thread defaults (用於測試)
    thread_max_messages: int = 40
    thread_compact_keep_recent: int = 6

    # summary defaults (用於測試)
    summary_char_budget: int = 6000
    summary_msg_limit: int = 1600

    def __post_init__(self) -> None:
        if self.model:
            self.model_context_window = get_model_context_window(self.model)

    @property
    def effective_window(self) -> int:
        """可用上下文視窗（扣除輸出預留與 prompt 開銷）。"""
        return self.model_context_window - self.output_reserve - self.prompt_overhead
