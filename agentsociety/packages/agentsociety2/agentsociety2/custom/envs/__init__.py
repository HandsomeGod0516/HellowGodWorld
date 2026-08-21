"""
自定義環境模組包

在此目錄下建立自定義環境模組類。
"""

from typing import List, Tuple, Type
from agentsociety2.env.base import EnvBase

# 動態載入所有自定義環境模組
# 注意：此檔案由系統自動維護，請勿手動編輯

_CUSTOM_ENVS: List[Tuple[str, Type[EnvBase]]] = []

def register_env(env_type: str, env_class: Type[EnvBase]):
    """註冊自定義環境模組"""
    _CUSTOM_ENVS.append((env_type, env_class))

def get_custom_envs() -> List[Tuple[str, Type[EnvBase]]]:
    """獲取所有自定義環境模組"""
    return _CUSTOM_ENVS.copy()

__all__ = ["register_env", "get_custom_envs"]
