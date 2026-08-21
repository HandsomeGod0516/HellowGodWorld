"""
数据分析子智能体模块：实验结果分析与报告生成。

分层：编排（Analyzer / Synthesizer）、数据（DataReader / ContextLoader）、
分析（`AnalysisAgent` 内嵌 LLM 流程；裁判类型见 models）、
输出（ReportWriter / Reporter / AssetManager / EDAGenerator）、
执行（CodeExecutor / ToolRegistry / AnalysisRunner）。

入口：`run_analysis`、`run_analysis_many`、`run_analysis_workflow`、`run_synthesis`。

人读文档见同目录 `README.md`（非扩展里的 `SKILL.md`）。
"""

from .models import (
    ExperimentStatus,
    ExperimentDesign,
    ExperimentContext,
    AnalysisResult,
    ReportContent,
    ReportAsset,
    AnalysisConfig,
    ContextSummary,
    AnalysisJudgment,
    StrategyJudgment,
    VisualizationJudgment,
    ExperimentSynthesis,
    HypothesisSummary,
    ExperimentPaths,
    PresentationPaths,
    # 路径常量
    DIR_HYPOTHESIS_PREFIX,
    DIR_EXPERIMENT_PREFIX,
    DIR_RUN,
    DIR_ARTIFACTS,
    DIR_CHARTS,
    DIR_PRESENTATION,
    DIR_SYNTHESIS,
    FILE_HYPOTHESIS_MD,
    FILE_EXPERIMENT_MD,
    FILE_SQLITE,
    LANG_ZH,
    LANG_EN,
    FILE_REPORT_ZH_MD,
    FILE_REPORT_ZH_HTML,
    FILE_REPORT_EN_MD,
    FILE_REPORT_EN_HTML,
    FILE_SYNTHESIS_REPORT_ZH_SUFFIX,
    FILE_SYNTHESIS_REPORT_EN_SUFFIX,
)

# 数据层
from .data import (
    DataReader,
    ContextLoader,
    DataSummary,
    DatabaseSchema,
    DataStats,
)

# 输出层
from .output import (
    ReportWriter,
    AssetManager,
    AssetProcessor,
    EDAGenerator,
    ReportPaths,
    ReportJudgment,
    Reporter,
    ReportGenerationResult,
)

# 执行器层
from .executor import (
    AnalysisRunner,
    CodeExecutor,
    CodeExecutionJudgment,
    ToolRegistry,
    ExecutionResult,
    ToolInfo,
    ToolResult,
)

from .utils import (
    AnalysisSkillMeta,
    XmlParseError,
    parse_llm_xml_response,
    parse_llm_xml_to_model,
    parse_llm_report_response,
    list_analysis_skills,
    get_analysis_skills,
    experiment_paths,
    presentation_paths,
    extract_database_schema,
    format_database_schema_markdown,
    collect_experiment_files,
    AnalysisProgressCallback,
)

from .service import (
    Analyzer,
    run_analysis,
    run_analysis_many,
    run_analysis_workflow,
    Synthesizer,
    run_synthesis,
)
from .agents import AnalysisAgent

__all__ = [
    # Models
    "ExperimentStatus",
    "ExperimentDesign",
    "ExperimentContext",
    "AnalysisResult",
    "ReportContent",
    "ReportAsset",
    "AnalysisConfig",
    "ContextSummary",
    "AnalysisJudgment",
    "StrategyJudgment",
    "VisualizationJudgment",
    "ExperimentSynthesis",
    "HypothesisSummary",
    "ExperimentPaths",
    "PresentationPaths",
    # 路径常量
    "DIR_HYPOTHESIS_PREFIX",
    "DIR_EXPERIMENT_PREFIX",
    "DIR_RUN",
    "DIR_ARTIFACTS",
    "DIR_CHARTS",
    "DIR_PRESENTATION",
    "DIR_SYNTHESIS",
    "FILE_HYPOTHESIS_MD",
    "FILE_EXPERIMENT_MD",
    "FILE_SQLITE",
    "LANG_ZH",
    "LANG_EN",
    "FILE_REPORT_ZH_MD",
    "FILE_REPORT_ZH_HTML",
    "FILE_REPORT_EN_MD",
    "FILE_REPORT_EN_HTML",
    "FILE_SYNTHESIS_REPORT_ZH_SUFFIX",
    "FILE_SYNTHESIS_REPORT_EN_SUFFIX",
    # 数据层
    "DataReader",
    "ContextLoader",
    "DataSummary",
    "DatabaseSchema",
    "DataStats",
    # 输出层
    "ReportWriter",
    "AssetManager",
    "AssetProcessor",
    "EDAGenerator",
    "ReportPaths",
    "ReportJudgment",
    "Reporter",
    "ReportGenerationResult",
    # 执行器层
    "AnalysisRunner",
    "CodeExecutor",
    "CodeExecutionJudgment",
    "ToolRegistry",
    "ExecutionResult",
    "ToolInfo",
    "ToolResult",
    # 工具函数
    "XmlParseError",
    "AnalysisSkillMeta",
    "parse_llm_xml_response",
    "parse_llm_xml_to_model",
    "parse_llm_report_response",
    "list_analysis_skills",
    "get_analysis_skills",
    "experiment_paths",
    "presentation_paths",
    "extract_database_schema",
    "format_database_schema_markdown",
    "collect_experiment_files",
    "AnalysisProgressCallback",
    "Analyzer",
    "run_analysis",
    "run_analysis_many",
    "run_analysis_workflow",
    "Synthesizer",
    "run_synthesis",
    "AnalysisAgent",
]
