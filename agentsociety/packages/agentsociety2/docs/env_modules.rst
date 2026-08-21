環境模組
===================

本部分介紹如何建立和使用環境模組。

建立自定義模組
------------------------

繼承 EnvBase
~~~~~~~~~~~~~~~~~~~~

要建立自定義環境模組，請繼承 ``EnvBase`` 並實現必需的方法：

需要實現的方法
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

建立自定義環境模組時，必須實現：

1. **async def step(self, tick: int, t: datetime) -> None**

   執行一個模擬步驟。在 AgentSociety 模擬期間自動呼叫。
   使用此方法更新時間相關的狀態。

   引數:
       tick: 此步驟的持續時間（秒）
       t: 此步驟後的當前模擬日期時間

2. **使用 @tool 裝飾的工具**

   使用 ``@tool`` 裝飾器將方法公開為智慧體的可呼叫函式。

   .. code-block:: python

      from agentsociety2.env import EnvBase, tool

      class MyModule(EnvBase):
          def __init__(self):
              super().__init__()
              # Your initialization

          @tool(readonly=True, kind="observe")
          def get_value(self, agent_id: int) -> str:
              """Get a value for an agent."""
              return f"Value for agent {agent_id}"

          @tool(readonly=False)
          def set_value(self, agent_id: int, value: int) -> str:
              """Set a value for an agent."""
              self._values[agent_id] = value
              return f"Set value for agent {agent_id}"

參考實現
^^^^^^^^^^^^^^^^^^^^^^^^

有關完整的參考實現，請參閱：

* ``SimpleSocialSpace`` - 社互動動模組
* ``PublicGoodsEnv`` - 公共物品博弈模組
* ``PrisonersDilemmaEnv`` - 囚徒困境模組

@tool 裝飾器
~~~~~~~~~~~~~~~~~~~

``@tool`` 裝飾器將方法標記為可被智慧體呼叫：

.. code-block:: python

   from agentsociety2.env import tool

   @tool(readonly=True, kind="observe")
   def get_status(self, agent_id: int) -> str:
       """Get the status of an agent."""
       return f"Agent {agent_id} status"

引數：

* **readonly** (bool): 工具是否修改環境
  * ``True`` = 只讀，可用於查詢
  * ``False`` = 修改狀態，可用於干預

* **kind** (str): 工具的型別
  * ``"observe"``: 單引數觀察（需要 readonly=True）
  * ``"statistics"``: 聚合查詢（無引數，需要 readonly=True）
  * ``None`` 或省略: 常規工具（任何簽名，任何 readonly 值）

工具型別
----------

* **observe**: 單引數觀察（需要 readonly=True）
* **statistics**: 聚合查詢（無引數，需要 readonly=True）
* **regular**: 任何其他工具（可以是隻讀或讀寫）

有關工具型別的更多詳情，請參閱 :doc:`concepts`。

註冊模組
--------------------

.. code-block:: python

   from agentsociety2.env import CodeGenRouter

   router = CodeGenRouter()
   router.register_module(MyModule(), name="my_module")

   # Or with default name (class name)
   router.register_module(MyModule())

完整示例
-----------------

.. code-block:: python

   from typing import Dict
   from datetime import datetime
   from agentsociety2.env import EnvBase, tool, CodeGenRouter
   from agentsociety2 import PersonAgent
   from agentsociety2.society import AgentSociety

   class WeatherEnvironment(EnvBase):
       """A simple weather environment module."""

       def __init__(self):
           super().__init__()
           self._weather = "sunny"
           self._temperature = 25
           self._agent_locations: Dict[int, str] = {}

       @tool(readonly=True, kind="observe")
       def get_weather(self, agent_id: int) -> str:
           """Get the current weather for an agent's location."""
           location = self._agent_locations.get(agent_id, "unknown")
           return f"The weather in {location} is {self._weather} with {self._temperature}°C."

       @tool(readonly=False)
       def change_weather(self, weather: str, temperature: int) -> str:
           """Change the weather conditions."""
           self._weather = weather
           self._temperature = temperature
           return f"Weather changed to {weather} at {temperature}°C."

       async def step(self, tick: int, t: datetime) -> None:
           """Update environment state for one simulation step."""
           self.t = t
           # Update time-dependent state here if needed

   # Use the custom module
   env_router = CodeGenRouter(env_modules=[WeatherEnvironment()])

   agent = PersonAgent(id=1, profile={"name": "Bob"})

   society = AgentSociety(
       agents=[agent],
       env_router=env_router,
       start_t=datetime.now(),
   )
   await society.init()

示例
--------

請參閱 ``agentsociety2.contrib.env`` 中的內建環境類（類名以當前程式碼為準）：

* ``SimpleSocialSpace``（社互動動）
* ``PublicGoodsEnv``（公共物品博弈）
* ``PrisonersDilemmaEnv``（囚徒困境）
* ``TrustGameEnv``（信任博弈）
