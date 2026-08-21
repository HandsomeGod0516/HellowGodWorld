"""LLM 配置模組。

本模組提供 LLM 路由器和配置管理功能：

- **Config**: 配置類，管理 API 金鑰、模型名稱等
- **get_llm_router**: 獲取指定角色的 litellm Router 例項
- **get_llm_router_and_model**: 同時獲取 Router 和模型名稱
- **get_model_name**: 獲取指定角色的模型名稱
- **extract_json**: 從 LLM 響應中提取 JSON

角色型別：
- ``default``: 預設 LLM（通用任務）
- ``coder``: 程式碼生成 LLM（更強大的模型）
- ``nano``: 高頻操作 LLM（更快的模型）
- ``embedding``: 嵌入模型

環境變數配置：
- ``AGENTSOCIETY_LLM_API_KEY``: 主 API 金鑰（必需）
- ``AGENTSOCIETY_LLM_API_BASE``: API 基礎 URL（必需）
- ``AGENTSOCIETY_LLM_MODEL``: 預設模型名稱
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
