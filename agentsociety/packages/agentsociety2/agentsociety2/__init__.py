"""AgentSociety 2 - 現代化的 LLM-native 智慧體模擬平臺。

本包提供構建和模擬 LLM 驅動智慧體的工具，用於社會科學研究。

注意：CI工作流已更新，ruff檢查現為非阻塞以便於發版流程。

主要元件
--------

**Agent 模組**:
- ``AgentBase``: 智慧體抽象基類
- ``PersonAgent``: skills-first 智慧體

**Env 模組**:
- ``EnvBase``: 環境模組基類
- ``RouterBase``: 路由器基類
- ``ReActRouter``: ReAct 正規化路由器
- ``PlanExecuteRouter``: 計劃-執行路由器
- ``CodeGenRouter``: 程式碼生成路由器
- ``TwoTierReActRouter``: 兩層 ReAct 路由器
- ``TwoTierPlanExecuteRouter``: 兩層計劃執行路由器
- ``SearchToolRouter``: 搜尋工具路由器
- ``tool``: 工具裝飾器

**Society 模組**:
- ``AgentSociety``: 主模擬編排器（位於 ``agentsociety2.society``）
- ``AgentSocietyHelper``: 模擬編排助手（頂層 re-export）

**Storage 模組**:
- ``ReplayWriter``: 環境回放資料寫入器

使用示例::

    from agentsociety2 import AgentBase, PersonAgent, EnvBase, tool
    from agentsociety2.society import AgentSociety

    # 定義自定義環境
    class MyEnv(EnvBase):
        @tool(readonly=True)
        def get_status(self) -> str:
            return "ok"

    # 定義自定義智慧體
    class MyAgent(AgentBase):
        async def step(self, tick: int, t) -> str:
            return "done"
        # ... 其他抽象方法
"""

__version__ = "2.1.5"

# Import main components for easy access
from .agent import AgentBase, PersonAgent
from .env import (
    EnvBase,
    RouterBase,
    ReActRouter,
    PlanExecuteRouter,
    CodeGenRouter,
    TwoTierReActRouter,
    TwoTierPlanExecuteRouter,
    SearchToolRouter,
    tool,
)
from .society import AgentSocietyHelper
from .storage import ReplayWriter

__all__ = [
    "AgentBase",
    "PersonAgent",
    "EnvBase",
    "RouterBase",
    "ReActRouter",
    "PlanExecuteRouter",
    "CodeGenRouter",
    "TwoTierReActRouter",
    "TwoTierPlanExecuteRouter",
    "SearchToolRouter",
    "tool",
    "AgentSocietyHelper",
    "ReplayWriter",
]
