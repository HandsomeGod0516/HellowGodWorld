from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ExtensionMetadata:
    """擴充套件後設資料"""
    id: str                      # 擴充套件唯一標識
    name: str                    # 副檔名稱
    version: str                 # 擴充套件版本
    description: str             # 擴充套件描述
    author: str                  # 擴充套件作者
    min_jiuwenclaw_version: str  # 最小相容版本
    dependencies: dict[str, str]  # 擴充套件依賴 {"extension_id": ">=1.0.0"}
    config_schema: dict | None   # 配置模式 (JSON Schema)


@dataclass
class ExtensionConfig:
    config: dict[str, Any]
    logger: Any
