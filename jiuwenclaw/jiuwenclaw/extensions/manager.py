from pathlib import Path
from typing import Any

from jiuwenclaw.common.config import get_config
from jiuwenclaw.extensions.loader import ExtensionLoader
from jiuwenclaw.extensions.registry import ExtensionRegistry
from jiuwenclaw.common.utils import logger


def _split_extension_dirs(value: str) -> list[str]:
    # 按需求使用 ';' 分割
    return [p.strip() for p in value.split(";") if p.strip()]


def _extension_dir_paths_from_config(cfg: dict) -> list[str]:
    """讀取 ``extensions.extension_dirs``（擴充套件包搜尋目錄：僅支援字串，用 ';' 分割）。"""
    ext = cfg.get("extensions")
    if not isinstance(ext, dict):
        return []
    dirs = ext.get("extension_dirs")
    if isinstance(dirs, str):
        return _split_extension_dirs(dirs)
    return []


class ExtensionManager:
    def __init__(
        self,
        registry: ExtensionRegistry,
    ):
        self.registry = registry
        self.loader = ExtensionLoader(registry)
        self._loaded_extensions: list[Any] = []
        self._setup_search_paths()

    def _setup_search_paths(self) -> None:
        extension_dirs = _extension_dir_paths_from_config(get_config())
        for path in extension_dirs:
            p = Path(path)
            if not p.is_absolute():
                # 相對路徑直接按當前工作目錄解析成絕對路徑
                p = p.resolve()
            if p.exists():
                self.loader.add_search_path(p)

    async def load_all_extensions(self) -> None:
        roots = self.loader.discover_extension_roots()
        logger.info("[ExtensionManager] 發現擴充套件路徑: %s", roots)
        for path in roots:
            try:
                loaded = await self.loader.load_extension(path)
                if loaded:
                    logger.info("[ExtensionManager] 載入 %s", loaded)
                    if isinstance(loaded, list):
                        self._loaded_extensions.extend(loaded)
                    else:
                        self._loaded_extensions.append(loaded)
            except Exception as e:
                logger.error("[ExtensionManager] 載入擴充套件 %s 失敗: %s", path, e)

    async def shutdown_all_extensions(self) -> None:
        for ext in self._loaded_extensions:
            try:
                if hasattr(ext, "shutdown"):
                    await ext.shutdown()
            except Exception as e:
                logger.warning("[ExtensionManager] 關閉擴充套件失敗: %s, error=%s", ext, e)
        self._loaded_extensions.clear()

    def list_extensions(self) -> list[dict]:
        return [
            {"id": p.metadata.id, "name": p.metadata.name, "version": p.metadata.version}
            for p in self._loaded_extensions
            if hasattr(p, "metadata")
        ]
