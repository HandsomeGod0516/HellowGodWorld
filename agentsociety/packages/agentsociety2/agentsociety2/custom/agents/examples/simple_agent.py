"""
簡單 Agent 示例

這是一個基礎的 Agent 示例，展示如何建立自定義 Agent。

建立完成後：
1. 將檔案複製到 custom/agents/ 目錄（不要放在 examples/ 中）
2. 執行 VSCode 命令 "掃描自定義模組"
3. 執行 VSCode 命令 "測試自定義模組" 驗證
"""

from agentsociety2.agent.base import AgentBase
from datetime import datetime


class SimpleAgent(AgentBase):
    """
    簡單的 LLM 驅動 Agent

    這是一個用於演示的簡單 Agent，展示基本的 Agent 功能。
    """

    @classmethod
    def mcp_description(cls) -> str:
        """
        返回 Agent 的描述資訊

        這個描述會顯示在模組列表中。
        """
        return """SimpleAgent: 簡單的 LLM 驅動 Agent 示例

這是一個基礎的 Agent 示例，用於演示如何建立自定義 Agent。

**Profile 欄位:**
- name (str): Agent 的名稱
- personality (str): Agent 的個性特徵

**初始化配置示例:**
```json
{
  "id": 0,
  "profile": {
    "name": "張三",
    "personality": "友好開朗"
  }
}
```
"""

    async def ask(self, message: str, readonly: bool = True) -> str:
        """
        回答來自環境的問題

        Args:
            message: 問題內容
            readonly: 是否只讀

        Returns:
            答案內容
        """
        # 構建提示詞
        prompt = f"""你是一個真實的人。你的個人資料：{self.get_profile()}

問題：{message}

請根據你的個人資料和個性來回答這個問題。"""

        try:
            response = await self.acompletion([{"role": "user", "content": prompt}])
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"抱歉，我無法回答這個問題：{str(e)}"

    async def step(self, tick: int, t: datetime) -> str:
        """
        執行一個模擬步驟

        Args:
            tick: 時間刻度（秒）
            t: 當前模擬時間

        Returns:
            步驟描述
        """
        # 查詢環境狀態
        try:
            _, observation = await self.ask_env(
                {"variables": {}},
                "當前環境狀態是什麼？",
                readonly=True
            )
        except Exception as e:
            observation = f"無法獲取環境狀態：{str(e)}"

        # 記錄狀態
        action = f"Agent {self.name} 觀察到：{observation}，繼續活動"
        return action

    async def dump(self) -> dict:
        """
        序列化 Agent 狀態

        Returns:
            狀態字典
        """
        return {
            "id": self._id,
            "profile": self.get_profile(),
            "name": self._name,
        }

    async def load(self, dump_data: dict):
        """
        從字典載入 Agent 狀態

        Args:
            dump_data: 狀態字典
        """
        self._id = dump_data.get("id", self._id)
        profile = dump_data.get("profile")
        if profile:
            self._profile = profile
        self._name = dump_data.get("name", self._name)
