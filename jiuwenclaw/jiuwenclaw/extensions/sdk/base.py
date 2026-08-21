from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from jiuwenclaw.extensions.types import ExtensionConfig, ExtensionMetadata

MANIFEST_FILENAME = "extension.yaml"


def _manifest_path(root: Path) -> Path | None:
    p = root / MANIFEST_FILENAME
    return p if p.exists() else None


class BaseExtension(ABC):
    _metadata_cache: Optional[ExtensionMetadata] = None
    _extension_dir: Optional[Path] = None
    _config_cache: Optional[dict] = None

    @abstractmethod
    async def initialize(self, config: ExtensionConfig) -> None:
        """擴充套件初始化

        Args:
            config: 擴充套件配置物件，包含全域性配置和 logger
                   擴充套件可透過 self._load_config_from_yaml() 載入自己的 config.yaml
        """
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """擴充套件關閉

        用於釋放擴充套件佔用的資源
        """
        pass

    @property
    def metadata(self) -> ExtensionMetadata:
        """擴充套件後設資料

        預設從擴充套件目錄下的 extension.yaml 載入，如果檔案不存在或解析失敗，
        子類可以覆蓋此屬性提供自定義實現。

        Returns:
            包含擴充套件資訊的 ExtensionMetadata 物件
        """
        if self._metadata_cache is not None:
            return self._metadata_cache

        self._metadata_cache = self._load_metadata_from_yaml()
        return self._metadata_cache

    def _load_metadata_from_yaml(self) -> ExtensionMetadata:
        """從擴充套件目錄的清單 YAML 載入後設資料"""
        import yaml

        root = self._get_extension_dir()
        if root is None:
            raise ValueError(
                "無法確定擴充套件目錄，請在子類中設定目錄或呼叫 set_extension_dir，或覆蓋 metadata 屬性"
            )

        yaml_path = _manifest_path(root)
        if yaml_path is None:
            raise FileNotFoundError(
                f"擴充套件後設資料檔案不存在（期望 {MANIFEST_FILENAME}）: {root}"
            )

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return ExtensionMetadata(
            id=data.get("id", ""),
            name=data.get("name", ""),
            version=data.get("version", ""),
            description=data.get("description", ""),
            author=data.get("author", ""),
            min_jiuwenclaw_version=data.get("min_jiuwenclaw_version", ""),
            dependencies=data.get("dependencies", {}),
            config_schema=data.get("config_schema"),
        )

    def _get_extension_dir(self) -> Optional[Path]:
        """獲取擴充套件包根目錄路徑"""
        if self._extension_dir is not None:
            return self._extension_dir

        import inspect

        cls = type(self)
        module = inspect.getmodule(cls)
        if module and hasattr(module, "__file__") and module.__file__:
            candidate = Path(module.__file__).parent
            if _manifest_path(candidate) is not None:
                return candidate

        return None

    def set_extension_dir(self, path: Path) -> None:
        """手動設定擴充套件根目錄（含清單 YAML）"""
        self._extension_dir = path
        self._metadata_cache = None
        self._config_cache = None

    def _load_config_from_yaml(self) -> dict:
        """從擴充套件目錄的 config.yaml 載入配置

        Returns:
            配置字典，如果檔案不存在則返回空字典
        """
        if self._config_cache is not None:
            return self._config_cache

        import yaml

        root = self._get_extension_dir()
        if root is None:
            return {}

        config_path = root / "config.yaml"
        if not config_path.exists():
            return {}

        with open(config_path, "r", encoding="utf-8") as f:
            self._config_cache = yaml.safe_load(f) or {}

        return self._config_cache
