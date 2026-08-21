使用智慧體
===================

本部分介紹如何在 AgentSociety 2 中使用智慧體。

建立智慧體
---------------

PersonAgent
~~~~~~~~~~~

``PersonAgent`` 是一個 **skills-first / tool-using** 智慧體實現。它本身是一個輕量編排器，核心行為是“對標 Claude Code 的工具迴圈”：在每個 step 內注入身份與技能目錄，然後由主模型逐輪選擇並執行工具（包括技能啟用與技能執行）。

.. code-block:: python

   from agentsociety2 import PersonAgent

   agent = PersonAgent(
       id=1,
       profile={
           "name": "Alice",
           "age": 28,
           "personality": "friendly and curious",
           "bio": "A software engineer who loves hiking."
       }
   )

內建 Skills
^^^^^^^^^^^

每個 simulation tick，PersonAgent 都會執行同一套“工具迴圈”的流程：

.. list-table::
     :widths: 28 72
     :header-rows: 1

     * - 階段
       - 說明
     * - 注入上下文
       - system prompt 注入身份資訊、技能目錄、工具表。
     * - 啟用技能
       - 需要某個技能時，先用 ``activate_skill`` 載入該技能完整說明（通常來自 ``SKILL.md``）。
     * - 執行技能/工具
       - 用 ``execute_skill`` 執行技能，或直接呼叫 ``bash`` / ``grep`` / ``glob`` / ``codegen`` 等工具。
     * - 結束條件
       - 當主模型輸出 ``done=true`` 時結束本 step。

常見內建技能包括 ``observation``、``needs``、``cognition``、``plan``、``memory``。
它們都不再屬於固定“必須執行層”，而是由 LLM 按上下文按需選擇。

詳細說明請參見 :doc:`agent_skills`。

配置檔案可以包含你希望的任何欄位；PersonAgent 會把這些資訊用於塑造其行為與決策。

自定義智慧體
~~~~~~~~~~~~~

.. note::

   對於擴充套件 PersonAgent 的認知能力，推薦使用 **Agent Skills** 系統。
   參見 :doc:`agent_skills` 瞭解如何建立自定義 skill。

   只有在需要完全不同的智慧體架構時，才需要建立自定義智慧體類。

要建立自定義智慧體，請繼承 ``AgentBase`` 並實現必需的抽象方法：

需要實現的方法
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

建立自定義智慧體時，必須實現 ``AgentBase`` 的這些抽象方法：

1. **async def ask(self, message: str, readonly: bool = True) -> str**

   處理來自環境或使用者的問題並返回響應。

   引數:
       message: 要處理的問題或指令
       readonly: 智慧體是否可以修改環境（False = 可以修改）

   返回:
       智慧體的響應字串

2. **async def step(self, tick: int, t: datetime) -> str**

   執行一個模擬步驟。在 AgentSociety 模擬執行期間呼叫。

   引數:
       tick: 此步驟的持續時間（秒）
       t: 此步驟後的當前模擬日期時間

   返回:
       智慧體在此步驟中的操作描述

3. **async def dump(self) -> dict**

   將智慧體狀態序列化為字典以便儲存/載入。

4. **async def load(self, dump_data: dict)**

   從先前轉儲的字典中恢復智慧體狀態。

參考實現
^^^^^^^^^^^^^^^^^^^^^^^^

有關完整參考，請參閱原始碼中的 ``PersonAgent``。

示例:
^^^^^^

.. code-block:: python

   from agentsociety2.agent import AgentBase
   from datetime import datetime

   class MyAgent(AgentBase):
       def __init__(self, id: int, profile: dict, **kwargs):
           super().__init__(id=id, profile=profile, **kwargs)
           # Add custom initialization
           self._custom_state = profile.get("custom_field", {})

       async def ask(self, question: str, readonly: bool = True) -> str:
           # Process the question and return a response
           # Use self._env to interact with the environment
           return await super().ask(question, readonly=readonly)

       async def step(self, tick: int, t: datetime) -> str:
           # Execute one simulation step
           return await super().step(tick, t)

       async def dump(self) -> dict:
           # Save state
           return {
               "custom_state": self._custom_state,
               "profile": self._profile,
           }

       async def load(self, dump_data: dict):
           # Restore state
           self._custom_state = dump_data.get("custom_state", {})

智慧體配置檔案
--------------

配置檔案設計
~~~~~~~~~~~~~

一個好的智慧體配置檔案應包括：

* **身份**: 姓名、年齡、角色
* **個性**: 特徵、偏好、怪癖
* **背景**: 歷史、專業知識、關係
* **目標**: 動機、慾望、恐懼

.. code-block:: python

   profile = {
       # Identity
       "name": "Dr. Sarah Chen",
       "age": 35,
       "occupation": "climate scientist",

       # Personality
       "personality": "analytical, passionate, slightly anxious",
       "traits": ["detail-oriented", "empathetic", "curious"],

       # Background
       "education": "PhD in Atmospheric Science",
       "experience": "10 years in climate research",
       "achievements": ["Published 30+ papers", "Nobel nominee"],

       # Goals
       "goal": "raise awareness about climate change",
       "fears": ["sea level rise", "ecosystem collapse"]
   }

與智慧體互動
-----------------------

ask() 方法
~~~~~~~~~~~~~~~~~

.. code-block:: python

   response = await agent.ask(
       "What's your opinion on renewable energy?",
       readonly=True  # No side effects
   )

``readonly`` 引數控制智慧體是否可以修改環境：

* ``readonly=True``: 僅查詢，無副作用
* ``readonly=False``: 可能呼叫修改狀態的環境工具

step() 方法
~~~~~~~~~~~~~~~~~

``step()`` 方法在 AgentSociety 模擬期間自動呼叫：

.. code-block:: python

   # Called by AgentSociety.run()
   # tick = duration in seconds, t = current simulation time
   action_description = await agent.step(tick=3600, t=datetime.now())

持久化
~~~~~~~~~~~~~~~

``PersonAgent`` 當前的持久化分成兩層：

1. **Agent workspace 檔案**：由 ``PersonAgent`` 自身維護，位於 ``run/agents/agent_xxxx/``。
2. **環境 replay dataset**：由環境模組透過 ``ReplayWriter`` 寫入 SQLite。

也就是說，``PersonAgent`` 不會把自己的 step 狀態直接寫入 ``agent_status`` 之類的
SQLite 表；如果你需要檢查 agent 過程資料，應優先檢視：

* ``agent_config.json``: Agent 配置
* ``session_state.json``: 會話狀態
* ``tool_calls.jsonl``: 工具呼叫日誌
* ``thread_messages.jsonl``: Thread 訊息
* ``AGENT_CONTEXT.md``: 動態上下文檔案
* ``AGENT_FILES.md``: 工作區檔案清單
* ``state/*.json``: 狀態檔案（情緒、需求、意圖、規劃等）
* ``wal/``: Write-Ahead Log 目錄

智慧體記憶
------------

在當前版本中，記憶能力推薦透過 **Agent Skills** 來實現（例如 `memory` 技能）。

也就是說：

1. `PersonAgent` 提供獨立工作目錄與工具能力（讀寫檔案、執行技能等）。
2. 是否寫入記憶、寫入什麼、以及持久化方式，由 `memory` 技能的 `SKILL.md` 與其指令碼實現決定。

如果你想替換/擴充套件記憶策略，優先做法是新增/替換 skill，而不是修改 `PersonAgent` 本體。
