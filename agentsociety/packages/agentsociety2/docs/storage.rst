儲存和回放系統
=========================

AgentSociety 2 目前有兩條持久化路徑：

* **ReplayWriter / SQLite**: 用於環境模組回放資料和 replay catalog 後設資料。
* **PersonAgent workspace**: 用於每個 agent 的本地工作目錄和會話檔案。

其中，``ReplayWriter`` 不再為新實驗寫入 ``agent_profile``、``agent_status``、
``agent_dialog`` 這三張 agent 框架表；這些舊錶僅用於相容讀取歷史實驗資料庫。

概述
--------

``ReplayWriter`` 負責以下內容：

* replay catalog 表：

  * ``replay_dataset_catalog``
  * ``replay_column_catalog``

* 環境模組註冊的動態 replay 表
* 這些表的行級寫入與批次寫入

``PersonAgent`` 本地工作目錄通常位於 ``<run_dir>/agents/agent_0001/``，常見檔案包括：

* ``agent_config.json``
* ``init_state.json``
* ``session_state.json``
* ``session_state_history.jsonl``
* ``step_replay.jsonl``
* ``tool_calls.jsonl``
* ``thread_messages.jsonl``

儲存架構
~~~~~~~~~~~~~~~~~

.. graphviz::

   digraph storage_architecture {
       rankdir=TB;
       node [shape=box, style=rounded];

       subgraph cluster_db {
           label = "SQLite 資料庫 (experiment.db)";
           style=filled;
           color=lightblue;

           Catalog [label="replay_dataset_catalog\nreplay_column_catalog"];
           Custom [label="環境模組 replay 表"];
       }

       subgraph cluster_workspace {
           label = "Agent Workspace";
           style=filled;
           color=lightgreen;

           Config [label="agent_config.json"];
           Session [label="session_state.json"];
           Thread [label="thread_messages.jsonl"];
           Logs [label="tool_calls.jsonl\nstep_replay.jsonl"];
       }

       ReplayWriter [label="ReplayWriter"];
       Env [label="環境模組"];
       Person [label="PersonAgent"];

       Env -> ReplayWriter;
       ReplayWriter -> Catalog;
       ReplayWriter -> Custom;
       Person -> Config;
       Person -> Session;
       Person -> Thread;
       Person -> Logs;
   }

資料寫入流程
~~~~~~~~~~~~~~~~~

.. graphviz::

   digraph write_flow {
       rankdir=LR;
       node [shape=box, style=rounded];

       Env [label="環境模組狀態/事件"];
       ReplayWriter [label="ReplayWriter.write()"];
       SQLite [label="SQLite 寫入"];
       Disk [label="磁碟儲存", shape=cylinder];

       Person [label="PersonAgent step"];
       Workspace [label="workspace JSON / JSONL"];

       Env -> ReplayWriter;
       ReplayWriter -> SQLite;
       SQLite -> Disk;

       Person -> Workspace;
       Workspace -> Disk;
   }

基本使用
-----------

**啟用環境回放：**

.. code-block:: python

   from datetime import datetime
   from pathlib import Path
   from agentsociety2.storage import ReplayWriter
   from agentsociety2 import PersonAgent
   from agentsociety2.env import CodeGenRouter
   from agentsociety2.contrib.env import SimpleSocialSpace
   from agentsociety2.society import AgentSociety

   writer = ReplayWriter(Path("experiment.db"))
   await writer.init()

   agents = [
       PersonAgent(id=i, profile={"name": f"Agent{i}"})
       for i in range(1, 11)
   ]

   env_router = CodeGenRouter(
       env_modules=[SimpleSocialSpace(
           agent_id_name_pairs=[(a.id, a.name) for a in agents]
       )],
       replay_writer=writer,
   )

   society = AgentSociety(
       agents=agents,
       env_router=env_router,
       start_t=datetime.now(),
       replay_writer=writer,
   )
   await society.init()
   await society.run(num_steps=100, tick=3600)
   await society.close()

**檢視 replay catalog：**

.. code-block:: bash

   sqlite3 experiment.db "SELECT dataset_id, table_name, kind FROM replay_dataset_catalog;"

**檢視某個環境表：**

.. code-block:: bash

   sqlite3 experiment.db "SELECT * FROM mobility_agent_state LIMIT 10;"

Replay catalog
----------------

``ReplayWriter`` 會自動維護兩張 catalog 表：

* ``replay_dataset_catalog``: 記錄每個 dataset 的表名、模組名、kind、capabilities、排序鍵等
* ``replay_column_catalog``: 記錄每一列的 sqlite 型別、邏輯型別、分析角色、描述等

這兩張表是 replay API 和後續分析的入口，推薦優先讀取它們來發現當前實驗實際生成了哪些資料表。

自定義表
-------------

環境模組可以註冊自定義 replay 表：

**註冊自定義表：**

.. code-block:: python

   from agentsociety2.storage import ColumnDef, TableSchema

   schema = TableSchema(
       name="location_history",
       columns=[
           ColumnDef("id", "INTEGER", nullable=False),
           ColumnDef("agent_id", "INTEGER", nullable=False),
           ColumnDef("location", "TEXT"),
           ColumnDef("timestamp", "TIMESTAMP", nullable=False),
       ],
       primary_key=["id"],
       indexes=[["agent_id"], ["timestamp"]],
   )

   await writer.register_table(schema)

**註冊 dataset 後設資料：**

.. code-block:: python

   from agentsociety2.storage import ReplayDatasetSpec

   await writer.register_dataset(
       ReplayDatasetSpec(
           dataset_id="mobility.location_history",
           table_name="location_history",
           module_name="MobilitySpace",
           kind="event_stream",
           title="Location History",
           description="Per-agent location changes.",
           entity_key="agent_id",
           step_key=None,
           time_key="timestamp",
           default_order=["timestamp", "agent_id"],
           capabilities=["timeseries"],
       ),
       schema.columns,
   )

**寫入自定義表：**

.. code-block:: python

   await writer.write(
       table_name="location_history",
       data={
           "id": 1,
           "agent_id": agent.id,
           "location": "Central Park",
           "timestamp": datetime.now(),
       },
   )

   await writer.write_batch(
       table_name="location_history",
       data_list=[
           {"id": 2, "agent_id": 1, "location": "Downtown", "timestamp": datetime.now()},
           {"id": 3, "agent_id": 2, "location": "Uptown", "timestamp": datetime.now()},
       ],
   )

讀取與匯出
-----------

``ReplayWriter`` 當前是寫入器，不提供通用 ``read()`` 介面。讀取 replay 資料有兩種推薦方式：

**方式 1：透過後端 replay API**

.. code-block:: text

   GET /api/v1/replay/{hypothesis_id}/{experiment_id}/datasets
   GET /api/v1/replay/{hypothesis_id}/{experiment_id}/datasets/{dataset_id}/rows

**方式 2：直接查詢 SQLite**

.. code-block:: python

   import sqlite3
   import pandas as pd

   with sqlite3.connect("experiment.db") as conn:
       df = pd.read_sql_query(
           "SELECT * FROM mobility_agent_state ORDER BY step, agent_id",
           conn,
       )

PersonAgent workspace
----------------------

``PersonAgent`` 不會把自身 step 狀態寫入 SQLite replay 表。它會把本地狀態寫到 workspace：

* ``agent_config.json``: 能力引數、init state、技能可見性覆蓋、已啟用技能
* ``session_state.json``: 最近一次 step 的可見技能與啟用技能
* ``session_state_history.jsonl``: 會話狀態時間線
* ``step_replay.jsonl``: 每個 step 的工具歷史
* ``tool_calls.jsonl``: 工具呼叫日誌
* ``thread_messages.jsonl``: 最近 thread 訊息
* ``AGENT_CONTEXT.md``: 動態維護的上下文檔案（身份、狀態摘要、最近事件）
* ``AGENT_FILES.md``: 工作區檔案清單（每 10 步自動更新）

**狀態檔案 (state/)**：

內建狀態檔案透過配置定義，支援使用者擴充套件：

* ``emotion.json``: 情緒狀態
* ``intention.json``: 意圖狀態
* ``needs.json``: 需求狀態
* ``plan_state.json``: 規劃狀態
* 使用者自定義: 任何 ``state/*.json`` 檔案都會被自動發現

**WAL (Write-Ahead Log)**：

* ``wal/wal.jsonl``: 操作日誌（追加寫入，記憶體索引）
* ``wal/index.json``: 操作索引

這些檔案適合除錯 agent 行為、恢復 thread 上下文、檢查技能執行過程。

歷史相容說明
----------------

舊版本實驗可能仍然包含 ``agent_profile``、``agent_status``、``agent_dialog`` 三張表。

* 新實驗不會再寫入這些表
* 後端 replay API 仍會在讀取舊資料庫時相容它們
* 若舊錶不存在，replay API 會優先從具備 ``agent_snapshot`` capability 的動態 dataset 回退讀取
