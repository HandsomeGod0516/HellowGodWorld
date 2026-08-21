"""
簡單環境模組示例

展示如何建立一個基礎的環境模組。
"""

from agentsociety2.env import EnvBase, tool
from datetime import datetime
from pydantic import BaseModel, Field


class SimpleEnvConfig(BaseModel):
    """簡單環境配置"""
    initial_value: int = Field(default=0, description="初始計數值")
    max_value: int = Field(default=100, description="最大值")


class SimpleEnv(EnvBase):
    """
    簡單的計數器環境

    這是一個基礎的環境模組示例，提供簡單的計數器功能。
    """

    def __init__(self, config: SimpleEnvConfig | dict = None):
        super().__init__()
        if config is None:
            config = SimpleEnvConfig()
        elif isinstance(config, dict):
            config = SimpleEnvConfig(**config)
        self._config = config
        self._counter = config.initial_value

    @classmethod
    def mcp_description(cls) -> str:
        """
        返回環境模組的描述資訊
        """
        return """SimpleEnv: 簡單的計數器環境示例

這是一個基礎的環境模組，提供簡單的計數器功能。

**配置引數:**
- initial_value (int): 初始計數值，預設 0
- max_value (int): 最大值，預設 100

**可用工具:**
- get_counter(agent_id): 獲取當前計數值（觀察）
- increment(agent_id, amount): 增加計數（操作）
- decrement(agent_id, amount): 減少計數（操作）
- reset_counter(agent_id): 重置計數（操作）

**初始化配置示例:**
```json
{
  "initial_value": 0,
  "max_value": 100
}
```
"""

    @tool(readonly=True, kind="observe")
    async def get_counter(self, agent_id: int) -> dict:
        """
        獲取當前計數值

        Args:
            agent_id: Agent ID

        Returns:
            包含當前計數值的字典
        """
        return {
            "counter": self._counter,
            "max_value": self._config.max_value,
            "agent_id": agent_id
        }

    @tool(readonly=False)
    async def increment(self, agent_id: int, amount: int = 1) -> dict:
        """
        增加計數值

        Args:
            agent_id: Agent ID
            amount: 增加的數量，預設 1

        Returns:
            操作結果
        """
        old_value = self._counter
        self._counter = min(self._counter + amount, self._config.max_value)

        return {
            "agent_id": agent_id,
            "old_value": old_value,
            "new_value": self._counter,
            "amount_added": self._counter - old_value,
            "success": True
        }

    @tool(readonly=False)
    async def decrement(self, agent_id: int, amount: int = 1) -> dict:
        """
        減少計數值

        Args:
            agent_id: Agent ID
            amount: 減少的數量，預設 1

        Returns:
            操作結果
        """
        old_value = self._counter
        self._counter = max(self._counter - amount, 0)

        return {
            "agent_id": agent_id,
            "old_value": old_value,
            "new_value": self._counter,
            "amount_subtracted": old_value - self._counter,
            "success": True
        }

    @tool(readonly=False)
    async def reset_counter(self, agent_id: int) -> dict:
        """
        重置計數值為初始值

        Args:
            agent_id: Agent ID

        Returns:
            操作結果
        """
        old_value = self._counter
        self._counter = self._config.initial_value

        return {
            "agent_id": agent_id,
            "old_value": old_value,
            "new_value": self._counter,
            "success": True
        }

    async def step(self, tick: int, t: datetime):
        """
        環境步驟

        Args:
            tick: 時間刻度
            t: 當前時間
        """
        self.t = t
        # 可以在這裡新增定期事件
        # 例如：每段時間自動增加一些計數
