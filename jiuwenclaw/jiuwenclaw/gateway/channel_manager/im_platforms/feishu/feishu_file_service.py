# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""飛書檔案服務，負責檔案的下載與上傳。"""

import asyncio
import mimetypes
import os
import re
import time
from typing import Any

from jiuwenclaw.common.utils import logger

# 型別別名，用於型別提示
FeishuConfig = Any  # 避免迴圈匯入

# ──────────────────────────────────────────────────────────────────────────────
# 飛書 file.create API 支援的 file_type 列舉
# https://open.feishu.cn/document/server-docs/im-v1/file/create
# ──────────────────────────────────────────────────────────────────────────────
_EXT_TO_FEISHU_FILE_TYPE: dict[str, str] = {
    # 音訊 —— 飛書原生格式為 opus，其他音訊作為 stream（可下載，不可線上播放）
    ".opus": "opus",
    ".mp3": "stream",
    ".wav": "stream",
    ".flac": "stream",
    ".ogg": "stream",
    ".aac": "stream",
    ".m4a": "stream",
    # 影片
    ".mp4": "mp4",
    ".mov": "mp4",
    ".avi": "mp4",
    ".mkv": "mp4",
    # 文件
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "stream",
    ".ppt": "ppt",
    ".pptx": "stream",
}

# 圖片副檔名集合（走 image.create 介面，不走 file.create）
_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".svg"}
)

# 音訊副檔名集合（可嘗試以 audio msg_type 傳送）
_AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".opus", ".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a"}
)

# 影片副檔名集合（以 media msg_type 傳送）
_VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm"}
)


def get_feishu_file_type(file_path: str) -> str:
    """根據副檔名返回飛書 file.create 所需的 file_type。"""
    ext = os.path.splitext(file_path)[1].lower()
    return _EXT_TO_FEISHU_FILE_TYPE.get(ext, "stream")


def is_image_file(file_path: str) -> bool:
    """判斷是否為圖片檔案。"""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in _IMAGE_EXTENSIONS


def is_audio_file(file_path: str) -> bool:
    """判斷是否為音訊檔案。"""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in _AUDIO_EXTENSIONS


def is_video_file(file_path: str) -> bool:
    """判斷是否為影片檔案。"""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in _VIDEO_EXTENSIONS


class FeishuFileService:
    """
    飛書檔案服務，負責檔案的下載與上傳。

    功能：
    - 從飛書下載使用者傳送的檔案（圖片/音訊/影片/普通檔案）
    - 上傳檔案到飛書用於傳送
    """

    def __init__(
        self,
        api_client: Any,
        config: FeishuConfig,
        workspace_dir: str,
    ):
        """
        初始化檔案服務。

        Args:
            api_client: 飛書 API 客戶端（lark.Client 例項）
            config: 飛書通道配置（FeishuConfig）
            workspace_dir: 工作空間目錄
        """
        self._api_client = api_client
        self._config = config
        self._workspace_dir = workspace_dir
        self._download_semaphore = asyncio.Semaphore(3)  # 限制併發下載數

    # ──────────────────────────────────────────────────────────────────────────
    # 工具方法
    # ──────────────────────────────────────────────────────────────────────────
    @classmethod
    def _ensure_dir(cls, path: str) -> None:
        """確保目錄存在。"""
        os.makedirs(path, exist_ok=True)

    def _get_download_dir(self, file_type: str) -> str:
        """獲取下載目錄路徑，並確保目錄存在。"""
        base_dir = os.path.join(self._workspace_dir, "feishu_files", "downloads", file_type)
        self._ensure_dir(base_dir)
        return base_dir

    @classmethod
    def _generate_local_filename(
        cls,
        message_id: str,
        file_key: str,
        original_name: str = "",
        extension: str = "",
    ) -> str:
        """
        生成本地檔名，確保唯一性。

        格式: {message_id}_{safe_file_key}{ext}
        """
        if not extension and original_name:
            extension = os.path.splitext(original_name)[1]

        # 清理 file_key 中的特殊字元，取前 20 位
        safe_key = re.sub(r"[^\w\-]", "", file_key[:20])

        return f"{message_id}_{safe_key}{extension}"

    @classmethod
    def _guess_mime_type(cls, file_name: str) -> str:
        """根據檔名推斷 MIME 型別。"""
        mime_type, _ = mimetypes.guess_type(file_name)
        return mime_type or "application/octet-stream"

    def _get_download_timeout(self) -> int:
        """獲取下載超時時間（秒）。"""
        return getattr(self._config, "download_timeout", 60)

    # ──────────────────────────────────────────────────────────────────────────
    # 檔案下載（核心工具）
    # ──────────────────────────────────────────────────────────────────────────

    async def _download_with_retry(
        self,
        download_func: Any,
        max_retries: int = 3,
    ) -> bytes | None:
        """
        帶重試的檔案下載（線上程池中執行同步 lark_oapi 呼叫）。

        Args:
            download_func: 無參可呼叫物件，執行後返回 lark_oapi response
            max_retries: 最大重試次數

        Returns:
            檔案內容 bytes，失敗返回 None
        """
        loop = asyncio.get_running_loop()
        timeout = self._get_download_timeout()

        for attempt in range(max_retries):
            try:
                # 線上程池執行同步 SDK 呼叫，並加超時控制
                response = await asyncio.wait_for(
                    loop.run_in_executor(None, download_func),
                    timeout=timeout,
                )

                if not response.success():
                    logger.warning(
                        "飛書檔案下載失敗 (嘗試 %d/%d): code=%s msg=%s",
                        attempt + 1, max_retries,
                        response.code, response.msg,
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1 * (attempt + 1))
                    continue

                # 相容不同的響應結構（lark_oapi 各版本差異）
                file_content: bytes | None = None
                if hasattr(response, "file") and response.file:
                    file_content = response.file.read()
                elif hasattr(response, "data") and response.data:
                    if hasattr(response.data, "file") and response.data.file:
                        file_content = response.data.file.read()

                if file_content:
                    return file_content

                # 響應成功但內容為空——視為可重試的瞬時錯誤
                logger.warning(
                    "飛書檔案下載響應中無檔案內容 (嘗試 %d/%d)，稍後重試",
                    attempt + 1, max_retries,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

            except asyncio.TimeoutError:
                logger.warning(
                    "飛書檔案下載超時 %ds (嘗試 %d/%d)",
                    timeout, attempt + 1, max_retries,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))
            except Exception as e:
                logger.error(
                    "飛書檔案下載異常 (嘗試 %d/%d): %s",
                    attempt + 1, max_retries, e,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # 各型別下載實現
    # ──────────────────────────────────────────────────────────────────────────

    async def _download_image_internal(
        self,
        image_key: str,
        message_id: str,
    ) -> dict | None:
        """
        下載圖片檔案。

        使用者傳送的圖片必須透過 messageResource 介面下載（而非 image.get，
        後者僅支援應用自己上傳的圖片）。
        """
        try:
            from lark_oapi.api.im.v1 import GetMessageResourceRequest

            def _do_download():
                request = (
                    GetMessageResourceRequest.builder()
                    .message_id(message_id)
                    .file_key(image_key)
                    .type("image")
                    .build()
                )
                return self._api_client.im.v1.message_resource.get(request)

            file_content = await self._download_with_retry(_do_download)
            if not file_content:
                return None

            # 透過檔案頭魔數推斷圖片格式
            extension = self._detect_image_extension(file_content)

            local_name = self._generate_local_filename(
                message_id, image_key, extension=extension
            )
            download_dir = self._get_download_dir("images")
            file_path = os.path.join(download_dir, local_name)

            with open(file_path, "wb") as f:
                f.write(file_content)

            logger.info("飛書圖片下載成功: %s", file_path)

            return {
                "path": file_path,
                "name": local_name,
                "size": len(file_content),
                "mime_type": self._guess_mime_type(local_name),
                "file_key": image_key,
                "file_category": "image",
            }

        except Exception as e:
            logger.error("下載飛書圖片失敗: %s", e)
            return None

    async def download_image(self, file_key: str, message_id: str) -> dict | None:
        """下載圖片檔案（公開介面）。"""
        return await self._download_image_internal(file_key, message_id)

    async def _download_file_internal(
        self,
        file_key: str,
        message_id: str,
        extra_info: dict[str, Any] | None = None,
    ) -> dict | None:
        """
        下載普通檔案。

        使用者傳送的檔案透過 messageResource 介面下載。
        """
        try:
            from lark_oapi.api.im.v1 import GetMessageResourceRequest

            extra_info = extra_info or {}
            original_name = extra_info.get("file_name", "")

            # 嘗試下載檔案，即使檔案大小為0（可能是飛書端顯示問題）
            def _do_download():
                request = (
                    GetMessageResourceRequest.builder()
                    .message_id(message_id)
                    .file_key(file_key)
                    .type("file")
                    .build()
                )
                return self._api_client.im.v1.message_resource.get(request)

            file_content = await self._download_with_retry(_do_download)
            if not file_content:
                return None

            extension = os.path.splitext(original_name)[1] if original_name else ""
            local_name = self._generate_local_filename(
                message_id, file_key, original_name, extension
            )
            download_dir = self._get_download_dir("files")
            file_path = os.path.join(download_dir, local_name)

            # 處理檔名衝突
            if os.path.exists(file_path):
                base, ext = os.path.splitext(file_path)
                file_path = f"{base}_{int(time.time())}{ext}"

            with open(file_path, "wb") as f:
                f.write(file_content)

            logger.info("飛書檔案下載成功: %s", file_path)

            return {
                "path": file_path,
                "name": original_name or local_name,
                "size": len(file_content),
                "mime_type": self._guess_mime_type(original_name or local_name),
                "file_key": file_key,
                "file_category": "file",
            }

        except Exception as e:
            logger.error("下載飛書檔案失敗: %s", e)
            return None

    async def download_file_resource(
        self, file_key: str, message_id: str, extra_info: dict | None = None
    ) -> dict | None:
        """下載普通檔案（公開介面）。"""
        return await self._download_file_internal(file_key, message_id, extra_info)

    async def _download_audio_internal(
        self,
        file_key: str,
        message_id: str,
    ) -> dict | None:
        """
        下載音訊檔案。

        飛書音訊訊息的原生格式為 Opus，messageResource 返回的也是 opus 編碼資料。
        """
        try:
            from lark_oapi.api.im.v1 import GetMessageResourceRequest

            def _do_download():
                request = (
                    GetMessageResourceRequest.builder()
                    .message_id(message_id)
                    .file_key(file_key)
                    .type("audio")
                    .build()
                )
                return self._api_client.im.v1.message_resource.get(request)

            file_content = await self._download_with_retry(_do_download)
            if not file_content:
                return None

            # 飛書音訊原生格式為 opus，透過檔案頭進一步確認
            extension = self._detect_audio_extension(file_content)

            local_name = self._generate_local_filename(
                message_id, file_key, extension=extension
            )
            download_dir = self._get_download_dir("audio")
            file_path = os.path.join(download_dir, local_name)

            with open(file_path, "wb") as f:
                f.write(file_content)

            logger.info("飛書音訊下載成功: %s", file_path)

            return {
                "path": file_path,
                "name": local_name,
                "size": len(file_content),
                "mime_type": self._guess_mime_type(local_name),
                "file_key": file_key,
                "file_category": "audio",
            }

        except Exception as e:
            logger.error("下載飛書音訊失敗: %s", e)
            return None

    async def download_audio(self, file_key: str, message_id: str) -> dict | None:
        """下載音訊檔案（公開介面）。"""
        return await self._download_audio_internal(file_key, message_id)

    async def _download_media_internal(
        self,
        file_key: str,
        message_id: str,
    ) -> dict | None:
        """
        下載影片/媒體檔案。

        飛書影片訊息透過 messageResource 介面下載。
        """
        try:
            from lark_oapi.api.im.v1 import GetMessageResourceRequest

            def _do_download():
                request = (
                    GetMessageResourceRequest.builder()
                    .message_id(message_id)
                    .file_key(file_key)
                    .type("media")
                    .build()
                )
                return self._api_client.im.v1.message_resource.get(request)

            file_content = await self._download_with_retry(_do_download)
            if not file_content:
                return None

            extension = self._detect_video_extension(file_content)

            local_name = self._generate_local_filename(
                message_id, file_key, extension=extension
            )
            download_dir = self._get_download_dir("media")
            file_path = os.path.join(download_dir, local_name)

            with open(file_path, "wb") as f:
                f.write(file_content)

            logger.info("飛書影片下載成功: %s", file_path)

            return {
                "path": file_path,
                "name": local_name,
                "size": len(file_content),
                "mime_type": self._guess_mime_type(local_name),
                "file_key": file_key,
                "file_category": "media",
            }

        except Exception as e:
            logger.error("下載飛書影片失敗: %s", e)
            return None

    async def download_media(self, file_key: str, message_id: str) -> dict | None:
        """下載影片檔案（公開介面）。"""
        return await self._download_media_internal(file_key, message_id)

    # ──────────────────────────────────────────────────────────────────────────
    # 檔案格式檢測輔助方法
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_image_extension(data: bytes) -> str:
        """透過檔案頭魔數推斷圖片副檔名，預設 .png。"""
        if len(data) < 12:
            return ".png"
        header = data[:12]
        if header[:4] == b"\x89PNG":
            return ".png"
        if header[:2] == b"\xff\xd8":
            return ".jpg"
        if header[:6] in (b"GIF87a", b"GIF89a"):
            return ".gif"
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return ".webp"
        if header[:2] in (b"BM",):
            return ".bmp"
        return ".png"

    @staticmethod
    def _detect_audio_extension(data: bytes) -> str:
        """
        透過檔案頭推斷音訊副檔名。

        飛書音訊原生格式為 Opus（OggOpus 容器），預設返回 .opus。
        """
        if len(data) < 12:
            return ".opus"
        header = data[:12]
        # OggS 容器（Opus/Vorbis）
        if header[:4] == b"OggS":
            return ".opus"
        # FLAC
        if header[:4] == b"fLaC":
            return ".flac"
        # WAVE
        if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
            return ".wav"
        # MP3 (ID3 tag or sync frame)
        if header[:3] == b"ID3" or header[:2] == b"\xff\xfb":
            return ".mp3"
        # 預設為 opus（飛書原生格式）
        return ".opus"

    @staticmethod
    def _detect_video_extension(data: bytes) -> str:
        """透過檔案頭推斷影片副檔名，預設 .mp4。"""
        if len(data) < 12:
            return ".mp4"
        header = data[:12]
        # MP4 / MOV (ftyp box)
        if header[4:8] in (b"ftyp", b"moof", b"moov"):
            return ".mp4"
        # MKV / WebM (EBML header)
        if header[:4] == b"\x1a\x45\xdf\xa3":
            return ".mkv"
        # FLV
        if header[:3] == b"FLV":
            return ".flv"
        # AVI (RIFF ... AVI )
        if header[:4] == b"RIFF" and header[8:12] == b"AVI ":
            return ".avi"
        return ".mp4"

    # ──────────────────────────────────────────────────────────────────────────
    # 檔案上傳
    # ──────────────────────────────────────────────────────────────────────────

    async def _upload_image_internal(self, file_path: str) -> dict | None:
        """
        上傳圖片到飛書（圖片 API，失敗時回退到檔案 API）。

        飛書圖片限制：20 MB。
        """
        try:
            from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

            file_size = os.path.getsize(file_path)
            if file_size > 20 * 1024 * 1024:
                logger.error("圖片超過飛書限制 20MB: %s (%d)", file_path, file_size)
                return None

            loop = asyncio.get_running_loop()

            def _do_upload():
                file_obj = open(file_path, "rb")
                try:
                    request = (
                        CreateImageRequest.builder()
                        .request_body(
                            CreateImageRequestBody.builder()
                            .image_type("message")
                            .image(file_obj)
                            .build()
                        )
                        .build()
                    )
                    return self._api_client.im.v1.image.create(request)
                finally:
                    file_obj.close()

            response = await loop.run_in_executor(None, _do_upload)

            if response.success():
                image_key = response.data.image_key
                logger.info("飛書圖片上傳成功: %s → %s", file_path, image_key)
                return {
                    "image_key": image_key,
                    "file_key": image_key,
                    "file_type": "image",
                }

            # 圖片 API 失敗，回退到檔案 API
            logger.warning(
                "上傳圖片失敗 (code=%s): %s，回退到檔案 API", response.code, response.msg
            )
            return await self._upload_image_as_file(file_path)

        except Exception as e:
            logger.error("上傳圖片異常: %s", e)
            return None

    async def _upload_image_as_file(self, file_path: str) -> dict | None:
        """使用檔案 API 上傳圖片（圖片 API 失敗時的回退方案）。"""
        try:
            from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody

            file_name = os.path.basename(file_path)
            loop = asyncio.get_running_loop()

            def _do_upload():
                file_obj = open(file_path, "rb")
                try:
                    request = (
                        CreateFileRequest.builder()
                        .request_body(
                            CreateFileRequestBody.builder()
                            .file_name(file_name)
                            .file_type("stream")
                            .file(file_obj)
                            .build()
                        )
                        .build()
                    )
                    return self._api_client.im.v1.file.create(request)
                finally:
                    file_obj.close()

            response = await loop.run_in_executor(None, _do_upload)

            if not response.success():
                logger.error(
                    "檔案 API 上傳圖片失敗: code=%s msg=%s", response.code, response.msg
                )
                return None

            file_key = response.data.file_key
            file_size = os.path.getsize(file_path)
            logger.info("飛書檔案 API 上傳圖片成功: %s → %s", file_path, file_key)
            return {
                "file_key": file_key,
                "file_name": file_name,
                "file_size": file_size,
                "file_type": "file",
            }

        except Exception as e:
            logger.error("檔案 API 上傳圖片異常: %s", e)
            return None

    async def upload_image(self, file_path: str) -> dict | None:
        """上傳圖片（公開介面）。"""
        return await self._upload_image_internal(file_path)

    async def _upload_file_internal(self, file_path: str) -> dict | None:
        """
        上傳普通檔案到飛書（file.create 介面）。

        飛書檔案限制：30 MB。
        根據副檔名自動選擇合適的 file_type（如 opus/mp4/pdf/doc/xls/ppt/stream）。
        """
        try:
            from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody

            file_size = os.path.getsize(file_path)
            if file_size > 30 * 1024 * 1024:
                logger.error("檔案超過飛書限制 30MB: %s (%d)", file_path, file_size)
                return None

            file_name = os.path.basename(file_path)
            feishu_file_type = get_feishu_file_type(file_path)
            loop = asyncio.get_running_loop()

            def _do_upload():
                file_obj = open(file_path, "rb")
                try:
                    request = (
                        CreateFileRequest.builder()
                        .request_body(
                            CreateFileRequestBody.builder()
                            .file_name(file_name)
                            .file_type(feishu_file_type)
                            .file(file_obj)
                            .build()
                        )
                        .build()
                    )
                    return self._api_client.im.v1.file.create(request)
                finally:
                    file_obj.close()

            response = await loop.run_in_executor(None, _do_upload)

            if not response.success():
                logger.error("上傳檔案失敗: code=%s msg=%s", response.code, response.msg)
                return None

            file_key = response.data.file_key
            logger.info(
                "飛書檔案上傳成功: %s → %s (file_type=%s)", file_path, file_key, feishu_file_type
            )
            return {
                "file_key": file_key,
                "file_name": file_name,
                "file_size": file_size,
                "file_type": feishu_file_type,
                "file_category": "audio" if is_audio_file(file_path) else (
                    "media" if is_video_file(file_path) else "file"
                ),
            }

        except Exception as e:
            logger.error("上傳檔案異常: %s", e)
            return None

    async def upload_file_resource(self, file_path: str) -> dict | None:
        """上傳普通檔案（公開介面）。"""
        return await self._upload_file_internal(file_path)
