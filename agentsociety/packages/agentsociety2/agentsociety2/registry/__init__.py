"""模块注册中心 - 提供 Agent 类和环境模块的集中注册管理。

本模块支持：
- 内置模块（来自 contrib 目录）
- 自定义模块（来自 custom 目录）

主要功能
--------

- **ModuleRegistry**: 模块注册中心类
- **get_registry**: 获取全局注册中心实例
- **get_registered_env_modules**: 获取已注册的环境模块列表
- **get_registered_agent_modules**: 获取已注册的 Agent 模块列表
- **get_env_module_class**: 根据名称获取环境模块类
- **get_agent_module_class**: 根据名称获取 Agent 模块类
- **list_all_modules**: 列出所有已注册模块
- **reload_modules**: 重新加载所有模块
- **scan_and_register_custom_modules**: 扫描并注册自定义模块
- **discover_and_register_builtin_modules**: 发现并注册内置模块

实现延迟加载 - 模块只在首次访问时才被发现。
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
