"""LLM 配置模块。

本模块提供 LLM 路由器和配置管理功能：

- **Config**: 配置类，管理 API 密钥、模型名称等
- **get_llm_router**: 获取指定角色的 litellm Router 实例
- **get_llm_router_and_model**: 同时获取 Router 和模型名称
- **get_model_name**: 获取指定角色的模型名称
- **extract_json**: 从 LLM 响应中提取 JSON

角色类型：
- ``default``: 默认 LLM（通用任务）
- ``coder``: 代码生成 LLM（更强大的模型）
- ``nano``: 高频操作 LLM（更快的模型）
- ``embedding``: 嵌入模型

环境变量配置：
- ``AGENTSOCIETY_LLM_API_KEY``: 主 API 密钥（必需）
- ``AGENTSOCIETY_LLM_API_BASE``: API 基础 URL（必需）
- ``AGENTSOCIETY_LLM_MODEL``: 默认模型名称
- ``AGENTSOCIETY_CODER_LLM_*``: Coder 角色配置
- ``AGENTSOCIETY_NANO_LLM_*``: Nano 角色配置
- ``AGENTSOCIETY_EMBEDDING_*``: Embedding 模型配置
"""

from .config import (
    Config,
    get_llm_router,
    get_llm_router_and_model,
    get_model_name,
    extract_json,
)

__all__ = [
    "Config",
    "get_llm_router",
    "get_llm_router_and_model",
    "get_model_name",
    "extract_json",
]
