"""模組自動發現與註冊。

該模組負責：

- 自動發現並註冊 ``contrib`` 下的內建環境模組與 agent；
- 掃描並註冊 ``custom`` 下的使用者自定義模組；
- 提供一組便捷函式給後端/CLI 呼叫（list/reload/get）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple, Type, Optional, Any
import importlib
import importlib.util
import pkgutil
import sys

from agentsociety2.agent.base import AgentBase
from agentsociety2.env.base import EnvBase
from agentsociety2.logger import get_logger

from agentsociety2.registry.base import ModuleRegistry, get_registry

logger = get_logger()


def _load_custom_class(
    *,
    file_path: str,
    class_name: str,
    module_prefix: str,
) -> type[Any]:
    """從 workspace 檔案載入自定義類。

    :param file_path: 檔案路徑。
    :param class_name: 目標類名。
    :param module_prefix: 注入到 sys.modules 的模組名字首（用於隔離）。
    :returns: 載入到的 class 物件。
    :raises ImportError: 無法構建 import spec 時丟擲。
    """

    spec = importlib.util.spec_from_file_location(
        f"{module_prefix}_{class_name}",
        file_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to create spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    module_name = f"{module_prefix}_{class_name}"
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return getattr(module, class_name)


def register_scanned_custom_modules(
    scan_result: Dict[str, Any],
    registry: Optional[ModuleRegistry] = None,
) -> Dict[str, Any]:
    """註冊 scanner 已發現的自定義模組。

    :param scan_result: scanner 輸出（包含 envs/agents/errors）。
    :param registry: 可選註冊中心；為空則使用全域性 registry。
    :returns: 更新後的 scan_result（會追加 registration_errors）。
    """

    if registry is None:
        registry = get_registry()

    registration_errors: list[str] = list(scan_result.get("registration_errors", []))

    for env_info in scan_result.get("envs", []):
        try:
            env_class = _load_custom_class(
                file_path=env_info["file_path"],
                class_name=env_info["class_name"],
                module_prefix="custom_env",
            )
            env_class._is_custom = True
            registry.register_env_module(
                env_info["class_name"],
                env_class,
                is_custom=True,
            )
        except Exception as exc:
            registration_errors.append(
                f"Env module {env_info.get('class_name')}: {exc}"
            )
            logger.warning(
                f"Failed to register custom env module {env_info.get('class_name')}: {exc}"
            )

    for agent_info in scan_result.get("agents", []):
        try:
            agent_class = _load_custom_class(
                file_path=agent_info["file_path"],
                class_name=agent_info["class_name"],
                module_prefix="custom_agent",
            )
            agent_class._is_custom = True
            registry.register_agent_module(
                agent_info["class_name"],
                agent_class,
                is_custom=True,
            )
        except Exception as exc:
            registration_errors.append(f"Agent {agent_info.get('class_name')}: {exc}")
            logger.warning(
                f"Failed to register custom agent {agent_info.get('class_name')}: {exc}"
            )

    scan_result["registration_errors"] = registration_errors
    return scan_result


def _discover_contrib_env_modules() -> Dict[str, Type[EnvBase]]:
    """發現 contrib.env 下所有環境模組。

    :returns: ``{class_name: class}`` 對映。
    """
    modules = {}

    try:
        from agentsociety2.contrib import env as env_package

        # Walk through all modules in contrib.env
        for importer, modname, ispkg in pkgutil.walk_packages(
            env_package.__path__, env_package.__name__ + "."
        ):
            if modname.startswith("__"):
                continue

            try:
                module = importlib.import_module(modname)

                # Find EnvBase subclasses in this module
                for name, obj in vars(module).items():
                    if (
                        isinstance(obj, type)
                        and issubclass(obj, EnvBase)
                        and obj is not EnvBase
                        and obj.__module__ == modname
                    ):
                        # Use class name directly as the key
                        if name and name not in modules:
                            modules[name] = obj
                            logger.debug(
                                f"Discovered env module: {name}"
                            )

            except Exception as e:
                logger.debug(f"Failed to import {modname}: {e}")

    except ImportError as e:
        logger.warning(f"Failed to import contrib.env: {e}")

    return modules


def _discover_contrib_agents() -> Dict[str, Type[AgentBase]]:
    """發現 contrib.agent 下所有 agent 類。

    :returns: ``{class_name: class}`` 對映。
    """
    agents = {}

    try:
        from agentsociety2.contrib import agent as agent_package

        # Walk through all modules in contrib.agent
        for importer, modname, ispkg in pkgutil.walk_packages(
            agent_package.__path__, agent_package.__name__ + "."
        ):
            if modname.startswith("__"):
                continue

            try:
                module = importlib.import_module(modname)

                # Find AgentBase subclasses in this module
                for name, obj in vars(module).items():
                    if (
                        isinstance(obj, type)
                        and issubclass(obj, AgentBase)
                        and obj is not AgentBase
                        and obj.__module__ == modname
                    ):
                        # Use class name directly as the key
                        if name and name not in agents:
                            agents[name] = obj
                            logger.debug(f"Discovered agent: {name}")

            except Exception as e:
                logger.debug(f"Failed to import {modname}: {e}")

    except ImportError as e:
        logger.warning(f"Failed to import contrib.agent: {e}")

    return agents


def _discover_builtin_agents() -> Dict[str, Type[AgentBase]]:
    """發現內建 agent（例如 PersonAgent）。"""
    agents = {}

    try:
        from agentsociety2.agent import person

        # Check PersonAgent
        if hasattr(person, "PersonAgent"):
            agents["PersonAgent"] = person.PersonAgent
            logger.debug("Discovered built-in agent: PersonAgent")

    except ImportError as e:
        logger.warning(f"Failed to import agentsociety2.agent: {e}")

    return agents


def _class_name_to_type(class_name: str) -> Optional[str]:
    """將類名轉換為 type identifier（CamelCase -> snake_case）。"""
    import re

    # Handle special cases
    if class_name == "LLMDonorAgent":
        return "llm_donor_agent"
    if class_name == "SocialMediaSpace":
        return "social_media"

    # Convert CamelCase to snake_case
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", class_name)
    snake_case = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    # Remove trailing "_agent" or "_env" if present
    if snake_case.endswith("_agent"):
        snake_case = snake_case[:-6] + "_agent"
    elif snake_case.endswith("_environment"):
        snake_case = snake_case[:-12]

    return snake_case


def discover_and_register_builtin_modules(registry: Optional[ModuleRegistry] = None) -> None:
    """發現並註冊所有內建模組（contrib + 內建 agent）。

    :param registry: 可選註冊中心；為空則使用全域性 registry。
    """
    if registry is None:
        registry = get_registry()

    # Discover and register environment modules
    env_modules = _discover_contrib_env_modules()
    for module_type, module_class in env_modules.items():
        registry.register_env_module(module_type, module_class, is_custom=False)

    # Discover and register agents
    agents = {}
    agents.update(_discover_contrib_agents())
    agents.update(_discover_builtin_agents())

    for agent_type, agent_class in agents.items():
        registry.register_agent_module(agent_type, agent_class, is_custom=False)

    logger.info(
        f"Registered {len(env_modules)} env modules and {len(agents)} agents "
        f"from built-in modules"
    )


def scan_and_register_custom_modules(
    workspace_path: Path, registry: Optional[ModuleRegistry] = None
) -> Dict[str, Any]:
    """掃描並註冊 custom/ 下的自定義模組。

    :param workspace_path: workspace 路徑。
    :param registry: 可選註冊中心；為空則使用全域性 registry。
    :returns: scan 結果（包含 envs/agents/errors）。
    """
    if registry is None:
        registry = get_registry()

    # Clear any previously registered custom modules to avoid accumulation
    # when user changes class names or module structure
    registry.clear_custom_modules()

    registry.set_workspace(workspace_path)

    from agentsociety2.backend.services.custom.scanner import CustomModuleScanner

    scanner = CustomModuleScanner(str(workspace_path))
    scan_result = scanner.scan_all()
    scan_result = register_scanned_custom_modules(scan_result, registry)

    logger.info(
        f"Registered {len(scan_result.get('envs', []))} custom env modules and "
        f"{len(scan_result.get('agents', []))} custom agents"
    )

    return scan_result


# Convenience functions

def get_registered_env_modules() -> List[Tuple[str, Type[EnvBase]]]:
    """:returns: 已註冊環境模組列表 ``[(module_type, module_class), ...]``。"""
    return get_registry().list_env_modules()


def get_registered_agent_modules() -> List[Tuple[str, Type[AgentBase]]]:
    """:returns: 已註冊 agent 列表 ``[(agent_type, agent_class), ...]``。"""
    return get_registry().list_agent_modules()


def get_env_module_class(module_type: str) -> Optional[Type[EnvBase]]:
    """按 type 獲取環境模組類。

    :param module_type: type identifier。
    :returns: 環境模組 class；未找到返回 ``None``。
    """
    return get_registry().get_env_module(module_type)


def get_agent_module_class(agent_type: str) -> Optional[Type[AgentBase]]:
    """按 type 獲取 agent 類。

    :param agent_type: type identifier。
    :returns: agent class；未找到返回 ``None``。
    """
    return get_registry().get_agent_module(agent_type)


def list_all_modules() -> Dict[str, List[Dict[str, Any]]]:
    """列出所有已註冊模組（含描述與是否 custom 標記）。"""
    registry = get_registry()

    env_modules = []
    for module_type, module_class in registry.list_env_modules():
        try:
            description = module_class.mcp_description() if hasattr(module_class, "mcp_description") else module_class.__doc__ or ""
        except Exception:
            description = ""
        env_modules.append({
            "type": module_type,
            "class_name": module_class.__name__,
            "description": description,
            "is_custom": getattr(module_class, "_is_custom", False),
        })

    agents = []
    for agent_type, agent_class in registry.list_agent_modules():
        try:
            description = agent_class.mcp_description() if hasattr(agent_class, "mcp_description") else agent_class.__doc__ or ""
        except Exception:
            description = ""
        agents.append({
            "type": agent_type,
            "class_name": agent_class.__name__,
            "description": description,
            "is_custom": getattr(agent_class, "_is_custom", False),
        })

    return {
        "env_modules": env_modules,
        "agents": agents,
    }


def reload_modules(workspace_path: Optional[Path] = None) -> None:
    """清空並重新發現模組（按需載入）。

    :param workspace_path: 可選 workspace 路徑（用於 custom 模組）。
    """
    registry = get_registry()

    # Clear registry
    registry._env_modules.clear()
    registry._agent_modules.clear()

    # Reset lazy loading flags
    registry._builtin_loaded = False
    registry._custom_loaded = False

    # Set workspace if provided
    if workspace_path:
        registry.set_workspace(workspace_path)

    logger.info("Module registry cleared (modules will be loaded on demand)")
