"""
简单 Agent 示例

这是一个基础的 Agent 示例，展示如何创建自定义 Agent。

创建完成后：
1. 将文件复制到 custom/agents/ 目录（不要放在 examples/ 中）
2. 运行 VSCode 命令 "扫描自定义模块"
3. 运行 VSCode 命令 "测试自定义模块" 验证
"""

from agentsociety2.agent.base import AgentBase
from datetime import datetime


class SimpleAgent(AgentBase):
    """
    简单的 LLM 驱动 Agent

    这是一个用于演示的简单 Agent，展示基本的 Agent 功能。
    """

    @classmethod
    def mcp_description(cls) -> str:
        """
        返回 Agent 的描述信息

        这个描述会显示在模块列表中。
        """
        return """SimpleAgent: 简单的 LLM 驱动 Agent 示例

这是一个基础的 Agent 示例，用于演示如何创建自定义 Agent。

**Profile 字段:**
- name (str): Agent 的名称
- personality (str): Agent 的个性特征

**初始化配置示例:**
```json
{
  "id": 0,
  "profile": {
    "name": "张三",
    "personality": "友好开朗"
  }
}
```
"""

    async def ask(self, message: str, readonly: bool = True) -> str:
        """
        回答来自环境的问题

        Args:
            message: 问题内容
            readonly: 是否只读

        Returns:
            答案内容
        """
        # 构建提示词
        prompt = f"""你是一个真实的人。你的个人资料：{self.get_profile()}

问题：{message}

请根据你的个人资料和个性来回答这个问题。"""

        try:
            response = await self.acompletion([{"role": "user", "content": prompt}])
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"抱歉，我无法回答这个问题：{str(e)}"

    async def step(self, tick: int, t: datetime) -> str:
        """
        执行一个仿真步骤

        Args:
            tick: 时间刻度（秒）
            t: 当前仿真时间

        Returns:
            步骤描述
        """
        # 查询环境状态
        try:
            _, observation = await self.ask_env(
                {"variables": {}},
                "当前环境状态是什么？",
                readonly=True
            )
        except Exception as e:
            observation = f"无法获取环境状态：{str(e)}"

        # 记录状态
        action = f"Agent {self.name} 观察到：{observation}，继续活动"
        return action

    async def dump(self) -> dict:
        """
        序列化 Agent 状态

        Returns:
            状态字典
        """
        return {
            "id": self._id,
            "profile": self.get_profile(),
            "name": self._name,
        }

    async def load(self, dump_data: dict):
        """
        从字典加载 Agent 状态

        Args:
            dump_data: 状态字典
        """
        self._id = dump_data.get("id", self._id)
        profile = dump_data.get("profile")
        if profile:
            self._profile = profile
        self._name = dump_data.get("name", self._name)
