# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Contact tools - 聯絡人工具.

命令結構要點：
- executeParam：intentName SearchContactLocal、bundleName aidispatchservice、appType OHOS_APP 等
- intentParam：name

包含：
- search_contact: 搜尋聯絡人
"""

from __future__ import annotations

import json
from typing import Any, Dict

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger
from .utils import (
    execute_device_command,
    format_success_response,
    ToolInputError,
)


@tool(
    name="search_contact",
    description=(
        "搜尋使用者裝置上的聯絡人資訊。根據姓名在通訊錄中檢索聯絡人詳細資訊"
        "（包括姓名、電話號碼、郵箱、組織、職位等）。"
        "注意:操作超時時間為60秒,請勿重複呼叫此工具,如果超時或失敗,最多重試一次。"
    ),
)
async def search_contact(name: str) -> Dict[str, Any]:
    """搜尋聯絡人

    Args:
        name: 聯絡人姓名，用於在通訊錄中檢索聯絡人資訊

    Returns:
        content[0].text 為裝置 outputs 的 JSON 字串
    """
    try:
        if not isinstance(name, str) or not name.strip():
            raise ToolInputError("缺少必填引數 name")

        name_clean = name.strip()

        logger.info(
            "[SEARCH_CONTACT_TOOL] Searching contacts - name=%r",
            name_clean,
        )

        command = {
            "header": {
                "namespace": "Common",
                "name": "Action",
            },
            "payload": {
                "cardParam": {},
                "executeParam": {
                    "executeMode": "background",
                    "intentName": "SearchContactLocal",
                    "bundleName": "com.huawei.hmos.aidispatchservice",
                    "needUnlock": True,
                    "actionResponse": True,
                    "appType": "OHOS_APP",
                    "timeOut": 5,
                    "intentParam": {"name": name_clean},
                    "permissionId": [],
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        outputs = await execute_device_command("SearchContactLocal", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        result = outputs.get("result")
        if not isinstance(result, dict):
            result = {}
        n = len(result.get("items", []))
        logger.info("[SEARCH_CONTACT_TOOL] found %s contacts", n)

        return format_success_response(
            dict(outputs),
            f"搜尋到聯絡人資訊（{n} 條）",
        )

    except ToolInputError:
        raise
    except Exception as e:
        logger.error(f"[SEARCH_CONTACT_TOOL] Failed to search contacts: {e}")
        raise RuntimeError(f"搜尋聯絡人失敗: {str(e)}") from e
