"""历史 agent 回放表的兼容模型（SQLModel）。

该模块保留 ``agent_profile``、``agent_status``、``agent_dialog`` 三张旧表的 ORM
定义，供后端读取历史 SQLite 数据库时使用。

当前 :class:`~agentsociety2.storage.ReplayWriter` 不再初始化或写入这些表。
"""

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class AgentProfile(SQLModel, table=True):
    """agent 档案信息（框架表）。"""

    __tablename__ = "agent_profile"

    id: int = Field(primary_key=True)
    name: str
    profile: Dict[str, Any] = Field(default={}, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.now)


class AgentStatus(SQLModel, table=True):
    """agent 在某一步的状态快照（框架表）。"""

    __tablename__ = "agent_status"

    id: int = Field(primary_key=True)
    step: int = Field(primary_key=True, index=True)
    t: datetime = Field(index=True)
    action: Optional[str] = None
    status: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.now)


class AgentDialog(SQLModel, table=True):
    """agent 对话记录（框架表）。"""

    __tablename__ = "agent_dialog"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: int = Field(index=True)
    step: int = Field(index=True)
    t: datetime
    type: int = Field(index=True)  # 0=反思 (thought/reflection); V2 only uses 0
    speaker: str
    content: str
    created_at: datetime = Field(default_factory=datetime.now)
