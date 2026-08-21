from abc import abstractmethod

from jiuwenclaw.gateway.routing.agent_client import AgentServerClient
from jiuwenclaw.extensions.sdk.base import BaseExtension


class AgentServerClientExtension(BaseExtension):
    """擴充套件入口：持有真正的 `AgentServerClient` 實現，透過 `get_client()` 暴露。"""

    @abstractmethod
    def get_client(self) -> AgentServerClient:
        """返回與 AgentServer 通訊使用的客戶端例項。"""
        ...

    async def shutdown(self) -> None:
        """擴充套件關閉"""
        pass
