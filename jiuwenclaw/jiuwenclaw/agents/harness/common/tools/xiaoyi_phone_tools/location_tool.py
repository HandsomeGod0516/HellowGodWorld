# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Location tool - 獲取手機當前定位.

透過 WebSocket 傳送 GetCurrentLocation 指令到手機端，返回裝置 outputs 的 JSON。
"""

from __future__ import annotations

import json
from typing import Any, Dict

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger
from .utils import execute_device_command, raise_if_device_error


@tool(
    name="get_user_location",
    description=(
        "獲取使用者當前位置（經緯度座標，WGS84座標系）。需要使用者裝置授權位置訪問許可權。"
        "注意:操作超時時間為60秒,請勿重複呼叫此工具,如果超時或失敗,最多重試一次。"
    ),
)
async def get_user_location() -> Dict[str, Any]:
    """獲取使用者當前地理位置.

    Returns:
        content[0].text 為裝置 outputs 的 JSON 字串
    """

    logger.info("[LOCATION_TOOL] Starting execution - Building GetCurrentLocation command...")
    command = {
        "header": {
            "namespace": "Common",
            "name": "Action",
        },
        "payload": {
            "cardParam": {},
            "executeParam": {
                "achieveType": "INTENT",
                "actionResponse": True,
                "bundleName": "com.huawei.hmos.aidispatchservice",
                "dimension": "",
                "executeMode": "background",
                "intentName": "GetCurrentLocation",
                "intentParam": {
                    "isNeedGeoAddress": True,
                },
                "needUnlock": True,
                "permissionId": [],
                "timeOut": 5,
            },
            "needUploadResult": True,
            "pageControlRelated": False,
            "responses": [
                {
                    "displayText": "",
                    "resultCode": "",
                    "ttsText": "",
                }
            ],
        },
    }

    logger.info("[LOCATION_TOOL] Waiting for location response...")
    outputs = await execute_device_command("GetCurrentLocation", command)

    if not isinstance(outputs, dict):
        outputs = {"value": outputs}

    raise_if_device_error(outputs, "獲取位置失敗")

    logger.info(
        f"[LOCATION_TOOL] Location retrieved successfully - outputs keys: {list(outputs.keys())}"
    )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(outputs, ensure_ascii=False),
            }
        ]
    }
