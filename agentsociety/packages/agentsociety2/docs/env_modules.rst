环境模块
===================

本部分介绍如何创建和使用环境模块。

创建自定义模块
------------------------

继承 EnvBase
~~~~~~~~~~~~~~~~~~~~

要创建自定义环境模块，请继承 ``EnvBase`` 并实现必需的方法：

需要实现的方法
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

创建自定义环境模块时，必须实现：

1. **async def step(self, tick: int, t: datetime) -> None**

   执行一个模拟步骤。在 AgentSociety 模拟期间自动调用。
   使用此方法更新时间相关的状态。

   参数:
       tick: 此步骤的持续时间（秒）
       t: 此步骤后的当前模拟日期时间

2. **使用 @tool 装饰的工具**

   使用 ``@tool`` 装饰器将方法公开为智能体的可调用函数。

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

参考实现
^^^^^^^^^^^^^^^^^^^^^^^^

有关完整的参考实现，请参阅：

* ``SimpleSocialSpace`` - 社交互动模块
* ``PublicGoodsEnv`` - 公共物品博弈模块
* ``PrisonersDilemmaEnv`` - 囚徒困境模块

@tool 装饰器
~~~~~~~~~~~~~~~~~~~

``@tool`` 装饰器将方法标记为可被智能体调用：

.. code-block:: python

   from agentsociety2.env import tool

   @tool(readonly=True, kind="observe")
   def get_status(self, agent_id: int) -> str:
       """Get the status of an agent."""
       return f"Agent {agent_id} status"

参数：

* **readonly** (bool): 工具是否修改环境
  * ``True`` = 只读，可用于查询
  * ``False`` = 修改状态，可用于干预

* **kind** (str): 工具的类型
  * ``"observe"``: 单参数观察（需要 readonly=True）
  * ``"statistics"``: 聚合查询（无参数，需要 readonly=True）
  * ``None`` 或省略: 常规工具（任何签名，任何 readonly 值）

工具类型
----------

* **observe**: 单参数观察（需要 readonly=True）
* **statistics**: 聚合查询（无参数，需要 readonly=True）
* **regular**: 任何其他工具（可以是只读或读写）

有关工具类型的更多详情，请参阅 :doc:`concepts`。

注册模块
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

请参阅 ``agentsociety2.contrib.env`` 中的内置环境类（类名以当前代码为准）：

* ``SimpleSocialSpace``（社交互动）
* ``PublicGoodsEnv``（公共物品博弈）
* ``PrisonersDilemmaEnv``（囚徒困境）
* ``TrustGameEnv``（信任博弈）
