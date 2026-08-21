# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""WecomFileService - 企業微信檔案服務

提供企業微信檔案的下載和上傳功能。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

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


class WecomFileService:
    """企業微信檔案服務，處理檔案下載和上傳。"""

    def __init__(
        self,
        ws_client: Any,
        max_download_size: int = 100 * 1024 * 1024,
        download_timeout: int = 60,
        workspace_dir: str = "",
    ):
        """初始化檔案服務。

        Args:
            ws_client: 企業微信 WebSocket 客戶端（WSClient 例項）
            max_download_size: 最大下載檔案大小（位元組）
            download_timeout: 下載超時時間（秒）
            workspace_dir: 工作空間目錄
        """
        self._ws_client = ws_client
        self.max_download_size = max_download_size
        self.download_timeout = download_timeout
        self.workspace_dir = workspace_dir
        self._download_semaphore = asyncio.Semaphore(3)

    def _get_download_dir(self, file_category: str) -> str:
        """獲取下載目錄路徑。"""
        base_dir = os.path.join(self.workspace_dir, "wecom_files", "downloads", file_category)
        os.makedirs(base_dir, exist_ok=True)
        return base_dir

    @staticmethod
    def _safe_filename(name: str) -> str:
        """生成安全的檔名。"""
        # 移除或替換不安全字元
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
        return safe[:100]  # 限制長度

    async def download_file(
        self,
        url: str,
        aes_key: str,
        message_id: str,
        file_category: str = "file",
        filename: str | None = None,
    ) -> dict | None:
        """下載檔案並儲存到本地。

        Args:
            url: 檔案下載地址
            aes_key: AES 解密金鑰（Base64 編碼）
            message_id: 訊息 ID（用於生成檔名）
            file_category: 檔案類別（image/file/voice/video）
            filename: 原始檔名（可選）

        Returns:
            檔案資訊字典，失敗返回 None
        """
        try:
            # 使用 SDK 的 download_file 方法（自動處理 AES 解密）
            async with self._download_semaphore:
                result = await asyncio.wait_for(
                    self._ws_client.download_file(url, aes_key),
                    timeout=self.download_timeout,
                )

            if not result or "buffer" not in result:
                logger.error("[WecomFileService] 下載檔案失敗：無返回資料")
                return None

            file_data = result["buffer"]
            original_filename = result.get("filename") or filename

            # 檢查檔案大小
            if len(file_data) > self.max_download_size:
                logger.warning(
                    f"[WecomFileService] 檔案過大: {len(file_data)} > {self.max_download_size}"
                )
                return None

            # 檢查空檔案
            if len(file_data) == 0:
                logger.warning("[WecomFileService] 下載的檔案為空")
                return None

            # 檢測副檔名
            extension = detect_file_extension(file_data)
            if not extension and original_filename:
                # 從原始檔名提取副檔名
                _, ext = os.path.splitext(original_filename)
                if ext:
                    extension = ext

            # 生成檔名
            timestamp = int(time.time() * 1000)
            if not extension:
                extension = ".bin"
            
            if file_category == "file" and original_filename:
                # 普通檔案保留原始檔名
                safe_name = self._safe_filename(original_filename)
                local_filename = f"{message_id}_{timestamp}_{safe_name}"
            else:
                # 圖片/語音/影片使用時間戳命名
                local_filename = f"{message_id}_{timestamp}{extension}"

            # 儲存檔案
            download_dir = self._get_download_dir(file_category)
            file_path = os.path.join(download_dir, local_filename)
            
            with open(file_path, 'wb') as f:
                f.write(file_data)

            # 構建檔案資訊
            file_info = {
                "path": file_path,
                "name": original_filename or local_filename,
                "size": len(file_data),
                "mime_type": get_mime_type(extension),
                "file_category": file_category,
            }

            logger.info(
                f"[WecomFileService] 檔案下載成功: {file_category}/{local_filename} "
                f"size={len(file_data)}"
            )
            return file_info

        except asyncio.TimeoutError:
            logger.error(f"[WecomFileService] 下載檔案超時: {url}")
            return None
        except Exception as e:
            logger.error(f"[WecomFileService] 下載檔案失敗: {e}")
            return None

    async def upload_file(
        self,
        file_path: str,
        media_type: str,
    ) -> str | None:
        """上傳檔案到企業微信。

        Args:
            file_path: 本地檔案路徑
            media_type: 媒體型別（file/image/voice/video）

        Returns:
            media_id，失敗返回 None
        """
        try:
            # 讀取檔案
            with open(file_path, 'rb') as f:
                file_data = f.read()

            filename = os.path.basename(file_path)

            # 使用 SDK 的 upload_media 方法
            result = await self._ws_client.upload_media(
                file_data,
                type=media_type,
                filename=filename,
            )

            if not result or "media_id" not in result:
                logger.error(f"[WecomFileService] 上傳檔案失敗：無 media_id 返回")
                return None

            media_id = result["media_id"]
            logger.info(
                f"[WecomFileService] 檔案上傳成功: {filename} -> {media_id}"
            )
            return media_id

        except Exception as e:
            logger.error(f"[WecomFileService] 上傳檔案失敗: {e}")
            return None

    @classmethod
    def get_media_type_for_file(cls, file_path: str) -> str:
        """根據副檔名確定媒體型別。

        Args:
            file_path: 檔案路徑

        Returns:
            媒體型別（file/image/voice/video）
        """
        ext = os.path.splitext(file_path)[1].lower()
        
        # 圖片
        if ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}:
            return 'image'
        
        # 語音
        if ext in {'.mp3', '.wav', '.aac', '.ogg', '.flac', '.m4a'}:
            return 'voice'
        
        # 影片
        if ext in {'.mp4', '.mov', '.avi', '.mkv', '.flv', '.webm'}:
            return 'video'
        
        # 其他檔案
        return 'file'
