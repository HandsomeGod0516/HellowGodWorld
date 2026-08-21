# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Photo tools - 相簿工具.

包含：
- search_photo_gallery: 搜尋相簿
- upload_photo: 上傳照片獲取公網 URL
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Union

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.common.utils import logger
from .utils import (
    execute_device_command,
    format_success_response,
    raise_if_device_error,
    ToolInputError,
)


@tool(
    name="search_photo_gallery",
    description="""外掛功能描述：搜尋使用者手機相簿中的照片

  工具使用約束：如果使用者說從手機相簿中或者從相簿中查詢xx圖片時呼叫此工具,注意此工具僅支援從本地相簿檢索，不支援雲空間相簿檢索。

  工具輸入輸出簡介：
  a. 根據影象描述語料檢索匹配的照片,返回照片在手機本地的 mediaUri以及thumbnailUri。
  b. 返回的 mediaUri以及thumbnailUri 是本地路徑,無法直接下載或訪問。
  如需下載、檢視、使用或展示照片,請使用 upload_photo 工具將 mediaUri或者thumbnailUri 轉換為可訪問的公網 URL。
  c. mediaUri代表手機相簿中的圖片原圖路徑，圖片大小比較大，清晰度比較高
  d. thumbnailUri代表手機相簿中的圖片縮圖路徑，圖片大小比較小，清晰度適中，建議在upload_photo 工具的入參中優先使用此路徑，不容易引起上傳超時等問題

  搜尋能力邊界：
  a. 支援口語化輸入：改寫模型會自動提取姓名、種類、地點等實體，可以使用自然語言描述（如"小狗的照片"、"南京拍的風景"）
  b. 支援相簿搜尋：可以在query中包含相簿名稱（如"西安之行相簿的照片"）
  c. 支援人像搜尋：前提是照片有人像tag，且需要口語化描述（如"張三的照片"）
  d. 不支援時間相對詞：不支援"最新"、"最舊"、"最早"等表述，需要使用具體時間（如"2024年的照片"而非"去年的照片"）
  e. 不支援多實體查詢：不支援"或"邏輯和時間範圍（如"南京或上海的照片"、"近三年的照片"），需要拆分成多次獨立查詢
  f. 不支援POI逆地理對映：照片的location是門牌號，用真實場地名稱可能搜不到
  g. 不支援收藏感知：無法感知照片是否被收藏
  h. 不支援細粒度品種：對於動物、植物等的具體品種識別能力有限
  i. 注意：POI提取可能不準確：地名可能作為語義搜尋條件，可能導致"xx湖"搜到"yy江"或"zz灣"的照片

  查詢最佳化建議：
  a. 時間查詢：將"最新"、"去年"、"近三年"等轉換為具體年份（如"2024年"、"2023年到2025年"需拆分成"2023年"、"2024年"、"2025年"三次查詢）
  b. 多條件查詢：將"或"邏輯拆分成多次查詢（如"南京或上海的照片"→先查"南京的照片"，再查"上海的照片"）
  c. 實體原子化：確保每個query只包含一個原子實體（地點、人名、物品等）
  d. 相簿名稱：如果知道相簿名，直接在query中包含相簿名可以提高準確度

  注意事項：
  a. 只有當使用者明確表達從手機相簿搜尋或者從相簿搜尋時才執行此工具，如果使用者僅表達要搜尋xxx圖片，並沒有說明搜尋資料來源，則不要貿然呼叫此外掛，可以優先嚐試websearch或者詢問使用者是否要從手機相簿中搜尋。
  b. 操作超時時間為60秒,請勿重複呼叫此工具,如果超時或失敗,最多重試一次。
  c. 如果使用者請求包含多個實體或時間範圍，需要主動拆分成多次查詢並告知使用者。
  """,
)
async def search_photo_gallery(
    query: str,
) -> Dict[str, Any]:
    """搜尋照片.

    Args:
        query: 影象描述語料

    Returns:
        裝置返回的完整 outputs，經 format_success_response 包裝
    """
    try:
        logger.info(f"[SEARCH_PHOTO_GALLERY_TOOL] Searching photos - query: {query}")

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
                    "intentName": "SearchPhotoVideo",
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

        outputs = await execute_device_command("SearchPhotoVideo", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        raise_if_device_error(outputs, "搜尋照片失敗")

        result = outputs.get("result")
        if not isinstance(result, dict):
            result = {}
        n = len(result.get("items", []))
        logger.info(f"[SEARCH_PHOTO_GALLERY_TOOL] Search completed, items={n}")

        return format_success_response(dict(outputs), f"搜尋到 {n} 張照片")

    except ToolInputError:
        raise
    except Exception as e:
        logger.error(f"[SEARCH_PHOTO_GALLERY_TOOL] Failed to search photos: {e}")
        raise RuntimeError(f"搜尋照片失敗: {str(e)}") from e


def _normalize_media_uris(param: Any) -> List[str]:
    """將 media_uris 規範為字串列表（支援陣列或 JSON 陣列字串）。"""
    if param is None:
        raise ToolInputError("缺少必填引數 media_uris")
    if isinstance(param, list):
        return param
    if isinstance(param, str):
        try:
            parsed = json.loads(param)
        except json.JSONDecodeError as e:
            raise ToolInputError(
                f"media_uris 必須是合法 JSON 陣列字串。解析錯誤: {e}"
            ) from e
        if not isinstance(parsed, list):
            raise ToolInputError("media_uris 解析後必須是陣列")
        return parsed
    raise ToolInputError(
        f"media_uris 必須是陣列或 JSON 陣列字串，當前型別: {type(param).__name__}"
    )


def _decode_image_url_escapes(url: str) -> str:
    """與 upload-photo-tool.ts getPhotoUrls 一致：替換 URL 中的 \\u003d、\\u0026。"""
    return url.replace("\\u003d", "=").replace("\\u0026", "&")


@tool(
    name="upload_photo",
    description="""工具能力描述：將手機本地檔案回傳並獲取可公網訪問的 URL。

  前置工具呼叫：此工具使用前必須先呼叫 search_photo_gallery 工具獲取照片的 mediaUri或者thumbnailUri
  工具引數說明：
  a. 入參中的mediaUris中的mediaUri必須與search_photo_gallery結果中對應的mediaUri或者thumbnailUri完全保持一致，不要自行修改，必須是file://開頭的路徑。
  b. 優先使用search_photo_gallery結果中的thumbnailUri作為入參，thumbnailUri是縮圖，清晰度與檔案大小都非常合適展示給使用者，如果thumbnailUri不存在或者使用者要求使用原圖，則使用search_photo_gallery結果中對應的mediaUri
  c. media_uris 是照片在手機本地的 URI 陣列（從 search_photo_gallery 工具響應中獲取）。限制：每次最多支援傳入 5 條 mediaUri

  注意事項：
  a. 操作超時時間為60秒,請勿重複呼叫此工具,如果超時或失敗,最多重試一次。
  b. 此工具返回的圖片連結為使用者公網可訪問的連結，如果需要後續操作需要下載到本地，如果需要返回給使用者檢視則直接以圖片markdown的形式返回給使用者""",
)
async def upload_photo(media_uris: Union[str, List[str]]) -> Dict[str, Any]:
    """上傳照片

    Args:
        media_uris:本地 URI 列表，或 JSON 陣列字串

    Returns:
        imageUrls、count、message；單次最多 5 條 URI
    """
    try:
        normalized = _normalize_media_uris(media_uris)
        logger.info(
            "[UPLOAD_PHOTO_TOOL] Normalized mediaUris count=%s",
            len(normalized),
        )

        if len(normalized) == 0:
            raise ToolInputError("mediaUris 陣列不能為空")

        if len(normalized) > 5:
            raise ToolInputError(
                f"最多支援 5 條 mediaUri，當前提供了 {len(normalized)} 條。請分批處理。"
            )

        for uri in normalized:
            if not isinstance(uri, str) or not uri.strip():
                raise ToolInputError("media_uris 中每項必須為非空字串")

        image_infos = [{"mediaUri": u.strip()} for u in normalized]

        command = {
            "header": {
                "namespace": "Common",
                "name": "Action",
            },
            "payload": {
                "cardParam": {},
                "executeParam": {
                    "executeMode": "background",
                    "intentName": "ImageUploadForClaw",
                    "bundleName": "com.huawei.hmos.vassistant",
                    "needUnlock": True,
                    "actionResponse": True,
                    "appType": "OHOS_APP",
                    "timeOut": 5,
                    "intentParam": {"imageInfos": image_infos},
                    "permissionId": [],
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        outputs = await execute_device_command("ImageUploadForClaw", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        result = outputs.get("result") if isinstance(outputs, dict) else None
        if not isinstance(result, dict):
            result = {}
        image_urls = result.get("imageUrls", [])
        if not isinstance(image_urls, list):
            image_urls = []

        decoded_urls: List[str] = []
        for url in image_urls:
            if not isinstance(url, str):
                logger.warning(
                    "[UPLOAD_PHOTO_TOOL] imageUrl 非字串: %s",
                    type(url),
                )
                continue
            decoded = _decode_image_url_escapes(url)
            if decoded != url:
                logger.info(
                    "[UPLOAD_PHOTO_TOOL] Decoded URL: %s -> %s",
                    url[:120] + ("..." if len(url) > 120 else ""),
                    decoded[:120] + ("..." if len(decoded) > 120 else ""),
                )
            decoded_urls.append(decoded)

        logger.info(
            "[UPLOAD_PHOTO_TOOL] Retrieved %s image URLs",
            len(decoded_urls),
        )

        payload = {
            "imageUrls": decoded_urls,
            "count": len(decoded_urls),
            "message": f"成功獲取 {len(decoded_urls)} 張照片的公網訪問 URL",
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
        logger.error(f"[UPLOAD_PHOTO_TOOL] Failed to upload photos: {e}")
        raise RuntimeError(f"上傳照片失敗: {str(e)}") from e
