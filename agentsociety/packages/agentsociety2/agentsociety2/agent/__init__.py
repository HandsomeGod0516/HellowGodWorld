"""Agent模組 - 提供智慧體核心類和基礎設施。

核心元件
========

**AgentBase**
    智慧體抽象基類，定義基本介面。

**PersonAgent**
    技能優先型Agent實現，支援獨立工作區和漸進式技能發現。

配置管理
========

**AgentConfig**
    統一配置管理，整合模型、迴圈、上下文、持久化、併發等所有配置。

    >>> from agentsociety2.agent import AgentConfig
    >>> config = AgentConfig()  # 使用預設值
    >>> config.model.context_window  # 200000

持久化
======

**Checkpoint** - 檢查點管理，支援崩潰恢復
**WriteAheadLog** - 預寫日誌，確保精確恢復
**WorkspaceCleaner** - 工作區清理
**SessionRecovery** - 會話恢復上下文構建

併發控制
========

**ParallelExecutor** - 並行工具執行器
**RateLimiter** - 令牌桶限流器
**TaskManager** - 後臺工作管理員
"""

from .base import AgentBase
from .person import PersonAgent
from .config import (
    AgentConfig,
    ModelConfig,
    LoopConfig,
    ContextConfig,
    PersistenceConfig,
    ConcurrencyConfig,
    LoopDetectionConfig,
    StateConfig,
    ALLOWED_ENV_VARS,
)
from .prompt_builder import PromptBuilder, PromptCacheManager, ToolTableBuilder
from .persistence import (
    Checkpoint,
    WriteAheadLog,
    WorkspaceCleaner,
    SessionRecovery,
    IntentStatus,
)
from .concurrent import (
    Priority,
    PrioritizedTask,
    PriorityScheduler,
    ParallelExecutor,
    RateLimiter,
    TaskManager,
    DeadlockDetector,
)
from .context import AgentMemory

__all__ = [
    # 核心類
    "AgentBase",
    "PersonAgent",
    # 配置
    "AgentConfig",
    "ModelConfig",
    "LoopConfig",
    "ContextConfig",
    "PersistenceConfig",
    "ConcurrencyConfig",
    "LoopDetectionConfig",
    "StateConfig",
    "ALLOWED_ENV_VARS",
    # Prompt
    "PromptBuilder",
    "PromptCacheManager",
    "ToolTableBuilder",
    # 持久化
    "Checkpoint",
    "WriteAheadLog",
    "WorkspaceCleaner",
    "SessionRecovery",
    "IntentStatus",
    # 併發
    "Priority",
    "PrioritizedTask",
    "PriorityScheduler",
    "ParallelExecutor",
    "RateLimiter",
    "TaskManager",
    "DeadlockDetector",
    # 上下文
    "AgentMemory",
]
