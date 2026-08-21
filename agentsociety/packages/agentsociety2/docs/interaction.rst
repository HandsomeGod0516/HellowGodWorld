與 AgentSociety 互動
==============================

本指南介紹如何在實驗期間與 AgentSociety 2 互動。

概述
--------

AgentSociety 2 提供兩種主要的互動模式：

1. **查詢模式** (只讀): 提問而不修改模擬狀態
2. **干預模式** (讀寫): 修改智慧體狀態或環境變數

這些互動可以在以下時間執行：

* **模擬期間**: 在步驟之間或特定時間點
* **模擬後**: 查詢最終狀態或收集調查資料

互動模式對比
~~~~~~~~~~~~~~~~~

.. graphviz::

   digraph interaction_modes {
       rankdir=TB;
       node [shape=box, style=rounded];

       Society [label="AgentSociety"];
       Ask [label="ask() 方法", shape=ellipse];
       Intervene [label="intervene() 方法", shape=ellipse];

       subgraph cluster_ask {
           label = "查詢模式";
           style=filled;
           color=lightgreen;
           Query [label="查詢狀態"];
           Read [label="只讀操作"];
           NoModify [label="不修改環境"];
       }

       subgraph cluster_intervene {
           label = "干預模式";
           style=filled;
           color=lightcoral;
           Modify [label="修改狀態"];
           Write [label="讀寫操作"];
           Change [label="改變環境"];
       }

       Society -> Ask;
       Society -> Intervene;
       Ask -> Query;
       Ask -> Read;
       Ask -> NoModify;
       Intervene -> Modify;
       Intervene -> Write;
       Intervene -> Change;
   }

基本互動模式
---------------------------

ask() 方法 - 只讀查詢
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

使用 ``society.ask()`` 進行不修改模擬的只讀查詢：

.. code-block:: python

   # Query about agent state
   response = await society.ask("What is Agent 1's current mood?")

   # Query about environment
   response = await society.ask("What is the current weather?")

   # Query about multiple agents
   response = await society.ask("List all agents who are unhappy")

intervene() 方法 - 讀寫修改
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

使用 ``society.intervene()`` 對模擬進行更改：

.. code-block:: python

   # Send a message to agents
   result = await society.intervene(
       "Send a message to all agents: 'Severe weather coming, go home!'"
   )

   # Modify environment variables
   result = await society.intervene(
       "Change the weather to rainy and temperature to 15°C"
   )

   # Modify agent states
   result = await society.intervene(
       "Set all agents' happiness to 0.8"
   )

模擬工作流程
-------------------

使用逐步控制執行
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from datetime import datetime, timedelta

   # Create and initialize society
   society = AgentSociety(
       agents=agents,
       env_router=env_router,
       start_t=datetime.now(),
   )
   await society.init()

   # Run for specific number of steps
   for step_num in range(10):
       # Query before step
       state = await society.ask("What's happening?")
       print(f"Step {step_num}: {state}")

       # Execute step (tick = duration in seconds)
       await society.step(tick=3600, t=datetime.now())

       # Intervene based on conditions
       if "emergency" in state.lower():
           await society.intervene("Broadcast emergency alert")

   await society.close()

資料收集
---------------

收集智慧體響應
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Collect responses from all agents
   for agent in agents:
       response = await society.ask(
           f"Agent {agent.id}, how do you feel about the current situation?"
       )
       print(f"Agent {agent.id}: {response}")
       # Store for analysis

   # Collect survey responses
   survey_questions = [
       "How satisfied are you with your current situation? (1-5)",
       "What would improve your quality of life?",
   ]

   for agent in agents:
       for question in survey_questions:
           answer = await society.ask(f"Agent {agent.id}: {question}")
           # Save answer to database or file

使用 ReplayWriter 進行環境資料收集
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pathlib import Path
   from agentsociety2.storage import ReplayWriter

   writer = ReplayWriter(Path("experiment.db"))
   await writer.init()

   society = AgentSociety(
       agents=agents,
       env_router=env_router,
       start_t=datetime.now(),
       replay_writer=writer,
   )
   await society.init()

   # Run simulation - environment replay datasets are recorded to SQLite
   await society.run(num_steps=10, tick=3600)

   # Query replay catalog or environment tables later
   # sqlite3 experiment.db "SELECT * FROM replay_dataset_catalog;"

   await society.close()

常見互動場景
-----------------------------

場景 1: 事件干預
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Normal simulation
   await society.run(num_steps=5, tick=3600)

   # Event occurs (e.g., hurricane)
   await society.intervene(
       "Broadcast: 'Hurricane warning! Seek shelter immediately!'"
   )

   # Continue simulation to observe reactions
   await society.run(num_steps=5, tick=3600)

   # Collect impact data
   impact = await society.ask("How did the hurricane affect everyone?")
   print(impact)

場景 2: 政策實驗
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Control group - no policy
   control_agents = [PersonAgent(id=i, profile=...) for i in range(1, 11)]
   control_society = AgentSociety(agents=control_agents, ...)
   await control_society.init()
   await control_society.run(num_steps=10, tick=3600)

   # Treatment group - with policy intervention
   treatment_agents = [PersonAgent(id=i+10, profile=...) for i in range(10)]
   treatment_society = AgentSociety(agents=treatment_agents, ...)
   await treatment_society.init()

   # Implement policy
   await treatment_society.intervene(
       "Implement UBI policy: everyone receives $1000 monthly"
   )

   await treatment_society.run(num_steps=10, tick=3600)

   # Compare outcomes
   control_outcome = await control_society.ask("What's the average happiness?")
   treatment_outcome = await treatment_society.ask("What's the average happiness?")

場景 3: 多個時間點收集資料
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Baseline data
   baseline = await society.ask("Record everyone's baseline mood")

   # Intervention
   await society.intervene("Announce new community program")

   # Short-term effects
   await society.run(num_steps=3, tick=3600)
   short_term = await society.ask("How is everyone feeling now?")

   # Long-term effects
   await society.run(num_steps=10, tick=3600)
   long_term = await society.ask("How is everyone feeling now?")

   # Analyze change over time

最佳實踐
--------------

1. **對查詢使用 ask()**: 只需要資訊時始終使用 ``ask()``

2. **對更改使用 intervene()**: 只在想修改狀態時使用 ``intervene()``

3. **結合 ReplayWriter**: 用環境 replay dataset 做實驗分析；agent 本地除錯則檢視 ``run/agents/agent_xxxx/`` 下的 workspace 檔案

4. **查詢特定智慧體**: 向特定智慧體提問以獲得有針對性的響應

5. **適時干預**: 在適當的模擬時間進行干預以獲得現實效果
