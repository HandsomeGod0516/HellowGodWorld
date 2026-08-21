快速入门
===========

本指南将帮助您快速上手 AgentSociety 2。

前置条件
----------------

在运行示例前，请先配置 LLM 环境变量（见 :doc:`installation`），或在项目目录中准备好 ``.env`` 并在入口处尽早加载。

您的第一个智能体
----------------

让我们使用 **AgentSociety** 创建一个简单的智能体并与它交互：

.. code-block:: python

   import asyncio
   from datetime import datetime
   from agentsociety2 import PersonAgent
   from agentsociety2.env import CodeGenRouter
   from agentsociety2.contrib.env import SimpleSocialSpace
   from agentsociety2.society import AgentSociety

   async def main():
       # Create agent with profile
       agent = PersonAgent(
           id=1,
           profile={
               "name": "Alice",
               "age": 28,
               "personality": "friendly and curious",
               "bio": "A software engineer who loves hiking."
           }
       )

       # Create environment module with agent info
       social_env = SimpleSocialSpace(
           agent_id_name_pairs=[(agent.id, agent.name)]
       )

       # Create environment router
       env_router = CodeGenRouter(env_modules=[social_env])

       # Create society
       society = AgentSociety(
           agents=[agent],
           env_router=env_router,
           start_t=datetime.now(),
       )

       # Initialize (set up environment for agents)
       await society.init()

       # Query (read-only)
       response = await society.ask("What's your favorite activity?")
       print(f"Agent: {response}")

       # Close society
       await society.close()

   if __name__ == "__main__":
       asyncio.run(main())

运行此代码将产生类似以下输出：

.. code-block:: text

   Agent: I really love hiking! Being in nature, exploring new trails, and enjoying beautiful scenery brings a sense of peace.
   It's a great way to relax and stay energized.

创建自定义环境
--------------

环境模块允许智能体与特定功能进行交互：

.. code-block:: python

   from agentsociety2.env import EnvBase, tool, CodeGenRouter

   class MyEnvironment(EnvBase):
       """A custom environment module."""

       @tool(readonly=True, kind="observe")
       def get_weather(self, agent_id: int) -> str:
           """Get current weather."""
           return "The weather is sunny, temperature 25°C."

       @tool(readonly=False)
       def set_mood(self, agent_id: int, mood: str) -> str:
           """Change agent's mood."""
           return f"Agent {agent_id}'s mood is now {mood}."

   # Use custom module in AgentSociety
   agent = PersonAgent(id=1, profile={"name": "Bob"})

   env_router = CodeGenRouter(env_modules=[MyEnvironment()])

   society = AgentSociety(
       agents=[agent],
       env_router=env_router,
       start_t=datetime.now(),
   )
   await society.init()

   # Agent can now use the environment's tools
   response = await society.ask("What's the weather like?")
   print(response)

   await society.close()

使用 CLI 运行实验
------------------

AgentSociety 2 提供了一个强大的 CLI 用于运行实验。

**前台运行（调试）:**

.. code-block:: bash

   python -m agentsociety2.society.cli \
       --config my_experiment/init/init_config.json \
       --steps my_experiment/init/steps.yaml \
       --run-dir my_experiment/run \
       --log-level DEBUG

**后台运行（生产）:**

.. code-block:: bash

   python -m agentsociety2.society.cli \
       --config my_experiment/init/init_config.json \
       --steps my_experiment/init/steps.yaml \
       --run-dir my_experiment/run \
       --log-level INFO \
       --log-file my_experiment/run/output.log &

**重要**: 后台运行时必须指定 ``--log-file`` 参数。

更多详情请参见 :doc:`cli`。

运行实验（代码方式）
--------------------

下面是一个使用 AgentSociety 的多智能体完整实验示例：

.. code-block:: python

   import asyncio
   from datetime import datetime
   from pathlib import Path
   from agentsociety2 import PersonAgent
   from agentsociety2.env import CodeGenRouter
   from agentsociety2.contrib.env import SimpleSocialSpace
   from agentsociety2.storage import ReplayWriter
   from agentsociety2.society import AgentSociety

   async def main():
       # Set up replay writer for environment datasets
       writer = ReplayWriter(Path("my_experiment.db"))
       await writer.init()

       # Create agents first (SimpleSocialSpace needs this)
       agents = [
           PersonAgent(
               id=i,
               profile={"name": f"Player{i}", "personality": "competitive"},
           )
           for i in range(1, 4)
       ]

       # Create environment router
       env_router = CodeGenRouter(
           env_modules=[SimpleSocialSpace(
               agent_id_name_pairs=[(a.id, a.name) for a in agents]
           )],
           replay_writer=writer,
       )

       # Create society
       society = AgentSociety(
           agents=agents,
           env_router=env_router,
           start_t=datetime.now(),
           replay_writer=writer,
       )
       await society.init()

       # Run interactions
       for agent in agents:
           response = await society.ask(
               f"Tell {agent._name} to introduce themselves to the group!"
           )
           print(f"{agent._name}: {response}")

       await society.close()

   if __name__ == "__main__":
       asyncio.run(main())

.. note::

   ``ReplayWriter`` 现在只记录环境侧 replay dataset。``PersonAgent`` 的本地状态、
   thread 和工具日志会落在 ``run/agents/agent_xxxx/`` 目录，而不是 SQLite 的
   ``agent_status`` / ``agent_profile`` 表。

下一步
----------

既然您已经掌握了基础知识，可以继续探索：

* :doc:`agents` - 详细了解智能体
* :doc:`env_modules` - 创建自定义环境模块
* :doc:`concepts` - 理解核心概念
* :doc:`storage` - 了解回放系统
* :doc:`examples` - 查看更多示例

常见模式
---------------

只读查询
~~~~~~~~~~~~~~~~

对于不修改状态的查询，使用 ``society.ask()``：

.. code-block:: python

   # society.ask() ensures read-only access
   response = await society.ask("What agents are in the simulation?")

进行修改
~~~~~~~~~~~~~~

对于修改环境的操作，使用 ``society.intervene()``：

.. code-block:: python

   # society.intervene() allows environment modifications
   result = await society.intervene("Make everyone feel better")

查询特定智能体
~~~~~~~~~~~~~~~~~~~~~~~~~

向特定智能体提问：

.. code-block:: python

   # Ask a specific agent
   response = await society.ask(
       "Alice, what are your thoughts on the current situation?"
   )
