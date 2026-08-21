# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Save tools - 儲存到手機工具.

包含：
- save_media_to_gallery: 將圖片/影片儲存到手機相簿
- save_file_to_file_manager: 將檔案儲存到手機檔案管理器
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import aiohttp

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger
from .utils import (
    execute_device_command,
    raise_if_device_error,
    ToolInputError,
)
from .file_upload_helpers import XiaoyiObsUploadConfig, upload_local_file_public_url


async def _ensure_public_url(
    url: str,
    obs_cfg: XiaoyiObsUploadConfig,
    session: aiohttp.ClientSession,
) -> str:
    """如果 url 是本地路徑，上傳獲取公網 URL；否則直接返回."""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    public_url = await upload_local_file_public_url(session, obs_cfg, url)
    if not public_url:
        raise RuntimeError("本地檔案上傳失敗，無法獲取公網URL")
    return public_url


def _get_obs_config() -> XiaoyiObsUploadConfig:
    """從配置中讀取 OBS 上傳配置."""
    from jiuwenclaw.common.config import get_config

    cfg = get_config()
    xc = cfg.get("channels", {}).get("xiaoyi", {})
    base = xc.get("file_upload_url")
    api_key = xc.get("api_key")
    uid = str(xc.get("uid"))
    if not base or not api_key or not uid:
        raise ToolInputError("缺少 channels.xiaoyi 的 file_upload_url / api_key / uid 配置，無法上傳檔案")
    return XiaoyiObsUploadConfig(base_url=base, api_key=api_key, uid=uid)


@tool(
    name="save_media_to_gallery",
    description="""將圖片檔案或者影片檔案儲存到手機相簿。
  工具引數說明：
  a. mediaType：非必填，string型別，不傳端側預設為pic。支援傳 pic(圖片) 或 video(影片)。
  b. fileName：非必填，string型別，檔名稱，不傳手機側預設生成隨機uuid。
  c. url：必填，string型別，支援本地路徑或者公網url路徑。如果是本地路徑，會先上傳獲取公網url再儲存到相簿。

  注意:
  a. 操作超時時間為60秒,請勿重複呼叫此工具
  b. 如果遇到各類呼叫失敗場景,最多隻能重試一次，不可以重複呼叫多次。
  c. 呼叫工具前需認真檢查呼叫引數是否滿足工具要求

  回覆約束：如果工具返回沒有授權或者其他報錯，只需要完整描述沒有授權或者其他報錯內容即可，不需要主動給使用者提供解決方案，例如告訴使用者如何授權，如何解決報錯等都是不需要的，請嚴格遵守。
  """,
)
async def save_media_to_gallery(
    url: str,
    media_type: Optional[str] = None,
    file_name: Optional[str] = None,
) -> Dict[str, Any]:
    """儲存圖片/影片到手機相簿（與 xy_channel save-media-to-gallery-tool.ts 對齊）.

    Args:
        url: 本地路徑或公網 URL（必填）
        media_type: pic 或 video（可選，預設 pic）
        file_name: 檔名稱（可選，自動去除字尾）

    Returns:
        content[0].text: JSON 字串（event.outputs）
    """
    try:
        if not url or not isinstance(url, str):
            raise ToolInputError("缺少必填引數: url")

        if media_type and media_type not in ("pic", "video"):
            raise ToolInputError(f"mediaType只支援 pic 或 video，當前值: {media_type}")

        # 去除 fileName 字尾
        sanitized_name = file_name
        if sanitized_name and isinstance(sanitized_name, str):
            last_dot = sanitized_name.rfind(".")
            if last_dot > 0:
                sanitized_name = sanitized_name[:last_dot]

        obs_cfg = _get_obs_config()

        async with aiohttp.ClientSession() as session:
            public_url = await _ensure_public_url(url, obs_cfg, session)

        intent_param: Dict[str, str] = {"url": public_url}
        if media_type:
            intent_param["mediaType"] = media_type
        if sanitized_name:
            intent_param["fileName"] = sanitized_name

        logger.info(
            "[SAVE_MEDIA_TO_GALLERY_TOOL] Saving media - type=%s, url=%s",
            media_type or "pic",
            public_url[:100] + "..." if len(public_url) > 100 else public_url,
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
                    "intentName": "SaveMediaToGallery",
                    "bundleName": "com.huawei.hmos.vassistant",
                    "dimension": "",
                    "needUnlock": True,
                    "actionResponse": True,
                    "appType": "OHOS_APP",
                    "timeOut": 5,
                    "intentParam": intent_param,
                    "permissionId": ["ohos.permission.WRITE_IMAGEVIDEO"],
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        outputs = await execute_device_command("SaveMediaToGallery", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        raise_if_device_error(outputs, "儲存媒體到相簿失敗")

        logger.info("[SAVE_MEDIA_TO_GALLERY_TOOL] Save completed successfully")

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
        logger.error(f"[SAVE_MEDIA_TO_GALLERY_TOOL] Failed to save media: {e}")
        raise RuntimeError(f"儲存媒體到相簿失敗: {str(e)}") from e


@tool(
    name="save_file_to_file_manager",
    description="""將檔案儲存到手機檔案管理器。
  工具引數說明：
  a. fileName：必填，string型別，檔名稱。
  b. url：必填，string型別，支援本地路徑或者公網url路徑。如果是本地路徑，會先上傳獲取公網url再儲存到手機。
  c. suffix：必填，string型別，檔案字尾，例如 ppt、doc、pdf 等。

  注意:
  a. 操作超時時間為60秒,請勿重複呼叫此工具
  b. 如果遇到各類呼叫失敗場景,不可以重試，直接返回錯誤。
  c. 呼叫工具前需認真檢查呼叫引數是否滿足工具要求

  回覆約束：如果工具返回沒有授權或者其他報錯，只需要完整描述沒有授權或者其他報錯內容即可，不需要主動給使用者提供解決方案，例如告訴使用者如何授權，如何解決報錯等都是不需要的，請嚴格遵守。
  """,
)
async def save_file_to_file_manager(
    file_name: str,
    url: str,
    suffix: str,
) -> Dict[str, Any]:
    """儲存檔案到手機檔案管理器（與 xy_channel save-file-to-phone-tool.ts 對齊）.

    Args:
        file_name: 檔名稱（必填）
        url: 本地路徑或公網 URL（必填）
        suffix: 檔案字尾，例如 ppt、doc、pdf（必填）

    Returns:
        content[0].text: JSON 字串（event.outputs）
    """
    try:
        if not url or not isinstance(url, str):
            raise ToolInputError("缺少必填引數: url")
        if not file_name or not isinstance(file_name, str):
            raise ToolInputError("缺少必填引數: fileName")
        if not suffix or not isinstance(suffix, str):
            raise ToolInputError("缺少必填引數: suffix")

        obs_cfg = _get_obs_config()

        async with aiohttp.ClientSession() as session:
            public_url = await _ensure_public_url(url, obs_cfg, session)

        intent_param: Dict[str, str] = {
            "fileName": file_name,
            "url": public_url,
            "suffix": suffix,
        }

        logger.info(
            "[SAVE_FILE_TO_PHONE_TOOL] Saving file - name=%s, suffix=%s",
            file_name,
            suffix,
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
                    "intentName": "SaveFileToFileManager",
                    "bundleName": "com.huawei.hmos.vassistant",
                    "dimension": "",
                    "needUnlock": True,
                    "actionResponse": True,
                    "appType": "OHOS_APP",
                    "timeOut": 5,
                    "timeout": 55000,
                    "intentParam": intent_param,
                    "permissionId": ["ohos.permission.WRITE_IMAGEVIDEO"],
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        outputs = await execute_device_command("SaveFileToFileManager", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        raise_if_device_error(outputs, "儲存檔案到手機失敗")

        logger.info("[SAVE_FILE_TO_PHONE_TOOL] Save completed successfully")

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
        logger.error(f"[SAVE_FILE_TO_PHONE_TOOL] Failed to save file: {e}")
        raise RuntimeError(f"儲存檔案到手機失敗: {str(e)}") from e
