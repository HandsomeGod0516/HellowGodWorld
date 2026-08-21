# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Calendar tools - 日曆工具.

包含：
- create_calendar_event: 建立日程
- search_calendar_event: 檢索日程
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger

from .utils import (
    execute_device_command,
    format_success_response,
    raise_if_device_error,
    ToolInputError,
)


def _format_timestamp_to_datetime(timestamp_ms: int) -> str:
    """將毫秒時間戳轉換為 yyyy-mm-dd hh:mm:ss 格式.

    Args:
        timestamp_ms: 毫秒時間戳

    Returns:
        格式化的日期時間字串
    """
    if not timestamp_ms:
        return ""
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _convert_event_timestamps(event: Dict[str, Any]) -> Dict[str, Any]:
    """將事件中的時間戳轉換為可讀格式.

    Args:
        event: 日程事件字典

    Returns:
        轉換後的事件字典
    """
    result = dict(event)
    if "dtStart" in result and result["dtStart"]:
        result["dtStart"] = _format_timestamp_to_datetime(result["dtStart"])
    if "dtEnd" in result and result["dtEnd"]:
        result["dtEnd"] = _format_timestamp_to_datetime(result["dtEnd"])
    return result


def _parse_time_string_ymd_hhmmss(time_str: str) -> int:
    """解析 YYYYMMDD hhmmss 格式為毫秒時間戳."""
    cleaned = " ".join(time_str.strip().split())
    parts = cleaned.split(" ")
    if len(parts) != 2:
        raise ValueError(
            f"Invalid time format: {time_str}. Expected format: YYYYMMDD hhmmss"
        )
    date_part, time_part = parts[0], parts[1]
    if len(date_part) != 8 or len(time_part) != 6:
        raise ValueError(
            f"Invalid time format: {time_str}. Expected format: YYYYMMDD hhmmss"
        )
    year = int(date_part[0:4])
    month = int(date_part[4:6])
    day = int(date_part[6:8])
    hours = int(time_part[0:2])
    minutes = int(time_part[2:4])
    seconds = int(time_part[4:6])
    dt = datetime(year, month, day, hours, minutes, seconds)
    return int(dt.timestamp() * 1000)


@tool(
    name="create_calendar_event",
    description=(
        "在使用者裝置上建立日程。需要提供日程標題、開始時間和結束時間。"
        "時間格式必須為：yyyy-mm-dd hh:mm:ss（例如：2024-01-15 14:30:00）。"
        "注意：該工具執行時間較長（最多60秒），請勿重複呼叫，超時或失敗時最多重試一次。\n"
        "  注意事項：使用該工具之前需獲取當前真實時間\n"
    ),
)
async def create_calendar_event(
    title: str,
    dt_start: str,
    dt_end: str,
) -> Dict[str, Any]:
    """建立日程.

    Args:
        title: 日程標題/名稱，必填
        dt_start: 開始時間，格式 yyyy-mm-dd hh:mm:ss
        dt_end: 結束時間，格式 yyyy-mm-dd hh:mm:ss

    Returns:
        包含建立結果的響應字典
    """
    try:
        logger.info(
            f"[CALENDAR_TOOL] Create calendar event - title: {title}, "
            f"dt_start: {dt_start}, dt_end: {dt_end}"
        )

        # 驗證引數
        if not title:
            raise ToolInputError("缺少必填引數 title（日程標題）")
        if not dt_start:
            raise ToolInputError("缺少必填引數 dt_start（開始時間）")
        if not dt_end:
            raise ToolInputError("缺少必填引數 dt_end（結束時間）")

        # 轉換時間字串為時間戳
        try:
            start_dt = datetime.strptime(dt_start, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(dt_end, "%Y-%m-%d %H:%M:%S")
            dt_start_ms = int(start_dt.timestamp() * 1000)
            dt_end_ms = int(end_dt.timestamp() * 1000)
        except ValueError:
            raise ToolInputError(
                "時間格式錯誤。必須使用：yyyy-mm-dd hh:mm:ss（例如：2024-01-15 14:30:00）"
            ) from ValueError

        intent_param = {
            "title": title,
            "dtStart": dt_start_ms,
            "dtEnd": dt_end_ms,
        }

        # CreateCalendarEvent：executeParam 不含 appType、permissionId
        command = {
            "header": {
                "namespace": "Common",
                "name": "ActionAndResult",
            },
            "payload": {
                "cardParam": {},
                "executeParam": {
                    "executeMode": "background",
                    "intentName": "CreateCalendarEvent",
                    "bundleName": "com.huawei.hmos.calendardata",
                    "dimension": "",
                    "needUnlock": True,
                    "actionResponse": True,
                    "timeOut": 5,
                    "intentParam": intent_param,
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        # 執行命令
        result = await execute_device_command("CreateCalendarEvent", command)

        if isinstance(result, dict):
            raise_if_device_error(result, "建立日程失敗")

        logger.info("[CALENDAR_TOOL] Calendar event created successfully")
        return format_success_response(
            {"title": title, "dt_start": dt_start, "dt_end": dt_end, "result": result},
            f"日程 '{title}' 建立成功",
        )

    except ToolInputError:
        raise
    except Exception as e:
        logger.error(f"[CALENDAR_TOOL] Failed to create calendar event: {e}")
        raise RuntimeError(f"建立日程失敗: {str(e)}") from e


@tool(
    name="search_calendar_event",
    description="""檢索使用者日曆中的日程安排。根據時間範圍和可選的日程標題進行檢索。時間格式必須為：YYYYMMDD hhmmss（例如：20240115 143000）。

時間範圍說明：
- 查詢某一天的日程：使用該天的 00:00:00 到 23:59:59（例如：20240115 000000 到 20240115 235959）
- 查詢上午的日程：使用 06:00:00 到 12:00:00
- 查詢下午的日程：使用 12:00:00 到 18:00:00
- 查詢晚上的日程：使用 18:00:00 到 23:59:59
- 查詢某個時刻附近的日程：使用該時刻前後1小時的區間（例如：查詢3點左右的日程，使用 14:00:00 到 16:00:00）

注意：
a. 該工具執行時間較長（最多60秒），請勿重複呼叫，超時或失敗時最多重試一次。
b. 使用該工具之前需獲取當前真實時間
""",
)
async def search_calendar_event(
    start_time: str,
    end_time: str,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """檢索日程.

    Args:
        start_time: 起始時間，格式 YYYYMMDD hhmmss
        end_time: 結束時間，格式 YYYYMMDD hhmmss
        title: 日程標題過濾（可選）

    Returns:
        包含日程列表的響應字典
    """
    try:
        logger.info(
            f"[SEARCH_CALENDAR_TOOL] Searching calendar events - "
            f"start_time: {start_time}, end_time: {end_time}, title: {title}"
        )

        if not start_time or not end_time:
            raise ToolInputError("缺少必填引數 start_time 與 end_time")

        try:
            start_time_ms = _parse_time_string_ymd_hhmmss(start_time)
            end_time_ms = _parse_time_string_ymd_hhmmss(end_time)
        except ValueError as e:
            raise ToolInputError(
                "時間格式錯誤。必須使用：YYYYMMDD hhmmss（例如：20240115 143000）。"
                f" {e}"
            ) from e

        intent_param: Dict[str, Any] = {
            "timeInterval": [start_time_ms, end_time_ms],
        }
        if title:
            intent_param["title"] = title

        command = {
            "header": {
                "namespace": "Common",
                "name": "Action",
            },
            "payload": {
                "cardParam": {},
                "executeParam": {
                    "executeMode": "background",
                    "intentName": "SearchCalendarEvent",
                    "bundleName": "com.huawei.hmos.calendardata",
                    "dimension": "",
                    "needUnlock": True,
                    "actionResponse": True,
                    "appType": "OHOS_APP",
                    "timeOut": 5,
                    "intentParam": intent_param,
                    "permissionId": [],
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        # 執行命令
        outputs = await execute_device_command("SearchCalendarEvent", command)

        raise_if_device_error(outputs, "檢索日程失敗")

        # 獲取結果
        result = outputs.get("result", {})
        items = result.get("items", []) if isinstance(result, dict) else []

        # 轉換時間戳為可讀格式
        formatted_items = [_convert_event_timestamps(item) for item in items]

        logger.info(f"[SEARCH_CALENDAR_TOOL] Found {len(formatted_items)} events")

        return format_success_response(
            {"events": formatted_items, "count": len(formatted_items)},
            f"搜尋到 {len(formatted_items)} 條日程",
        )

    except ToolInputError:
        raise
    except Exception as e:
        logger.error(f"[SEARCH_CALENDAR_TOOL] Failed to search calendar: {e}")
        raise RuntimeError(f"搜尋日程失敗: {str(e)}") from e
