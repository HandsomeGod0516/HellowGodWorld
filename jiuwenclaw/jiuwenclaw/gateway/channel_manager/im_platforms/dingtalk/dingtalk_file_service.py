# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""DingTalk File Service

提供釘釘檔案的下載和上傳功能。
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Callable

import httpx
from loguru import logger


# 檔案魔數對映（用於格式檢測）
FILE_SIGNATURES = {
    # 圖片
    b'\x89PNG': '.png',
    b'\xff\xd8\xff': '.jpg',
    b'GIF8': '.gif',
    b'RIFF': '.webp',  # 需要進一步檢查 WEBP 標識
    # 音訊
    b'ID3': '.mp3',
    b'\xff\xfb': '.mp3',
    b'\xff\xfa': '.mp3',
    b'fLaC': '.flac',
    b'OggS': '.ogg',
    # 影片
    b'ftyp': '.mp4',
    b'moof': '.mp4',
    b'moov': '.mp4',
    b'\x1a\x45\xdf\xa3': '.mkv',
    b'FLV': '.flv',
}

# MIME 型別對映
MIME_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.ogg': 'audio/ogg',
    '.flac': 'audio/flac',
    '.mp4': 'video/mp4',
    '.mkv': 'video/x-matroska',
    '.flv': 'video/x-flv',
    '.pdf': 'application/pdf',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.txt': 'text/plain',
    '.zip': 'application/zip',
    '.json': 'application/json',
}


def detect_file_extension(content: bytes) -> str:
    """透過檔案頭魔數檢測副檔名。"""
    if len(content) < 12:
        return ''

    # 檢查 WEBP（RIFF....WEBP）
    if content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        return '.webp'

    # 檢查 WAV（RIFF....WAVE）
    if content[:4] == b'RIFF' and content[8:12] == b'WAVE':
        return '.wav'

    # 檢查 MP4（ftyp/moof/moov）
    if content[4:8] in (b'ftyp', b'moof', b'moov'):
        return '.mp4'

    # 檢查其他格式
    for signature, ext in FILE_SIGNATURES.items():
        if content.startswith(signature):
            return ext

    return ''


def get_mime_type(extension: str) -> str:
    """獲取副檔名對應的 MIME 型別。"""
    return MIME_TYPES.get(extension.lower(), 'application/octet-stream')


class DingTalkFileService:
    """釘釘檔案服務，處理檔案下載和上傳。"""

    def __init__(
        self,
        client_id: str,
        get_token_func: Callable[[], asyncio.coroutines.Coroutine[Any, Any, str | None]],
        http_client: httpx.AsyncClient,
        max_download_size: int = 100 * 1024 * 1024,
        download_timeout: int = 60,
        workspace_dir: str = "",
    ):
        """初始化檔案服務。

        Args:
            client_id: 釘釘應用 client_id（robotCode）
            get_token_func: 獲取 access_token 的非同步函式
            http_client: HTTP 客戶端
            max_download_size: 最大下載檔案大小（位元組）
            download_timeout: 下載超時時間（秒）
            workspace_dir: 工作空間目錄
        """
        self._client_id = client_id
        self._get_token = get_token_func
        self._http = http_client
        self._max_download_size = max_download_size
        self._download_timeout = download_timeout
        self._workspace_dir = workspace_dir
        self._download_semaphore = asyncio.Semaphore(3)

    def _get_download_dir(self, file_category: str) -> str:
        """獲取下載目錄路徑。"""
        base_dir = os.path.join(self._workspace_dir, "dingtalk_files", "downloads", file_category)
        os.makedirs(base_dir, exist_ok=True)
        return base_dir

    @classmethod
    def _safe_filename(cls, name: str) -> str:
        """生成安全的檔名。"""
        # 移除或替換不安全字元
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
        return safe[:100]  # 限制長度

    async def _download_with_retry(
        self,
        download_code: str,
        file_type: str,
        max_retries: int = 3,
    ) -> bytes | None:
        """帶重試的檔案下載。

        Args:
            download_code: 檔案下載碼
            file_type: 檔案型別（image/file/voice/video）
            max_retries: 最大重試次數

        Returns:
            檔案內容，失敗返回 None
        """
        token = await self._get_token()
        if not token:
            logger.error("[DingTalkFileService] 無法獲取 access_token")
            return None

        url = "https://api.dingtalk.com/v1.0/robot/messageFiles/download"
        # 釘釘下載 API 使用 POST 方法，引數放在請求體中
        body = {
            "downloadCode": download_code,
            "robotCode": self._client_id,
        }
        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }

        for attempt in range(max_retries):
            try:
                async with self._download_semaphore:
                    # 第一步：POST 請求獲取 downloadUrl
                    response = await asyncio.wait_for(
                        self._http.post(url, json=body, headers=headers),
                        timeout=self._download_timeout,
                    )

                if response.status_code != 200:
                    logger.warning(
                        f"[DingTalkFileService] 獲取下載連結失敗 status={response.status_code} "
                        f"attempt={attempt + 1}/{max_retries} response={response.text}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    continue

                # 解析響應獲取 downloadUrl
                result = response.json()
                download_url = result.get("downloadUrl")
                if not download_url:
                    logger.warning(
                        f"[DingTalkFileService] 響應缺少 downloadUrl: {result}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    continue

                # 第二步：GET 請求下載實際檔案內容
                download_response = await asyncio.wait_for(
                    self._http.get(download_url),
                    timeout=self._download_timeout,
                )

                if download_response.status_code != 200:
                    logger.warning(
                        f"[DingTalkFileService] 下載檔案失敗 status={download_response.status_code} "
                        f"attempt={attempt + 1}/{max_retries}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    continue

                content = download_response.content
                if not content:
                    logger.warning(
                        f"[DingTalkFileService] 下載內容為空 attempt={attempt + 1}/{max_retries}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    continue

                if len(content) > self._max_download_size:
                    logger.warning(
                        f"[DingTalkFileService] 檔案大小 {len(content)} 超過限制 {self._max_download_size}，跳過下載"
                    )
                    return None

                return content

            except asyncio.TimeoutError:
                logger.warning(
                    f"[DingTalkFileService] 下載超時 attempt={attempt + 1}/{max_retries}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** (attempt + 1))
            except Exception as e:
                logger.error(f"[DingTalkFileService] 下載異常: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        return None

    async def download_image(self, download_code: str, message_id: str) -> dict | None:
        """下載圖片檔案。

        Args:
            download_code: 圖片下載碼
            message_id: 訊息 ID

        Returns:
            檔案資訊字典，失敗返回 None
        """
        content = await self._download_with_retry(download_code, "image")
        if not content:
            return None

        # 檢測檔案格式
        ext = detect_file_extension(content)
        if not ext:
            ext = '.png'  # 預設 PNG

        # 生成檔名
        safe_code = self._safe_filename(download_code[:20])
        filename = f"{message_id}_{safe_code}{ext}"

        # 儲存檔案
        download_dir = self._get_download_dir("images")
        file_path = os.path.join(download_dir, filename)

        try:
            with open(file_path, 'wb') as f:
                f.write(content)

            return {
                "path": file_path,
                "name": filename,
                "size": len(content),
                "mime_type": get_mime_type(ext),
                "download_code": download_code,
                "file_category": "image",
            }
        except Exception as e:
            logger.error(f"[DingTalkFileService] 儲存圖片失敗: {e}")
            return None

    async def download_file(self, download_code: str, message_id: str, original_name: str = "") -> dict | None:
        """下載普通檔案。

        Args:
            download_code: 檔案下載碼
            message_id: 訊息 ID
            original_name: 原始檔名

        Returns:
            檔案資訊字典，失敗返回 None
        """
        content = await self._download_with_retry(download_code, "file")
        if not content:
            return None

        # 確定副檔名
        if original_name:
            ext = os.path.splitext(original_name)[1].lower()
        else:
            ext = detect_file_extension(content) or '.bin'

        # 生成檔名
        safe_code = self._safe_filename(download_code[:20])
        if original_name:
            filename = self._safe_filename(original_name)
        else:
            filename = f"{message_id}_{safe_code}{ext}"

        # 儲存檔案
        download_dir = self._get_download_dir("files")
        file_path = os.path.join(download_dir, filename)

        try:
            with open(file_path, 'wb') as f:
                f.write(content)

            return {
                "path": file_path,
                "name": filename,
                "size": len(content),
                "mime_type": get_mime_type(ext),
                "download_code": download_code,
                "file_category": "file",
            }
        except Exception as e:
            logger.error(f"[DingTalkFileService] 儲存檔案失敗: {e}")
            return None

    async def download_audio(self, download_code: str, message_id: str) -> dict | None:
        """下載音訊檔案。

        Args:
            download_code: 音訊下載碼
            message_id: 訊息 ID

        Returns:
            檔案資訊字典，失敗返回 None
        """
        content = await self._download_with_retry(download_code, "voice")
        if not content:
            return None

        # 檢測檔案格式
        ext = detect_file_extension(content)
        if not ext:
            ext = '.mp3'  # 預設 MP3

        # 生成檔名
        safe_code = self._safe_filename(download_code[:20])
        filename = f"{message_id}_{safe_code}{ext}"

        # 儲存檔案
        download_dir = self._get_download_dir("audio")
        file_path = os.path.join(download_dir, filename)

        try:
            with open(file_path, 'wb') as f:
                f.write(content)

            return {
                "path": file_path,
                "name": filename,
                "size": len(content),
                "mime_type": get_mime_type(ext),
                "download_code": download_code,
                "file_category": "audio",
            }
        except Exception as e:
            logger.error(f"[DingTalkFileService] 儲存音訊失敗: {e}")
            return None

    async def download_video(self, download_code: str, message_id: str) -> dict | None:
        """下載影片檔案。

        Args:
            download_code: 影片下載碼
            message_id: 訊息 ID

        Returns:
            檔案資訊字典，失敗返回 None
        """
        content = await self._download_with_retry(download_code, "video")
        if not content:
            return None

        # 檢測檔案格式
        ext = detect_file_extension(content)
        if not ext:
            ext = '.mp4'  # 預設 MP4

        # 生成檔名
        safe_code = self._safe_filename(download_code[:20])
        filename = f"{message_id}_{safe_code}{ext}"

        # 儲存檔案
        download_dir = self._get_download_dir("video")
        file_path = os.path.join(download_dir, filename)

        try:
            with open(file_path, 'wb') as f:
                f.write(content)

            return {
                "path": file_path,
                "name": filename,
                "size": len(content),
                "mime_type": get_mime_type(ext),
                "download_code": download_code,
                "file_category": "video",
            }
        except Exception as e:
            logger.error(f"[DingTalkFileService] 儲存影片失敗: {e}")
            return None

    async def upload_media(self, file_path: str, file_type: str) -> str | None:
        """上傳媒體檔案到釘釘。

        Args:
            file_path: 本地檔案路徑
            file_type: 檔案型別（image/file/voice/video）

        Returns:
            mediaId，失敗返回 None
        """
        if not os.path.isfile(file_path):
            logger.warning(f"[DingTalkFileService] 檔案不存在: {file_path}")
            return None

        token = await self._get_token()
        if not token:
            logger.error("[DingTalkFileService] 無法獲取 access_token")
            return None

        # 使用釘釘舊版 API（與 dingtalk-stream SDK 一致）
        from urllib.parse import quote_plus
        url = f"https://oapi.dingtalk.com/media/upload?access_token={quote_plus(token)}"

        try:
            filename = os.path.basename(file_path)
            mime_type = get_mime_type(os.path.splitext(file_path)[1])
            with open(file_path, 'rb') as f:
                files = {
                    "media": (filename, f.read(), mime_type),
                }
                data = {
                    "type": file_type,
                }
                response = await self._http.post(url, data=data, files=files)

            if response.status_code != 200:
                logger.error(f"[DingTalkFileService] 上傳失敗: {response.text}")
                return None

            result = response.json()
            # 舊版 API 返回 media_id（下劃線）
            media_id = result.get("media_id")
            if not media_id:
                logger.error(f"[DingTalkFileService] 上傳響應缺少 media_id: {result}")
                return None

            logger.debug(f"[DingTalkFileService] 上傳成功: {filename} -> {media_id}")
            return media_id

        except Exception as e:
            logger.error(f"[DingTalkFileService] 上傳異常: {e}")
            return None

