# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""File tools - 檔案工具.

包含：
- search_file: 搜尋手機檔案
- upload_file: 上傳手機檔案獲取公網 URL
- send_file_to_user: 將本地檔案或公網檔案傳到使用者手機
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

import aiohttp

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger
from .utils import (
    ToolInputError,
    execute_device_command,
    format_success_response,
    raise_if_device_error,
)


def _normalize_file_infos(param: Any) -> List[Dict[str, Any]]:
    """將 fileInfos 規範為陣列（支援陣列或 JSON 陣列字串）。"""
    if param is None:
        raise ToolInputError("缺少必填引數 fileInfos")
    if isinstance(param, list):
        return param
    if isinstance(param, str):
        try:
            parsed = json.loads(param)
        except json.JSONDecodeError as e:
            raise ToolInputError(
                f"fileInfos 必須是合法 JSON 陣列字串。解析錯誤: {e}"
            ) from e
        if not isinstance(parsed, list):
            raise ToolInputError(
                "fileInfos 必須是陣列或表示陣列的 JSON 字串（解析結果不是陣列）"
            )
        return parsed
    raise ToolInputError(
        f"fileInfos 必須是陣列或 JSON 陣列字串，當前型別: {type(param).__name__}"
    )


@tool(
    name="search_file",
    description="""搜尋手機檔案系統的檔案。

【重要】使用約束：此工具僅在使用者顯著說明要從手機搜尋時才執行，例如：
- "從我手機裡面搜尋xxxx"
- "從手機檔案系統找一下xxxx"
- "在手機上查詢檔案xxxx"
- "搜尋手機裡的檔案"

如果使用者沒有明確說明從手機搜尋（如僅說"搜尋檔案"、"找一下xxxx"），應預設從 openclaw 本地的檔案系統查詢，不要呼叫此工具。

功能說明：根據關鍵詞搜尋檔名稱或內容，返回匹配的檔案列表（包括檔名、路徑、大小、修改時間等資訊）。

注意事項：操作超時時間為60秒，請勿重複呼叫此工具，如果超時或失敗，最多重試一次。""",
)
async def search_file(
    query: str,
) -> Dict[str, Any]:
    """搜尋檔案.

    Args:
        query: 搜尋關鍵詞，用於匹配檔名稱、字尾名或檔案內容

    Returns:
        裝置返回的完整 outputs（JSON 序列化後置於 content）
    """
    try:
        logger.info(f"[SEARCH_FILE_TOOL] Searching files - query: {query}")

        if not query or not isinstance(query, str):
            raise ToolInputError("缺少必填引數 query（搜尋關鍵詞）")

        query = query.strip()
        if not query:
            raise ToolInputError("query 不能為空")

        command = {
            "header": {
                "namespace": "Common",
                "name": "Action",
            },
            "payload": {
                "cardParam": {},
                "executeParam": {
                    "executeMode": "background",
                    "intentName": "SearchFile",
                    "bundleName": "com.huawei.hmos.aidispatchservice",
                    "needUnlock": True,
                    "actionResponse": True,
                    "appType": "OHOS_APP",
                    "timeOut": 5,
                    "intentParam": {
                        "query": query,
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

        # 成功時返回完整 outputs，不在此處按 code 攔截
        outputs = await execute_device_command("SearchFile", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        raise_if_device_error(outputs, "搜尋檔案失敗")

        result = outputs.get("result")
        if not isinstance(result, dict):
            result = {}
        n = len(result.get("items", []))
        logger.info(f"[SEARCH_FILE_TOOL] Found {n} files")

        return format_success_response(dict(outputs), f"搜尋到 {n} 個檔案")

    except ToolInputError:
        raise
    except Exception as e:
        logger.error(f"[SEARCH_FILE_TOOL] Failed to search files: {e}")
        raise RuntimeError(f"搜尋檔案失敗: {str(e)}") from e


@tool(
    name="upload_file",
    description="""工具能力描述：將手機本地檔案上傳並獲取可公網訪問的 URL。

  前置工具呼叫：此工具使用前必須先呼叫 search_file 或者 query_collection 工具獲取檔案的 uri

  工具引數說明：
  a. 入參中的file_Infos陣列，每個元素必須包含mediaUri欄位（對應於search_file工具或者query_collection返回結果中的uri），必須與search_file結果中對應的uri完全保持一致，不要自行修改。
  b. file_infos 中的timeout欄位是可選的，表示上傳檔案超時時間，單位是毫秒，預設是20000（20秒）。
  c. file_infos 是檔案在手機本地的資訊陣列（從 search_file 工具或者 query_collection 響應中獲取）。限制：每次最多支援傳入 5 條檔案資訊。

  注意事項：
  a. 操作超時時間為60秒,請勿重複呼叫此工具,如果超時或失敗,最多重試一次。
  b. 此工具返回的檔案連結為使用者公網可訪問的連結，如果需要對檔案進行額外的操作，需要先根據返回的url下載檔案，然後進行下一步處理。""",
)
async def upload_file(file_infos: Union[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """上傳檔案

    Args:
        file_infos: 檔案資訊陣列或 JSON 陣列字串；每項含 mediaUri（必需）、timeout（可選，預設 20000 毫秒）

    Returns:
        content[0].text 為 JSON：fileUrls、count、message
    """
    try:
        file_infos_list = _normalize_file_infos(file_infos)
        logger.info(
            "[UPLOAD_FILE_TOOL] Uploading files - fileInfos count: %s",
            len(file_infos_list),
        )

        if len(file_infos_list) == 0:
            raise ToolInputError("fileInfos 陣列不能為空")

        if len(file_infos_list) > 5:
            raise ToolInputError(
                f"最多支援 5 條檔案資訊，當前提供了 {len(file_infos_list)} 條。請分批處理。"
            )

        for i, file_info in enumerate(file_infos_list):
            if not isinstance(file_info, dict):
                raise ToolInputError(
                    f"fileInfos[{i}] 必須是包含 mediaUri 的物件"
                )
            if not file_info.get("mediaUri") or not isinstance(
                file_info["mediaUri"], str
            ):
                raise ToolInputError(
                    f"fileInfos[{i}] 必須包含有效的 mediaUri 字串"
                )
            if not file_info.get("timeout"):
                file_info["timeout"] = "20000"

        command = {
            "header": {
                "namespace": "Common",
                "name": "Action",
            },
            "payload": {
                "cardParam": {},
                "executeParam": {
                    "executeMode": "background",
                    "intentName": "FileUploadForClaw",
                    "bundleName": "com.huawei.hmos.vassistant",
                    "needUnlock": True,
                    "actionResponse": True,
                    "appType": "OHOS_APP",
                    "timeOut": 5,
                    "intentParam": {"fileInfos": file_infos_list},
                    "permissionId": [],
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        outputs = await execute_device_command("FileUploadForClaw", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        raise_if_device_error(outputs, "獲取檔案 URL 失敗")

        result = outputs.get("result", {}) if isinstance(outputs, dict) else {}
        file_urls: List[Any] = []
        if isinstance(result, dict):
            raw = result.get("fileUrls")
            if isinstance(raw, list):
                file_urls = raw

        decoded_urls: List[str] = []
        for url in file_urls:
            if not isinstance(url, str):
                logger.warning(
                    "[UPLOAD_FILE_TOOL] URL 不是字串: %s",
                    type(url),
                )
                continue
            decoded = url.replace("\\u003d", "=").replace("\\u0026", "&")
            if decoded:
                decoded_urls.append(decoded)

        logger.info(
            "[UPLOAD_FILE_TOOL] Retrieved %s file URLs",
            len(decoded_urls),
        )

        # 與 upload-file-tool.ts 一致：content[0].text 僅為 { fileUrls, count, message }
        payload = {
            "fileUrls": decoded_urls,
            "count": len(decoded_urls),
            "message": f"成功獲取 {len(decoded_urls)} 個檔案的公網訪問 URL",
        }
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            ]
        }

    except ToolInputError:
        raise
    except Exception as e:
        logger.error(f"[UPLOAD_FILE_TOOL] Failed to upload files: {e}")
        raise RuntimeError(f"上傳檔案失敗: {str(e)}") from e


# ---------------------------------------------------------------------------
# send_file_to_user - 將本地檔案或公網檔案傳到使用者手機
# ---------------------------------------------------------------------------

_FILE_TYPE_TO_MIME_TYPE: Dict[str, str] = {
    "txt": "text/plain",
    "html": "text/html",
    "css": "text/css",
    "js": "application/javascript",
    "json": "application/json",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
    "zip": "application/zip",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "mp3": "audio/mpeg",
    "mp4": "video/mp4",
}


def _get_mime_type(filename: str) -> str:
    """根據副檔名獲取 MIME 型別."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _FILE_TYPE_TO_MIME_TYPE.get(ext, "text/plain")


async def _download_remote_file(url: str) -> str:
    """下載遠端檔案到臨時檔案，返回本地路徑.

    Raises:
        RuntimeError: 下載失敗
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status}: {resp.reason}")
            data = await resp.read()

    # 從 URL 提取檔名
    parsed = urlparse(url)
    raw_name = os.path.basename(parsed.path) or "downloaded_file"
    raw_name = raw_name.split("?")[0]

    suffix = os.path.splitext(raw_name)[1] or ""
    base_name = os.path.splitext(raw_name)[0] or "downloaded_file"
    unique_name = f"{base_name}_{int(time.time())}{suffix}"

    tmp_dir = tempfile.gettempdir()
    local_path = os.path.join(tmp_dir, unique_name)

    with open(local_path, "wb") as f:
        f.write(data)

    logger.info("[SEND_FILE_TO_USER] Downloaded remote file: %s -> %s", url, local_path)
    return local_path
