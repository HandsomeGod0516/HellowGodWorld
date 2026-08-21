儲存模組
========

本模組提供實驗資料的儲存與回放功能。

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

相容資料模型
-------------

以下模型僅用於相容讀取歷史 SQLite 資料庫；新實驗預設不再寫入這些 agent 表。

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
