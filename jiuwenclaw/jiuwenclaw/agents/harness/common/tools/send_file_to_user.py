# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Send File Toolkit

提供傳送檔案到使用者的工具。支援傳送一個或多個檔案。

使用方式：
1. 建立 SendFileToolkit 例項
2. 呼叫 get_tools() 獲取工具列表
3. 工具會自動註冊到 Runner 中
"""

from __future__ import annotations

import json
import os
import logging
from typing import Any, List, Union

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard


logger = logging.getLogger(__name__)


class SendFileToolkit:
    """Toolkit for sending files to users."""

    def __init__(
        self,
        request_id: str,
        session_id: str,
        channel_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Initialize SendFileToolkit.

        Args:
            request_id: Request identifier for message routing.
            session_id: Session identifier for message routing.
            channel_id: Channel identifier for message routing.
            metadata: 與 AgentRequest.metadata 一致（E2A channel_context 對映結果），用於 send_push。
        """
        self.request_id = request_id
        self.session_id = session_id
        self.channel_id = channel_id
        self._request_metadata = dict(metadata) if metadata else None
        logger.debug(
            "[SendFileToolkit] 初始化 request_id=%s session_id=%s channel_id=%s has_metadata=%s",
            request_id,
            session_id,
            channel_id,
            bool(self._request_metadata),
        )

    async def send_file(self, abs_file_path_list: Union[List[str], str]) -> str:
        """Send files to user.

        Args:
            abs_file_path_list: List of absolute file paths to send.

        Returns:
            Success message or error description.
        """
        if isinstance(abs_file_path_list, str):
            try:
                parsed = json.loads(abs_file_path_list)
                if isinstance(parsed, list):
                    abs_file_path_list = parsed
                elif isinstance(parsed, str):
                    abs_file_path_list = [parsed]
                else:
                    abs_file_path_list = [abs_file_path_list]
            except (TypeError, ValueError):
                abs_file_path_list = [abs_file_path_list]

        if not isinstance(abs_file_path_list, list):
            abs_file_path_list = [str(abs_file_path_list)]

        valid_files = []
        missing_files = []
        for fp in abs_file_path_list:
            fp = str(fp).strip()
            if not fp:
                continue
            if os.path.isfile(fp):
                valid_files.append(fp)
            else:
                missing_files.append(fp)
                logger.warning("[SendFileToolkit] 檔案不存在: %s", fp)

        if not valid_files:
            msg_parts = ["傳送檔案失敗：所有檔案均不存在"]
            for mf in missing_files:
                msg_parts.append(f"  - {mf}")
            return "\n".join(msg_parts)

        logger.info(
            "[SendFileToolkit] send_file 開始 session_id=%s 有效檔案=%d 缺失=%d",
            self.session_id,
            len(valid_files),
            len(missing_files),
        )

        try:
            from jiuwenclaw.server.agent_ws_server import AgentWebSocketServer

            server = AgentWebSocketServer.get_instance()
            files_payload = [
                {
                    "path": file_path,
                    "name": os.path.basename(file_path),
                }
                for file_path in valid_files
            ]
            msg = {
                "request_id": self.request_id,
                "channel_id": self.channel_id,
                "session_id": self.session_id,
                "payload": {
                    "event_type": "chat.file",
                    "files": files_payload,
                },
                "is_complete": False,
            }
            if self._request_metadata:
                msg["metadata"] = dict(self._request_metadata)
            await server.send_push(msg)
            result_parts = [f"成功傳送 {len(valid_files)} 個檔案"]
            if missing_files:
                result_parts.append("以下檔案不存在，未傳送：")
                for mf in missing_files:
                    result_parts.append(f"  - {mf}")
            return "\n".join(result_parts)
        except Exception as e:
            logger.exception(
                "[SendFileToolkit] send_file 失敗 session_id=%s error=%s",
                self.session_id,
                str(e),
            )
            return f"提交檔案失敗: {str(e)}"

    def get_tools(self) -> List[Tool]:
        """Return tools for registration in Runner.

        Returns:
            List of tools for sending files.
        """
        session_id = self.session_id

        def make_tool(
            name: str,
            description: str,
            input_params: dict,
            func,
        ) -> Tool:
            card = ToolCard(
                name=name,
                description=description,
                input_params=input_params,
            )
            return LocalFunction(card=card, func=func)

        return [
            make_tool(
                name="send_file_to_user",
                description=(
                    "【檔案傳送工具】當需要將生成的檔案、匯出的資料、建立的文件等傳送給使用者時使用此工具。"
                    "使用場景包括：使用者請求匯出/下載檔案、任務完成後需要交付檔案、生成報告/文件後傳送給使用者。"
                    "引數格式：接受單個路徑字串或路徑陣列，路徑必須是絕對路徑。"
                    "示例：'/tmp/report.pdf' 或 ['/tmp/file1.csv', '/tmp/file2.xlsx']"
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "abs_file_path_list": {
                            "type": ["array", "string"],
                            "items": {"type": "string"},
                            "description": (
                                "要傳送的檔案絕對路徑。"
                                "可以是單個路徑字串如 '/path/to/file.pdf'，"
                                "或路徑陣列如 ['/path/file1.csv', '/path/file2.xlsx']。"
                                "支援任意檔案型別（pdf、xlsx、docx、png、zip等）。"
                            ),
                        }
                    },
                    "required": ["abs_file_path_list"],
                },
                func=self.send_file,
            ),
        ]
