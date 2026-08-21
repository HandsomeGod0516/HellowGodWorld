# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""E2A 出處常量與 ACP 方法名 / SessionUpdate 判別式（與 `ACP-reference.md` 一致）。"""

# E2A provenance.source_protocol 約定（可擴充套件）
E2A_SOURCE_PROTOCOL_E2A = "e2a"
E2A_SOURCE_PROTOCOL_ACP = "acp"
E2A_SOURCE_PROTOCOL_A2A = "a2a"

# E2AResponse.status（與 docs/zh/E2A-protocol.md §12 一致）
E2A_RESPONSE_STATUS_SUCCEEDED = "succeeded"
E2A_RESPONSE_STATUS_FAILED = "failed"
E2A_RESPONSE_STATUS_IN_PROGRESS = "in_progress"

# E2AResponse.response_kind：具名常量（程式碼中請用下列符號，避免與元組漂移）
E2A_RESPONSE_KIND_E2A_COMPLETE = "e2a.complete"
E2A_RESPONSE_KIND_E2A_CHUNK = "e2a.chunk"
E2A_RESPONSE_KIND_E2A_ERROR = "e2a.error"
E2A_RESPONSE_KIND_ACP_SESSION_UPDATE = "acp.session_update"
E2A_RESPONSE_KIND_ACP_PROMPT_RESULT = "acp.prompt_result"
E2A_RESPONSE_KIND_ACP_JSONRPC_ERROR = "acp.jsonrpc_error"
E2A_RESPONSE_KIND_ACP_OUTPUT_REQUEST = "acp.output_request"
E2A_RESPONSE_KIND_A2A_TASK = "a2a.task"
E2A_RESPONSE_KIND_A2A_MESSAGE = "a2a.message"
E2A_RESPONSE_KIND_A2A_STREAM_EVENT = "a2a.stream_event"
E2A_RESPONSE_KIND_CRON = "cron"
E2A_RESPONSE_KIND_EXT = "ext"

# 執行時以本元組為準（與 docs §12 一致）
E2A_RESPONSE_KINDS: tuple[str, ...] = (
    E2A_RESPONSE_KIND_E2A_COMPLETE,
    E2A_RESPONSE_KIND_E2A_CHUNK,
    E2A_RESPONSE_KIND_E2A_ERROR,
    E2A_RESPONSE_KIND_ACP_SESSION_UPDATE,
    E2A_RESPONSE_KIND_ACP_PROMPT_RESULT,
    E2A_RESPONSE_KIND_ACP_JSONRPC_ERROR,
    E2A_RESPONSE_KIND_ACP_OUTPUT_REQUEST,
    E2A_RESPONSE_KIND_A2A_TASK,
    E2A_RESPONSE_KIND_A2A_MESSAGE,
    E2A_RESPONSE_KIND_A2A_STREAM_EVENT,
    E2A_RESPONSE_KIND_CRON,
    E2A_RESPONSE_KIND_EXT,
)

# a2a.stream_event.body.branch → A2A StreamResponse oneof（JSON 鍵用 snake_case）
E2A_A2A_STREAM_BRANCHES: tuple[str, ...] = (
    "task",
    "message",
    "status_update",
    "artifact_update",
)

# 客戶端 → Agent（JSON-RPC method 名）
ACP_CLIENT_TO_AGENT_METHODS: tuple[str, ...] = (
    "initialize",
    "authenticate",
    "session/new",
    "session/load",
    "session/list",
    "session/set_mode",
    "session/set_config_option",
    "session/prompt",
    "session/set_model",
    "session/fork",
    "session/resume",
    "session/close",
    "logout",
)

# Agent → 客戶端（供下行事件或橋接時使用）
ACP_AGENT_TO_CLIENT_METHODS: tuple[str, ...] = (
    "session/update",
    "session/request_permission",
    "fs/read_text_file",
    "fs/write_text_file",
    "terminal/create",
    "terminal/output",
    "terminal/release",
    "terminal/wait_for_exit",
    "terminal/kill",
    "session/elicitation",
)

ACP_NOTIFICATION_NAMES: tuple[str, ...] = (
    "session/cancel",
    "session/update",
    "session/elicitation/complete",
)

# session/update 內 SessionUpdate.sessionUpdate 取值
# AgentServer → Gateway WebSocket：編碼失敗時整包舊 JSON 寫入 E2AResponse.metadata 的鍵（勿與業務鍵衝突）
E2A_WIRE_LEGACY_AGENT_RESPONSE_KEY = "_e2a_wire_legacy_agent_response"
E2A_WIRE_LEGACY_AGENT_CHUNK_KEY = "_e2a_wire_legacy_agent_chunk"
# AgentServer send_push：與 RPC 響應共用 WebSocket，須標出以免搶佔 unary/stream 等待佇列
E2A_WIRE_SERVER_PUSH_KEY = "_jiuwenclaw_server_push"

# 僅用於編解碼 / 佇列語義，不得隨業務 channel metadata 下發給 Message.metadata
E2A_WIRE_INTERNAL_METADATA_KEYS: frozenset[str] = frozenset(
    {
        E2A_WIRE_SERVER_PUSH_KEY,
        E2A_WIRE_LEGACY_AGENT_CHUNK_KEY,
        E2A_WIRE_LEGACY_AGENT_RESPONSE_KEY,
    }
)

ACP_SESSION_UPDATE_KINDS: tuple[str, ...] = (
    "user_message_chunk",
    "agent_message_chunk",
    "agent_thought_chunk",
    "tool_call",
    "tool_call_update",
    "todo_update",
    "plan",
    "available_commands_update",
    "current_mode_update",
    "config_option_update",
    "session_info_update",
    "usage_update",
)
