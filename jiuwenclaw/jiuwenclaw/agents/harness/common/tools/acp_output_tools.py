# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
ACP 輸出工具：AgentServer 向 IDE 傳送請求。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, List, TYPE_CHECKING

from jiuwenclaw.common.e2a.constants import (
    E2A_RESPONSE_KIND_ACP_OUTPUT_REQUEST,
    E2A_RESPONSE_STATUS_IN_PROGRESS,
    E2A_SOURCE_PROTOCOL_E2A,
    E2A_WIRE_SERVER_PUSH_KEY,
)
from jiuwenclaw.common.e2a.models import (
    E2A_PROTOCOL_VERSION,
    E2AProvenance,
    E2AResponse,
    IdentityOrigin,
    utc_now_iso,
)

if TYPE_CHECKING:
    from openjiuwen.core.foundation.tool import Tool

logger = logging.getLogger(__name__)

_ACP_REQUEST_TIMEOUT_SECONDS = 30.0
try:
    _ACP_WAIT_FOR_EXIT_TIMEOUT_SECONDS = float(
        os.getenv("ACP_WAIT_FOR_EXIT_TIMEOUT_SECONDS", "30")
    )
except ValueError:
    _ACP_WAIT_FOR_EXIT_TIMEOUT_SECONDS = 30.0


@dataclass
class AcpOutputRequest:
    jsonrpc_id: str
    method: str
    params: dict[str, Any]
    future: asyncio.Future[dict[str, Any]]
    request_id: str


class AcpOutputManager:
    _instance: AcpOutputManager | None = None
    _pending: dict[str, AcpOutputRequest]
    _jsonrpc_counter: int
    _send_push_callback: Any

    def __new__(cls) -> AcpOutputManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._pending = {}
            cls._instance._jsonrpc_counter = 0
            cls._instance._send_push_callback = None
        return cls._instance

    def __init__(self) -> None:
        pass

    def set_send_push_callback(self, callback: Any) -> None:
        self._send_push_callback = callback

    def reset_state(self) -> None:
        """Reset runtime state.

        Intended for unit tests and controlled lifecycle cleanup.
        """
        self._pending.clear()
        self._jsonrpc_counter = 0

    def add_pending_request(self, request: AcpOutputRequest) -> None:
        """Register a pending request explicitly.

        Used by tests to seed pending state without touching protected members.
        """
        self._pending[str(request.jsonrpc_id)] = request

    def complete_jsonrpc_response(
        self,
        jsonrpc_id: str | int | None,
        response: dict[str, Any],
    ) -> bool:
        """Complete a pending ACP JSON-RPC request."""
        jsonrpc_key = str(jsonrpc_id or "").strip()
        if not jsonrpc_key:
            return False

        pending = self._pending.pop(jsonrpc_key, None)
        if pending is None:
            logger.warning(
                "[AcpOutput] completion dropped: jsonrpc_id=%s pending_keys=%s",
                jsonrpc_key,
                list(self._pending.keys()),
            )
            return False

        if pending.future.done():
            return False

        pending.future.set_result(dict(response or {}))
        logger.info(
            "[AcpOutput] request completed: jsonrpc_id=%s method=%s",
            jsonrpc_key,
            pending.method,
        )
        return True

    def fail_jsonrpc_response(
        self,
        jsonrpc_id: str | int | None,
        exc: BaseException,
    ) -> bool:
        jsonrpc_key = str(jsonrpc_id or "").strip()
        if not jsonrpc_key:
            return False

        pending = self._pending.pop(jsonrpc_key, None)
        if pending is None:
            return False
        if pending.future.done():
            return False
        pending.future.set_exception(exc)
        return True

    async def send_jsonrpc_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        channel_id: str = "acp",
        session_id: str | None = None,
        timeout: float = _ACP_REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        logger.info(
            "[AcpOutput] send_jsonrpc_request called: method=%s params=%s",
            method,
            params,
        )
        if self._send_push_callback is None:
            logger.error("[AcpOutput] send_push callback is None!")
            raise RuntimeError("ACP output send_push callback not set")

        self._jsonrpc_counter += 1
        jsonrpc_id = str(self._jsonrpc_counter)
        request_id = f"acp_out_{uuid.uuid4().hex[:12]}"

        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_event_loop().create_future()
        )
        acp_req = AcpOutputRequest(
            jsonrpc_id=jsonrpc_id,
            method=method,
            params=params,
            future=future,
            request_id=request_id,
        )
        self._pending[jsonrpc_id] = acp_req
        logger.info(
            "[AcpOutput] added to pending: jsonrpc_id=%s pending_keys=%s",
            jsonrpc_id,
            list(self._pending.keys()),
        )

        ts = utc_now_iso()
        prov = E2AProvenance(
            source_protocol=E2A_SOURCE_PROTOCOL_E2A,
            converter="jiuwenclaw.agents.harness.common.tools.acp_output_tools:send_jsonrpc_request",
            converted_at=ts,
            details={"kind": "acp_output_request", "acp_method": method},
        )

        e2a_response = E2AResponse(
            protocol_version=E2A_PROTOCOL_VERSION,
            response_id=f"acp_out_resp_{uuid.uuid4().hex[:12]}",
            request_id=request_id,
            sequence=0,
            is_final=False,
            status=E2A_RESPONSE_STATUS_IN_PROGRESS,
            response_kind=E2A_RESPONSE_KIND_ACP_OUTPUT_REQUEST,
            timestamp=ts,
            provenance=prov,
            body={
                "jsonrpc": "2.0",
                "id": jsonrpc_id,
                "method": method,
                "params": {**params, "sessionId": session_id} if session_id else params,
            },
            jsonrpc_id=jsonrpc_id,
            session_id=session_id,
            channel=channel_id,
            identity_origin=IdentityOrigin.AGENT,
            metadata={E2A_WIRE_SERVER_PUSH_KEY: True},
        )

        push_msg = e2a_response.to_dict()
        if "channel_id" not in push_msg and "channel" in push_msg:
            push_msg["channel_id"] = push_msg["channel"]
        if "payload" not in push_msg and "body" in push_msg:
            push_msg["payload"] = push_msg["body"]

        try:
            callback_result = self._send_push_callback(push_msg)
            if inspect.isawaitable(callback_result):
                await callback_result
        except Exception as exc:
            self._pending.pop(jsonrpc_id, None)
            raise RuntimeError(f"Failed to send ACP output request: {exc}") from exc

        logger.info(
            "[AcpOutput] sent E2A request: jsonrpc_id=%s method=%s request_id=%s",
            jsonrpc_id,
            method,
            request_id,
        )

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(jsonrpc_id, None)
            logger.warning(
                "[AcpOutput] request timed out: jsonrpc_id=%s method=%s timeout=%.1fs",
                jsonrpc_id,
                method,
                timeout,
            )
            raise


def get_acp_output_manager() -> AcpOutputManager:
    return AcpOutputManager()


class AcpOutputError(Exception):
    def __init__(self, method: str, code: int, message: str, data: Any = None):
        self.method = method
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"[{method}] error {code}: {message}")


# ============================================================================
# ACP 工具函式
# ============================================================================


async def read_text_file(
    path: str,
    *,
    offset: int | None = None,
    limit: int | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """讀取檔案內容。"""
    mgr = get_acp_output_manager()
    params: dict[str, Any] = {"path": path}
    if offset is not None:
        params["offset"] = offset
    if limit is not None:
        params["limit"] = limit

    response = await mgr.send_jsonrpc_request(
        "fs/read_text_file", params, session_id=session_id
    )

    if "error" in response:
        err = response["error"]
        raise AcpOutputError(
            method="fs/read_text_file",
            code=err.get("code", -32000),
            message=err.get("message", "Unknown error"),
            data=err.get("data"),
        )

    return response.get("result", {})


async def write_text_file(
    path: str,
    content: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """寫入檔案。"""
    mgr = get_acp_output_manager()
    params = {"path": path, "content": content}

    response = await mgr.send_jsonrpc_request(
        "fs/write_text_file", params, session_id=session_id
    )

    if "error" in response:
        err = response["error"]
        raise AcpOutputError(
            method="fs/write_text_file",
            code=err.get("code", -32000),
            message=err.get("message", "Unknown error"),
            data=err.get("data"),
        )

    return response.get("result", {})


async def create_terminal(
    cmd: str,
    *,
    cwd: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """建立終端並執行命令。"""
    mgr = get_acp_output_manager()
    params: dict[str, Any] = {"command": cmd}
    if cwd is not None:
        params["cwd"] = cwd

    response = await mgr.send_jsonrpc_request(
        "terminal/create", params, session_id=session_id
    )

    if "error" in response:
        err = response["error"]
        raise AcpOutputError(
            method="terminal/create",
            code=err.get("code", -32000),
            message=err.get("message", "Unknown error"),
            data=err.get("data"),
        )

    return response.get("result", {})


async def read_terminal_output(
    terminal_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """讀取終端輸出。"""
    mgr = get_acp_output_manager()
    params = {"terminalId": terminal_id}

    response = await mgr.send_jsonrpc_request(
        "terminal/output", params, session_id=session_id
    )

    if "error" in response:
        err = response["error"]
        raise AcpOutputError(
            method="terminal/output",
            code=err.get("code", -32000),
            message=err.get("message", "Unknown error"),
            data=err.get("data"),
        )

    return response.get("result", {})


async def wait_for_terminal_exit(
    terminal_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """等待終端退出。"""
    mgr = get_acp_output_manager()
    params = {"terminalId": terminal_id}

    try:
        response = await mgr.send_jsonrpc_request(
            "terminal/wait_for_exit",
            params,
            session_id=session_id,
            timeout=_ACP_WAIT_FOR_EXIT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.info(
            "[AcpOutput] wait_for_terminal_exit soft-timeout: terminal_id=%s timeout=%.1fs",
            terminal_id,
            _ACP_WAIT_FOR_EXIT_TIMEOUT_SECONDS,
        )
        return {
            "exitCode": None,
            "signal": None,
            "timedOut": True,
            "running": True,
            "shouldRetry": True,
        }

    if "error" in response:
        err = response["error"]
        raise AcpOutputError(
            method="terminal/wait_for_exit",
            code=err.get("code", -32000),
            message=err.get("message", "Unknown error"),
            data=err.get("data"),
        )

    result = dict(response.get("result", {}) or {})
    # Normalize terminal wait result so callers can deterministically decide
    # whether they should poll again.
    result.setdefault("timedOut", False)
    if "running" not in result and (
        result.get("exitCode") is not None or result.get("signal") is not None
    ):
        result["running"] = False
    result["shouldRetry"] = bool(result.get("timedOut")) or bool(result.get("running"))
    return result


async def release_terminal(
    terminal_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """釋放終端資源。"""
    mgr = get_acp_output_manager()
    params = {"terminalId": terminal_id}

    response = await mgr.send_jsonrpc_request(
        "terminal/release", params, session_id=session_id
    )

    if "error" in response:
        err = response["error"]
        raise AcpOutputError(
            method="terminal/release",
            code=err.get("code", -32000),
            message=err.get("message", "Unknown error"),
            data=err.get("data"),
        )

    return response.get("result", {})


# ============================================================================
# 工具註冊
# ============================================================================


def get_tools(session_id: str = "", request_id: str = "") -> List["Tool"]:
    """返回 ACP 輸出工具列表，用於註冊到 Agent。"""
    from openjiuwen.core.foundation.tool import ToolCard, LocalFunction

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

    # 建立繫結了 session_id 的函式
    async def read_text_file_bound(
        path: str, offset: int | None = None, limit: int | None = None
    ) -> dict:
        return await read_text_file(
            path, offset=offset, limit=limit, session_id=session_id
        )

    async def write_text_file_bound(path: str, content: str) -> dict:
        return await write_text_file(path, content, session_id=session_id)

    async def create_terminal_bound(cmd: str, cwd: str | None = None) -> dict:
        return await create_terminal(cmd, cwd=cwd, session_id=session_id)

    async def read_terminal_output_bound(terminal_id: str) -> dict:
        return await read_terminal_output(terminal_id, session_id=session_id)

    async def wait_for_terminal_exit_bound(terminal_id: str) -> dict:
        return await wait_for_terminal_exit(terminal_id, session_id=session_id)

    async def release_terminal_bound(terminal_id: str) -> dict:
        return await release_terminal(terminal_id, session_id=session_id)

    return [
        make_tool(
            name="read_text_file",
            description=(
                "[ACP] 透過 IDE 讀取使用者本地檔案內容。"
                "這是唯一可用的檔案讀取工具。"
                "必須呼叫此工具才能讀取檔案。"
            ),
            input_params={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要讀取的檔案路徑"},
                    "offset": {
                        "type": "integer",
                        "description": "起始行號（可選，從 1 開始）",
                    },
                    "limit": {"type": "integer", "description": "讀取行數（可選）"},
                },
                "required": ["path"],
            },
            func=read_text_file_bound,
        ),
        make_tool(
            name="write_text_file",
            description=(
                "[ACP] 透過 IDE 寫入檔案到使用者本地。"
                "這是唯一可用的檔案寫入工具。"
                "必須呼叫此工具才能寫入檔案。"
            ),
            input_params={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要寫入的檔案路徑"},
                    "content": {"type": "string", "description": "要寫入的檔案內容"},
                },
                "required": ["path", "content"],
            },
            func=write_text_file_bound,
        ),
        make_tool(
            name="create_terminal",
            description=(
                "[ACP] 透過 IDE 建立終端並執行命令。"
                "這是唯一可用的命令執行工具。"
                "必須呼叫此工具才能執行命令。"
            ),
            input_params={
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "要執行的命令"},
                    "cwd": {"type": "string", "description": "工作目錄（可選）"},
                },
                "required": ["cmd"],
            },
            func=create_terminal_bound,
        ),
        make_tool(
            name="read_terminal_output",
            description="讀取終端輸出內容。",
            input_params={
                "type": "object",
                "properties": {
                    "terminal_id": {"type": "string", "description": "終端 ID"},
                },
                "required": ["terminal_id"],
            },
            func=read_terminal_output_bound,
        ),
        make_tool(
            name="wait_for_terminal_exit",
            description=(
                "等待終端命令執行完成。若返回 timedOut=true / running=true / shouldRetry=true，"
                "表示任務仍在執行中，應繼續使用同一個 terminal_id 再次呼叫本工具輪詢。"
            ),
            input_params={
                "type": "object",
                "properties": {
                    "terminal_id": {"type": "string", "description": "終端 ID"},
                },
                "required": ["terminal_id"],
            },
            func=wait_for_terminal_exit_bound,
        ),
        make_tool(
            name="release_terminal",
            description="釋放終端資源（命令完成後必須呼叫）。",
            input_params={
                "type": "object",
                "properties": {
                    "terminal_id": {"type": "string", "description": "終端 ID"},
                },
                "required": ["terminal_id"],
            },
            func=release_terminal_bound,
        ),
    ]

