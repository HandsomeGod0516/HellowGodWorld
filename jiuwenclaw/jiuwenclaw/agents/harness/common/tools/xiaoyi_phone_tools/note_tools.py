# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Note tools - 備忘錄工具.

包含：
- create_note: 建立備忘錄
- search_notes: 搜尋備忘錄
- modify_note: 修改備忘錄
"""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger
from .utils import (
    execute_device_command,
    format_success_response,
    raise_if_device_error,
    ToolInputError,
)


@tool(
    name="create_note",
    description="""在使用者裝置上建立備忘錄。需要提供備忘錄標題和內容。
  注意:
  a. 操作超時時間為60秒,請勿重複呼叫此工具
  b. 如果遇到各類呼叫失敗場景,最多隻能重試一次，不可以重複呼叫多次。
  c. 呼叫工具前需認真檢查呼叫引數是否滿足工具要求
  """,
)
async def create_note(title: str, content: str) -> Dict[str, Any]:
    """建立備忘錄.

    Args:
        title: 備忘錄標題，必填
        content: 備忘錄內容，必填

    Returns:
        裝置返回的完整 outputs，經 format_success_response 包裝
    """
    try:
        logger.info(f"[CREATE_NOTE_TOOL] Creating note - title: {title}")

        if not title or not isinstance(title, str):
            raise ToolInputError("缺少必填引數 title（備忘錄標題）")
        if not content or not isinstance(content, str):
            raise ToolInputError("缺少必填引數 content（備忘錄內容）")

        # CreateNote：executeParam 不含 appType、permissionId
        command = {
            "header": {
                "namespace": "Common",
                "name": "Action",
            },
            "payload": {
                "cardParam": {},
                "executeParam": {
                    "executeMode": "background",
                    "intentName": "CreateNote",
                    "bundleName": "com.huawei.hmos.notepad",
                    "dimension": "",
                    "needUnlock": True,
                    "actionResponse": True,
                    "timeOut": 5,
                    "intentParam": {
                        "title": title,
                        "content": content,
                    },
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        outputs = await execute_device_command("CreateNote", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        raise_if_device_error(outputs, "建立備忘錄失敗")

        logger.info("[CREATE_NOTE_TOOL] Note create completed")

        return format_success_response(dict(outputs), f"備忘錄 '{title}' 建立成功")

    except ToolInputError:
        raise
    except Exception as e:
        logger.error(f"[CREATE_NOTE_TOOL] Failed to create note: {e}")
        raise RuntimeError(f"建立備忘錄失敗: {str(e)}") from e


@tool(
    name="search_notes",
    description=(
        "搜尋使用者裝置上的備忘錄內容。根據關鍵詞在備忘錄的標題、內容和附件名稱中進行檢索。"
        "注意:操作超時時間為60秒,請勿重複呼叫此工具,如果超時或失敗,最多重試一次。"
    ),
)
async def search_notes(query: str) -> Dict[str, Any]:
    """搜尋備忘錄.

    Args:
        query: 搜尋關鍵詞

    Returns:
        裝置返回的完整 outputs，經 format_success_response 包裝
    """
    try:
        logger.info(f"[SEARCH_NOTE_TOOL] Searching notes - query: {query}")

        if not query or not isinstance(query, str):
            raise ToolInputError("缺少必填引數 query（搜尋關鍵詞）")

        query = query.strip()
        if not query:
            raise ToolInputError("query 不能為空")

        # SearchNote：executeParam 不含 appType、permissionId
        command = {
            "header": {
                "namespace": "Common",
                "name": "Action",
            },
            "payload": {
                "cardParam": {},
                "executeParam": {
                    "executeMode": "background",
                    "intentName": "SearchNote",
                    "bundleName": "com.huawei.hmos.notepad",
                    "dimension": "",
                    "needUnlock": True,
                    "actionResponse": True,
                    "timeOut": 5,
                    "intentParam": {
                        "query": query,
                    },
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        outputs = await execute_device_command("SearchNote", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        raise_if_device_error(outputs, "搜尋備忘錄失敗")

        result = outputs.get("result")
        if not isinstance(result, dict):
            result = {}
        n = len(result.get("items", []))
        logger.info(f"[SEARCH_NOTE_TOOL] Search completed, items={n}")

        return format_success_response(dict(outputs), f"搜尋到 {n} 條備忘錄")

    except ToolInputError:
        raise
    except Exception as e:
        logger.error(f"[SEARCH_NOTE_TOOL] Failed to search notes: {e}")
        raise RuntimeError(f"搜尋備忘錄失敗: {str(e)}") from e


@tool(
    name="modify_note",
    description=(
        "在指定備忘錄中追加新內容。使用前必須先呼叫 search_notes 工具獲取備忘錄的 entityId。"
        "引數說明：entityId 是備忘錄的唯一識別符號（從 search_notes 工具獲取），"
        "text 是要追加的文字內容。"
        "注意:操作超時時間為60秒,請勿重複呼叫此工具,如果超時或失敗,最多重試一次。"
    ),
)
async def modify_note(
    entity_id: str,
    text: str,
) -> Dict[str, Any]:
    """修改備忘錄（追加模式）.

    Args:
        entity_id: 備忘錄實體 ID（裝置側欄位名為 entityId）
        text: 要追加的文字

    Returns:
        裝置返回的完整 outputs，經 format_success_response 包裝
    """
    try:
        logger.info(f"[MODIFY_NOTE_TOOL] Modifying note - entity_id: {entity_id}")

        if not entity_id or not isinstance(entity_id, str):
            raise ToolInputError("缺少必填引數 entity_id（裝置側 entityId）")
        if not text or not isinstance(text, str):
            raise ToolInputError("缺少必填引數 text（要追加的文字內容）")

        command = {
            "header": {
                "namespace": "Common",
                "name": "Action",
            },
            "payload": {
                "cardParam": {},
                "executeParam": {
                    "executeMode": "background",
                    "intentName": "ModifyNote",
                    "bundleName": "com.huawei.hmos.notepad",
                    "needUnlock": True,
                    "actionResponse": True,
                    "appType": "OHOS_APP",
                    "timeOut": 5,
                    "intentParam": {
                        "contentType": "1",
                        "text": text,
                        "entityId": entity_id,
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

        outputs = await execute_device_command("ModifyNote", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        raise_if_device_error(outputs, "修改備忘錄失敗")

        logger.info("[MODIFY_NOTE_TOOL] Note modified successfully")

        return format_success_response(dict(outputs), "備忘錄修改成功")

    except ToolInputError:
        raise
    except Exception as e:
        logger.error(f"[MODIFY_NOTE_TOOL] Failed to modify note: {e}")
        raise RuntimeError(f"修改備忘錄失敗: {str(e)}") from e
