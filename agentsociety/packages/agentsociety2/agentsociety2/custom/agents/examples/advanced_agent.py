"""
高階 Agent 示例

展示帶有記憶、情緒等高階功能的 Agent。
"""

from agentsociety2.agent.base import AgentBase
from datetime import datetime
from typing import Any, List


class AdvancedAgent(AgentBase):
    """
    高階 Agent 示例

    展示如何新增記憶、情緒等高階功能。
    """

    def __init__(self, id: int, profile: Any, name: str = None):
        super().__init__(id, profile, name)
        # 新增自定義屬性
        self._memories: List[str] = []  # 記憶列表
        self._mood: str = "平靜"  # 當前情緒

    @classmethod
    def mcp_description(cls) -> str:
        return """AdvancedAgent: 帶有記憶和情緒的高階 Agent 示例

展示如何實現帶記憶、情緒等高階功能的 Agent。

**Profile 欄位:**
- name (str): Agent 名稱
- personality (str): 個性特徵
- occupation (str): 職業

**自定義屬性:**
- memories: 記憶列表
- mood: 當前情緒（平靜、開心、悲傷等）

**初始化配置示例:**
```json
{
  "id": 0,
  "profile": {
    "name": "李華",
    "personality": "理性、深思熟慮",
    "occupation": "研究員"
  }
}
```
"""

    async def ask(self, message: str, readonly: bool = True) -> str:
        """回答問題，結合記憶和情緒"""
        # 構建包含記憶和情緒的提示詞
        memory_text = "\n".join(self._memories[-5:]) if self._memories else "暫無記憶"

        prompt = f"""你是一個真實的人。

**你的資料:**
{self.get_profile()}

**當前情緒:** {self._mood}

**最近的記憶:**
{memory_text}

問題：{message}

請根據你的資料、記憶和當前情緒來回答這個問題。"""

        try:
            response = await self.acompletion([{"role": "user", "content": prompt}])

            answer = response.choices[0].message.content or ""

            # 記錄這次互動
            memory = f"Q: {message}\nA: {answer[:100]}..."
            self._memories.append(memory)

            return answer
        except Exception as e:
            return f"抱歉，我無法回答這個問題：{str(e)}"

    async def step(self, tick: int, t: datetime) -> str:
        """執行模擬步驟，更新情緒"""
        try:
            _, observation = await self.ask_env(
                {"variables": {}},
                "當前環境狀態是什麼？",
                readonly=True
            )
        except Exception:
            observation = "環境正常"

        # 根據觀察更新情緒（簡單邏輯）
        if "好" in observation or "順利" in observation:
            self._mood = "開心"
        elif "壞" in observation or "困難" in observation:
            self._mood = "沮喪"
        else:
            self._mood = "平靜"

        action = f"Agent {self.name}（情緒：{self._mood}）觀察到：{observation}"
        return action

    async def dump(self) -> dict:
        """序列化狀態，包含記憶和情緒"""
        return {
            "id": self._id,
            "profile": self.get_profile(),
            "name": self._name,
            "memories": self._memories,
            "mood": self._mood,
        }

    async def load(self, dump_data: dict):
        """載入狀態"""
        self._id = dump_data.get("id", self._id)
        profile = dump_data.get("profile")
        if profile:
            self._profile = profile
        self._name = dump_data.get("name", self._name)
        self._memories = dump_data.get("memories", [])
        self._mood = dump_data.get("mood", "平靜")
