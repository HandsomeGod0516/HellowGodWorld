from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MemoryHookContext:
    session_id: str
    request_id: str
    channel_id: str | None
    agent_name: str
    workspace_dir: str
    assistant_message: str | None = None
    # 輸入擴充套件
    extra: dict[str, Any] = field(default_factory=dict)
    # 記憶內容（before_chat 擴充套件寫入，宿主從本欄位讀取拼接結果）
    memory_blocks: list[str] = field(default_factory=list)
    # 輸出擴充套件
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GatewayChatHookContext:
    request_id: str
    channel_id: str
    session_id: str | None
    req_method: str | None
    # 擴充套件可直接原地修改 params，Gateway 會將其繼續傳給 AgentRequest.params
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentServerChatHookContext:
    request_id: str
    channel_id: str
    session_id: str | None
    req_method: str | None
    # 擴充套件可直接原地修改 params，AgentServer 後續邏輯會繼續使用 request.params
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SystemPromptHookContext:
    # 擴充套件可設定此目錄，用於覆蓋預設的 home_dir
    home_dir: str | None = None
    # 擴充套件可設定此目錄，用於擴充套件預設的 skill_dir
    skill_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
