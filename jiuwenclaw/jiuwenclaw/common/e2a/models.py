# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
E2A 資料模型：請求信封 ``E2AEnvelope``、響應 ``E2AResponse`` 與子結構。

完整約定、易混點與 JSON 示例見倉庫 ``docs/zh/E2A-protocol.md``（``docs/en/E2A-protocol.md``）。欄位以本模組 dataclass 為準。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from jiuwenclaw.common.e2a.constants import (
    E2A_RESPONSE_STATUS_IN_PROGRESS,
    E2A_SOURCE_PROTOCOL_A2A,
    E2A_SOURCE_PROTOCOL_ACP,
    E2A_SOURCE_PROTOCOL_E2A,
)


E2A_PROTOCOL_VERSION = "1.0"


def utc_now_iso() -> str:
    """當前 UTC 時刻的 RFC 3339 字串（``provenance.converted_at``、響應 ``timestamp`` 預設等）。"""

    return datetime.now(timezone.utc).isoformat()


class IdentityOrigin(str, Enum):
    """身份來源：誰觸發了本次對 Agent 的請求。"""

    SYSTEM = "system"
    USER = "user"
    AGENT = "agent"
    SERVICE = "service"


@dataclass
class E2AProvenance:
    """
    記錄 E2A 信封的出處。

    - E2A 為統一載體：ACP、A2A 等訊息經轉換後均應落在此結構中。
    - ``source_protocol`` 標明**進入 E2A 之前**所依據的主要協議或「原生 E2A」。
    - ``converter`` / ``converted_at`` / ``details`` 標明由誰、何時、從何種具體呼叫轉換而來。
    """

    source_protocol: str = E2A_SOURCE_PROTOCOL_E2A
    converter: str | None = None
    converted_at: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class E2AFileRef:
    """檔案引用（用於 ``params.files`` / ``params.attachments`` 等元素，對齊 MCP/A2A 常見形態）。"""

    uri: str
    name: str | None = None
    mime_type: str | None = None
    size: int | None = None
    _meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class E2AAuth:
    """
    身份鑑權資訊（按需填充）。

    建議：生產環境用 credential_ref / oauth 等間接引用，由閘道器在受控環境換票。
    """

    method_id: str | None = None
    bearer_token: str | None = None
    api_key_ref: str | None = None
    credential_ref: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    _meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class E2AEnvelope:
    """
    E2A 統一信封：單結構相容多協議入口，由閘道器或適配層解析後呼叫 Agent。

    基礎欄位：
    - protocol_version：E2A 載荷版本。
    - provenance：出處（原生 e2a 或由 acp / a2a 等轉換）。
    - request_id：閘道器↔AgentServer 主請求 id（流式 chunk 關聯）。
    - jsonrpc_id / correlation_id：JSON-RPC id、分散式追蹤等（可與 request_id 並存）。
    - task_id / context_id / session_id / message_id：對齊 A2A / ACP 側概念。
    - is_stream：是否流式響應。

    事件語義：
    - method：**閘道器 RPC**（如 ``chat.send``）或 ACP 轉入時的 JSON-RPC method；``ext`` + ``ext_method`` 用於自定義。
    - **params**：**唯一業務引數字典**（JSON-RPC params、使用者正文、``content_blocks``、附件列表等均放此處，見倉庫 ``docs/zh/E2A-protocol.md``）。

    通道與互操作：
    - channel_context：**可選溢位**；主路徑上通道側資訊應在**閘道器入口**對映為規範化欄位。
    - a2a_metadata / acp_meta：與 A2A/ACP 互操作時使用。
    """

    # --- 基礎 / 關聯 ---
    protocol_version: str = E2A_PROTOCOL_VERSION
    provenance: E2AProvenance = field(default_factory=E2AProvenance)
    request_id: str | None = None
    jsonrpc_id: str | int | None = None
    correlation_id: str | None = None
    task_id: str | None = None
    context_id: str | None = None
    session_id: str | None = None
    message_id: str | None = None

    # --- 時間戳：規範為 RFC 3339 UTC 字串；from_dict 可將歷史 float 紀元秒規範化 ---
    timestamp: str | None = None

    # --- 身份與入口 ---
    identity_origin: IdentityOrigin = IdentityOrigin.USER
    channel: str | None = None
    user_id: str | None = None
    chat_id: str | None = None
    source_agent_id: str | None = None

    # --- 閘道器 RPC（原 req_method）；ACP 轉入時同欄位承載 JSON-RPC method ---
    method: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    ext_method: str | None = None
    session_update_kind: str | None = None
    is_stream: bool = False

    # --- 期望輸出（對齊 A2A acceptedOutputModes）---
    expected_output_modes: list[str] = field(default_factory=list)

    # --- 鑑權 ---
    auth: E2AAuth | None = None

    # --- 擴充套件槽 ---
    channel_context: dict[str, Any] = field(default_factory=dict)
    a2a_metadata: dict[str, Any] = field(default_factory=dict)
    acp_meta: dict[str, Any] = field(default_factory=dict)

    def ensure_timestamp(self) -> None:
        """若未設定 timestamp，則填當前 UTC ISO8601。"""
        if self.timestamp is None:
            self.timestamp = utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        """序列化為 JSON 友好 dict（列舉轉為值）。"""
        d = _dataclass_to_json_dict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> E2AEnvelope:
        return _envelope_from_dict(data)


@dataclass
class E2AResponse:
    """
    E2A 統一響應：每條出站記錄（含流式多幀）一條例項；與 ``E2AEnvelope`` 對稱。

    分層語義見 ``docs/zh/E2A-protocol.md`` / ``docs/en/E2A-protocol.md`` §12；``response_kind`` 取值以
    ``constants.E2A_RESPONSE_KINDS`` 為準。

    ``metadata``：通道/業務自定義鍵值；相容舊版 ``AgentResponse.metadata``；協議轉換失敗時可臨時寫入兜底資訊
    （如原始片段、錯誤說明），與 ``a2a_metadata`` / ``acp_meta`` 分工不同。
    """

    protocol_version: str = E2A_PROTOCOL_VERSION
    response_id: str | None = None
    request_id: str | None = None
    sequence: int = 0
    is_final: bool = False
    status: str = E2A_RESPONSE_STATUS_IN_PROGRESS
    response_kind: str = ""
    timestamp: str | None = None
    provenance: E2AProvenance = field(default_factory=E2AProvenance)
    body: dict[str, Any] = field(default_factory=dict)

    jsonrpc_id: str | int | None = None
    correlation_id: str | None = None
    task_id: str | None = None
    context_id: str | None = None
    session_id: str | None = None
    message_id: str | None = None
    is_stream: bool = False
    identity_origin: IdentityOrigin = IdentityOrigin.AGENT
    channel: str | None = None
    user_id: str | None = None
    source_agent_id: str | None = None
    method: str | None = None

    projections: dict[str, Any] = field(default_factory=dict)
    channel_context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    a2a_metadata: dict[str, Any] = field(default_factory=dict)
    acp_meta: dict[str, Any] = field(default_factory=dict)

    def ensure_timestamp(self) -> None:
        """若未設定 timestamp，則填當前 UTC ISO8601。"""
        if self.timestamp is None:
            self.timestamp = utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        """序列化為 JSON 友好 dict（列舉轉為值）。"""
        return _dataclass_to_json_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> E2AResponse:
        return _e2a_response_from_dict(data)


def _enum_value(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    return obj


def _dataclass_to_json_dict(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "__dataclass_fields__"):
        out: dict[str, Any] = {}
        for f in fields(obj):
            v = getattr(obj, f.name)
            if v is None and f.name.startswith("_"):
                continue
            key = f.name
            if isinstance(v, Enum):
                out[key] = v.value
            elif hasattr(v, "__dataclass_fields__"):
                out[key] = _dataclass_to_json_dict(v)
            elif isinstance(v, list):
                out[key] = [
                    _dataclass_to_json_dict(x)
                    if hasattr(x, "__dataclass_fields__")
                    else _enum_value(x)
                    for x in v
                ]
            elif isinstance(v, dict):
                out[key] = {
                    k: _dataclass_to_json_dict(x)
                    if hasattr(x, "__dataclass_fields__")
                    else x
                    for k, x in v.items()
                }
            else:
                out[key] = v
        return out
    return obj


def _provenance_from_dict(raw: Any) -> E2AProvenance:
    if raw is None:
        return E2AProvenance()
    if isinstance(raw, E2AProvenance):
        return raw
    if not isinstance(raw, dict):
        return E2AProvenance()
    return E2AProvenance(
        source_protocol=str(raw.get("source_protocol", E2A_SOURCE_PROTOCOL_E2A)),
        converter=raw.get("converter"),
        converted_at=raw.get("converted_at"),
        details=dict(raw.get("details") or {}),
    )


def _normalize_timestamp_value(raw: Any) -> str | None:
    """規範為 RFC 3339 UTC 字串；接受 str 或歷史 float/int 紀元秒。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
    return str(raw)


def _migrate_legacy_binding(data: dict[str, Any], prov: E2AProvenance) -> E2AProvenance:
    """舊版 ``binding`` 欄位遷入 provenance.details，避免丟失資訊。"""
    legacy = data.get("binding")
    if legacy is None or prov.details.get("migrated_from_binding") is not None:
        return prov
    if isinstance(legacy, dict) and "value" in legacy:
        legacy = legacy["value"]
    legacy_s = str(legacy) if legacy is not None else ""
    d = dict(prov.details)
    d["migrated_from_binding"] = legacy_s
    sp = prov.source_protocol
    if sp == E2A_SOURCE_PROTOCOL_E2A:
        if legacy_s == E2A_SOURCE_PROTOCOL_ACP:
            sp = E2A_SOURCE_PROTOCOL_ACP
        elif legacy_s == E2A_SOURCE_PROTOCOL_A2A:
            sp = E2A_SOURCE_PROTOCOL_A2A
        elif legacy_s in ("internal", "hybrid"):
            sp = E2A_SOURCE_PROTOCOL_E2A
    return E2AProvenance(
        source_protocol=sp,
        converter=prov.converter,
        converted_at=prov.converted_at,
        details=d,
    )


def _params_with_optional_legacy_payload(data: dict[str, Any]) -> dict[str, Any]:
    """
    以 ``params`` 為真源；若存在頂層 ``payload`` 物件，將其鍵合併進 params（不覆蓋已有鍵）。
    """
    p = dict(data.get("params") or {})
    raw = data.get("payload")
    if not isinstance(raw, dict) or not raw:
        return p
    for k, v in raw.items():
        if k in p:
            continue
        if v is None:
            continue
        if v == [] or v == {}:
            continue
        p[k] = v
    return p


def _envelope_from_dict(data: dict[str, Any]) -> E2AEnvelope:
    prov = _provenance_from_dict(data.get("provenance"))
    prov = _migrate_legacy_binding(data, prov)

    origin = data.get("identity_origin", IdentityOrigin.USER.value)
    if isinstance(origin, str):
        origin = IdentityOrigin(origin)

    params = _params_with_optional_legacy_payload(data)

    auth_raw = data.get("auth")
    auth: E2AAuth | None
    if auth_raw is None:
        auth = None
    elif isinstance(auth_raw, E2AAuth):
        auth = auth_raw
    else:
        auth = E2AAuth(
            method_id=auth_raw.get("method_id"),
            bearer_token=auth_raw.get("bearer_token"),
            api_key_ref=auth_raw.get("api_key_ref"),
            credential_ref=auth_raw.get("credential_ref"),
            extra_headers=dict(auth_raw.get("extra_headers") or {}),
            _meta=dict(auth_raw.get("_meta") or {}),
        )

    # channel_context：合併 wire 頂層 metadata 中尚未出現的鍵。
    channel_context = dict(data.get("channel_context") or {})
    meta_top = data.get("metadata")
    if isinstance(meta_top, dict) and meta_top:
        for k, v in meta_top.items():
            if k not in channel_context:
                channel_context[k] = v

    ch = data.get("channel")
    if ch is None:
        ch = data.get("channel_id")

    raw_method = data.get("method")
    if raw_method is None and "req_method" in data:
        rm = data["req_method"]
        if isinstance(rm, str):
            raw_method = rm
        elif hasattr(rm, "value"):
            raw_method = str(rm.value)

    return E2AEnvelope(
        protocol_version=data.get("protocol_version", E2A_PROTOCOL_VERSION),
        provenance=prov,
        request_id=data.get("request_id"),
        jsonrpc_id=data.get("jsonrpc_id"),
        correlation_id=data.get("correlation_id"),
        task_id=data.get("task_id"),
        context_id=data.get("context_id"),
        session_id=data.get("session_id"),
        message_id=data.get("message_id"),
        timestamp=_normalize_timestamp_value(data.get("timestamp")),
        identity_origin=origin,
        channel=ch,
        user_id=data.get("user_id"),
        chat_id=data.get("chat_id"),
        source_agent_id=data.get("source_agent_id"),
        method=raw_method,
        params=params,
        ext_method=data.get("ext_method"),
        session_update_kind=data.get("session_update_kind"),
        is_stream=bool(data.get("is_stream", False)),
        expected_output_modes=list(data.get("expected_output_modes") or []),
        auth=auth,
        channel_context=channel_context,
        a2a_metadata=dict(data.get("a2a_metadata") or {}),
        acp_meta=dict(data.get("acp_meta") or {}),
    )


def _e2a_response_from_dict(data: dict[str, Any]) -> E2AResponse:
    prov = _provenance_from_dict(data.get("provenance"))

    origin = data.get("identity_origin", IdentityOrigin.AGENT.value)
    if isinstance(origin, str):
        origin = IdentityOrigin(origin)

    seq_raw = data.get("sequence", 0)
    try:
        sequence = int(seq_raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        sequence = 0

    ch = data.get("channel")
    if ch is None:
        ch = data.get("channel_id")

    return E2AResponse(
        protocol_version=data.get("protocol_version", E2A_PROTOCOL_VERSION),
        response_id=data.get("response_id"),
        request_id=data.get("request_id"),
        sequence=sequence,
        is_final=bool(data.get("is_final", False)),
        status=str(data.get("status", E2A_RESPONSE_STATUS_IN_PROGRESS)),
        response_kind=str(data.get("response_kind") or ""),
        timestamp=_normalize_timestamp_value(data.get("timestamp")),
        provenance=prov,
        body=dict(data.get("body") or {}),
        jsonrpc_id=data.get("jsonrpc_id"),
        correlation_id=data.get("correlation_id"),
        task_id=data.get("task_id"),
        context_id=data.get("context_id"),
        session_id=data.get("session_id"),
        message_id=data.get("message_id"),
        is_stream=bool(data.get("is_stream", False)),
        identity_origin=origin,
        channel=ch,
        user_id=data.get("user_id"),
        source_agent_id=data.get("source_agent_id"),
        method=data.get("method"),
        projections=dict(data.get("projections") or {}),
        channel_context=dict(data.get("channel_context") or {}),
        metadata=dict(data.get("metadata") or {}),
        a2a_metadata=dict(data.get("a2a_metadata") or {}),
        acp_meta=dict(data.get("acp_meta") or {}),
    )


def merge_params_to_acp_prompt(envelope: E2AEnvelope) -> dict[str, Any]:
    """
    當 ``method == "session/prompt"`` 時，從 ``envelope.params`` 補全 ACP 所需 ``prompt``，返回新引數字典。

    優先順序：
    1. 已有 ``params.prompt`` 則不修改。
    2. 否則若有 ``params.content_blocks``（非空 list），用作 ``prompt``。
    3. 否則用 ``params.text``、``params.content``、``params.query`` 中第一個非空字串生成單條 text ContentBlock。

    隨後按需補 ``session_id``、``params._meta``（來自 ``envelope.acp_meta``）。
    """
    p = dict(envelope.params)
    if envelope.method != "session/prompt":
        return p
    if "prompt" in p:
        return p
    blocks: list[dict[str, Any]] = []
    cb = p.get("content_blocks")
    if isinstance(cb, list) and cb:
        blocks.extend(cb)
    else:
        text = p.get("text") or p.get("content") or p.get("query")
        if isinstance(text, str) and text:
            blocks.append({"type": "text", "text": text})
    if blocks:
        p["prompt"] = blocks
    if envelope.session_id and "session_id" not in p:
        p["session_id"] = envelope.session_id
    if envelope.acp_meta:
        p.setdefault("_meta", {}).update(envelope.acp_meta)
    return p
