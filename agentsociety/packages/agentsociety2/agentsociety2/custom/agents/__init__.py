"""
自定义 Agent 包

在此目录下创建自定义 Agent 类。
"""

from typing import List, Tuple, Type
from agentsociety2.agent.base import AgentBase

# 动态加载所有自定义 Agent
# 注意：此文件由系统自动维护，请勿手动编辑

_CUSTOM_AGENTS: List[Tuple[str, Type[AgentBase]]] = []

def register_agent(agent_type: str, agent_class: Type[AgentBase]):
    """注册自定义 Agent"""
    _CUSTOM_AGENTS.append((agent_type, agent_class))

def get_custom_agents() -> List[Tuple[str, Type[AgentBase]]]:
    """获取所有自定义 Agent"""
    return _CUSTOM_AGENTS.copy()

__all__ = ["register_agent", "get_custom_agents"]
