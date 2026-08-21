"""
标准化实验分析数据模型与统一配置。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


DIR_HYPOTHESIS_PREFIX = "hypothesis_"
DIR_EXPERIMENT_PREFIX = "experiment_"
DIR_RUN = "run"
DIR_ARTIFACTS = "artifacts"  # run/artifacts: 实验执行产物
DIR_PRESENTATION = "presentation"
DIR_SYNTHESIS = "synthesis"
DIR_DATA = "data"  # presentation 下 data: 分析子智能体写入的分析数据
DIR_CHARTS = "charts"  # presentation 下 charts: AnalysisAgent 写图目录
DIR_REPORT_ASSETS = "assets"  # presentation 下 assets: 报告嵌入资源（复制自 charts + run/artifacts），包括图表、报告、分析数据等
FILE_SQLITE = "sqlite.db"
FILE_PID = "pid.json"
FILE_HYPOTHESIS_MD = "HYPOTHESIS.md"
FILE_EXPERIMENT_MD = "EXPERIMENT.md"
FILE_REPORT_MD = "report.md"
FILE_REPORT_HTML = "report.html"
FILE_ANALYSIS_SUMMARY_JSON = "analysis_summary.json"
FILE_README_MD = "README.md"
FILE_SYNTHESIS_REPORT_PREFIX = "synthesis_report_"

# Language suffixes for bilingual reports
LANG_ZH = "zh"
LANG_EN = "en"

# Bilingual report file patterns
FILE_REPORT_ZH_MD = f"report_{LANG_ZH}.md"
FILE_REPORT_ZH_HTML = f"report_{LANG_ZH}.html"
FILE_REPORT_EN_MD = f"report_{LANG_EN}.md"
FILE_REPORT_EN_HTML = f"report_{LANG_EN}.html"
FILE_SYNTHESIS_REPORT_ZH_SUFFIX = f"_{LANG_ZH}"
FILE_SYNTHESIS_REPORT_EN_SUFFIX = f"_{LANG_EN}"

SUPPORTED_IMAGE_FORMATS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
SUPPORTED_ASSET_FORMATS = SUPPORTED_IMAGE_FORMATS | {".pdf"}


class ExperimentPaths(BaseModel):
    """单实验在 workspace 下的约定路径（只读，由 utils.experiment_paths 构建）。"""

    hypothesis_base: Path = Field(..., description="hypothesis_<id> directory")
    experiment_path: Path = Field(..., description="experiment_<id> directory")
    run_path: Path = Field(..., description="run directory")
    db_path: Path = Field(..., description="sqlite.db path")
    pid_path: Path = Field(..., description="pid.json path")
    assets_path: Path = Field(..., description="run/artifacts directory")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class PresentationPaths(BaseModel):
    """
    单实验分析产物的输出路径（presentation 下，由 utils.presentation_paths 构建）。

    生成产物布局：
    - output_dir/
      - report.md, report.html, README.md
      - data/analysis_summary.json
    - charts/  （AnalysisAgent 写图目录，再被复制到 assets）
      - assets/  （报告引用的图片，DIR_REPORT_ASSETS）
    """

    output_dir: Path = Field(
        ..., description="presentation/hypothesis_<id>/experiment_<id>"
    )
    charts_dir: Path = Field(
        ..., description="Charts output directory (AnalysisAgent writes here)"
    )
    report_assets_dir: Path = Field(
        ..., description="Report assets directory (assets/, referenced by report)"
    )
    report_md: Path = Field(..., description="Markdown report path")
    report_html: Path = Field(..., description="HTML report path")
    result_json: Path = Field(..., description="analysis_summary.json path")
    readme: Path = Field(..., description="README.md path")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ExperimentStatus(str, Enum):
    """实验执行的状态"""

    SUCCESSFUL = "successful"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class ExperimentDesign(BaseModel):
    """实验设计"""

    hypothesis: str = Field(..., description="Primary hypothesis being tested")
    objectives: List[str] = Field(
        default_factory=list, description="Experiment objectives"
    )
    variables: Dict[str, Any] = Field(default_factory=dict, description="Variables")
    methodology: str = Field(default="", description="Experimental methodology")
    success_criteria: List[str] = Field(
        default_factory=list, description="Success criteria"
    )

    hypothesis_markdown: Optional[str] = Field(
        default=None, description="Raw content of HYPOTHESIS.md if available"
    )
    experiment_markdown: Optional[str] = Field(
        default=None, description="Raw content of EXPERIMENT.md if available"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ExperimentContext(BaseModel):
    """完整实验状态"""

    experiment_id: str = Field(..., description="Experiment identifier")
    hypothesis_id: str = Field(..., description="Hypothesis identifier")
    design: ExperimentDesign = Field(..., description="Experiment design")

    duration_seconds: Optional[float] = Field(None, description="Duration in seconds")
    execution_status: ExperimentStatus = Field(
        default=ExperimentStatus.UNKNOWN, description="Execution status"
    )
    completion_percentage: float = Field(
        default=0.0, description="Completion percentage"
    )
    error_messages: List[str] = Field(
        default_factory=list, description="Error messages"
    )


class AnalysisResult(BaseModel):
    """实验分析结果"""

    experiment_id: str = Field(..., description="Experiment identifier")
    hypothesis_id: str = Field(..., description="Hypothesis identifier")

    insights: List[Any] = Field(default_factory=list, description="Generated insights")
    findings: List[Any] = Field(default_factory=list, description="Key findings")
    conclusions: Any = Field(default="", description="Conclusions")
    recommendations: List[Any] = Field(
        default_factory=list, description="Recommendations"
    )

    generated_at: datetime = Field(
        default_factory=datetime.now, description="Generation time"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ReportContent(BaseModel):
    """报告内容（支持中英双语）"""

    title: str = Field(..., description="Report title")
    subtitle: str = Field(default="", description="Report subtitle")
    format_preference: str = Field(
        default="markdown", description="Preferred format: markdown, html, or both"
    )
    # 双语字段
    full_content_markdown_zh: Optional[str] = Field(
        default=None, description="Chinese markdown report content"
    )
    full_content_html_zh: Optional[str] = Field(
        default=None, description="Chinese HTML report content"
    )
    full_content_markdown_en: Optional[str] = Field(
        default=None, description="English markdown report content"
    )
    full_content_html_en: Optional[str] = Field(
        default=None, description="English HTML report content"
    )

    @property
    def full_content_markdown(self) -> Optional[str]:
        """中文优先，否则英文。"""
        return self.full_content_markdown_zh or self.full_content_markdown_en

    @property
    def full_content_html(self) -> Optional[str]:
        """中文优先，否则英文。"""
        return self.full_content_html_zh or self.full_content_html_en

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ReportAsset(BaseModel):
    """报告所需要的其他资源"""

    asset_id: str = Field(..., description="Asset identifier")
    asset_type: str = Field(..., description="Asset type")
    title: str = Field(..., description="Asset title")
    description: str = Field(default="", description="Asset description")

    file_path: str = Field(..., description="File path")
    embedded_content: Optional[str] = Field(None, description="Base64 content")
    file_size: int = Field(default=0, description="File size in bytes")

    created_at: datetime = Field(
        default_factory=datetime.now, description="Creation time"
    )
    dimensions: Optional[Dict[str, int]] = Field(None, description="Dimensions")

    model_config = ConfigDict(arbitrary_types_allowed=True)


@dataclass
class ContextSummary:
    """工具迭代时压缩后的上下文（供策略调整 prompt）。"""

    key_findings: List[str] = field(default_factory=list)
    failed_attempts: List[str] = field(default_factory=list)
    successful_tools: List[str] = field(default_factory=list)
    recommendations: str = ""
    iteration_count: int = 0


class AnalysisJudgment(BaseModel):
    """洞察阶段 LLM 裁判输出。"""

    success: bool
    reason: str
    should_retry: bool = False
    retry_instruction: str = ""


class StrategyJudgment(BaseModel):
    """分析策略阶段裁判输出。"""

    success: bool
    reason: str
    should_retry: bool = False
    retry_instruction: str = ""


class VisualizationJudgment(BaseModel):
    """可视化计划阶段裁判输出。"""

    success: bool
    reason: str
    should_retry: bool = False
    retry_instruction: str = ""


class AnalysisConfig(BaseModel):
    """分析子智能体统一配置，各组件均从此读取。"""

    workspace_path: str = Field(..., description="Workspace path")
    max_analysis_retries: int = Field(
        5,
        ge=1,
        le=20,
        description="Max retries for analysis/report generation",
    )
    max_strategy_retries: int = Field(
        3,
        ge=1,
        le=10,
        description="Max retries for analysis strategy judgment",
    )
    max_visualization_retries: int = Field(
        3,
        ge=1,
        le=10,
        description="Max retries for visualization judgment",
    )
    max_tool_iterations: int = Field(
        3,
        ge=1,
        le=10,
        description="Max iterations for tool execution loop",
    )
    max_synthesis_report_retries: int = Field(
        3,
        ge=1,
        le=10,
        description="Max retries for synthesis report generation",
    )
    max_code_gen_retries: int = Field(
        5,
        ge=1,
        le=20,
        description="Max retries for code generation in tool executor",
    )
    completion_success_threshold: float = Field(
        70.0,
        ge=0.0,
        le=100.0,
        description="Completion percentage threshold for successful experiment",
    )
    temperature: float = Field(
        0.7,
        ge=0.0,
        le=2.0,
        description="LLM temperature",
    )
    code_execution_timeout: int = Field(
        600,
        ge=60,
        le=3600,
        description="Code executor timeout in seconds",
    )
    synthesis_output_dir_name: str = Field(
        default=DIR_SYNTHESIS,
        description="Subdir under workspace for synthesis reports",
    )
    llm_profile_default: str = Field(
        default="default",
        description="LLM profile for simple tasks (fallback)",
    )
    llm_profile_analysis: str = Field(
        default="analysis",
        description="LLM profile for analysis, insight generation, and report writing. Use a capable model.",
    )
    llm_profile_coder: str = Field(
        default="coder",
        description="LLM profile for code generation",
    )
    analysis_skill_names: List[str] = Field(
        default_factory=lambda: [
            "tool_catalog",
            "subagent_workflow",
            "visualization_reliability",
            "core_skills",
            "advanced_analysis",
        ],
        description=(
            "instruction_md 条目的 name（不含 frontmatter 注入正文）。"
            "标记 required 的片段（如 xml_contract）始终注入，无需出现在此列表。"
        ),
    )
    analysis_skill_strict_selection: bool = Field(
        default=True,
        description=(
            "True：只注入 required + 本列表中的条目。False：未指定列表时注入全部 instruction_md。"
        ),
    )

    @field_validator("workspace_path")
    @classmethod
    def validate_workspace_path(cls, v):
        path = Path(v)
        if not path.exists():
            raise ValueError(f"Workspace path does not exist: {v}")
        return str(path.absolute())

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
    )


class HypothesisSummary(BaseModel):
    """跨多个实验的分析结果"""

    hypothesis_id: str = Field(..., description="Hypothesis identifier")
    hypothesis_text: str = Field(..., description="Hypothesis text")

    experiment_count: int = Field(default=0, description="Number of experiments")
    successful_experiments: int = Field(
        default=0, description="Number of successful experiments"
    )
    total_completion: float = Field(
        default=0.0, description="Average completion percentage"
    )

    key_insights: List[str] = Field(default_factory=list, description="Key insights")
    main_findings: List[str] = Field(default_factory=list, description="Main findings")
    experiment_results: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Analysis results for each experiment",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ExperimentSynthesis(BaseModel):
    """多假设/多实验的综合结果。"""

    synthesis_id: str = Field(..., description="Synthesis identifier")
    workspace_path: str = Field(..., description="Workspace path")
    synthesis_timestamp: datetime = Field(
        default_factory=datetime.now, description="Analysis timestamp"
    )

    hypothesis_summaries: List[HypothesisSummary] = Field(
        default_factory=list,
        description="Summary information for each hypothesis",
    )

    synthesis_strategy: str = Field(
        default="", description="Analysis strategy decided by LLM"
    )
    cross_hypothesis_analysis: str = Field(
        default="", description="Cross-hypothesis analysis"
    )
    comparative_insights: List[str] = Field(
        default_factory=list, description="Comparative insights"
    )
    unified_conclusions: str = Field(default="", description="Unified conclusions")
    recommendations: List[str] = Field(
        default_factory=list, description="Comprehensive recommendations"
    )

    best_hypothesis: Optional[str] = Field(
        None, description="Best hypothesis identifier"
    )
    best_hypothesis_reason: str = Field(
        default="", description="Reason for best hypothesis"
    )
    overall_assessment: str = Field(default="", description="Overall assessment")

    synthesis_report_path: Optional[str] = Field(
        None, description="Synthesis report Markdown file path"
    )
    synthesis_report_html_path: Optional[str] = Field(
        None, description="Synthesis report HTML file path"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)
