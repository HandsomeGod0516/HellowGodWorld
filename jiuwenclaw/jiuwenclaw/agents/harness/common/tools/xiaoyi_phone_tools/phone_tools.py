# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Phone tools - 電話工具.

包含：
- call_phone: 撥打電話
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger
from .utils import (
    execute_device_command,
    raise_if_device_error,
    ToolInputError,
)


@tool(
    name="call_phone",
    description=(
        "撥打電話。需要提供要撥打的電話號碼。"
        "slotId引數可選，預設為0（主卡），如果使用者明確要求使用副卡則設定為1。"
        "注意:操作超時時間為60秒,請勿重複呼叫此工具,如果超時或失敗,最多重試一次。"
    ),
)
async def call_phone(
    phone_number: str,
    slot_id: Optional[int] = None,
) -> Dict[str, Any]:
    """撥打電話.

    Args:
        phone_number: 要撥打的電話號碼，必填
        slot_id: SIM 卡槽（裝置欄位 slotId）；未傳時按 0

    Returns:
        success、code、phoneNumber、slotId、message（與裝置成功回撥欄位一致）
    """
    try:
        if slot_id is None:
            slot_id = 0

        logger.info(
            f"[CALL_PHONE_TOOL] Calling - phone_number: {phone_number}, slotId: {slot_id}"
        )

        if not phone_number or not isinstance(phone_number, str):
            raise ToolInputError("缺少必填引數 phone_number（電話號碼）")

        phone_number = phone_number.strip()
        if not phone_number:
            raise ToolInputError("phone_number 不能為空")

        if slot_id not in (0, 1):
            raise ToolInputError("slot_id 必須是 0（主卡）或 1（副卡）")

        command = {
            "header": {
                "namespace": "Common",
                "name": "Action",
            },
            "payload": {
                "cardParam": {},
                "executeParam": {
                    "executeMode": "background",
                    "intentName": "StartCall",
                    "bundleName": "com.huawei.hmos.aidispatchservice",
                    "dimension": "",
                    "needUnlock": True,
                    "actionResponse": True,
                    "timeOut": 5,
                    "intentParam": {
                        "phoneNumber": phone_number,
                        "slotId": slot_id,
                    },
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        outputs = await execute_device_command("StartCall", command)

        if not isinstance(outputs, dict):
            outputs = {}

        raise_if_device_error(outputs, "撥打電話失敗")

        logger.info("[CALL_PHONE_TOOL] Call initiated successfully")

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(outputs, ensure_ascii=False),
                }
            ]
        }

    except ToolInputError:
        raise
    except Exception as e:
        logger.error(f"[CALL_PHONE_TOOL] Failed to initiate call: {e}")
        raise RuntimeError(f"撥打電話失敗: {str(e)}") from e
