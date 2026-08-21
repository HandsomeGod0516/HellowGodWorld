"""Pydantic models for AgentSociety2 experiment configuration validation"""

from datetime import datetime
from typing import Dict, Any, List, Union, Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "EnvModuleConfig",
    "AgentConfig",
    "CodeGenRouterConfig",
    "InitConfig",
    "RunStep",
    "AskStep",
    "InterveneStep",
    "QuestionItem",
    "QuestionnaireStep",
    "StepUnion",
    "StepsConfig",
]


class EnvModuleConfig(BaseModel):
    """環境模組配置模型"""
    
    module_type: str = Field(..., description="環境模組型別")
    kwargs: Dict[str, Any] = Field(default_factory=dict, description="環境模組初始化引數")


class AgentConfig(BaseModel):
    """Agent配置模型，匹配init_config.json中的格式"""
    
    agent_id: int = Field(..., description="Agent的唯一ID")
    agent_type: str = Field(..., description="Agent型別")
    kwargs: Dict[str, Any] = Field(..., description="Agent初始化引數，包含id、profile等所有引數")
    
    @field_validator("kwargs")
    @classmethod
    def validate_kwargs(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """驗證kwargs中必須包含id欄位"""
        if "id" not in v:
            raise ValueError("kwargs must contain 'id' field")
        return v


class CodeGenRouterConfig(BaseModel):
    """CodeGenRouter 配置模型"""

    final_summary_enabled: bool = Field(True, description="是否啟用 ask 最終 summary")


class InitConfig(BaseModel):
    """初始化配置檔案模型"""

    env_modules: List[EnvModuleConfig] = Field(..., min_length=1, description="環境模組列表")
    agents: List[AgentConfig] = Field(..., min_length=1, description="Agent列表")
    codegen_router: CodeGenRouterConfig = Field(
        default_factory=CodeGenRouterConfig,
        description="CodeGenRouter 配置",
    )


class RunStep(BaseModel):
    """執行指定步數的步驟"""
    
    type: Literal["run"] = Field("run", description="步驟型別")
    num_steps: int = Field(..., gt=0, description="執行的步數")
    tick: int = Field(1, gt=0, description="每步的時間間隔（秒）")


class AskStep(BaseModel):
    """提問步驟"""
    
    type: Literal["ask"] = Field("ask", description="步驟型別")
    question: str = Field(..., min_length=1, description="要提問的問題")


class InterveneStep(BaseModel):
    """干預步驟"""
    
    type: Literal["intervene"] = Field("intervene", description="步驟型別")
    instruction: str = Field(..., min_length=1, description="干預指令")


class QuestionItem(BaseModel):
    """單道問卷題目配置。"""

    id: str = Field(..., min_length=1, description="題目唯一標識")
    prompt: str = Field(..., min_length=1, description="題目提示文字")
    response_type: Literal["text", "integer", "float", "choice", "json"] = Field(
        "text",
        description="回答型別",
    )
    choices: List[str] = Field(default_factory=list, description="choice 題型可選項")

    @field_validator("choices")
    @classmethod
    def validate_choices(cls, value: List[str]) -> List[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return cleaned

    @field_validator("choices")
    @classmethod
    def validate_choice_question(cls, value: List[str], info) -> List[str]:
        if info.data.get("response_type") == "choice" and not value:
            raise ValueError("choices are required when response_type='choice'")
        return value


class QuestionnaireStep(BaseModel):
    """問卷步驟。"""

    type: Literal["questionnaire"] = Field("questionnaire", description="步驟型別")
    questionnaire_id: str = Field(..., min_length=1, description="問卷唯一標識")
    title: str | None = Field(None, description="問卷標題")
    description: str | None = Field(None, description="問卷說明")
    target_agent_ids: List[int] | None = Field(
        None,
        description="目標 Agent ID 列表；為空時發給全部 Agent",
    )
    questions: List[QuestionItem] = Field(..., min_length=1, description="題目列表")


StepUnion = Union[RunStep, AskStep, InterveneStep, QuestionnaireStep]


class StepsConfig(BaseModel):
    """Steps.yaml配置檔案模型"""
    
    start_t: str = Field(..., description="模擬開始時間（ISO格式）")
    steps: List[StepUnion] = Field(..., min_length=1, description="步驟列表")
    
    @field_validator("start_t")
    @classmethod
    def validate_start_t(cls, v: str) -> str:
        """驗證開始時間格式"""
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError(f"Invalid ISO datetime format: {v}")
        return v
