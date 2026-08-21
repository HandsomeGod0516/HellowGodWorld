"""LLM 配置（从环境变量读取）。

该模块集中管理 AgentSociety2 的 LLM 相关配置，并提供若干便捷函数：

- :class:`~agentsociety2.config.config.Config`：从环境变量读取各类 API Key / base_url / model name 等。
- :func:`~agentsociety2.config.config.get_llm_router`：获取（并缓存）指定用途的 LiteLLM :class:`litellm.router.Router`。
- :func:`~agentsociety2.config.config.get_llm_router_and_model`：同时返回 Router 与模型名。
- :func:`~agentsociety2.config.config.get_model_name`：获取指定用途的模型名。
- :func:`~agentsociety2.config.config.extract_json`：从 LLM 响应文本中提取 JSON 片段。

.. important::
   ``AGENTSOCIETY_LLM_API_KEY`` 与 ``AGENTSOCIETY_LLM_API_BASE`` 在创建 LLM Router
   或启动实验时校验。这样只需要配置向导的后端也能在缺少 API key 时启动。
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any, Literal, Optional
from litellm.router import Router

from agentsociety2.logger import get_logger, setup_litellm_logging

# 禁用遥测（避免连接 Posthog/Facebook 等外部服务）
# mem0 telemetry has per-call Posthog client creation in current upstream version,
# which may lead to excessive background threads in long simulations.
# Keep override capability: users can still export MEM0_TELEMETRY=true explicitly.
os.environ.setdefault("MEM0_TELEMETRY", "False")

# ChromaDB 也使用 Posthog 进行遥测，必须禁用
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from mem0.memory.main import MemoryConfig
import mem0.memory.main as _mem0_main

__all__ = [
    "Config",
    "get_llm_router",
    "get_llm_router_and_model",
    "get_model_name",
    "extract_json",
]

logger = get_logger()


def _redact_router_config_for_log(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "api_key" and isinstance(v, str) and v:
                out[k] = (v[:4] + "…") if len(v) > 4 else "****"
            else:
                out[k] = _redact_router_config_for_log(v)
        return out
    if isinstance(obj, list):
        return [_redact_router_config_for_log(x) for x in obj]
    return obj


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _disable_mem0_telemetry_if_needed() -> None:
    """必要时强制禁用 mem0 遥测。

    避免上游版本在每次调用时创建 Posthog client，导致长仿真中后台线程过多。
    若用户显式设置 ``MEM0_TELEMETRY=true`` 则尊重用户配置，不做覆盖。
    """
    if _is_truthy(os.getenv("MEM0_TELEMETRY", "False")):
        return

    def _noop_capture_event(*args, **kwargs):
        return None

    _mem0_main.capture_event = _noop_capture_event


_disable_mem0_telemetry_if_needed()

# Initialize LiteLLM logging once
_litellm_logging_initialized = False


class Config:
    """AgentSociety2 配置（环境变量来源）。

    该类以“类属性”的形式暴露配置项，便于在不实例化的情况下读取。

    主要环境变量（节选）：

    - ``AGENTSOCIETY_HOME_DIR``：数据目录
    - ``AGENTSOCIETY_LLM_API_KEY`` / ``AGENTSOCIETY_LLM_API_BASE`` / ``AGENTSOCIETY_LLM_MODEL``：默认模型配置
    - ``AGENTSOCIETY_CODER_LLM_*``：代码生成模型配置
    - ``AGENTSOCIETY_NANO_LLM_*``：高频/低延迟模型配置
    - ``AGENTSOCIETY_ANALYSIS_LLM_*``：分析写作模型配置
    - ``AGENTSOCIETY_EMBEDDING_*``：embedding 配置
    """

    HOME_DIR: str = os.getenv("AGENTSOCIETY_HOME_DIR", "./agentsociety_data")
    """
    Base directory path for storing agent data, memories, and persistent files.

    Environment variable: AGENTSOCIETY_HOME_DIR
    Default: "./agentsociety_data"

    This directory will contain subdirectories for memories, agent states, and other
    persistent data. The path can be absolute or relative to the current working directory.
    """

    # Default LLM settings
    # These are used for general-purpose language model operations throughout the system.

    LLM_API_KEY: Optional[str] = os.getenv("AGENTSOCIETY_LLM_API_KEY")
    """
    API key for authenticating with the default LLM service.

    Environment variable: AGENTSOCIETY_LLM_API_KEY
    Default: None (must be set for the system to function)

    This is the primary API key used for most LLM operations. If not set, the system
    will raise an error when attempting to create LLM routers. Other specialized LLM
    configurations (coder, nano, embedding) will fall back to this key if their specific
    keys are not provided.
    """

    LLM_API_BASE: str = os.getenv(
        "AGENTSOCIETY_LLM_API_BASE", "https://api.openai.com/v1"
    )
    """
    Base URL endpoint for the default LLM API service.

    Environment variable: AGENTSOCIETY_LLM_API_BASE
    Default: "https://api.openai.com/v1"

    This should point to the API endpoint that supports OpenAI-compatible API calls.
    The URL should include the protocol (https://) and the base path, but not the
    specific model endpoint (e.g., /chat/completions).
    """

    LLM_MODEL: str = os.getenv("AGENTSOCIETY_LLM_MODEL", "gpt-5.4")
    """
    Model identifier for the default LLM used in general operations.

    Environment variable: AGENTSOCIETY_LLM_MODEL
    Default: "gpt-5.4"

    This model is used for most language understanding and generation tasks that don't
    require specialized models. The model name should match what your API provider expects.
    """

    # Coder LLM settings
    # These are specifically optimized for code generation, analysis, and programming tasks.

    CODER_LLM_API_KEY: Optional[str] = (
        os.getenv("AGENTSOCIETY_CODER_LLM_API_KEY") or LLM_API_KEY
    )
    """
    API key for the code generation LLM service.

    Environment variable: AGENTSOCIETY_CODER_LLM_API_KEY
    Default: Falls back to LLM_API_KEY if not set

    This key is used specifically for code-related operations. If not provided, the system
    will use the default LLM_API_KEY. Setting a separate key allows you to use a different
    API provider or account for code generation tasks, which may have different rate limits
    or pricing structures.
    """

    CODER_LLM_API_BASE: str = (
        os.getenv("AGENTSOCIETY_CODER_LLM_API_BASE") or LLM_API_BASE
    )
    """
    Base URL endpoint for the code generation LLM API.

    Environment variable: AGENTSOCIETY_CODER_LLM_API_BASE
    Default: Falls back to LLM_API_BASE if not set

    Allows you to use a different API endpoint specifically for code generation tasks.
    This is useful if you want to route code generation requests to a different service
    or region for better performance or cost optimization.
    """

    CODER_LLM_MODEL: str = os.getenv("AGENTSOCIETY_CODER_LLM_MODEL") or LLM_MODEL
    """
    Model identifier for code generation and programming tasks.

    Environment variable: AGENTSOCIETY_CODER_LLM_MODEL
    Default: Falls back to LLM_MODEL if not set

    This model is specifically used for code generation, code analysis, and other
    programming-related operations. Choose a model that is optimized for code understanding
    and generation, such as models trained on codebases.
    """

    # Nano LLM settings
    # These are optimized for high-frequency, low-latency operations that require fast responses.

    NANO_LLM_API_KEY: Optional[str] = (
        os.getenv("AGENTSOCIETY_NANO_LLM_API_KEY") or LLM_API_KEY
    )
    """
    API key for the nano LLM service used in high-frequency operations.

    Environment variable: AGENTSOCIETY_NANO_LLM_API_KEY
    Default: Falls back to LLM_API_KEY if not set

    The nano LLM is used for operations that require frequent, fast responses such as
    memory operations, quick decision-making, and low-latency tasks. Setting a separate
    key allows you to use a faster or cheaper model for these high-frequency operations.
    """

    NANO_LLM_API_BASE: str = os.getenv("AGENTSOCIETY_NANO_LLM_API_BASE") or LLM_API_BASE
    """
    Base URL endpoint for the nano LLM API.

    Environment variable: AGENTSOCIETY_NANO_LLM_API_BASE
    Default: Falls back to LLM_API_BASE if not set

    Allows routing high-frequency operations to a different endpoint, which may be
    optimized for low latency or located in a different geographic region for better
    response times.
    """

    NANO_LLM_MODEL: str = os.getenv("AGENTSOCIETY_NANO_LLM_MODEL") or "gpt-5.4-nano"
    """
    Model identifier for high-frequency, low-latency operations.

    Environment variable: AGENTSOCIETY_NANO_LLM_MODEL
    Default: "gpt-5.4-nano"

    This model is used for operations that require fast responses, such as memory
    retrieval, quick reasoning, and other tasks where latency is critical. Typically,
    you might choose a smaller or faster model for these operations to reduce response time.
    """

    # Analysis LLM settings
    # These are optimized for data analysis, insight generation, and report writing.

    ANALYSIS_LLM_API_KEY: Optional[str] = (
        os.getenv("AGENTSOCIETY_ANALYSIS_LLM_API_KEY") or LLM_API_KEY
    )
    """
    API key for the analysis LLM service.

    Environment variable: AGENTSOCIETY_ANALYSIS_LLM_API_KEY
    Default: Falls back to LLM_API_KEY if not set

    This key is used specifically for data analysis, insight generation, and report
    writing tasks. Setting a separate key allows you to use a more capable model
    for these complex reasoning tasks.
    """

    ANALYSIS_LLM_API_BASE: str = (
        os.getenv("AGENTSOCIETY_ANALYSIS_LLM_API_BASE") or LLM_API_BASE
    )
    """
    Base URL endpoint for the analysis LLM API.

    Environment variable: AGENTSOCIETY_ANALYSIS_LLM_API_BASE
    Default: Falls back to LLM_API_BASE if not set

    Allows you to use a different API endpoint specifically for analysis tasks.
    This is useful if you want to route analysis requests to a more capable model
    or a different service.
    """

    ANALYSIS_LLM_MODEL: str = os.getenv("AGENTSOCIETY_ANALYSIS_LLM_MODEL") or LLM_MODEL
    """
    Model identifier for data analysis and report generation tasks.

    Environment variable: AGENTSOCIETY_ANALYSIS_LLM_MODEL
    Default: Falls back to LLM_MODEL if not set

    This model is specifically used for data analysis, insight generation,
    visualization planning, and report writing. Choose a model with strong
    reasoning and writing capabilities for best results.
    """

    # Embedding model settings
    # These are used for converting text into vector embeddings for semantic search and similarity.

    EMBEDDING_API_KEY: Optional[str] = (
        os.getenv("AGENTSOCIETY_EMBEDDING_API_KEY") or LLM_API_KEY
    )
    """
    API key for the embedding model service.

    Environment variable: AGENTSOCIETY_EMBEDDING_API_KEY
    Default: Falls back to LLM_API_KEY if not set

    This key is used specifically for embedding operations, which convert text into
    high-dimensional vectors for semantic search, similarity matching, and memory
    operations. Some providers offer separate embedding services with different pricing.
    """

    EMBEDDING_API_BASE: str = (
        os.getenv("AGENTSOCIETY_EMBEDDING_API_BASE") or LLM_API_BASE
    )
    """
    Base URL endpoint for the embedding API service.

    Environment variable: AGENTSOCIETY_EMBEDDING_API_BASE
    Default: Falls back to LLM_API_BASE if not set

    Allows you to use a different API endpoint specifically for embedding operations.
    Some providers have dedicated embedding endpoints that may offer better performance
    or different pricing models for embedding tasks.
    """

    EMBEDDING_MODEL: str = os.getenv(
        "AGENTSOCIETY_EMBEDDING_MODEL", "text-embedding-3-large"
    )
    """
    Model identifier for text embedding generation.

    Environment variable: AGENTSOCIETY_EMBEDDING_MODEL
    Default: "text-embedding-3-large"

    This model is used to convert text into dense vector representations (embeddings).
    The embeddings are used for semantic search, similarity matching, and storing
    memories in vector databases. Choose a model that produces high-quality embeddings
    for your use case and language.
    """

    EMBEDDING_DIMS: int = int(os.getenv("AGENTSOCIETY_EMBEDDING_DIMS", "1024"))
    """
    Dimensionality of the embedding vectors produced by the embedding model.

    Environment variable: AGENTSOCIETY_EMBEDDING_DIMS
    Default: 1024

    This specifies the size of the vector space for embeddings. Higher dimensions
    can capture more nuanced semantic information but require more storage and computation.
    The value must match the actual output dimensionality of the selected embedding model.
    Common values are 384, 512, 768, 1024, or 1536 depending on the model.
    """

    # Web Search API settings

    LITERATURE_SEARCH_API_URL: str = (
        os.getenv("LITERATURE_SEARCH_API_URL", "").strip()
        or "http://localhost:8008/api/search"
    )
    """
    Base URL for the literature search service.

    Environment variable: LITERATURE_SEARCH_API_URL
    Default: "http://localhost:8008/api/search"
    """

    LITERATURE_SEARCH_API_KEY: str = os.getenv("LITERATURE_SEARCH_API_KEY", "").strip()
    """
    API key for the literature search service authentication.

    Environment variable: LITERATURE_SEARCH_API_KEY
    Default: "" (empty, must be set for authenticated requests)
    """

    WEB_SEARCH_API_URL: str = os.getenv("WEB_SEARCH_API_URL", "").strip()
    """
    Base URL for the Web Search / MiroFlow MCP HTTP endpoint.

    Environment variable: WEB_SEARCH_API_URL
    Example: "http://localhost:8003/api/v1/search"
    """

    WEB_SEARCH_API_TOKEN: str = os.getenv("WEB_SEARCH_API_TOKEN", "").strip()
    """
    Authentication token for the Web Search / MiroFlow MCP server.

    Environment variable: WEB_SEARCH_API_TOKEN
    The token is sent as a Bearer token in the Authorization header.
    """

    MIROFLOW_DEFAULT_LLM: str = os.getenv("MIROFLOW_DEFAULT_LLM", "qwen-3").strip()
    """
    Default LLM model name used by MiroFlow MCP tasks.

    Environment variable: MIROFLOW_DEFAULT_LLM
    Default: "qwen-3"
    """

    MIROFLOW_DEFAULT_AGENT: str = os.getenv(
        "MIROFLOW_DEFAULT_AGENT", "mirothinker_v1.5_keep5_max200"
    ).strip()
    """
    Default agent configuration name used by MiroFlow MCP tasks.

    Environment variable: MIROFLOW_DEFAULT_AGENT
    Default: "mirothinker_v1.5_keep5_max200"
    """

    # EasyPaper API (for generate_paper tool)

    EASYPAPER_API_URL: str = (
        os.getenv("EASYPAPER_API_URL", "").strip() or "http://localhost:8004"
    )
    """
    EasyPaper paper typesetting service API base URL.

    Environment variable: EASYPAPER_API_URL
    Default: "http://localhost:8004"

    Used by the generate_paper tool to call EasyPaper's /metadata/generate endpoint.
    EasyPaper must be deployed separately (see https://github.com/tsinghua-fib-lab/EasyPaper).

    EasyPaper uses two model types (configured in EasyPaper's YAML, not here):
    - LLM: for planning, writing, review, typesetting (model_name, api_key, base_url per agent).
    - VLM: for PDF layout review / overflow check (vision-capable model in vlm_review agent).
    To set LLM/VLM API and models in one place, use AgentSociety2 config page (generates
    easypaper_agentsociety.yaml); or edit EasyPaper's configs/example.yaml and start
    EasyPaper with AGENT_CONFIG_PATH pointing to that file.
    """

    @classmethod
    def get_router(
        cls, model_type: Literal["default", "coder", "nano", "analysis"] = "default"
    ) -> Router:
        """获取指定用途的 LLM Router（不做全局缓存）。

        :param model_type: ``default`` / ``coder`` / ``nano`` / ``analysis``。
        :returns: LiteLLM :class:`litellm.router.Router` 实例。
        :raises ValueError: 当所需 API key 未配置时抛出。
        """
        global _litellm_logging_initialized

        # Initialize LiteLLM logging on first router creation
        if not _litellm_logging_initialized:
            setup_litellm_logging()
            _litellm_logging_initialized = True

        if model_type == "analysis":
            # Analysis model with fallback to default, then nano
            analysis_api_key = cls.ANALYSIS_LLM_API_KEY
            analysis_api_base = cls.ANALYSIS_LLM_API_BASE
            analysis_model = cls.ANALYSIS_LLM_MODEL

            default_api_key = cls.LLM_API_KEY
            default_api_base = cls.LLM_API_BASE
            default_model = cls.LLM_MODEL

            nano_api_key = cls.NANO_LLM_API_KEY
            nano_api_base = cls.NANO_LLM_API_BASE
            nano_model = cls.NANO_LLM_MODEL

            if not analysis_api_key:
                raise ValueError(
                    "API key not configured for analysis model. "
                    "Set AGENTSOCIETY_ANALYSIS_LLM_API_KEY or AGENTSOCIETY_LLM_API_KEY"
                )
            if not default_api_key:
                raise ValueError(
                    "API key not configured for default model (fallback). "
                    "Set AGENTSOCIETY_LLM_API_KEY"
                )
            if not nano_api_key:
                raise ValueError(
                    "API key not configured for nano model (fallback). "
                    "Set AGENTSOCIETY_NANO_LLM_API_KEY or AGENTSOCIETY_LLM_API_KEY"
                )

            # Build model_list with all three models
            model_list = [
                {
                    "model_name": analysis_model,
                    "litellm_params": {
                        "model": f"openai/{analysis_model}",
                        "api_key": analysis_api_key,
                        "api_base": analysis_api_base,
                    },
                },
                {
                    "model_name": default_model,
                    "litellm_params": {
                        "model": f"openai/{default_model}",
                        "api_key": default_api_key,
                        "api_base": default_api_base,
                    },
                },
                {
                    "model_name": nano_model,
                    "litellm_params": {
                        "model": f"openai/{nano_model}",
                        "api_key": nano_api_key,
                        "api_base": nano_api_base,
                    },
                },
            ]

            # Configure fallback chain: analysis -> default -> nano
            fallbacks = [{analysis_model: [default_model, nano_model]}]

            logger.debug(
                "Model list for analysis (with fallbacks): %s",
                _redact_router_config_for_log(model_list),
            )
            logger.debug("Fallbacks: %s", fallbacks)

            return Router(
                model_list=model_list,
                fallbacks=fallbacks,
                cache_responses=True,
                num_retries=10,
            )
        elif model_type == "coder":
            # Coder model with fallback to default, then nano
            coder_api_key = cls.CODER_LLM_API_KEY
            coder_api_base = cls.CODER_LLM_API_BASE
            coder_model = cls.CODER_LLM_MODEL

            default_api_key = cls.LLM_API_KEY
            default_api_base = cls.LLM_API_BASE
            default_model = cls.LLM_MODEL

            nano_api_key = cls.NANO_LLM_API_KEY
            nano_api_base = cls.NANO_LLM_API_BASE
            nano_model = cls.NANO_LLM_MODEL

            if not coder_api_key:
                raise ValueError(
                    "API key not configured for coder model. "
                    "Set AGENTSOCIETY_CODER_LLM_API_KEY or AGENTSOCIETY_LLM_API_KEY"
                )
            if not default_api_key:
                raise ValueError(
                    "API key not configured for default model (fallback). "
                    "Set AGENTSOCIETY_LLM_API_KEY"
                )
            if not nano_api_key:
                raise ValueError(
                    "API key not configured for nano model (fallback). "
                    "Set AGENTSOCIETY_NANO_LLM_API_KEY or AGENTSOCIETY_LLM_API_KEY"
                )

            # Build model_list with all three models
            model_list = [
                {
                    "model_name": coder_model,
                    "litellm_params": {
                        "model": f"openai/{coder_model}",
                        "api_key": coder_api_key,
                        "api_base": coder_api_base,
                    },
                },
                {
                    "model_name": default_model,
                    "litellm_params": {
                        "model": f"openai/{default_model}",
                        "api_key": default_api_key,
                        "api_base": default_api_base,
                    },
                },
                {
                    "model_name": nano_model,
                    "litellm_params": {
                        "model": f"openai/{nano_model}",
                        "api_key": nano_api_key,
                        "api_base": nano_api_base,
                    },
                },
            ]

            # Configure fallback chain: coder -> default -> nano
            # fallbacks should be a list of dicts, where each dict maps primary model to fallback models
            fallbacks = [{coder_model: [default_model, nano_model]}]

            logger.debug(
                "Model list for coder (with fallbacks): %s",
                _redact_router_config_for_log(model_list),
            )
            logger.debug("Fallbacks: %s", fallbacks)

            return Router(
                model_list=model_list,
                fallbacks=fallbacks,
                cache_responses=True,
                num_retries=10,  # 设置429错误的重试次数为10次
            )
        elif model_type == "default":
            # Default model with fallback to nano
            default_api_key = cls.LLM_API_KEY
            default_api_base = cls.LLM_API_BASE
            default_model = cls.LLM_MODEL

            nano_api_key = cls.NANO_LLM_API_KEY
            nano_api_base = cls.NANO_LLM_API_BASE
            nano_model = cls.NANO_LLM_MODEL

            if not default_api_key:
                raise ValueError(
                    "API key not configured for default model. "
                    "Set AGENTSOCIETY_LLM_API_KEY"
                )
            if not nano_api_key:
                raise ValueError(
                    "API key not configured for nano model (fallback). "
                    "Set AGENTSOCIETY_NANO_LLM_API_KEY or AGENTSOCIETY_LLM_API_KEY"
                )

            # Build model_list with default and nano models
            model_list = [
                {
                    "model_name": default_model,
                    "litellm_params": {
                        "model": f"openai/{default_model}",
                        "api_key": default_api_key,
                        "api_base": default_api_base,
                    },
                },
                {
                    "model_name": nano_model,
                    "litellm_params": {
                        "model": f"openai/{nano_model}",
                        "api_key": nano_api_key,
                        "api_base": nano_api_base,
                    },
                },
            ]

            # Configure fallback chain: default -> nano
            fallbacks = [{default_model: [nano_model]}]

            logger.debug(
                "Model list for default (with fallbacks): %s",
                _redact_router_config_for_log(model_list),
            )
            logger.debug("Fallbacks: %s", fallbacks)

            return Router(
                model_list=model_list,
                fallbacks=fallbacks,
                cache_responses=True,
                num_retries=10,  # 设置429错误的重试次数为10次
            )
        else:  # nano
            api_key = cls.NANO_LLM_API_KEY
            api_base = cls.NANO_LLM_API_BASE
            model = cls.NANO_LLM_MODEL

            if not api_key:
                raise ValueError(
                    f"API key not configured for {model_type} model. "
                    f"Set AGENTSOCIETY_{model_type.upper()}_LLM_API_KEY or AGENTSOCIETY_LLM_API_KEY"
                )

            model_list = [
                {
                    "model_name": model,
                    "litellm_params": {
                        "model": f"openai/{model}",
                        "api_key": api_key,
                        "api_base": api_base,
                    },
                },
            ]
            logger.info("Nano LLM configured: model=%s api_base=%s", model, api_base)
            logger.debug(
                "Nano model_list: %s", _redact_router_config_for_log(model_list)
            )
            return Router(
                model_list=model_list,
                cache_responses=True,
                num_retries=10,  # 设置429错误的重试次数为10次
            )

    @classmethod
    def get_literature_search_api_url(cls) -> str:
        """Get literature search API URL, preferring the latest environment value."""
        return (
            os.getenv("LITERATURE_SEARCH_API_URL", "").strip()
            or cls.LITERATURE_SEARCH_API_URL
        )

    @classmethod
    def get_literature_search_api_key(cls) -> str:
        """Get literature search API key, preferring the latest environment value."""
        return (
            os.getenv("LITERATURE_SEARCH_API_KEY", "").strip()
            or cls.LITERATURE_SEARCH_API_KEY
        )

    @classmethod
    def get_web_search_api_url(cls) -> str:
        """Get web search MCP URL, preferring the latest environment value."""
        return os.getenv("WEB_SEARCH_API_URL", "").strip() or cls.WEB_SEARCH_API_URL

    @classmethod
    def get_web_search_api_token(cls) -> str:
        """Get web search MCP token, preferring the latest environment value."""
        return os.getenv("WEB_SEARCH_API_TOKEN", "").strip() or cls.WEB_SEARCH_API_TOKEN

    @classmethod
    def get_miroflow_default_llm(cls) -> str:
        """Get default MiroFlow LLM, preferring the latest environment value."""
        return os.getenv("MIROFLOW_DEFAULT_LLM", "").strip() or cls.MIROFLOW_DEFAULT_LLM

    @classmethod
    def get_miroflow_default_agent(cls) -> str:
        """Get default MiroFlow agent, preferring the latest environment value."""
        return (
            os.getenv("MIROFLOW_DEFAULT_AGENT", "").strip()
            or cls.MIROFLOW_DEFAULT_AGENT
        )

    @classmethod
    def get_easypaper_api_url(cls) -> str:
        """Get EasyPaper API URL, preferring the latest environment value."""
        return os.getenv("EASYPAPER_API_URL", "").strip() or cls.EASYPAPER_API_URL

    @classmethod
    def get_default_router(cls) -> Router:
        """:returns: 默认用途的 LLM Router。"""
        return cls.get_router("default")

    @classmethod
    def get_mem0_config(cls, id: str) -> MemoryConfig:
        # Generate a random string to avoid path conflicts
        random_suffix = uuid.uuid4().hex[:8]
        memory_config = {
            "vector_store": {
                "provider": "chroma",
                "config": {
                    "collection_name": f"memories_{id}_{random_suffix}",
                    "path": os.path.join(
                        cls.HOME_DIR, f"memories_{id}_{random_suffix}"
                    ),
                },
            },
            "storage_config": {
                "provider": "sqlite",
                "path": os.path.join(cls.HOME_DIR, f"memories_{id}_{random_suffix}.db"),
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": cls.NANO_LLM_MODEL,
                    "api_key": cls.NANO_LLM_API_KEY,
                    "openai_base_url": cls.NANO_LLM_API_BASE,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": cls.EMBEDDING_MODEL,
                    "api_key": cls.EMBEDDING_API_KEY,
                    "openai_base_url": cls.EMBEDDING_API_BASE,
                    "embedding_dims": cls.EMBEDDING_DIMS,
                },
            },
        }
        return MemoryConfig.model_validate(memory_config)


# Required model configuration is intentionally validated when a model router is
# created or an experiment starts, not at module import time. The GOD setup UI
# needs the backend to boot before the operator has entered an API key.


# Global router instances (lazy initialization)
_default_router: Optional[Router] = None
_coder_router: Optional[Router] = None
_nano_router: Optional[Router] = None
_analysis_router: Optional[Router] = None


def get_llm_router(model_type: str = "default") -> Router:
    """获取（并缓存）指定用途的 LLM Router。

    :param model_type: ``default`` / ``coder`` / ``nano`` / ``analysis``。
    :returns: LiteLLM :class:`litellm.router.Router` 实例（进程内单例缓存）。
    """
    global _default_router, _coder_router, _nano_router, _analysis_router

    if model_type == "analysis":
        if _analysis_router is None:
            _analysis_router = Config.get_router("analysis")
        return _analysis_router
    elif model_type == "coder":
        if _coder_router is None:
            _coder_router = Config.get_router("coder")
        return _coder_router
    elif model_type == "nano":
        if _nano_router is None:
            _nano_router = Config.get_router("nano")
        return _nano_router
    else:  # default
        if _default_router is None:
            _default_router = Config.get_router("default")
        return _default_router


def get_model_name(model_type: str = "default") -> str:
    """获取指定用途的模型名。

    :param model_type: ``default`` / ``coder`` / ``nano`` / ``analysis``。
    :returns: 模型名字符串。
    """
    if model_type == "analysis":
        return Config.ANALYSIS_LLM_MODEL
    elif model_type == "coder":
        return Config.CODER_LLM_MODEL
    elif model_type == "nano":
        return Config.NANO_LLM_MODEL
    else:  # default
        return Config.LLM_MODEL


def get_llm_router_and_model(model_type: str = "default") -> tuple[Router, str]:
    """同时获取 Router 与模型名（Router 使用缓存）。

    :param model_type: ``default`` / ``coder`` / ``nano`` / ``analysis``。
    :returns: ``(router, model_name)``。
    """
    router = get_llm_router(model_type)
    model_name = get_model_name(model_type)
    return router, model_name


def extract_json(text: str) -> str | None:
    """从文本中尽量稳健地提取 JSON 字符串片段。

    该函数只做“截取”，不负责修复不合法 JSON；若需要修复，请配合 ``json_repair`` 等工具。

    :param text: 可能包含 JSON 的文本（例如 LLM 输出，可能夹杂 Markdown code fences）。
    :returns: 提取出的 JSON 文本；若未找到则返回 ``None``。
    """
    if not text:
        return None

    # Try to find JSON code blocks first (common in LLM responses)
    json_block_pattern = r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```"
    json_block_match = re.search(json_block_pattern, text, re.DOTALL)
    if json_block_match:
        return json_block_match.group(1)

    # Find the first occurrence of { or [
    start_idx = -1
    start_char = None
    end_char = None

    for i, char in enumerate(text):
        if char == "{":
            start_idx = i
            start_char = "{"
            end_char = "}"
            break
        elif char == "[":
            start_idx = i
            start_char = "["
            end_char = "]"
            break

    if start_idx == -1:
        return None

    # Find matching closing bracket/brace
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start_idx, len(text)):
        char = text[i]

        if escape_next:
            escape_next = False
            continue

        if char == "\\":
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == start_char:
            depth += 1
        elif char == end_char:
            depth -= 1
            if depth == 0:
                # Found matching closing bracket
                return text[start_idx : i + 1]

    # If we didn't find a closing bracket, return what we have
    # (might be incomplete JSON, but let json_repair handle it)
    return text[start_idx:]
