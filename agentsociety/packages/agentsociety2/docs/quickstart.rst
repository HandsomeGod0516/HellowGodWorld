快速入門
===========

本指南將幫助您快速上手 AgentSociety 2。

前置條件
----------------

在執行示例前，請先配置 LLM 環境變數（見 :doc:`installation`），或在專案目錄中準備好 ``.env`` 並在入口處儘早載入。

您的第一個智慧體
----------------

讓我們使用 **AgentSociety** 建立一個簡單的智慧體並與它互動：

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

執行此程式碼將產生類似以下輸出：

.. code-block:: text

   Agent: I really love hiking! Being in nature, exploring new trails, and enjoying beautiful scenery brings a sense of peace.
   It's a great way to relax and stay energized.

建立自定義環境
--------------

環境模組允許智慧體與特定功能進行互動：

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

使用 CLI 執行實驗
------------------

AgentSociety 2 提供了一個強大的 CLI 用於執行實驗。

**前臺執行（除錯）:**

.. code-block:: bash

   python -m agentsociety2.society.cli \
       --config my_experiment/init/init_config.json \
       --steps my_experiment/init/steps.yaml \
       --run-dir my_experiment/run \
       --log-level DEBUG

**後臺執行（生產）:**

.. code-block:: bash

   python -m agentsociety2.society.cli \
       --config my_experiment/init/init_config.json \
       --steps my_experiment/init/steps.yaml \
       --run-dir my_experiment/run \
       --log-level INFO \
       --log-file my_experiment/run/output.log &

**重要**: 後臺執行時必須指定 ``--log-file`` 引數。

更多詳情請參見 :doc:`cli`。

執行實驗（程式碼方式）
--------------------

下面是一個使用 AgentSociety 的多智慧體完整實驗示例：

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

   ``ReplayWriter`` 現在只記錄環境側 replay dataset。``PersonAgent`` 的本地狀態、
   thread 和工具日誌會落在 ``run/agents/agent_xxxx/`` 目錄，而不是 SQLite 的
   ``agent_status`` / ``agent_profile`` 表。

下一步
----------

既然您已經掌握了基礎知識，可以繼續探索：

* :doc:`agents` - 詳細瞭解智慧體
* :doc:`env_modules` - 建立自定義環境模組
* :doc:`concepts` - 理解核心概念
* :doc:`storage` - 瞭解回放系統
* :doc:`examples` - 檢視更多示例

常見模式
---------------

只讀查詢
~~~~~~~~~~~~~~~~

對於不修改狀態的查詢，使用 ``society.ask()``：

.. code-block:: python

   # society.ask() ensures read-only access
   response = await society.ask("What agents are in the simulation?")

進行修改
~~~~~~~~~~~~~~

對於修改環境的操作，使用 ``society.intervene()``：

.. code-block:: python

   # society.intervene() allows environment modifications
   result = await society.intervene("Make everyone feel better")

查詢特定智慧體
~~~~~~~~~~~~~~~~~~~~~~~~~

向特定智慧體提問：

.. code-block:: python

   # Ask a specific agent
   response = await society.ask(
       "Alice, what are your thoughts on the current situation?"
   )
