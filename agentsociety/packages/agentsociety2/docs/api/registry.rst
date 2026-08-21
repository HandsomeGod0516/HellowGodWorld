Registry 模組
=============

本模組提供智慧體和環境模組的集中註冊中心，支援延遲載入。

ModuleRegistry
--------------

.. autoclass:: agentsociety2.registry.ModuleRegistry
   :members:
   :undoc-members:
   :show-inheritance:

工具函式
--------

.. autofunction:: agentsociety2.registry.get_registry

註冊函式
--------

.. autofunction:: agentsociety2.registry.get_registered_env_modules

.. autofunction:: agentsociety2.registry.get_registered_agent_modules

.. autofunction:: agentsociety2.registry.get_env_module_class

.. autofunction:: agentsociety2.registry.get_agent_module_class

.. autofunction:: agentsociety2.registry.list_all_modules

.. autofunction:: agentsociety2.registry.reload_modules

.. autofunction:: agentsociety2.registry.scan_and_register_custom_modules

.. autofunction:: agentsociety2.registry.discover_and_register_builtin_modules

請求/響應模型
-------------

.. autoclass:: agentsociety2.registry.EnvModuleInitConfig
   :members:
   :undoc-members:

.. autoclass:: agentsociety2.registry.AgentInitConfig
   :members:
   :undoc-members:

.. autoclass:: agentsociety2.registry.CreateInstanceRequest
   :members:
   :undoc-members:

.. autoclass:: agentsociety2.registry.AskRequest
   :members:
   :undoc-members:

.. autoclass:: agentsociety2.registry.InterventionRequest
   :members:
   :undoc-members:
