# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Rail Extension Manager - 管理使用者自定義的 Rail 擴充套件."""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

from jiuwenclaw.common.utils import get_agent_workspace_dir

logger = logging.getLogger(__name__)


@dataclass
class RailExtension:
    """Rail 擴充套件資訊."""

    name: str  # 副檔名稱 (資料夾名稱)
    class_name: str = "CustomRail"  # Rail 類名 (從 rail.py 中提取)
    enabled: bool = True  # 是否啟用
    description: str = ""  # 描述
    priority: int = 50  # 優先順序

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "class_name": self.class_name,
            "enabled": self.enabled,
            "description": self.description,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RailExtension:
        return cls(
            name=data["name"],
            class_name=data.get("class_name", "CustomRail"),
            enabled=data.get("enabled", True),
            description=data.get("description", ""),
            priority=data.get("priority", 50),
        )


class RailManager:
    """Rail 擴充套件管理器."""

    _instance = None
    _extensions_dir: Path
    _config_file: Path
    _extensions: dict[str, RailExtension] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化 Rail 管理器."""
        if hasattr(self, "_initialized"):
            return

        self._extensions_dir = get_agent_workspace_dir() / "extensions"
        self._config_file = self._extensions_dir / "extensions_config.json"

        # 確保目錄存在
        self._extensions_dir.mkdir(parents=True, exist_ok=True)

        # 載入配置
        self._load_config()

        # 跟蹤已註冊的rail副檔名稱
        self._registered_rails: set[str] = set()
        # DeepAgent 例項引用，用於 register/unregister
        self._agent_instance: Any = None
        # 快取已載入的 rail 例項，確保同一個 rail 只例項化一次
        self._rail_instances: dict[str, Any] = {}

        self._initialized = True
        logger.info("[RailManager] 初始化完成，擴充套件目錄: %s", self._extensions_dir)

    def _load_config(self) -> None:
        """從配置檔案載入擴充套件資訊."""
        if self._config_file.exists():
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._extensions = {
                        name: RailExtension.from_dict(ext_data)
                        for name, ext_data in data.items()
                    }
                logger.info("[RailManager] 載入了 %d 個擴充套件配置", len(self._extensions))
            except Exception as e:
                logger.error("[RailManager] 載入配置檔案失敗: %s", e)
                self._extensions = {}
        else:
            self._extensions = {}

    def _save_config(self) -> None:
        """儲存擴充套件資訊到配置檔案."""
        try:
            data = {
                name: ext.to_dict()
                for name, ext in self._extensions.items()
            }
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug("[RailManager] 儲存配置檔案成功")
        except Exception as e:
            logger.error("[RailManager] 儲存配置檔案失敗: %s", e)
            raise

    def list_extensions(self) -> List[dict]:
        """獲取所有擴充套件列表."""
        return [ext.to_dict() for ext in self._extensions.values()]

    def import_extension(self, folder_path: str) -> dict:
        """匯入一個新的 Rail 擴充套件（資料夾結構）.

        Args:
            folder_path: 擴充套件資料夾路徑

        Returns:
            匯入的擴充套件資訊

        Raises:
            ValueError: 資料夾名稱無效或結構不符合要求
            Exception: 其他錯誤
        """
        source_path = Path(folder_path)
        if not source_path.exists() or not source_path.is_dir():
            raise ValueError(f"資料夾不存在或不是目錄: {folder_path}")

        # 獲取資料夾名稱
        name = source_path.name

        # 驗證資料夾名稱是否為有效的英文識別符號
        if not name.isidentifier() or not name.isascii():
            raise ValueError(f"資料夾名稱 '{name}' 必須是有效的英文識別符號")

        # 檢查是否已存在
        if name in self._extensions:
            raise ValueError(f"擴充套件 '{name}' 已存在")

        # 驗證資料夾結構：必須包含 rail.py
        plugin_file = source_path / "rail.py"
        if not plugin_file.exists():
            raise ValueError(f"擴充套件資料夾必須包含 rail.py 檔案")

        # 讀取並驗證 rail.py 內容
        try:
            with open(plugin_file, "r", encoding="utf-8") as f:
                plugin_content = f.read()
            self._validate_rail_file(plugin_content, name)
        except Exception as e:
            logger.error("[RailManager] rail.py 驗證失敗: %s", e)
            raise ValueError("rail.py 驗證失敗") from e

        # 複製整個資料夾到擴充套件目錄
        dest_path = self._extensions_dir / name
        try:
            if dest_path.exists():
                shutil.rmtree(dest_path)
            shutil.copytree(source_path, dest_path)
            logger.info("[RailManager] 複製資料夾成功: %s -> %s", source_path, dest_path)
        except Exception as e:
            logger.error("[RailManager] 複製資料夾失敗: %s", e)
            raise

        # 建立擴充套件記錄
        class_name = self._extract_class_name(plugin_content, name)
        description = self._extract_description(plugin_content)
        priority = self._extract_priority(plugin_content)

        extension = RailExtension(
            name=name,
            class_name=class_name,
            enabled=False,
            description=description,
            priority=priority,
        )

        self._extensions[name] = extension
        self._save_config()

        logger.info("[RailManager] 匯入擴充套件成功: %s", name)
        return extension.to_dict()

    @staticmethod
    def _validate_rail_file(file_str: str, name: str) -> None:
        """驗證 Rail 檔案內容是否有效.

        Args:
            file_str: 檔案內容字串
            name: 副檔名稱

        Raises:
            ValueError: 檔案內容無效
        """
        # 簡單驗證：檔案中必須包含繼承自 DeepAgentRail 或 AgentRail 的類
        required_patterns = ["DeepAgentRail", "AgentRail"]
        has_required_import = any(pattern in file_str for pattern in required_patterns)

        if not has_required_import:
            raise ValueError("檔案必須包含對 DeepAgentRail 或 AgentRail 的匯入")

        # 驗證語法
        try:
            compile(file_str, f"{name}.py", "exec")
        except SyntaxError as e:
            logger.error("[RailManager] rail.py 驗證失敗: %s", e)
            raise ValueError("語法錯誤") from e

    @staticmethod
    def _extract_class_name(file_str: str, default_name: str) -> str:
        """從檔案內容中提取 Rail 類名.

        Args:
            file_str: 檔案內容字串
            default_name: 預設類名 (使用副檔名的首字母大寫形式)

        Returns:
            提取到的類名
        """
        # 嘗試匹配 "class XXXRail(DeepAgentRail):" 或 "class XXXRail(AgentRail):"
        import re

        pattern = r"class\s+(\w+Rail)\s*\(\s*(DeepAgentRail|AgentRail)\s*\)"
        matches = re.findall(pattern, file_str)
        if matches:
            return matches[0][0]

        # 預設使用副檔名 + "Rail"
        return default_name.capitalize() + "Rail"

    @staticmethod
    def _extract_description(file_str: str) -> str:
        """從檔案內容中提取描述資訊.

        Args:
            file_str: 檔案內容字串

        Returns:
            提取到的描述
        """
        import re

        # 嘗試匹配類文件字串
        pattern = r'class\s+\w+Rail[^:]*:\s*"""([^"]*?)"""'
        match = re.search(pattern, file_str)
        if match:
            return match.group(1).strip()

        return ""

    @staticmethod
    def _extract_priority(file_str: str) -> int:
        """從檔案內容中提取優先順序.

        Args:
            file_str: 檔案內容字串

        Returns:
            提取到的優先順序
        """
        import re

        # 嘗試匹配 priority: int = XX
        pattern = r'priority\s*:\s*int\s*=\s*(\d+)'
        match = re.search(pattern, file_str)
        if match:
            return int(match.group(1))

        return 50  # 預設優先順序

    def get_registered_rail_names(self) -> set[str]:
        """獲取所有已註冊的 rail 副檔名稱集合.

        Returns:
            已註冊的 rail 名稱集合的副本
        """
        return self._registered_rails.copy()

    def delete_extension(self, name: str) -> bool:
        """刪除一個擴充套件（整個資料夾）.

        Args:
            name: 副檔名稱

        Returns:
            是否刪除成功

        Raises:
            ValueError: 擴充套件不存在
        """
        if name not in self._extensions:
            raise ValueError(f"擴充套件 '{name}' 不存在")

        # 如果擴充套件已註冊，從已註冊集合中移除
        if name in self._registered_rails:
            self._registered_rails.discard(name)
            logger.info("[RailManager] 擴充套件 '%s' 從已註冊集合中移除", name)

        # 清除快取的例項
        if name in self._rail_instances:
            del self._rail_instances[name]
            logger.info("[RailManager] 擴充套件 '%s' 的快取例項已清除", name)

        # 刪除整個資料夾
        folder_path = self._extensions_dir / name
        if folder_path.exists():
            try:
                if folder_path.is_dir():
                    shutil.rmtree(folder_path)
                else:
                    folder_path.unlink()
            except Exception as e:
                logger.error("[RailManager] 刪除資料夾失敗: %s", e)
                raise

        # 刪除擴充套件記錄
        del self._extensions[name]
        self._save_config()

        logger.info("[RailManager] 刪除擴充套件成功: %s", name)
        return True

    def toggle_extension(self, name: str, enabled: bool) -> dict:
        """切換擴充套件的啟用狀態（僅更新配置檔案）.

        Args:
            name: 副檔名稱
            enabled: 是否啟用

        Returns:
            更新後的擴充套件資訊

        Raises:
            ValueError: 擴充套件不存在
        """
        if name not in self._extensions:
            raise ValueError(f"擴充套件 '{name}' 不存在")

        self._extensions[name].enabled = enabled
        self._save_config()

        logger.info("[RailManager] 切換擴充套件狀態（配置檔案）: %s -> %s", name, enabled)
        return self._extensions[name].to_dict()

    def set_agent_instance(self, agent_instance: Any) -> None:
        """設定 DeepAgent 例項，用於熱更新 rail."""
        self._agent_instance = agent_instance
        logger.info("[RailManager] DeepAgent 例項已設定")

    async def hot_reload_rail(self, name: str, enabled: bool) -> None:
        """熱更新 rail：根據 enabled 狀態註冊或登出 rail 例項.

        Args:
            name: 副檔名稱
            enabled: 是否啟用

        Raises:
            ValueError: 擴充套件不存在或未設定 agent 例項
        """
        if name not in self._extensions:
            raise ValueError(f"擴充套件 '{name}' 不存在")

        if self._agent_instance is None:
            raise ValueError("DeepAgent 例項未設定，請先呼叫 set_agent_instance()")

        if enabled:
            # 開啟：註冊 rail
            if name in self._registered_rails:
                logger.warning("[RailManager] 擴充套件 '%s' 已註冊，跳過", name)
                return

            try:
                rail_instance = self.load_rail_instance_without_enabled_check(name)
                await self._agent_instance.register_rail(rail_instance)
                self._registered_rails.add(name)
                logger.info("[RailManager] 成功註冊 rail 擴充套件: %s", name)
            except Exception as e:
                logger.error("[RailManager] 註冊 rail 擴充套件失敗: %s, 錯誤: %s", name, e)
                raise
        else:
            # 關閉：登出 rail
            if name not in self._registered_rails:
                logger.warning("[RailManager] 擴充套件 %s 未註冊，跳過", name)
                return

            try:
                rail_instance = self.load_rail_instance_without_enabled_check(name)
                await self._agent_instance.unregister_rail(rail_instance)
                self._registered_rails.discard(name)
                logger.info("[RailManager] 成功登出 rail 擴充套件: %s", name)
            except Exception as e:
                logger.error("[RailManager] 登出 rail 擴充套件失敗: %s, 錯誤: %s", name, e)
                raise

    def is_rail_registered(self, name: str) -> bool:
        """檢查 rail 是否已註冊."""
        return name in self._registered_rails

    def get_extensions(self) -> List[dict]:
        """獲取所有擴充套件列表."""
        return [ext.to_dict() for ext in self._extensions.values()]

    def load_rail_instance(self, name: str) -> Any:
        """動態載入並例項化 Rail（需要擴充套件已啟用）.

        Args:
            name: 副檔名稱

        Returns:
            Rail 例項

        Raises:
            ValueError: 擴充套件不存在或未啟用
            Exception: 載入失敗
        """
        if name not in self._extensions:
            raise ValueError(f"擴充套件 '{name}' 不存在")

        extension = self._extensions[name]
        if not extension.enabled:
            raise ValueError(f"擴充套件 '{name}' 未啟用")

        return self._load_rail_instance_impl(name)

    def load_rail_instance_without_enabled_check(self, name: str) -> Any:
        """動態載入並例項化 Rail（不檢查啟用狀態，用於熱更新）.

        Args:
            name: 副檔名稱

        Returns:
            Rail 例項

        Raises:
            ValueError: 擴充套件不存在
            Exception: 載入失敗
        """
        if name not in self._extensions:
            raise ValueError(f"擴充套件 '{name}' 不存在")

        return self._load_rail_instance_impl(name)

    def _load_rail_class(self, name: str) -> type:
        """載入 Rail 類（不例項化，不快取）."""
        extension = self._extensions[name]

        folder_path = self._extensions_dir / name
        plugin_file = folder_path / "rail.py"
        if not plugin_file.exists():
            raise ValueError(f"擴充套件外掛檔案 '{name}/rail.py' 不存在")

        try:
            module: Any
            if (folder_path / "__init__.py").exists():
                package_name = f"jiuwenclaw_rail_extension_{name}"
                package_spec = importlib.util.spec_from_file_location(
                    package_name,
                    folder_path / "__init__.py",
                    submodule_search_locations=[str(folder_path)],
                )
                if package_spec is None or package_spec.loader is None:
                    raise ValueError(f"無法載入包規範: {name}")

                package_module = importlib.util.module_from_spec(package_spec)
                sys.modules[package_name] = package_module
                package_spec.loader.exec_module(package_module)

                module_name = f"{package_name}.rail"
                rail_spec = importlib.util.spec_from_file_location(module_name, plugin_file)
                if rail_spec is None or rail_spec.loader is None:
                    raise ValueError(f"無法載入 Rail 模組: {name}")

                module = importlib.util.module_from_spec(rail_spec)
                sys.modules[module_name] = module
                rail_spec.loader.exec_module(module)
            else:
                spec = importlib.util.spec_from_file_location(
                    f"rail_extension_{name}", plugin_file
                )
                if spec is None or spec.loader is None:
                    raise ValueError(f"無法載入模組規範: {name}")

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

            rail_class = getattr(module, extension.class_name, None)
            if rail_class is None:
                raise ValueError(f"模組中未找到類: {extension.class_name}")

            return rail_class
        except ImportError as e:
            if "attempted relative import with no known parent package" in str(e):
                raise ValueError(
                    f"擴充套件 '{name}' 使用了相對匯入但缺少 __init__.py 檔案。"
                    f"請確保擴充套件資料夾中包含 __init__.py 檔案以支援相對匯入。"
                ) from e
            raise
        except Exception as e:
            logger.error("[RailManager] 載入 Rail 類失敗: %s, 錯誤: %s", name, e)
            raise

    def _load_rail_instance_impl(self, name: str) -> Any:
        """載入 rail 例項的實現（快取機制，確保主 agent 的 rail 只例項化一次）."""
        if name in self._rail_instances:
            logger.debug("[RailManager] 返回快取的 Rail 例項: %s", name)
            return self._rail_instances[name]

        rail_class = self._load_rail_class(name)
        rail_instance = rail_class()
        self._rail_instances[name] = rail_instance
        logger.info("[RailManager] 載入並快取 Rail 例項成功: %s", name)
        return rail_instance

    def create_fresh_rail_instance(self, name: str) -> Any:
        """為 team 子 agent 建立獨立的 rail 例項（不使用快取，每次返回新例項）.

        Args:
            name: 副檔名稱

        Returns:
            新的 Rail 例項

        Raises:
            ValueError: 擴充套件不存在
            Exception: 載入失敗
        """
        if name not in self._extensions:
            raise ValueError(f"擴充套件 '{name}' 不存在")

        rail_class = self._load_rail_class(name)
        rail_instance = rail_class()
        logger.debug("[RailManager] 建立新 Rail 例項（team 專用）: %s -> %s", name, rail_instance)
        return rail_instance


def get_rail_manager() -> RailManager:
    """獲取 Rail 管理器單例."""
    return RailManager()
