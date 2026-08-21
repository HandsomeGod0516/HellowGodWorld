"""
高级 Agent 示例

展示带有记忆、情绪等高级功能的 Agent。
"""

from agentsociety2.agent.base import AgentBase
from datetime import datetime
from typing import Any, List


class AdvancedAgent(AgentBase):
    """
    高级 Agent 示例

    展示如何添加记忆、情绪等高级功能。
    """

    def __init__(self, id: int, profile: Any, name: str = None):
        super().__init__(id, profile, name)
        # 添加自定义属性
        self._memories: List[str] = []  # 记忆列表
        self._mood: str = "平静"  # 当前情绪

    @classmethod
    def mcp_description(cls) -> str:
        return """AdvancedAgent: 带有记忆和情绪的高级 Agent 示例

展示如何实现带记忆、情绪等高级功能的 Agent。

**Profile 字段:**
- name (str): Agent 名称
- personality (str): 个性特征
- occupation (str): 职业

**自定义属性:**
- memories: 记忆列表
- mood: 当前情绪（平静、开心、悲伤等）

**初始化配置示例:**
```json
{
  "id": 0,
  "profile": {
    "name": "李华",
    "personality": "理性、深思熟虑",
    "occupation": "研究员"
  }
}
```
"""

    async def ask(self, message: str, readonly: bool = True) -> str:
        """回答问题，结合记忆和情绪"""
        # 构建包含记忆和情绪的提示词
        memory_text = "\n".join(self._memories[-5:]) if self._memories else "暂无记忆"

        prompt = f"""你是一个真实的人。

**你的资料:**
{self.get_profile()}

**当前情绪:** {self._mood}

**最近的记忆:**
{memory_text}

问题：{message}

请根据你的资料、记忆和当前情绪来回答这个问题。"""

        try:
            response = await self.acompletion([{"role": "user", "content": prompt}])

            answer = response.choices[0].message.content or ""

            # 记录这次交互
            memory = f"Q: {message}\nA: {answer[:100]}..."
            self._memories.append(memory)

            return answer
        except Exception as e:
            return f"抱歉，我无法回答这个问题：{str(e)}"

    async def step(self, tick: int, t: datetime) -> str:
        """执行仿真步骤，更新情绪"""
        try:
            _, observation = await self.ask_env(
                {"variables": {}},
                "当前环境状态是什么？",
                readonly=True
            )
        except Exception:
            observation = "环境正常"

        # 根据观察更新情绪（简单逻辑）
        if "好" in observation or "顺利" in observation:
            self._mood = "开心"
        elif "坏" in observation or "困难" in observation:
            self._mood = "沮丧"
        else:
            self._mood = "平静"

        action = f"Agent {self.name}（情绪：{self._mood}）观察到：{observation}"
        return action

    async def dump(self) -> dict:
        """序列化状态，包含记忆和情绪"""
        return {
            "id": self._id,
            "profile": self.get_profile(),
            "name": self._name,
            "memories": self._memories,
            "mood": self._mood,
        }

    async def load(self, dump_data: dict):
        """加载状态"""
        self._id = dump_data.get("id", self._id)
        profile = dump_data.get("profile")
        if profile:
            self._profile = profile
        self._name = dump_data.get("name", self._name)
        self._memories = dump_data.get("memories", [])
        self._mood = dump_data.get("mood", "平静")
