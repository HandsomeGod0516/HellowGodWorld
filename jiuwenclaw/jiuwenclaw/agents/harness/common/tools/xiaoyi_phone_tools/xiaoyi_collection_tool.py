# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Collection tools - 小藝收藏工具.

包含：
- query_collection: 檢索使用者在小藝收藏中記下來的公共知識資料
- add_collection: 向小藝收藏中新增公共知識資料
- delete_collection: 從小藝收藏中刪除已儲存的公共知識資料
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger
from .utils import (
    execute_device_command,
    raise_if_device_error,
    ToolInputError,
)
from .file_upload_helpers import XiaoyiObsUploadConfig, upload_local_file_public_url


@tool(
    name="query_collection",
    description="""檢索使用者在小藝收藏中記下來的公共知識資料，本技能支援查詢使用者收藏的
公共知識資料，也可以根據特定語義化描述進行特定內容的檢索，透過引數進行控制。
本技能返回結果
中，linkTitle是收藏內容的標題，description是對收藏內容的總結，label是收藏內容的標籤，
linkUrl是可以直接訪問的原始內容連結。如果你認為某條資料對使用者互動有用，可以透過
linkUrl抓取更加豐富的原始資料。
  注意:
  a. 操作超時時間為60秒,請勿重複呼叫此工具
  b. 如果遇到各類呼叫失敗場景,最多隻能重試一次，不可以重複呼叫多次。
  c. 呼叫工具前需認真檢查呼叫引數是否滿足工具要求

  回覆約束：如果工具返回沒有授權或者其他報錯，只需要完整描述沒有授權或者其他報錯
內容即可，不需要主動給使用者提供解決方案，例如告訴使用者如何授權，如何解決報錯等都是
不需要的，請嚴格遵守。
  """,
)
async def query_collection(
    query_all: str = "true",
    query: Optional[str] = None,
) -> Dict[str, Any]:
    """檢索小藝收藏（與 xy_channel xiaoyi-collection-tool.ts 行為對齊）.

    Args:
        query_all: 是否查詢全部收藏，預設 "true"
        query: 查詢條件，queryAll 不為 "true" 時必填

    Returns:
        content[0].text: JSON 字串（event.outputs）
    """
    try:
        logger.info(
            "[QUERY_COLLECTION_TOOL] Starting execution - queryAll=%r, query=%r",
            query_all,
            query,
        )

        if query_all != "true" and (not query or not isinstance(query, str)):
            raise ToolInputError("queryAll不為true時，query引數必填")

        intent_param: Dict[str, str] = {}
        if query_all == "true":
            intent_param["queryAll"] = "true"
        else:
            intent_param["queryAll"] = "false"
            intent_param["query"] = query

        command = {
            "header": {
                "namespace": "Common",
                "name": "Action",
            },
            "payload": {
                "cardParam": {},
                "executeParam": {
                    "executeMode": "background",
                    "intentName": "QueryCollection",
                    "bundleName": "com.huawei.hmos.vassistant",
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

        outputs = await execute_device_command("QueryCollection", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        raise_if_device_error(outputs, "查詢小藝收藏失敗")

        logger.info("[QUERY_COLLECTION_TOOL] Query completed successfully")

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
        logger.error(f"[QUERY_COLLECTION_TOOL] Failed to query collection: {e}")
        raise RuntimeError(f"查詢小藝收藏失敗: {str(e)}") from e


def _normalize_item_ids(param: Any) -> List[str]:
    """將 item_ids 規範為字串列表（支援陣列或 JSON 陣列字串）。"""
    if param is None:
        raise ToolInputError("缺少必填引數 itemIds")
    if isinstance(param, list):
        return param
    if isinstance(param, str):
        try:
            parsed = json.loads(param)
        except json.JSONDecodeError as e:
            raise ToolInputError(
                f"itemIds must be a valid JSON array string. Parse error: {e}"
            ) from e
        if not isinstance(parsed, list):
            raise ToolInputError(
                "itemIds must be an array or a JSON string representing an array"
            )
        return parsed
    raise ToolInputError(
        f"itemIds must be an array or a JSON string, got {type(param).__name__}"
    )


@tool(
    name="delete_collection",
    description="""從小藝收藏中刪除之前已儲存的公共知識資料。任何使用者希望刪除已儲存到
個人
知識庫的資料都可以呼叫本技能。如果使用者想更新之前的收藏資料，需要先query獲取itemId
然後再delete，最後執行Add，按照這個步驟完成收藏資料更新。
  注意:
  a. 操作超時時間為60秒,請勿重複呼叫此工具
  b. 如果遇到各類呼叫失敗場景,最多隻能重試一次，不可以重複呼叫多次。
  c. 呼叫工具前需認真檢查呼叫引數是否滿足工具要求

  回覆約束：如果工具返回沒有授權或者其他報錯，只需要完整描述沒有授權或者其他報錯
內容即可，不需要主動給使用者提供解決方案，例如告訴使用者如何授權，如何解決報錯等都是
不需要的，請嚴格遵守。
  """,
)
async def delete_collection(
    item_ids: Union[str, List[str]],
) -> Dict[str, Any]:
    """刪除小藝收藏（與 xy_channel xiaoyi-delete-collection-tool.ts 對齊）.

    Args:
        item_ids: 待刪除的資料的 itemId 合集，支援陣列或 JSON 字串

    Returns:
        content[0].text: JSON 字串（event.outputs）
    """
    try:
        normalized = _normalize_item_ids(item_ids)

        if not normalized or len(normalized) == 0:
            raise ToolInputError("itemIds array cannot be empty")

        logger.info(
            "[DELETE_COLLECTION_TOOL] Deleting %s collection item(s)",
            len(normalized),
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
                    "intentName": "DeleteCollection",
                    "bundleName": "com.huawei.hmos.vassistant",
                    "needUnlock": True,
                    "actionResponse": True,
                    "appType": "OHOS_APP",
                    "timeOut": 5,
                    "intentParam": {
                        "itemIds": normalized,
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

        outputs = await execute_device_command("DeleteCollection", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        raise_if_device_error(outputs, "刪除小藝收藏失敗")

        logger.info("[DELETE_COLLECTION_TOOL] Delete completed successfully")

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
        logger.error(f"[DELETE_COLLECTION_TOOL] Failed to delete collection: {e}")
        raise RuntimeError(f"刪除小藝收藏失敗: {str(e)}") from e


@tool(
    name="add_collection",
    description="""向小藝收藏中新增公共知識資料，可以給使用者提供個性化體驗。任何使用者
希望
儲存到個人化知識庫中的資料都可以呼叫本技能。不同型別的資料對應的資料要求如下：
請求入參說明：
● content:必填欄位，資料型別為string，功能描述是該欄位是使用者新增收藏的連結url或
  文字原文。適用於HYPER_LINK和TEXT型別。
● uri:必填欄位，資料型別為string，功能描述是該欄位是圖片或檔案的端儲存地址連結。
  適用於IMAGE和FILE型別。
● sourceAppBundleName:非必填欄位，資料型別為string，功能描述是標識該資料的來源
  應用。
● dataType:必填欄位，資料型別為string，功能描述是標識資料型別。HYPER_LINK標識
  網頁，TEXT標識文字，IMAGE標識圖片，FILE標識檔案。
● title:非必填欄位，資料型別為string，功能描述是標識檔案型別資料的檔名稱。
  適用於FILE型別。
說明：如果dataType為HYPER_LINK或TEXT，則content欄位必填且不能為空；如果dataType
為IMAGE或FILE，則uri欄位必填且不能為空。當使用者希望收藏海報、截圖等圖片類資料時，
請將資料以圖片IMAGE的形式存入到小藝幫記；當使用者希望收藏電子書、筆記、報告、素材、
文件、合同、協議、簡歷、證書、報表、日誌、安裝包、壓縮包等描述的檔案時，請將資料
以檔案FILE的形式存入到小藝幫記。
當你成功收藏這個資料到小藝幫記後，請在最後顯示"已成功把資料新增到[小藝幫記]
(vassistant://voice/main?page=CollectionPage&jumpHomePageTab=myCollection)"，
  注意:
  a. 操作超時時間為60秒,請勿重複呼叫此工具
  b. 如果遇到各類呼叫失敗場景,最多隻能重試一次，不可以重複呼叫多次。
  c. 呼叫工具前需認真檢查呼叫引數是否滿足工具要求

  回覆約束：如果工具返回沒有授權或者其他報錯，只需要完整描述沒有授權或者其他報錯
內容即可，不需要主動給使用者提供解決方案，例如告訴使用者如何授權，如何解決報錯等都是
不需要的，請嚴格遵守。
  """,
)
async def add_collection(
    data_type: str,
    content: Optional[str] = None,
    uri: Optional[str] = None,
    source_app_bundle_name: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """新增小藝收藏（與 xy_channel xiaoyi-add-collection-tool.ts 對齊）.

    Args:
        data_type: 資料型別，HYPER_LINK/TEXT/IMAGE/FILE
        content: 連結url或文字原文（HYPER_LINK/TEXT 型別時必填）
        uri: 圖片或檔案的地址連結（IMAGE/FILE 型別時必填）
        source_app_bundle_name: 來源應用標識
        title: 檔名稱（FILE 型別時使用）

    Returns:
        content[0].text: JSON 字串（event.outputs）
    """
    try:
        valid_types = ("HYPER_LINK", "TEXT", "IMAGE", "FILE")
        if not data_type or data_type not in valid_types:
            raise ToolInputError(
                f"dataType必填且必須為 HYPER_LINK、TEXT、IMAGE、FILE 之一，當前值: {data_type}"
            )

        if data_type in ("HYPER_LINK", "TEXT") and (not content or not isinstance(content, str)):
            raise ToolInputError(f"dataType為{data_type}時，content欄位必填且不能為空")

        if data_type in ("IMAGE", "FILE") and (not uri or not isinstance(uri, str)):
            raise ToolInputError(f"dataType為{data_type}時，uri欄位必填且不能為空")

        logger.info(
            "[ADD_COLLECTION_TOOL] Adding collection - dataType=%s",
            data_type,
        )

        # 如果 uri 是本地路徑，上傳獲取公網 URL
        public_uri = uri
        _remote_prefixes = ("http://", "https://", "file://")
        if uri and not uri.startswith(_remote_prefixes):
            import aiohttp
            from jiuwenclaw.common.config import get_config

            cfg = get_config()
            xc = cfg.get("channels", {}).get("xiaoyi", {})
            base = xc.get("file_upload_url")
            api_key = xc.get("api_key")
            uid = str(xc.get("uid"))
            if not base or not api_key or not uid:
                raise RuntimeError("缺少 channels.xiaoyi 的 file_upload_url / api_key / uid 配置")

            obs_cfg = XiaoyiObsUploadConfig(base_url=base, api_key=api_key, uid=uid)
            async with aiohttp.ClientSession() as session:
                public_uri = await upload_local_file_public_url(session, obs_cfg, uri)

            if not public_uri:
                raise RuntimeError("本地檔案上傳失敗，無法獲取公網URL")

        intent_param: Dict[str, str] = {"dataType": data_type}
        if content:
            intent_param["content"] = content
        if public_uri:
            intent_param["uri"] = public_uri
        if source_app_bundle_name:
            intent_param["sourceAppBundleName"] = source_app_bundle_name
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
                    "intentName": "AddCollection",
                    "bundleName": "com.huawei.hmos.vassistant",
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

        outputs = await execute_device_command("AddCollection", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        raise_if_device_error(outputs, "新增小藝收藏失敗")

        logger.info("[ADD_COLLECTION_TOOL] Add completed successfully")

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
        logger.error(f"[ADD_COLLECTION_TOOL] Failed to add collection: {e}")
        raise RuntimeError(f"新增小藝收藏失敗: {str(e)}") from e
