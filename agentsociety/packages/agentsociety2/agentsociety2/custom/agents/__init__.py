"""
自定義 Agent 包

在此目錄下建立自定義 Agent 類。
"""

from typing import List, Tuple, Type
from agentsociety2.agent.base import AgentBase

# 動態載入所有自定義 Agent
# 注意：此檔案由系統自動維護，請勿手動編輯

_CUSTOM_AGENTS: List[Tuple[str, Type[AgentBase]]] = []

def register_agent(agent_type: str, agent_class: Type[AgentBase]):
    """註冊自定義 Agent"""
    _CUSTOM_AGENTS.append((agent_type, agent_class))

def get_custom_agents() -> List[Tuple[str, Type[AgentBase]]]:
    """獲取所有自定義 Agent"""
    return _CUSTOM_AGENTS.copy()

__all__ = ["register_agent", "get_custom_agents"]
