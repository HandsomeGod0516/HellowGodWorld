"""
高階環境模組示例

展示帶有資源管理、多 Agent 互動等高階功能的環境模組。
"""

from agentsociety2.env import EnvBase, tool
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Dict


class AdvancedEnvConfig(BaseModel):
    """高階環境配置"""
    total_resources: int = Field(default=1000, description="總資源量")
    regeneration_rate: float = Field(default=0.1, description="資源再生率")


class AdvancedEnv(EnvBase):
    """
    高階資源管理環境

    展示如何實現帶有資源管理、多 Agent 互動的環境。
    """

    def __init__(self, config: AdvancedEnvConfig | dict = None):
        super().__init__()
        if config is None:
            config = AdvancedEnvConfig()
        elif isinstance(config, dict):
            config = AdvancedEnvConfig(**config)
        self._config = config
        self._resources = config.total_resources
        self._agent_contributions: Dict[int, float] = {}
        self._agent_consumptions: Dict[int, float] = {}

    @classmethod
    def mcp_description(cls) -> str:
        return """AdvancedEnv: 高階資源管理環境示例

展示帶有資源管理、多 Agent 互動的環境模組。

**配置引數:**
- total_resources (int): 總資源量，預設 1000
- regeneration_rate (float): 資源再生率（每步），預設 0.1

**可用工具:**
- get_resources(agent_id): 獲取當前資源量（觀察）
- consume_resources(agent_id, amount): 消耗資源（操作）
- contribute_resources(agent_id, amount): 貢獻資源（操作）
- get_agent_stats(agent_id): 獲取 Agent 統計（觀察）
- get_leaderboard(): 獲取貢獻排行榜（觀察）

**初始化配置示例:**
```json
{
  "total_resources": 1000,
  "regeneration_rate": 0.1
}
```
"""

    @tool(readonly=True, kind="observe")
    async def get_resources(self, agent_id: int) -> dict:
        """獲取當前資源量"""
        return {
            "current_resources": self._resources,
            "total_resources": self._config.total_resources,
            "utilization": f"{self._resources / self._config.total_resources * 100:.1f}%",
            "agent_id": agent_id
        }

    @tool(readonly=True, kind="observe")
    async def get_agent_stats(self, agent_id: int) -> dict:
        """獲取 Agent 統計資訊"""
        contribution = self._agent_contributions.get(agent_id, 0)
        consumption = self._agent_consumptions.get(agent_id, 0)
        net_contribution = contribution - consumption

        return {
            "agent_id": agent_id,
            "total_contributed": contribution,
            "total_consumed": consumption,
            "net_contribution": net_contribution,
            "status": "貢獻者" if net_contribution >= 0 else "消費者"
        }

    @tool(readonly=True, kind="statistics")
    async def get_leaderboard(self) -> dict:
        """獲取貢獻排行榜"""
        # 計算淨貢獻
        net_contributions = {}
        for agent_id in set(list(self._agent_contributions.keys()) +
                           list(self._agent_consumptions.keys())):
            contribution = self._agent_contributions.get(agent_id, 0)
            consumption = self._agent_consumptions.get(agent_id, 0)
            net_contributions[agent_id] = contribution - consumption

        # 排序
        sorted_agents = sorted(
            net_contributions.items(),
            key=lambda x: x[1],
            reverse=True
        )

        return {
            "leaderboard": [
                {"agent_id": aid, "net_contribution": contrib}
                for aid, contrib in sorted_agents
            ],
            "total_agents": len(sorted_agents)
        }

    @tool(readonly=False)
    async def consume_resources(self, agent_id: int, amount: float = 10) -> dict:
        """消耗資源"""
        if amount > self._resources:
            return {
                "agent_id": agent_id,
                "success": False,
                "message": f"資源不足，當前: {self._resources:.1f}, 請求: {amount}"
            }

        self._resources -= amount

        # 記錄消耗
        if agent_id not in self._agent_consumptions:
            self._agent_consumptions[agent_id] = 0
        self._agent_consumptions[agent_id] += amount

        return {
            "agent_id": agent_id,
            "success": True,
            "amount_consumed": amount,
            "remaining_resources": self._resources
        }

    @tool(readonly=False)
    async def contribute_resources(self, agent_id: int, amount: float = 10) -> dict:
        """貢獻資源"""
        self._resources += amount

        # 記錄貢獻
        if agent_id not in self._agent_contributions:
            self._agent_contributions[agent_id] = 0
        self._agent_contributions[agent_id] += amount

        return {
            "agent_id": agent_id,
            "success": True,
            "amount_contributed": amount,
            "total_resources": self._resources
        }

    async def step(self, tick: int, t: datetime):
        """環境步驟 - 資源再生"""
        self.t = t

        # 資源自然再生
        regeneration = self._config.total_resources * self._config.regeneration_rate
        self._resources = min(
            self._resources + regeneration,
            self._config.total_resources
        )
