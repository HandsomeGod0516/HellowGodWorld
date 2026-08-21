# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
將 ACP JSON-RPC、A2A SendMessage 等外部形態轉換為 E2A，並寫入 provenance。

``envelope_from_a2a_send_message`` 的引數 ``metadata`` 僅對應 E2A 的 ``a2a_metadata``（A2A 規範側），
與閘道器通道上的 ``metadata`` / ``channel_context`` 無關。

``e2a_response_to_acp_jsonrpc_response`` / ``e2a_response_to_a2a_stream_payload`` 將 ``E2AResponse`` 投影為
外協議形狀；不適用時返回 ``None``（見 ``docs/zh/E2A-protocol.md`` §8、§12）。
"""


from __future__ import annotations

import time
import uuid as uuid_module
from typing import Any

from jiuwenclaw.common.e2a.constants import (
    E2A_A2A_STREAM_BRANCHES,
    E2A_RESPONSE_KIND_ACP_JSONRPC_ERROR,
    E2A_RESPONSE_KIND_ACP_PROMPT_RESULT,
    E2A_RESPONSE_KIND_A2A_STREAM_EVENT,
    E2A_RESPONSE_KIND_E2A_ERROR,
    E2A_SOURCE_PROTOCOL_ACP,
    E2A_SOURCE_PROTOCOL_A2A,
)
from jiuwenclaw.common.e2a.models import (
    E2AEnvelope,
    E2AProvenance,
    E2AResponse,
    IdentityOrigin,
    merge_params_to_acp_prompt,
    utc_now_iso,
)

_CONVERTER_ACP = "jiuwenclaw.common.e2a.adapters:envelope_from_acp_jsonrpc"
_CONVERTER_A2A = "jiuwenclaw.common.e2a.adapters:envelope_from_a2a_send_message"


def envelope_from_acp_jsonrpc(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    jsonrpc_id: str | int | None = None,
    session_id: str | None = None,
    channel: str | None = None,
    identity_origin: IdentityOrigin = IdentityOrigin.USER,
    converter: str | None = None,
    extra_provenance_details: dict[str, Any] | None = None,
) -> E2AEnvelope:
    """由 ACP JSON-RPC 呼叫構造 E2A；provenance 標明來源為 acp。"""
    p = dict(params or {})
    sid = session_id or p.get("session_id")
    details: dict[str, Any] = {
        "kind": "jsonrpc_request",
        "jsonrpc_method": method,
    }
    if jsonrpc_id is not None:
        details["jsonrpc_id"] = jsonrpc_id
    if extra_provenance_details:
        details.update(extra_provenance_details)
    return E2AEnvelope(
        provenance=E2AProvenance(
            source_protocol=E2A_SOURCE_PROTOCOL_ACP,
            converter=converter or _CONVERTER_ACP,
            converted_at=utc_now_iso(),
            details=details,
        ),
        jsonrpc_id=jsonrpc_id,
        method=method,
        params=p,
        session_id=sid if isinstance(sid, str) else None,
        channel=channel,
        identity_origin=identity_origin,
    )


def envelope_from_a2a_send_message(
    *,
    task_id: str | None,
    context_id: str | None,
    message_body: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    configuration: dict[str, Any] | None = None,
    channel: str | None = None,
    identity_origin: IdentityOrigin = IdentityOrigin.USER,
    converter: str | None = None,
    extra_provenance_details: dict[str, Any] | None = None,
) -> E2AEnvelope:
    """
    將 A2A SendMessage 語義轉為 E2A；provenance 標明來源為 a2a。

    預設 method 為 ``session/prompt``，完整 message/configuration 保留在 params 與 a2a_metadata。
    """
    meta = dict(metadata or {})
    accepted: list[str] = []
    if configuration:
        acc = configuration.get("acceptedOutputModes")
        if isinstance(acc, list):
            accepted = [str(x) for x in acc]
    details: dict[str, Any] = {
        "kind": "a2a_send_message",
        "abstract_operation": "SendMessage",
    }
    if extra_provenance_details:
        details.update(extra_provenance_details)
    return E2AEnvelope(
        provenance=E2AProvenance(
            source_protocol=E2A_SOURCE_PROTOCOL_A2A,
            converter=converter or _CONVERTER_A2A,
            converted_at=utc_now_iso(),
            details=details,
        ),
        method="session/prompt",
        task_id=task_id,
        context_id=context_id,
        channel=channel,
        identity_origin=identity_origin,
        expected_output_modes=accepted,
        params={"message": message_body, "configuration": configuration or {}},
        a2a_metadata=meta,
    )


def envelope_to_acp_jsonrpc_call(envelope: E2AEnvelope) -> dict[str, Any]:
    """
    將信封轉為 JSON-RPC 風格單條呼叫描述（日誌或下游 ACP 端點）。

    若 ``envelope.method`` 為閘道器 RPC（如 ``chat.send``），輸出中的 ``method`` 對純 ACP 端可能無效，
    需在業務層先對映為 ACP method（如 ``session/prompt``）再傳送。
    """
    method = envelope.ext_method if envelope.method == "ext" and envelope.ext_method else envelope.method
    params = merge_params_to_acp_prompt(envelope) if envelope.method == "session/prompt" else dict(envelope.params)
    return {
        "jsonrpc": "2.0",
        "id": envelope.jsonrpc_id,
        "method": method,
        "params": params,
    }


def e2a_response_to_acp_jsonrpc_response(response: E2AResponse) -> dict[str, Any] | None:
    """
    將 ``E2AResponse`` 轉為單條 JSON-RPC 2.0 **響應**物件（僅 ``result`` 或 ``error``，無 ``method``）。

    優先使用 ``projections.acp``：若為已組裝的完整響應（含 ``jsonrpc`` 與 ``result``/``error``），原樣返回副本。
    否則按 ``response_kind`` 從 ``body`` 構造：

    - ``acp.prompt_result`` → ``result`` = ``body``
    - ``acp.jsonrpc_error`` → ``error`` = ``body``（須含 JSON-RPC 所需欄位）
    - ``e2a.error`` → ``error``：``code`` 非 int 時用 ``-32603``，字串碼寫入 ``data.e2a_code``
    """
    proj = response.projections.get("acp") if isinstance(response.projections, dict) else None
    if isinstance(proj, dict) and proj.get("jsonrpc") == "2.0":
        if "result" in proj or "error" in proj:
            out = dict(proj)
            out.setdefault("id", response.jsonrpc_id)
            return out

    rpc_id = response.jsonrpc_id
    kind = response.response_kind
    body = dict(response.body or {})

    if kind == E2A_RESPONSE_KIND_ACP_PROMPT_RESULT:
        return {"jsonrpc": "2.0", "id": rpc_id, "result": body}

    if kind == E2A_RESPONSE_KIND_ACP_JSONRPC_ERROR:
        return {"jsonrpc": "2.0", "id": rpc_id, "error": body}

    if kind == E2A_RESPONSE_KIND_E2A_ERROR:
        code_raw = body.get("code")
        code = code_raw if isinstance(code_raw, int) else -32603
        message = str(body.get("message") or "")
        data: dict[str, Any] = {}
        det = body.get("details")
        if det is not None:
            data["details"] = det
        ext = body.get("external")
        if ext is not None:
            data["external"] = ext
        if code_raw is not None and not isinstance(code_raw, int):
            data["e2a_code"] = code_raw
        err: dict[str, Any] = {"code": code, "message": message}
        if data:
            err["data"] = data
        return {"jsonrpc": "2.0", "id": rpc_id, "error": err}

    return None


def e2a_response_to_a2a_stream_payload(response: E2AResponse) -> dict[str, Any] | None:
    """
    將 ``response_kind == \"a2a.stream_event\"`` 的 ``E2AResponse`` 轉為 A2A ``StreamResponse`` 形 JSON：

    外層鍵為 ``task`` / ``message`` / ``statusUpdate`` / ``artifactUpdate`` 之一（與常見 JSON 繫結一致）。

    若 ``projections.a2a`` 已為四選一單鍵物件，則原樣返回副本。
    ``body.branch`` 須為 ``E2A_A2A_STREAM_BRANCHES`` 之一；``body.payload`` 為對應分支物件。
    """
    if response.response_kind != E2A_RESPONSE_KIND_A2A_STREAM_EVENT:
        return None

    proj = response.projections.get("a2a") if isinstance(response.projections, dict) else None
    if isinstance(proj, dict) and len(proj) == 1:
        key = next(iter(proj))
        if key in ("task", "message", "statusUpdate", "artifactUpdate"):
            return dict(proj)

    body = dict(response.body or {})
    branch = body.get("branch")
    payload = body.get("payload")
    if branch not in E2A_A2A_STREAM_BRANCHES or not isinstance(payload, dict):
        return None

    key_map = {
        "task": "task",
        "message": "message",
        "status_update": "statusUpdate",
        "artifact_update": "artifactUpdate",
    }
    outer = key_map.get(branch)
    if outer is None:
        return None
    return {outer: payload}


def build_acp_tool_response_message(
    jsonrpc_id: str,
    response_data: dict[str, Any],
    session_id: str | None,
    channel_id: str = "acp",
) -> Any:
    """Build an internal Message for an ACP tool response (JSON-RPC response from client).

    Shared by AcpRouteHandler (WebSocket gateway mode) and AcpChannel (stdio mode)
    to avoid duplicated Message construction logic.
    """
    from jiuwenclaw.common.schema.message import Message, ReqMethod

    return Message(
        id=f"acp_tool_resp_{uuid_module.uuid4().hex[:12]}",
        type="req",
        channel_id=channel_id,
        session_id=session_id,
        params={
            "jsonrpc_id": jsonrpc_id,
            "response": dict(response_data),
            "session_id": session_id,
        },
        timestamp=time.time(),
        ok=True,
        req_method=ReqMethod.ACP_TOOL_RESPONSE,
        is_stream=False,
        metadata={"acp": {"jsonrpc_id": jsonrpc_id, "kind": "tool_response"}},
    )
