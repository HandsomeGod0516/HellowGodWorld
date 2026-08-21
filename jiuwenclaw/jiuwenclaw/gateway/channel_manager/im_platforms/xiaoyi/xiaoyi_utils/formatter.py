# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""XiaoYi Formatter - 訊息格式化和傳送模組。
基於 TypeScript formatter.ts 實現。
"""

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from jiuwenclaw.common.schema.message import EventType, Message


# ==================== Data Classes ====================

@dataclass
class FileInfo:
    """檔案資訊."""
    file_name: str
    file_type: str
    file_id: str


# ==================== A2A Protocol Builders ====================

def _build_agent_response_wrapper(
    agent_id: str,
    session_id: str,
    task_id: str,
    response_body: dict[str, Any],
) -> dict[str, Any]:
    """
    構建 agent_response 包裝訊息（A2A 格式）。

    Args:
        agent_id: Agent ID
        session_id: Session ID
        task_id: Task ID
        response_body: JSON-RPC 響應體

    Returns:
        dict: agent_response 包裝的訊息
    """
    return {
        "msgType": "agent_response",
        "agentId": agent_id,
        "sessionId": session_id,
        "taskId": task_id,
        "msgDetail": json.dumps(response_body, ensure_ascii=False),
    }


def _build_json_rpc_response(
    message_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """
    構建 JSON-RPC 2.0 響應。

    Args:
        message_id: 訊息 ID
        result: 結果物件

    Returns:
        dict: JSON-RPC 2.0 響應
    """
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "result": result,
    }


def build_status_update_response(
    task_id: str,
    text: str,
    state: str,
) -> dict[str, Any]:
    """
    構建 A2A status-update 事件。

    Args:
        task_id: Task ID
        text: 狀態文字
        state: 狀態值

    Returns:
        dict: status-update 事件
    """
    return {
        "taskId": task_id,
        "kind": "status-update",
        "final": False,
        "status": {
            "message": {
                "role": "agent",
                "parts": [
                    {
                        "kind": "text",
                        "text": text,
                    },
                ],
            },
            "state": state,
        },
    }


def build_clear_context_response() -> dict[str, Any]:
    """
    構建 clearContext 響應。

    Returns:
        dict: clearContext 響應
    """
    return {
        "status": {
            "state": "cleared",
        },
        "error": {
            "code": 0,
            "message": "",
        },
    }


def build_tasks_cancel_response(task_id: str) -> dict[str, Any]:
    """
    構建 tasks/cancel 響應。

    Args:
        task_id: Task ID

    Returns:
        dict: tasks/cancel 響應
    """
    return {
        "id": task_id,
        "status": {
            "state": "canceled",
        },
        "error": {
            "code": 0,
            "message": "",
        },
    }


# ==================== Message Part Builders ====================

def build_text_part(text: str) -> dict[str, Any]:
    """
    構建文字訊息部分。

    Args:
        text: 文字內容

    Returns:
        dict: 文字訊息部分
    """
    return {
        "kind": "text",
        "text": text,
    }


def build_reasoning_text_part(text: str) -> dict[str, Any]:
    """
    構建推理文字訊息部分（reasoningText）。

    Args:
        text: 推理文字內容

    Returns:
        dict: 推理文字訊息部分
    """
    return {
        "kind": "reasoningText",
        "reasoningText": text,
    }


def build_file_part(files: list[FileInfo]) -> dict[str, Any]:
    """
    構建檔案訊息部分。

    Args:
        files: 檔案資訊列表

    Returns:
        dict: 檔案訊息部分
    """
    return {
        "kind": "data",
        "data": {
            "fileInfo": [
                {
                    "fileName": f.file_name,
                    "fileType": f.file_type,
                    "fileId": f.file_id,
                }
                for f in files
            ],
        },
    }


def build_command_part(command: dict[str, Any]) -> dict[str, Any]:
    """
    構建命令訊息部分。

    Args:
        command: 命令物件

    Returns:
        dict: 命令訊息部分
    """
    return {
        "kind": "data",
        "data": {
            "commands": [command],
        },
    }


# ==================== Main Formatter Functions ====================

class MessageFormatter:
    """訊息格式化器，用於將 JiuwenClaw 訊息轉換為 A2A 格式。"""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._accumulated_texts: dict[str, str] = {}
        self._last_text_lengths: dict[str, int] = {}

    @staticmethod
    def get_message_id(self) -> str:
        """生成訊息 ID。"""
        return f"msg_{int(time.time() * 1000)}"

    @staticmethod
    def get_artifact_id(self) -> str:
        """生成 artifact ID。"""
        return str(uuid.uuid4())

    def get_accumulated_text(self, session_id: str) -> str:
        """獲取累積的文字。"""
        return self._accumulated_texts.get(session_id, "")

    def update_accumulated_text(self, session_id: str, text: str) -> None:
        """更新累積的文字。"""
        self._accumulated_texts[session_id] = text

    def clear_accumulated_text(self, session_id: str) -> None:
        """清除累積的文字。"""
        self._accumulated_texts.pop(session_id, None)
        self._last_text_lengths.pop(session_id, None)

    def calculate_delta_text(self, session_id: str, current_text: str) -> str:
        """
        計算增量文字。

        Args:
            session_id: Session ID
            current_text: 當前完整文字

        Returns:
            str: 增量文字
        """
        previous_text = self._accumulated_texts.get(session_id, "")
        self._accumulated_texts[session_id] = current_text

        if current_text.startswith(previous_text):
            return current_text[len(previous_text):]
        else:
            # 如果不是追加模式，返回完整文字
            return current_text

    def format_text_response(
        self,
        session_id: str,
        task_id: str,
        text: str,
        *,
        append: bool = False,
        last_chunk: bool = True,
        is_final: bool = True,
        message_id: str | None = None,
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        """
        格式化文字響應（A2A artifact-update）。

        Args:
            session_id: Session ID
            task_id: Task ID
            text: 文字內容
            append: 是否追加
            last_chunk: 是否為最後一塊
            is_final: 是否為最終訊息
            message_id: 訊息 ID（可選，預設自動生成）
            artifact_id: Artifact ID（可選，預設自動生成）

        Returns:
            dict: agent_response 包裝的訊息
        """
        if message_id is None:
            message_id = self.get_message_id()
        if artifact_id is None:
            artifact_id = self.get_artifact_id()

        # 根據 last_chunk 選擇使用 text 或 reasoningText
        if last_chunk:
            data_part = build_text_part(text)
        else:
            data_part = build_reasoning_text_part(text)

        artifact_update = {
            "taskId": task_id,
            "kind": "artifact-update",
            "append": append,
            "lastChunk": last_chunk,
            "final": is_final,
            "artifact": {
                "artifactId": artifact_id,
                "parts": [data_part],
            },
        }

        json_rpc_response = _build_json_rpc_response(message_id, artifact_update)

        return _build_agent_response_wrapper(
            agent_id=self.agent_id,
            session_id=session_id,
            task_id=task_id,
            response_body=json_rpc_response,
        )

    def format_status_update(
        self,
        session_id: str,
        task_id: str,
        text: str,
        state: str,
        *,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """
        格式化狀態更新（A2A status-update）。

        Args:
            session_id: Session ID
            task_id: Task ID
            text: 狀態文字
            state: 狀態值
            message_id: 訊息 ID（可選，預設自動生成）

        Returns:
            dict: agent_response 包裝的訊息
        """
        if message_id is None:
            message_id = self.get_message_id()

        status_update = build_status_update_response(task_id, text, state)
        json_rpc_response = _build_json_rpc_response(message_id, status_update)

        return _build_agent_response_wrapper(
            agent_id=self.agent_id,
            session_id=session_id,
            task_id=task_id,
            response_body=json_rpc_response,
        )

    def format_command(
        self,
        session_id: str,
        task_id: str,
        command: dict[str, Any],
        *,
        message_id: str | None = None,
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        """
        格式化命令（A2A artifact-update with command）。

        Args:
            session_id: Session ID
            task_id: Task ID
            command: 命令物件
            message_id: 訊息 ID（可選，預設自動生成）
            artifact_id: Artifact ID（可選，預設自動生成）

        Returns:
            dict: agent_response 包裝的訊息
        """
        if message_id is None:
            message_id = self.get_message_id()
        if artifact_id is None:
            artifact_id = self.get_artifact_id()

        artifact_update = {
            "taskId": task_id,
            "kind": "artifact-update",
            "append": False,
            "lastChunk": True,
            "final": False,
            "artifact": {
                "artifactId": artifact_id,
                "parts": [build_command_part(command)],
            },
        }

        json_rpc_response = _build_json_rpc_response(message_id, artifact_update)

        return _build_agent_response_wrapper(
            agent_id=self.agent_id,
            session_id=session_id,
            task_id=task_id,
            response_body=json_rpc_response,
        )

    def format_clear_context(
        self,
        session_id: str,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """
        格式化 clearContext 響應。

        Args:
            session_id: Session ID
            message_id: 訊息 ID（可選，預設自動生成）

        Returns:
            dict: agent_response 包裝的訊息
        """
        if message_id is None:
            message_id = self.get_message_id()

        clear_context_response = build_clear_context_response()
        json_rpc_response = _build_json_rpc_response(message_id, clear_context_response)

        return _build_agent_response_wrapper(
            agent_id=self.agent_id,
            session_id=session_id,
            task_id=session_id,  # Use sessionId as taskId for clearContext
            response_body=json_rpc_response,
        )

    def format_tasks_cancel(
        self,
        session_id: str,
        task_id: str,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """
        格式化 tasks/cancel 響應。

        Args:
            session_id: Session ID
            task_id: Task ID
            message_id: 訊息 ID（可選，預設自動生成）

        Returns:
            dict: agent_response 包裝的訊息
        """
        if message_id is None:
            message_id = self.get_message_id()

        cancel_response = build_tasks_cancel_response(task_id)
        json_rpc_response = _build_json_rpc_response(message_id, cancel_response)

        return _build_agent_response_wrapper(
            agent_id=self.agent_id,
            session_id=session_id,
            task_id=task_id,
            response_body=json_rpc_response,
        )


# ==================== Event Type Utilities ====================

def should_send_as_reasoning_text(event_type: EventType | None) -> bool:
    """
    判斷是否應該將訊息作為 reasoningText 傳送。

    Args:
        event_type: 事件型別

    Returns:
        bool: 是否應該作為 reasoningText 傳送
    """
    if event_type is None:
        return False

    # Reasoning text 用於以下事件：
    # - CHAT_DELTA: 流式輸出中的增量文字
    # - CHAT_TOOL_RESULT: 工具結果
    # - CHAT_SUBTASK_UPDATE: 子任務更新
    # - CHAT_PROCESSING_STATUS: 處理狀態
    reasoning_text_events = {
        EventType.CHAT_DELTA,
        EventType.CHAT_SUBTASK_UPDATE,
        EventType.CHAT_PROCESSING_STATUS,
    }

    return event_type in reasoning_text_events


def should_send_as_text(event_type: EventType | None) -> bool:
    """
    判斷是否應該將訊息作為 text 傳送。

    Args:
        event_type: 事件型別

    Returns:
        bool: 是否應該作為 text 傳送
    """
    if event_type is None:
        return True  # Default to text

    # Text 用於以下事件：
    # - CHAT_FINAL: 最終完整回覆
    # - CHAT_MEDIA: 媒體訊息
    # - CHAT_ERROR: 錯誤訊息
    # - CHAT_INTERRUPT_RESULT: 中斷結果
    text_events = {
        EventType.CHAT_FINAL,
        EventType.CHAT_MEDIA,
        EventType.CHAT_ERROR,
        EventType.CHAT_INTERRUPT_RESULT,
    }

    return event_type in text_events


def should_send_as_status_update(event_type: EventType | None) -> bool:
    """
    判斷是否應該作為 status update 傳送。

    Args:
        event_type: 事件型別

    Returns:
        bool: 是否應該作為 status update 傳送
    """
    if event_type is None:
        return False

    # Status update 用於以下事件：
    # - CHAT_TOOL_CALL: 工具呼叫
    # - CHAT_TOOL_RESULT: 工具結果
    # - CHAT_PROCESSING_STATUS: 處理狀態
    status_events = {
        EventType.CHAT_TOOL_CALL,
        EventType.CHAT_TOOL_RESULT
    }

    return event_type in status_events


def get_status_state_for_event(event_type: EventType | None, payload: dict | None = None) -> str:
    """
    根據事件型別獲取狀態值。

    Args:
        event_type: 事件型別
        payload: 訊息載荷

    Returns:
        str: 狀態值
    """
    if event_type is None:
        return "unknown"

    if event_type == EventType.CHAT_PROCESSING_STATUS:
        if payload and isinstance(payload, dict):
            is_processing = payload.get("is_processing", True)
            return "working" if is_processing else "completed"
        return "working"

    status_map = {
        EventType.CHAT_TOOL_CALL: "working",
        EventType.CHAT_TOOL_RESULT: "working",
        EventType.CHAT_FINAL: "completed",
        EventType.CHAT_ERROR: "failed",
    }

    return status_map.get(event_type, "unknown")


def get_status_text_for_event(event_type: EventType | None, payload: dict | None = None) -> str:
    """
    根據事件型別獲取狀態文字。

    Args:
        event_type: 事件型別
        payload: 訊息載荷

    Returns:
        str: 狀態文字
    """
    if event_type is None:
        return "處理中"

    if payload and isinstance(payload, dict):
        if event_type == EventType.CHAT_TOOL_CALL:
            tool_call = payload.get("tool_call", {})
            if isinstance(tool_call, dict):
                tool_name = tool_call.get("name", "")
                if tool_name:
                    return f"正在使用工具：{tool_name}"
            return "正在使用工具..."
        if event_type == EventType.CHAT_TOOL_RESULT:
            tool_name = payload.get("tool_name", "")
            if tool_name:
                return f"工具 {tool_name} 執行完成"
            return "工具執行完成"
        if event_type == EventType.CHAT_PROCESSING_STATUS:
            is_processing = payload.get("is_processing", True)
            if is_processing:
                return "任務正在處理中，請稍後~"
            return "任務處理已完成~"
        content = payload.get("content", "")
        if isinstance(content, str) and content:
            return content
        if isinstance(content, dict):
            return content.get("output", "處理中")

    status_text_map = {
        EventType.CHAT_TOOL_CALL: "正在使用工具...",
        EventType.CHAT_TOOL_RESULT: "工具執行完成",
        EventType.CHAT_PROCESSING_STATUS: "任務正在處理中",
        EventType.CHAT_FINAL: "任務已完成",
        EventType.CHAT_ERROR: "處理失敗，請稍後重試",
        EventType.CHAT_INTERRUPT_RESULT: "任務已中斷",
    }

    return status_text_map.get(event_type, "處理中")


def extract_msg_content(msg: Message) -> str:
    """提取msg 資訊"""
    content = ""
    payload = msg.payload if msg.payload else {}
    if not isinstance(payload, dict):
        return str(msg.payload)
    if msg.event_type == EventType.CHAT_TOOL_CALL:
        content = payload.get("tool_call", {}).get("name", "")
    elif msg.event_type == EventType.CHAT_TOOL_RESULT:
        content = payload.get("result", "")
    else:
        content = msg.payload.get("content", "")
        if isinstance(content, dict):
            content = content.get("output", str(content))
        content = str(content)
    return content