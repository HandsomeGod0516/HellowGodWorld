# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""image_reading：

依賴配置 channels.xiaoyi：file_upload_url、api_key、uid
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

import aiohttp

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger

from .file_upload_helpers import XiaoyiObsUploadConfig, upload_local_file_public_url
from .utils import ToolInputError


def _is_remote_url(value: str) -> bool:
    try:
        u = urlparse(value.strip())
        return u.scheme in ("http", "https")
    except Exception:
        return False


def _suffix_from_url(url: str) -> str:
    """從 URL 取副檔名，預設 .jpg。"""
    try:
        path = urlparse(url).path
        name = unquote(path.rsplit("/", 1)[-1] or "downloaded_image")
        name = name.split("?")[0]
        ext = os.path.splitext(name)[1]
        return ext if ext else ".jpg"
    except Exception:
        return ".jpg"


async def _download_remote_to_temp(url: str) -> str:
    """下載遠端圖片到臨時檔案；呼叫方負責刪除。"""
    suffix = _suffix_from_url(url)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status}: {resp.reason}")
            data = await resp.read()

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    path = tmp.name
    try:
        tmp.write(data)
        tmp.flush()
    except Exception:
        tmp.close()
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    tmp.close()
    return path


async def _call_image_understanding_api(
    image_url: str, text: str, api_key: str, uid: str, file_upload_url: str
) -> str:
    api_url = (
        f"{file_upload_url}/celia-claw/v1/sse-api/skill/execute"
    )
    trace_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "x-hag-trace-id": trace_id,
        "x-api-key": api_key,
        "x-request-from": "openclaw",
        "x-uid": uid,
        "x-skill-id": "image_comprehension",
        "x-prd-pkg-name": "com.huawei.hag",
    }
    payload: Dict[str, Any] = {
        "version": "1.0",
        "session": {
            "isNew": False,
            "sessionId": "",
            "interactionId": 0,
        },
        "endpoint": {
            "device": {
                "sid": "",
                "deviceId": "",
                "prdVer": "",
                "phoneType": "",
                "sysVer": "",
                "deviceType": 0,
                "timezone": "",
            },
            "locale": "",
            "sysLocale": "",
            "countryCode": "",
        },
        "utterance": {"type": "text", "original": text},
        "actions": [
            {
                "actionSn": str(uuid.uuid4()),
                "actionExecutorTask": {
                    "pluginId": "",
                    "agentState": "OnShelf",
                    "actionName": "imageUnderStandStream",
                    "content": {"imageUrl": image_url, "text": text},
                },
            }
        ],
    }

    last_caption = ""
    buffer = ""

    async with aiohttp.ClientSession() as session:
        async with session.post(
            api_url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if not resp.ok:
                body = await resp.text()
                raise RuntimeError(f"API request failed: {resp.status} {body[:500]}")
            async for chunk in resp.content.iter_chunked(4096):
                if not chunk:
                    continue
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.rstrip("\r")
                    if not line or not line.startswith("data:"):
                        continue
                    data_content = line[5:].strip()
                    if not data_content or data_content == "[DONE]":
                        continue
                    try:
                        data_json = json.loads(data_content)
                    except json.JSONDecodeError:
                        continue
                    for info in data_json.get("abilityInfos") or []:
                        reply = (info.get("actionExecutorResult") or {}).get("reply") or {}
                        si = reply.get("streamInfo") or {}
                        sc = si.get("streamContent")
                        if sc:
                            last_caption = sc
    if not last_caption:
        raise RuntimeError("No caption received from image understanding API")
    return last_caption


def _content_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """與 TS 一致：content[0].text 為 JSON 字串。"""
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False),
            }
        ]
    }


@tool(
    name="image_reading",
    description="""
工具使用場景：
【必須呼叫此工具的情況】
1. 使用者訊息中包含 mediaPath 欄位且不為空（表示使用者傳送了圖片）
2. 使用者希望理解圖片內容，詢問圖片是什麼，例如：
   - "這是什麼？"
   - "圖片裡有什麼？"
   - "幫我看看這張圖"
   - "描述一下這張圖片"
   - "分析一下這張照片"
   - "這個圖片是什麼意思"
   - "識別一下圖片內容"
   - 或任何關於圖片內容的理解、識別、分析類詢問

當同時滿足以上兩個條件時，必須優先呼叫此工具進行影象理解。

工具能力描述：對圖片進行理解和分析，返回圖片的描述內容。

工具引數說明：
a. local_url：本地圖片檔案路徑（可選，通常從使用者訊息的 mediaPath 欄位獲取）
b. remote_url：公網圖片地址（可選）
c. prompt：對圖片的提示問題，預設為"描述這張圖片內容"，可根據使用者的具體問題自定義
d. local_url 與 remote_url 任意一個不為空即可，優先使用 local_url

注意事項：
a. 支援常見圖片格式（jpg, png, gif等）
b. 遠端圖片會先下載到本地再處理
c. 操作超時時間為2分鐘（120秒）
d. 返回影象理解的文字描述內容
""",
)
async def image_reading(
    local_url: Optional[str] = None,
    remote_url: Optional[str] = None,
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """影象理解（與 image-reading-tool.ts 對齊）."""
    lu_sel = local_url if isinstance(local_url, str) and local_url else ""
    ru_sel = remote_url if isinstance(remote_url, str) and remote_url else ""
    if not lu_sel and not ru_sel:
        raise ToolInputError("須至少提供 localUrl 或 remoteUrl 之一")

    # 與 TS：params.prompt || "描述這張圖片內容"
    text = prompt if isinstance(prompt, str) and prompt else "描述這張圖片內容"

    from jiuwenclaw.common.config import get_config

    cfg = get_config()
    xc = cfg.get("channels", {}).get("xiaoyi", {})
    base = xc.get("file_upload_url")
    api_key = xc.get("api_key")
    uid = str(xc.get("uid"))
    if not base or not api_key or not uid:
        raise ToolInputError(
            "缺少 channels.xiaoyi 的 file_upload_url / api_key / uid 配置，無法上傳圖片"
        )

    obs_cfg = XiaoyiObsUploadConfig(base_url=base, api_key=api_key, uid=uid)
    image_input = lu_sel or ru_sel
    image_source = "local" if lu_sel else "remote"

    downloaded: Optional[str] = None
    image_obs_url: Optional[str] = None
    try:
        async with aiohttp.ClientSession() as session:
            # 與 processImageInput：先遠端 URL，再本地檔案
            if _is_remote_url(image_input):
                logger.info("[IMAGE_READING_TOOL] remote URL, download then upload")
                downloaded = await _download_remote_to_temp(image_input)
                image_obs_url = await upload_local_file_public_url(
                    session, obs_cfg, downloaded
                )
            elif os.path.isfile(image_input):
                logger.info("[IMAGE_READING_TOOL] local file upload")
                image_obs_url = await upload_local_file_public_url(
                    session, obs_cfg, image_input
                )
            else:
                raise RuntimeError(
                    f"Invalid image input: must be a remote URL or local file path, "
                    f"got: {image_input}"
                )

        if not image_obs_url:
            raise RuntimeError("圖片上傳失敗：無法獲取圖片訪問地址")

        caption = await _call_image_understanding_api(
            image_obs_url, text, api_key, uid, base
        )
        return _content_payload(
            {
                "caption": caption,
                "prompt": text,
                "imageSource": image_source,
                "success": True,
            }
        )
    except ToolInputError:
        raise
    except Exception as e:
        logger.error("[IMAGE_READING_TOOL] execution failed: %s", e)
        msg = str(e) if str(e) else "圖片分析失敗"
        return _content_payload(
            {
                "error": msg,
                "prompt": text,
                "imageSource": image_source,
                "success": False,
            }
        )
    finally:
        if downloaded and os.path.isfile(downloaded):
            try:
                os.unlink(downloaded)
            except OSError:
                pass
