# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Timestamp tool - 時間戳轉換工具.

包含：
- convert_timestamp_to_utc8_time: 將時間戳轉換為 UTC+8 時間格式
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger
from .utils import ToolInputError


@tool(
    name="convert_timestamp_to_utc8_time",
    description="""將時間戳轉換為標準 UTC+8 時間格式。支援秒級時間戳和毫秒級時間戳。

輸入引數：
- timestamp: 時間戳（數字型別），可以是秒級（10位）或毫秒級（13位）

輸出格式：
- YYYYMMDD hhmmss（例如：20240315 143000 表示 2024年3月15日 14:30:00 北京時間）

重要說明：
搜尋日程工具（search_calendar_event）和搜尋鬧鐘工具（search_alarm）等工具中返回結果如果包含時間戳。
建議優先呼叫本時間戳轉換工具，將時間戳轉換為標準北京時間格式，再基於標準時間進行使用者回答或下一步操作。

示例：
- 輸入：1710498600（秒級）或 1710498600000（毫秒級）
- 輸出：20240315 143000""",
)
def convert_timestamp_to_utc8_time(timestamp: float) -> dict:
    """將時間戳轉換為 UTC+8 時間格式."""
    if timestamp is None:
        raise ToolInputError("缺少必需引數：timestamp")

    if not isinstance(timestamp, (int, float)):
        raise ToolInputError("timestamp 必須是數字型別")

    import math
    if math.isnan(timestamp) or math.isinf(timestamp):
        raise ToolInputError("timestamp 不是有效數字")

    # 判斷秒級還是毫秒級
    ts_abs = abs(timestamp)
    ts_str = str(int(ts_abs))

    if len(ts_str) == 13:
        timestamp_in_ms = timestamp
    elif len(ts_str) == 10:
        timestamp_in_ms = timestamp * 1000
    elif ts_abs > 1000000000000:
        timestamp_in_ms = timestamp
    else:
        timestamp_in_ms = timestamp * 1000

    # 轉換為 UTC+8
    utc8_tz = timezone(timedelta(hours=8))
    try:
        dt = datetime.fromtimestamp(timestamp_in_ms / 1000, tz=utc8_tz)
    except (OSError, OverflowError, ValueError) as e:
        raise ToolInputError(f"無效的時間戳，無法轉換為日期: {e}") from e

    formatted = dt.strftime("%Y%m%d %H%M%S")

    logger.info(
        "[TIMESTAMP_TOOL] Converted timestamp %s -> %s",
        timestamp,
        formatted,
    )

    return {
        "content": [
            {
                "type": "text",
                "text": formatted,
            }
        ]
    }
