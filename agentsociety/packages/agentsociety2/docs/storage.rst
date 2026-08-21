存储和回放系统
=========================

AgentSociety 2 目前有两条持久化路径：

* **ReplayWriter / SQLite**: 用于环境模块回放数据和 replay catalog 元数据。
* **PersonAgent workspace**: 用于每个 agent 的本地工作目录和会话文件。

其中，``ReplayWriter`` 不再为新实验写入 ``agent_profile``、``agent_status``、
``agent_dialog`` 这三张 agent 框架表；这些旧表仅用于兼容读取历史实验数据库。

概述
--------

``ReplayWriter`` 负责以下内容：

* replay catalog 表：

  * ``replay_dataset_catalog``
  * ``replay_column_catalog``

* 环境模块注册的动态 replay 表
* 这些表的行级写入与批量写入

``PersonAgent`` 本地工作目录通常位于 ``<run_dir>/agents/agent_0001/``，常见文件包括：

* ``agent_config.json``
* ``init_state.json``
* ``session_state.json``
* ``session_state_history.jsonl``
* ``step_replay.jsonl``
* ``tool_calls.jsonl``
* ``thread_messages.jsonl``

存储架构
~~~~~~~~~~~~~~~~~

.. graphviz::

   digraph storage_architecture {
       rankdir=TB;
       node [shape=box, style=rounded];

       subgraph cluster_db {
           label = "SQLite 数据库 (experiment.db)";
           style=filled;
           color=lightblue;

           Catalog [label="replay_dataset_catalog\nreplay_column_catalog"];
           Custom [label="环境模块 replay 表"];
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
       Env [label="环境模块"];
       Person [label="PersonAgent"];

       Env -> ReplayWriter;
       ReplayWriter -> Catalog;
       ReplayWriter -> Custom;
       Person -> Config;
       Person -> Session;
       Person -> Thread;
       Person -> Logs;
   }

数据写入流程
~~~~~~~~~~~~~~~~~

.. graphviz::

   digraph write_flow {
       rankdir=LR;
       node [shape=box, style=rounded];

       Env [label="环境模块状态/事件"];
       ReplayWriter [label="ReplayWriter.write()"];
       SQLite [label="SQLite 写入"];
       Disk [label="磁盘存储", shape=cylinder];

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

**启用环境回放：**

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

**查看 replay catalog：**

.. code-block:: bash

   sqlite3 experiment.db "SELECT dataset_id, table_name, kind FROM replay_dataset_catalog;"

**查看某个环境表：**

.. code-block:: bash

   sqlite3 experiment.db "SELECT * FROM mobility_agent_state LIMIT 10;"

Replay catalog
----------------

``ReplayWriter`` 会自动维护两张 catalog 表：

* ``replay_dataset_catalog``: 记录每个 dataset 的表名、模块名、kind、capabilities、排序键等
* ``replay_column_catalog``: 记录每一列的 sqlite 类型、逻辑类型、分析角色、描述等

这两张表是 replay API 和后续分析的入口，推荐优先读取它们来发现当前实验实际生成了哪些数据表。

自定义表
-------------

环境模块可以注册自定义 replay 表：

**注册自定义表：**

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

**注册 dataset 元数据：**

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

**写入自定义表：**

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

读取与导出
-----------

``ReplayWriter`` 当前是写入器，不提供通用 ``read()`` 接口。读取 replay 数据有两种推荐方式：

**方式 1：通过后端 replay API**

.. code-block:: text

   GET /api/v1/replay/{hypothesis_id}/{experiment_id}/datasets
   GET /api/v1/replay/{hypothesis_id}/{experiment_id}/datasets/{dataset_id}/rows

**方式 2：直接查询 SQLite**

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

``PersonAgent`` 不会把自身 step 状态写入 SQLite replay 表。它会把本地状态写到 workspace：

* ``agent_config.json``: 能力参数、init state、技能可见性覆盖、已激活技能
* ``session_state.json``: 最近一次 step 的可见技能与激活技能
* ``session_state_history.jsonl``: 会话状态时间线
* ``step_replay.jsonl``: 每个 step 的工具历史
* ``tool_calls.jsonl``: 工具调用日志
* ``thread_messages.jsonl``: 最近 thread 消息
* ``AGENT_CONTEXT.md``: 动态维护的上下文文件（身份、状态摘要、最近事件）
* ``AGENT_FILES.md``: 工作区文件清单（每 10 步自动更新）

**状态文件 (state/)**：

内置状态文件通过配置定义，支持用户扩展：

* ``emotion.json``: 情绪状态
* ``intention.json``: 意图状态
* ``needs.json``: 需求状态
* ``plan_state.json``: 规划状态
* 用户自定义: 任何 ``state/*.json`` 文件都会被自动发现

**WAL (Write-Ahead Log)**：

* ``wal/wal.jsonl``: 操作日志（追加写入，内存索引）
* ``wal/index.json``: 操作索引

这些文件适合调试 agent 行为、恢复 thread 上下文、检查技能执行过程。

历史兼容说明
----------------

旧版本实验可能仍然包含 ``agent_profile``、``agent_status``、``agent_dialog`` 三张表。

* 新实验不会再写入这些表
* 后端 replay API 仍会在读取旧数据库时兼容它们
* 若旧表不存在，replay API 会优先从具备 ``agent_snapshot`` capability 的动态 dataset 回退读取
