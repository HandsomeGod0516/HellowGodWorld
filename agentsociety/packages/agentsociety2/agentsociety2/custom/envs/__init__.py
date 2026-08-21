"""
自定义环境模块包

在此目录下创建自定义环境模块类。
"""

from typing import List, Tuple, Type
from agentsociety2.env.base import EnvBase

# 动态加载所有自定义环境模块
# 注意：此文件由系统自动维护，请勿手动编辑

_CUSTOM_ENVS: List[Tuple[str, Type[EnvBase]]] = []

def register_env(env_type: str, env_class: Type[EnvBase]):
    """注册自定义环境模块"""
    _CUSTOM_ENVS.append((env_type, env_class))

def get_custom_envs() -> List[Tuple[str, Type[EnvBase]]]:
    """获取所有自定义环境模块"""
    return _CUSTOM_ENVS.copy()

__all__ = ["register_env", "get_custom_envs"]
