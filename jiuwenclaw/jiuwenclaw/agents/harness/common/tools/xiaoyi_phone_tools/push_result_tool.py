# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Push result tool - 檢視推送記錄工具.

包含：
- view_push_result: 檢視定時任務或推送訊息的執行結果
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger

from .pushdata_manager import search_push_data, get_all_push_data


@tool(
    name="view_push_result",
    description="""檢視定時任務或推送訊息的執行結果。當使用者說"檢視我xxx的定時任務執行結果"、"檢視我的xxxx的推送訊息"或類似語料時呼叫此工具。

功能說明：
- 支援關鍵詞搜尋：如果使用者提到具體任務名稱或內容，可以按關鍵詞篩選
- 無關鍵詞時：返回最近的推送記錄（預設10條）
- 返回內容包括：推送ID、時間、內容摘要

使用場景：
- "檢視我昨天的定時任務執行結果"
- "幫我看看天氣推送訊息"
- "檢視最近的推送記錄"
- "我的提醒任務執行了嗎" """,
)
def view_push_result(
    keywords: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """檢視推送記錄（與 xy_channel view-push-result-tool.ts 對齊）.

    Args:
        keywords: 可選的搜尋關鍵詞，用於篩選推送記錄
        limit: 返回的最大記錄數，預設10條，最多50條

    Returns:
        content[0].text: JSON 字串（success, count, items, message）
    """
    try:
        effective_limit = min(limit or 10, 50)
        kw = keywords.strip() if keywords and isinstance(keywords, str) else None
        logger.info(
            "[VIEW_PUSH_RESULT_TOOL] 開始查詢 keywords=%s limit=%s",
            kw, effective_limit,
        )

        # 根據是否有關鍵詞決定呼叫哪個方法
        results = search_push_data(kw) if kw else get_all_push_data()
        logger.info(
            "[VIEW_PUSH_RESULT_TOOL] 資料來源返回 %d 條記錄, 查詢方式=%s",
            len(results), "關鍵詞搜尋" if kw else "全量查詢",
        )

        # 按時間倒序排序（最新的在前）
        results.sort(key=lambda x: x.get("time", ""), reverse=True)

        # 限制返回條數
        results = results[:effective_limit]
        logger.info("[VIEW_PUSH_RESULT_TOOL] 擷取後返回 %d 條記錄", len(results))

        if not results:
            logger.info("[VIEW_PUSH_RESULT_TOOL] 無匹配記錄, keywords=%s", kw)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": True,
                                "count": 0,
                                "items": [],
                                "message": (
                                    f'未找到包含關鍵詞"{kw}"的推送記錄'
                                    if kw
                                    else "暫無推送記錄"
                                ),
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            }

        # 格式化返回結果
        formatted_items = []
        for item in results:
            detail = item.get("dataDetail", "")
            formatted_items.append(
                {
                    "pushDataId": item.get("pushDataId", "")[:8],
                    "fullPushDataId": item.get("pushDataId", ""),
                    "time": item.get("time", ""),
                    "dataDetail": (
                        detail[:200] + "..." if len(detail) > 200 else detail
                    ),
                    "fullLength": len(detail),
                }
            )

        logger.info(
            "[VIEW_PUSH_RESULT_TOOL] 查詢完成, 返回 %d 條記錄",
            len(formatted_items),
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": True,
                            "count": len(formatted_items),
                            "totalMatched": len(results),
                            "items": formatted_items,
                            "message": (
                                f'找到 {len(formatted_items)} 條包含"{kw}"的推送記錄'
                                if kw
                                else f"返回最近 {len(formatted_items)} 條推送記錄"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                }
            ]
        }

    except Exception as e:
        logger.error("[VIEW_PUSH_RESULT_TOOL] Failed: %s", e)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": str(e),
                            "message": "查詢推送記錄失敗",
                        },
                        ensure_ascii=False,
                    ),
                }
            ]
        }
