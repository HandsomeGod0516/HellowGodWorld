"""環境模組 - 提供 Agent 與模擬環境互動的基礎設施。

本模組包含兩個核心概念：

**EnvBase** — 環境模組基類：
- 定義 Agent 可執行的操作（透過 ``@tool`` 裝飾器）
- 管理環境狀態
- 提供 ``observe()`` 方法供 Agent 感知環境

**RouterBase** — 路由器基類：
- 將 Agent 的自然語言指令轉換為工具呼叫
- 支援多種路由策略（ReAct、PlanExecute、CodeGen 等）

路由器實現：
- ``ReActRouter``: ReAct 正規化（推理-行動迴圈）
- ``PlanExecuteRouter``: 計劃-執行正規化
- ``CodeGenRouter``: 程式碼生成正規化
- ``TwoTierReActRouter``: 兩層 ReAct 路由
- ``TwoTierPlanExecuteRouter``: 兩層計劃執行路由
- ``SearchToolRouter``: 搜尋工具路由

工具裝飾器 ``@tool``：
- ``readonly=True``: 只讀工具，不修改環境狀態
- ``readonly=False``: 可修改環境狀態的工具
- ``kind="observe"``: 觀察類工具（自動呼叫）
- ``kind="statistics"``: 統計類工具

使用示例::

    from agentsociety2.env import EnvBase, tool

    class MyEnv(EnvBase):
        @tool(readonly=True, kind="observe")
        def get_location(self, agent_id: int) -> str:
            return self._locations.get(agent_id, "unknown")

        @tool(readonly=False)
        def move(self, agent_id: int, location: str) -> str:
            self._locations[agent_id] = location
            return f"Moved to {location}"
"""

from .base import (
    EnvBase,
    PersonStepConstraints,
    merge_person_step_constraints,
    tool,
)
from .router_base import RouterBase
from .router_codegen import CodeGenRouter
from .router_react import ReActRouter
from .router_plan_execute import PlanExecuteRouter
from .router_two_tier_react import TwoTierReActRouter
from .router_two_tier_plan_execute import TwoTierPlanExecuteRouter
from .router_search_tool import SearchToolRouter
from .benchmark import EnvRouterBenchmarkData

__all__ = [
    "EnvBase",
    "PersonStepConstraints",
    "merge_person_step_constraints",
    "RouterBase",
    "CodeGenRouter",
    "ReActRouter",
    "PlanExecuteRouter",
    "TwoTierReActRouter",
    "TwoTierPlanExecuteRouter",
    "SearchToolRouter",
    "tool",
    "EnvRouterBenchmarkData",
]
