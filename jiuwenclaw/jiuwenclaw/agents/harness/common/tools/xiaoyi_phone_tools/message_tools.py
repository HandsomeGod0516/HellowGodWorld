# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Message tools - 簡訊/訊息工具.

包含：
- send_message: 傳送簡訊
- search_message: 搜尋簡訊
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger
from .utils import (
    _outputs_top_level_code_ok,
    execute_device_command,
    format_success_response,
    raise_if_device_error,
    ToolInputError,
)


def _mask_phone_for_log(phone: str) -> str:
    """日誌中脫敏手機號."""
    p = (phone or "").strip()
    if len(p) <= 7:
        return "***"
    return f"{p[:3]}***{p[-4:]}"


def _nested_result_error_message(outputs: Dict[str, Any]) -> Optional[str]:
    """部分 Intent 在 result 子物件中帶 code/errMsg，與頂層 outputs 分離."""
    r = outputs.get("result")
    if not isinstance(r, dict):
        return None
    c = r.get("code")
    if c is None or _outputs_top_level_code_ok(c):
        return None
    msg = r.get("errorMsg") or r.get("errMsg") or str(c)
    return str(msg)


def _log_outputs_summary(intent: str, outputs: Dict[str, Any]) -> None:
    try:
        blob = json.dumps(outputs, ensure_ascii=False)
    except Exception:
        blob = str(outputs)
    logger.info(
        "[%s_TOOL] outputs summary len=%s preview=%s",
        intent,
        len(blob),
        blob[:800],
    )


@tool(
    name="send_message",
    description=(
        "透過手機傳送簡訊。需要提供接收方手機號碼和簡訊內容。"
        "手機號碼會自動新增+86字首（如果沒有的話）。"
        "注意:操作超時時間為60秒,請勿重複呼叫此工具,如果超時或失敗,最多重試一次。"
    ),
)
async def send_message(phone_number: str, content: str) -> Dict[str, Any]:
    """傳送簡訊.

    Args:
        phone_number: 接收方手機號碼（會自動規範為 +86 字首）
        content: 簡訊內容

    Returns:
        裝置返回的完整 outputs，經 format_success_response 包裝
    """
    try:
        logger.info(
            f"[SEND_MESSAGE_TOOL] Starting exec - phone_number: {phone_number}, content len: {len(content)}"
        )

        if not phone_number or not isinstance(phone_number, str):
            raise ToolInputError("缺少必填引數 phone_number（接收方手機號碼）")

        if not content or not isinstance(content, str):
            raise ToolInputError("缺少必填引數 content（簡訊內容）")

        phone_number = phone_number.strip()
        content = content.strip()

        if not phone_number:
            raise ToolInputError("phone_number 不能為空")

        if not content:
            raise ToolInputError("content 不能為空")

        # 未帶 +86 時：去掉前導 0、再去 86 字首，再加 +86
        if not phone_number.startswith("+86"):
            if phone_number.startswith("0"):
                phone_number = phone_number[1:]
            if phone_number.startswith("86"):
                phone_number = phone_number[2:]
            phone_number = f"+86{phone_number}"

        logger.info(
            "[SEND_MESSAGE_TOOL] Normalized phone -> %s",
            _mask_phone_for_log(phone_number),
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
                    "intentName": "SendShortMessage",
                    "bundleName": "com.huawei.hmos.aidispatchservice",
                    "needUnlock": True,
                    "actionResponse": True,
                    "appType": "OHOS_APP",
                    "timeOut": 5,
                    "intentParam": {
                        "phoneNumber": phone_number,
                        "content": content,
                    },
                    "permissionId": [],
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        outputs = await execute_device_command("SendShortMessage", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        _log_outputs_summary("SEND_MESSAGE", dict(outputs))
        raise_if_device_error(outputs, "傳送簡訊失敗")
        nested_err = _nested_result_error_message(outputs)
        if nested_err:
            logger.error(
                "[SEND_MESSAGE_TOOL] nested result error: %s",
                nested_err,
            )
            raise RuntimeError(f"傳送簡訊失敗: {nested_err}") from None

        logger.info("[SEND_MESSAGE_TOOL] Message send completed")

        return format_success_response(dict(outputs), f"簡訊已傳送至 {phone_number}")

    except ToolInputError:
        raise
    except Exception as e:
        logger.error(f"[SEND_MESSAGE_TOOL] Failed to send message: {e}")
        raise RuntimeError(f"傳送簡訊失敗: {str(e)}") from e


@tool(
    name="search_message",
    description=(
        "搜尋手機簡訊。根據關鍵詞搜尋簡訊內容。"
        "注意:操作超時時間為60秒,請勿重複呼叫此工具,如果超時或失敗,最多重試一次。"
    ),
)
async def search_message(
    content: str,
) -> Dict[str, Any]:
    """搜尋簡訊

    Args:
        content: 搜尋關鍵詞，用於在簡訊內容中進行匹配

    Returns:
        content[0].text 為裝置 outputs 的 JSON 字串
    """
    try:
        if (
            not content
            or not isinstance(content, str)
            or content.strip() == ""
        ):
            raise ToolInputError("缺少必填引數 content（須為非空字串）")

        content = content.strip()
        logger.info(
            "[SEARCH_MESSAGE_TOOL] Searching messages - keyword=%r",
            content,
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
                    "intentName": "SearchMessage",
                    "bundleName": "com.huawei.hmos.aidispatchservice",
                    "needUnlock": True,
                    "actionResponse": True,
                    "appType": "OHOS_APP",
                    "timeOut": 5,
                    "intentParam": {
                        "content": content,
                    },
                    "permissionId": [],
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        outputs = await execute_device_command("SearchMessage", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        _log_outputs_summary("SEARCH_MESSAGE", dict(outputs))

        # 與 search-message-tool.ts 一致：text 為完整 event.outputs 的 JSON
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
        logger.error(f"[SEARCH_MESSAGE_TOOL] Failed to search messages: {e}")
        raise RuntimeError(f"搜尋簡訊失敗: {str(e)}") from e
