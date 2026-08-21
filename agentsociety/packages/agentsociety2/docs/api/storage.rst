存储模块
========

本模块提供实验数据的存储与回放功能。

ReplayWriter
------------

.. autoclass:: agentsociety2.storage.ReplayWriter
   :members:
   :undoc-members:
   :show-inheritance:

ReplayDatasetSpec
-----------------

.. autoclass:: agentsociety2.storage.ReplayDatasetSpec
   :members:
   :undoc-members:

ColumnDef
---------

.. autoclass:: agentsociety2.storage.ColumnDef
   :members:
   :undoc-members:

TableSchema
-----------

.. autoclass:: agentsociety2.storage.TableSchema
   :members:
   :undoc-members:

兼容数据模型
-------------

以下模型仅用于兼容读取历史 SQLite 数据库；新实验默认不再写入这些 agent 表。

AgentProfile
~~~~~~~~~~~~

.. autoclass:: agentsociety2.storage.models.AgentProfile
   :members:
   :undoc-members:

AgentStatus
~~~~~~~~~~~

.. autoclass:: agentsociety2.storage.models.AgentStatus
   :members:
   :undoc-members:

AgentDialog
~~~~~~~~~~~

.. autoclass:: agentsociety2.storage.models.AgentDialog
   :members:
   :undoc-members:
