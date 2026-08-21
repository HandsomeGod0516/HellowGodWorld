"""模組註冊中心 - 提供 Agent 類和環境模組的集中註冊管理。

本模組支援：
- 內建模組（來自 contrib 目錄）
- 自定義模組（來自 custom 目錄）

主要功能
--------

- **ModuleRegistry**: 模組註冊中心類
- **get_registry**: 獲取全域性註冊中心例項
- **get_registered_env_modules**: 獲取已註冊的環境模組列表
- **get_registered_agent_modules**: 獲取已註冊的 Agent 模組列表
- **get_env_module_class**: 根據名稱獲取環境模組類
- **get_agent_module_class**: 根據名稱獲取 Agent 模組類
- **list_all_modules**: 列出所有已註冊模組
- **reload_modules**: 重新載入所有模組
- **scan_and_register_custom_modules**: 掃描並註冊自定義模組
- **discover_and_register_builtin_modules**: 發現並註冊內建模組

實現延遲載入 - 模組只在首次訪問時才被發現。
"""

from agentsociety2.registry.base import ModuleRegistry, get_registry
from agentsociety2.registry.modules import (
    get_registered_env_modules,
    get_registered_agent_modules,
    get_env_module_class,
    get_agent_module_class,
    list_all_modules,
    reload_modules,
    register_scanned_custom_modules,
    scan_and_register_custom_modules,
    discover_and_register_builtin_modules,
)
from agentsociety2.registry.models import (
    EnvModuleInitConfig,
    AgentInitConfig,
    CreateInstanceRequest,
    AskRequest,
    InterventionRequest,
)

__all__ = [
    # Registry
    "ModuleRegistry",
    "get_registry",
    "get_registered_env_modules",
    "get_registered_agent_modules",
    "get_env_module_class",
    "get_agent_module_class",
    "list_all_modules",
    "reload_modules",
    "register_scanned_custom_modules",
    "scan_and_register_custom_modules",
    "discover_and_register_builtin_modules",
    # Models
    "EnvModuleInitConfig",
    "AgentInitConfig",
    "CreateInstanceRequest",
    "AskRequest",
    "InterventionRequest",
]
