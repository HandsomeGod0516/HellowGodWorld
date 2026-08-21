自定義模組
==============

AgentSociety 2 支援建立和註冊自定義智慧體和環境模組，
允許您使用自己的模擬元件擴充套件平臺。

概述
--------

自定義模組系統允許您：

* 建立具有專門行為的自定義智慧體類
* 建立具有特定領域工具的自定義環境模組
* 透過 API 自動發現和註冊模組
* 使用自動生成的測試指令碼測試自定義模組
* 與現有 AgentSociety 框架無縫整合
* 透過 Progressive Disclosure workflow 持久化需求、設計和驗證產物

目錄結構
-------------------

自定義模組放置在工作區內的 ``custom/`` 目錄中::

   workspace/
   ├── custom/                    # User-created directory
   │   ├── agents/                # Custom agent classes
   │   │   └── my_agent.py
   │   └── envs/                  # Custom environment modules
   │       └── my_env.py
   └── .agentsociety/             # Auto-generated configuration
       ├── agent_classes/
       ├── env_modules/
       └── custom_env_skill/
           └── runs/

建立自定義智慧體
-------------------------

所有自定義智慧體必須繼承 ``AgentBase`` 並實現必需的方法：

.. code-block:: python

   from agentsociety2.agent.base import AgentBase
   from datetime import datetime
   from typing import Any

   class MyAgent(AgentBase):
       """My custom Agent"""

       @classmethod
       def mcp_description(cls) -> str:
           return """MyAgent: A custom agent for specific tasks

       This agent demonstrates custom behavior.
       """

       async def ask(self, message: str, readonly: bool = True) -> str:
           """Respond to questions from the environment"""
           prompt = f"Question: {message}\nPlease answer:"
           response = await self.acompletion([{"role": "user", "content": prompt}])
           return response.choices[0].message.content or ""

       async def step(self, tick: int, t: datetime) -> str:
           """Execute one simulation step"""
           return f"Agent {self.id} executing step {tick}"

       async def dump(self) -> dict:
           """Serialize agent state"""
           return {"id": self._id, "profile": self._profile}

       async def load(self, dump_data: dict):
           """Load agent state"""
           self._id = dump_data.get("id", self._id)
           self._profile = dump_data.get("profile", self._profile)

必需方法
~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Method
     - Description
   * - ``mcp_description()``
     - 返回模組描述（類方法，建議覆蓋；``AgentBase``/``EnvBase`` 有預設描述）
   * - ``ask()``
     - 回答環境的問題
   * - ``step()``
     - 執行一個模擬步驟
   * - ``dump()``
     - 序列化智慧體狀態
   * - ``load()``
     - 從字典載入智慧體狀態

建立自定義環境
------------------------------

自定義環境必須繼承 ``EnvBase`` 並使用 ``@tool`` 裝飾器註冊方法：

.. code-block:: python

   from agentsociety2.env import EnvBase, tool
   from datetime import datetime

   class MyEnv(EnvBase):
       """My custom environment"""

       def __init__(self, config=None):
           super().__init__()
           # Initialize your environment state

       @classmethod
       def mcp_description(cls) -> str:
           return """MyEnv: A custom environment

       This environment provides custom tools for agents.
       """

       @tool(readonly=True, kind="observe")
       async def get_state(self, agent_id: int) -> dict:
           """Get current environment state (observation tool)"""
           return {"agent_id": agent_id, "state": "normal"}

       @tool(readonly=False)
       async def do_action(self, agent_id: int, action: str) -> dict:
           """Perform an action (modification tool)"""
           return {"agent_id": agent_id, "action": action, "result": "success"}

       async def step(self, tick: int, t: datetime):
           """Environment step"""
           self.t = t

現實相容約束
~~~~~~~~~~~~~~~~~~~

生成的自定義環境模組仍然必須遵循當前倉庫的真實相容約束：

* 檔案必須位於 ``custom/envs/*.py``
* 類定義必須直接位於該檔案中，不能只做 re-export
* 註冊 key 繼續使用 ``class_name``
* 至少存在一個合法 ``@tool``
* ``step()`` 必須存在
* 預設應支援無參例項化 ``cls()``
* 若模組需要觀察能力，應提供 readonly ``kind="observe"`` 工具
* 建議提供資訊完整的 ``mcp_description()``（未覆蓋時會顯示基類預設描述）

.. note::

   掃描器會跳過路徑中包含 ``examples/`` 的檔案（示例僅供參考，不參與註冊）。

@tool 裝飾器
~~~~~~~~~~~~~~~~~~~

``@tool`` 裝飾器將方法註冊為智慧體可訪問的工具：

.. list-table::
   :header-rows: 1

   * - Parameter
     - Description
   * - ``readonly=True``
     - 工具不修改環境狀態
   * - ``readonly=False``
     - 工具可以修改環境狀態
   * - ``kind="observe"``
     - 觀察工具（單個 agent_id 引數，readonly=True）
   * - ``kind="statistics"``
     - 統計工具（無引數，readonly=True）
   * - ``kind=None``
     - 常規工具（任何引數，可以是 readonly=False）

註冊自定義模組
---------------------------

建立自定義模組後，使用 API 註冊它們：

**掃描並註冊**

.. code-block:: bash

   curl -X POST http://localhost:8001/api/v1/custom/scan \
     -H "Content-Type: application/json" \
     -d '{"workspace_path": "/path/to/workspace"}'

**列出已註冊的模組**

.. code-block:: bash

   curl http://localhost:8001/api/v1/custom/list

**測試自定義模組**

.. code-block:: bash

   curl -X POST http://localhost:8001/api/v1/custom/test \
     -H "Content-Type: application/json" \
     -d '{"workspace_path": "/path/to/workspace"}'

**建立或恢復 workflow run**

.. code-block:: bash

   curl -X POST http://localhost:8001/api/v1/custom/workflow/runs \
     -H "Content-Type: application/json" \
     -d '{"workspace_path": "/path/to/workspace", "user_request": "create a resource env"}'

**驗證 workflow run**

.. code-block:: bash

   curl -X POST http://localhost:8001/api/v1/custom/workflow/runs/<run_id>/validate \
     -H "Content-Type: application/json" \
     -d '{"module_path": "custom/envs/my_env.py", "class_name": "MyEnv"}'

API 端點
~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Endpoint
     - Method
     - Description
   * - ``/api/v1/custom/scan``
     - POST
     - 掃描並註冊自定義模組
   * - ``/api/v1/custom/test``
     - POST
     - 測試自定義模組
   * - ``/api/v1/custom/clean``
     - POST
     - 清理自定義模組配置
   * - ``/api/v1/custom/list``
     - GET
     - 列出已註冊的自定義模組
   * - ``/api/v1/custom/status``
     - GET
     - 獲取模組狀態概述
   * - ``/api/v1/custom/workflow/runs``
     - POST
     - 建立或恢復自定義環境 workflow run
   * - ``/api/v1/custom/workflow/runs/{run_id}/validate``
     - POST
     - 執行 scanner/tester/registry 的端到端校驗

示例
--------

示例智慧體和環境可在 ``custom/`` 目錄中找到：

* ``custom/agents/examples/simple_agent.py`` - 基本智慧體示例
* ``custom/agents/examples/advanced_agent.py`` - 具有記憶和情緒的智慧體
* ``custom/envs/examples/simple_env.py`` - 計數器環境
* ``custom/envs/examples/advanced_env.py`` - 資源管理環境

這些示例演示了建立自定義模組的最佳實踐。

配置
-------------

設定 ``WORKSPACE_PATH`` 環境變數以指向您的工作區：

.. code-block:: bash

   export WORKSPACE_PATH=/path/to/workspace

或新增到您的 ``.env`` 檔案：

.. code-block:: ini

   WORKSPACE_PATH=/path/to/workspace

此設定告訴系統在哪裡找到 ``custom/`` 目錄。

最佳實踐
--------------

**命名約定**

* 智慧體類名應以 ``Agent`` 結尾
* 環境類名應以 ``Env`` 結尾
* 檔名應使用小寫字母和下劃線：``my_agent.py``

**錯誤處理**

* 返回有意義的錯誤訊息
* 對關鍵路徑保留必要日誌，便於覆盤與定位問題

**狀態管理**

* 使用 ``dump()`` 和 ``load()`` 進行狀態持久化
* 在回放中記錄重要的狀態更改
* 保持狀態可序列化（JSON 相容）

**工具設計**

* 對只讀觀察使用 ``kind="observe"``
* 對聚合資料使用 ``kind="statistics"``
* 對操作使用 ``kind=None`` 和 ``readonly=False``
* 生成後優先透過 workflow 產物中的 ``validation_report.json`` 定位失敗原因
