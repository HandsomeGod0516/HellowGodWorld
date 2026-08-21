"""模組註冊中心（agent/env 的集中註冊與惰性發現）。"""

from __future__ import annotations

from typing import Dict, List, Tuple, Type, Optional, Any
from pathlib import Path
import inspect
import os

from agentsociety2.agent.base import AgentBase
from agentsociety2.env.base import EnvBase
from agentsociety2.logger import get_logger

logger = get_logger()


class ModuleRegistry:
    """agent 與環境模組的集中註冊中心（單例）。

    支援兩類來源：

    - 內建模組：來自 ``agentsociety2.contrib`` 與內建 agent（例如 PersonAgent）
    - 自定義模組：來自 workspace 的 ``custom/`` 目錄

    預設啟用惰性載入：只有在第一次訪問 registry 內容時才觸發發現與註冊。
    """

    _instance: Optional["ModuleRegistry"] = None

    def __new__(cls) -> "ModuleRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._env_modules: Dict[str, Type[EnvBase]] = {}
        self._agent_modules: Dict[str, Type[AgentBase]] = {}
        self._workspace_path: Optional[Path] = None

        # Lazy loading flags
        self._builtin_loaded: bool = False
        self._custom_loaded: bool = False
        self._lazy_enabled: bool = True  # Can be disabled to force eager loading

        logger.info("ModuleRegistry initialized (lazy loading enabled)")

    def _ensure_builtin_loaded(self) -> None:
        """確保內建模組已載入（惰性載入觸發點）。"""
        if not self._lazy_enabled:
            return
        if self._builtin_loaded:
            return

        from agentsociety2.registry.modules import discover_and_register_builtin_modules

        discover_and_register_builtin_modules(self)
        self._builtin_loaded = True

    def _ensure_custom_loaded(self) -> None:
        """確保自定義模組已載入（惰性載入觸發點）。"""
        if not self._lazy_enabled:
            return
        if self._custom_loaded:
            return

        workspace_path = self._resolve_workspace_path()
        if workspace_path is None:
            # No workspace set, nothing to load
            self._custom_loaded = True
            return

        from agentsociety2.registry.modules import scan_and_register_custom_modules

        scan_and_register_custom_modules(workspace_path, self)
        self._custom_loaded = True

    def _ensure_loaded(self) -> None:
        """確保內建與自定義模組都已載入（惰性載入觸發點）。"""
        self._ensure_builtin_loaded()
        self._ensure_custom_loaded()

    @property
    def env_modules(self) -> Dict[str, Type[EnvBase]]:
        """:returns: 已註冊環境模組對映（訪問會觸發惰性載入）。"""
        self._ensure_loaded()
        return self._env_modules.copy()

    @property
    def agent_modules(self) -> Dict[str, Type[AgentBase]]:
        """:returns: 已註冊 agent 對映（訪問會觸發惰性載入）。"""
        self._ensure_loaded()
        return self._agent_modules.copy()

    def register_env_module(
        self, module_type: str, module_class: Type[EnvBase], is_custom: bool = False
    ) -> None:
        """註冊環境模組。

        :param module_type: type identifier（例如 ``simple_social_space``）。
        :param module_class: 環境模組類。
        :param is_custom: 是否為自定義模組。
        """
        if module_type in self._env_modules and not is_custom:
            logger.debug(f"Env module '{module_type}' already registered, skipping")
            return

        self._env_modules[module_type] = module_class
        logger.debug(f"Registered env module: {module_type} -> {module_class.__name__}")

    def register_agent_module(
        self, agent_type: str, agent_class: Type[AgentBase], is_custom: bool = False
    ) -> None:
        """註冊 agent。

        :param agent_type: type identifier（例如 ``person_agent``）。
        :param agent_class: agent 類。
        :param is_custom: 是否為自定義 agent。
        """
        if agent_type in self._agent_modules and not is_custom:
            logger.debug(f"Agent '{agent_type}' already registered, skipping")
            return

        self._agent_modules[agent_type] = agent_class
        logger.debug(f"Registered agent: {agent_type} -> {agent_class.__name__}")

    def get_env_module(self, module_type: str) -> Optional[Type[EnvBase]]:
        """按 type 獲取環境模組類（會觸發惰性載入）。

        :param module_type: type identifier。
        :returns: 環境模組類；未找到返回 ``None``。
        """
        self._ensure_loaded()
        return self._env_modules.get(module_type)

    def get_agent_module(self, agent_type: str) -> Optional[Type[AgentBase]]:
        """按 type 獲取 agent 類（會觸發惰性載入）。

        :param agent_type: type identifier。
        :returns: agent 類；未找到返回 ``None``。
        """
        self._ensure_loaded()
        return self._agent_modules.get(agent_type)

    def list_env_modules(self) -> List[Tuple[str, Type[EnvBase]]]:
        """:returns: 已註冊環境模組列表（會觸發惰性載入）。"""
        self._ensure_loaded()
        return list(self._env_modules.items())

    def list_agent_modules(self) -> List[Tuple[str, Type[AgentBase]]]:
        """:returns: 已註冊 agent 列表（會觸發惰性載入）。"""
        self._ensure_loaded()
        return list(self._agent_modules.items())

    def set_workspace(self, workspace_path: Path) -> None:
        """設定 workspace 路徑（用於 custom 模組發現）。

        :param workspace_path: workspace 目錄。
        """
        self._workspace_path = workspace_path.resolve()
        # Reset custom loaded flag so modules will be discovered on next access
        self._custom_loaded = False
        logger.debug(f"Registry workspace set to: {self._workspace_path}")

    def _resolve_workspace_path(self) -> Optional[Path]:
        """:returns: 用於 custom 模組發現的 workspace 路徑；若無法推斷則返回 ``None``。"""

        if self._workspace_path is not None:
            return self._workspace_path

        env_workspace = os.getenv("WORKSPACE_PATH")
        if env_workspace:
            self._workspace_path = Path(env_workspace).resolve()
            logger.debug(f"Registry workspace inferred from WORKSPACE_PATH: {self._workspace_path}")
            return self._workspace_path

        cwd = Path.cwd().resolve()
        candidates = [cwd, *cwd.parents]
        for candidate in candidates:
            if (candidate / "custom" / "envs").exists() or (candidate / "custom" / "agents").exists():
                self._workspace_path = candidate
                logger.debug(f"Registry workspace inferred from cwd: {self._workspace_path}")
                return self._workspace_path

        return None

    def load_builtin_modules(self) -> None:
        """主動載入內建模組（禁用惰性等待）。"""
        self._ensure_builtin_loaded()

    def load_custom_modules(self) -> None:
        """主動載入自定義模組（禁用惰性等待）。"""
        self._ensure_custom_loaded()

    def load_all_modules(self) -> None:
        """主動載入全部模組（內建 + 自定義）。"""
        self._ensure_loaded()

    def clear_custom_modules(self) -> None:
        """清除 registry 中所有 custom 模組。"""
        to_remove = [
            mt for mt, mc in self._env_modules.items()
            if getattr(mc, "_is_custom", False)
        ]
        for mt in to_remove:
            del self._env_modules[mt]

        to_remove = [
            at for at, ac in self._agent_modules.items()
            if getattr(ac, "_is_custom", False)
        ]
        for at in to_remove:
            del self._agent_modules[at]

        # Reset custom loaded flag so modules will be re-discovered on next access
        self._custom_loaded = False

        logger.info(f"Cleared {len(to_remove)} custom modules")

    def get_module_info(self, module_type: str, kind: str) -> Dict[str, Any]:
        """獲取模組資訊（會觸發惰性載入）。

        :param module_type: type identifier。
        :param kind: ``env_module`` 或 ``agent``。
        :returns: 模組資訊字典（含引數簽名、描述、是否 custom 等）。
        """
        self._ensure_builtin_loaded()

        if kind == "env_module":
            cls = self.get_env_module(module_type)
        else:
            cls = self.get_agent_module(module_type)

        if cls is None:
            return {
                "success": False,
                "error": f"Module '{module_type}' not found",
            }

        # Try to get description
        description = ""
        try:
            if hasattr(cls, "mcp_description"):
                description = cls.mcp_description()
            else:
                description = cls.__doc__ or f"{cls.__name__}"
        except Exception:
            description = f"{cls.__name__}"

        # Get constructor signature
        params = {}
        try:
            sig = inspect.signature(cls.__init__)
            for name, param in list(sig.parameters.items())[1:]:  # Skip 'self'
                params[name] = {
                    "annotation": str(param.annotation)
                    if param.annotation != inspect.Parameter.empty
                    else "Any",
                    "default": str(param.default)
                    if param.default != inspect.Parameter.empty
                    else None,
                    "kind": str(param.kind),
                }
        except Exception:
            pass

        return {
            "success": True,
            "type": module_type,
            "class_name": cls.__name__,
            "description": description,
            "parameters": params,
            "is_custom": getattr(cls, "_is_custom", False),
        }


# Global registry instance
_registry: Optional[ModuleRegistry] = None


def get_registry() -> ModuleRegistry:
    """:returns: 全域性 :class:`~agentsociety2.registry.base.ModuleRegistry` 單例。"""
    global _registry
    if _registry is None:
        _registry = ModuleRegistry()
    return _registry
