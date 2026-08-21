"""社會模擬模組 - 提供多 Agent 模擬的核心編排功能。

本模組包含：

**AgentSociety** — 主模擬編排器：
- 管理 Agent 和 Environment 的生命週期
- 執行模擬步驟（tick-by-tick）
- 協調 Agent 與環境的互動
- 管理環境 replay writer，並協調 agent workspace 生命週期

**AgentSocietyHelper** — 計劃執行助手：
- 處理外部問題和干預
- 提供便捷的 ask/intervene 介面

**配置模型**：
- ``InitConfig``: 初始化配置（Agent、Env、LLM 配置等）
- ``StepsConfig``: 步驟配置（Ask/Intervene/Run 操作序列）
- ``AgentConfig``: Agent 配置
- ``EnvModuleConfig``: 環境模組配置

使用示例::

    from agentsociety2.society import AgentSociety, InitConfig, StepsConfig

    # 載入配置
    config = InitConfig.from_file("config.json")
    steps = StepsConfig.from_file("steps.yaml")

    # 建立並執行模擬
    society = AgentSociety(config)
    await society.run(steps)
"""

from .society import AgentSociety
from .helper import AgentSocietyHelper
from .models import (
    EnvModuleConfig,
    AgentConfig,
    InitConfig,
    RunStep,
    AskStep,
    InterveneStep,
    QuestionItem,
    QuestionnaireStep,
    StepUnion,
    StepsConfig,
)
from .questionnaire import (
    AgentQuestionnaireResult,
    Questionnaire,
    QuestionnaireAnswer,
    QuestionnaireResponse,
)

__all__ = [
    "AgentSociety",
    "AgentSocietyHelper",
    "EnvModuleConfig",
    "AgentConfig",
    "InitConfig",
    "RunStep",
    "AskStep",
    "InterveneStep",
    "QuestionItem",
    "QuestionnaireStep",
    "StepUnion",
    "StepsConfig",
    "Questionnaire",
    "QuestionnaireAnswer",
    "AgentQuestionnaireResult",
    "QuestionnaireResponse",
]
