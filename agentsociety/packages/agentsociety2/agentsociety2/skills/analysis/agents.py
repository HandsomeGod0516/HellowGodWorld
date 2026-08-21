"""`AnalysisAgent`：資料優先、多階段洞察 + ReAct 工具環 + 視覺化與上下文壓縮。"""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import json_repair
from agentsociety2.logger import get_logger
from agentsociety2.config import get_llm_router_and_model, get_model_name
from litellm import AllMessageValues

from .models import (
    ExperimentContext,
    AnalysisResult,
    AnalysisConfig,
    AnalysisJudgment,
    StrategyJudgment,
    VisualizationJudgment,
    ContextSummary,
    SUPPORTED_ASSET_FORMATS,
    DIR_DATA,
)
from .llm_contracts import (
    judgment_prompt,
    analysis_xml_contract,
    strategy_xml_contract,
    adjust_tools_xml_contract,
    visualization_xml_contract,
    summary_xml_contract,
)
from .utils import (
    XmlParseError,
    parse_llm_xml_response,
    parse_llm_xml_to_model,
    get_analysis_skills,
    collect_experiment_files,
    AnalysisProgressCallback,
)
from .data import DataReader, DataSummary
from .executor import AnalysisRunner
from .output import EDAGenerator


def _system_with_skills(config: Optional[AnalysisConfig] = None) -> str:
    """返回分析子智慧體的技能說明，並要求返回XML格式。"""
    selected_names = config.analysis_skill_names if config else None
    strict_selection = config.analysis_skill_strict_selection if config else True
    skills = get_analysis_skills(
        selected_names=selected_names,
        strict_selection=strict_selection,
    )
    base = "Return only the XML the prompt requests."
    return f"{skills}\n\n---\n\n{base}" if skills else base


class AnalysisAgent:
    """
    統一分析智慧體：資料優先的分析流程。

    流程：
    1. 讀取並理解資料結構
    2. 基於實際資料生成洞察
    3. 決定分析策略和視覺化方案
    4. 執行資料分析程式碼
    5. 生成視覺化圖表

    併發安全：
    - 例項變數均為只讀或不可變（config, workspace_path, llm_router, model_name）
    - 每次分析呼叫建立獨立的 AnalysisRunner、DataReader 等區域性變數
    - 臨時目錄透過 tempfile.mkdtemp 建立，互不干擾
    - 可安全用於 asyncio 併發任務
    """

    def __init__(
        self,
        config: AnalysisConfig,
        llm_router=None,
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
        workspace_path: Optional[Path] = None,
    ):
        """初始化統一分析智慧體。"""
        self.logger = get_logger()
        self.config = config
        self.temperature = (
            temperature if temperature is not None else config.temperature
        )
        self.workspace_path = workspace_path or Path.cwd()
        self.max_retries = max(1, min(20, config.max_analysis_retries))

        # LLM 配置：分析使用 analysis profile，程式碼生成使用 coder profile
        profile = config.llm_profile_analysis
        if llm_router is None:
            self.llm_router, self.model_name = get_llm_router_and_model(profile)
        else:
            self.llm_router = llm_router
            self.model_name = (
                model_name if model_name is not None else get_model_name(profile)
            )

        self.logger.info("統一分析智慧體初始化完成，使用模型: %s", self.model_name)

    async def analyze(
        self,
        context: ExperimentContext,
        db_path: Optional[Path],
        output_dir: Path,
        custom_instructions: Optional[str] = None,
        literature_summary: Optional[str] = None,
        on_progress: AnalysisProgressCallback = None,
    ) -> Tuple[AnalysisResult, Dict[str, Any]]:
        """
        執行完整的資料分析流程。

        Returns:
            (AnalysisResult, 資料分析產物字典)
        """
        self.logger.info("開始分析實驗 %s", context.experiment_id)

        async def progress(msg: str) -> None:
            if on_progress:
                await on_progress(msg)

        # Step 1: 讀取並理解資料
        data_summary = DataSummary()
        if db_path and db_path.exists():
            await progress("Reading and understanding data structure...")
            data_summary = DataReader(db_path).read_full_summary()
            self.logger.info(
                "資料理解完成: %s 個表, 總行數: %s",
                len(data_summary.tables),
                sum(data_summary.row_counts.values()),
            )
        else:
            self.logger.info("未找到資料庫，跳過資料分析")

        # Step 2: 基於資料生成洞察
        await progress("Generating insights from data...")
        analysis_result = await self._generate_insights_with_data(
            context,
            data_summary,
            custom_instructions,
            literature_summary,
        )

        # Step 3: 執行資料分析和視覺化
        data_analysis_result: Dict[str, Any] = {
            "analysis_plan": {},
            "tool_results": {},
            "visualization_plan": [],
            "generated_charts": [],
            "eda_profile_path": None,
            "eda_sweetviz_path": None,
        }

        if db_path and db_path.exists():
            await progress("Planning data analysis...")
            tool_executor = AnalysisRunner(
                self.workspace_path,
                output_dir,
                tool_registry=None,
                config=self.config,
            )

            # Step 3.1: 決定分析策略
            analysis_plan = await self._decide_analysis_strategy_with_judgment(
                context, analysis_result, data_summary, tool_executor, on_progress
            )
            data_analysis_result["analysis_plan"] = analysis_plan

            # Step 3.2: 執行工具
            if analysis_plan.get("tools_to_use"):
                await progress("Running data analysis tools...")
                tool_results = await self._execute_tools_with_feedback(
                    tool_executor,
                    analysis_plan.get("tools_to_use", []),
                    db_path,
                    output_dir,
                    context,
                    analysis_result,
                    data_summary,
                    on_progress=on_progress,
                )
                data_analysis_result["tool_results"] = tool_results

                # 提取 EDA 路徑
                data_analysis_result["eda_profile_path"] = tool_results.get(
                    "eda_profile", {}
                ).get("path")
                data_analysis_result["eda_sweetviz_path"] = tool_results.get(
                    "eda_sweetviz", {}
                ).get("path")

            # Step 3.3: 生成視覺化
            await progress("Generating visualizations...")
            viz_plan, charts = (
                await self._decide_and_generate_visualizations_with_judgment(
                    context,
                    analysis_result,
                    data_summary,
                    data_analysis_result["tool_results"],
                    db_path,
                    output_dir,
                    tool_executor,
                    on_progress=on_progress,
                )
            )
            data_analysis_result["visualization_plan"] = viz_plan
            data_analysis_result["generated_charts"] = charts

        # 將 data_summary 新增到返回結果中，供後續報告生成使用
        data_analysis_result["data_summary"] = data_summary

        return analysis_result, data_analysis_result

    async def _generate_insights_with_data(
        self,
        context: ExperimentContext,
        data_summary: DataSummary,
        custom_instructions: Optional[str] = None,
        literature_summary: Optional[str] = None,
    ) -> AnalysisResult:
        """基於實際資料生成洞察。

        使用 LLM 總結長文件，確保洞察基於實際資料結構，避免幻覺。
        支援重試機制，累積錯誤歷史以提高迭代效率。

        Args:
            context: 實驗上下文，包含假設和實驗設計資訊。
            data_summary: 資料摘要，包含 schema 和行數資訊。
            custom_instructions: 自定義分析指令，可選。
            literature_summary: 文獻摘要，可選。

        Returns:
            AnalysisResult 物件，包含 insights、findings、conclusions 等。
        """
        hypothesis_md_block = ""
        if getattr(context.design, "hypothesis_markdown", None):
            hyp_md = await self._summarize_document(
                context.design.hypothesis_markdown,
                "hypothesis",
                max_length=800,
            )
            hypothesis_md_block = f"\n## Hypothesis Document\n\n```markdown\n{hyp_md}\n```\n"

        experiment_md_block = ""
        if getattr(context.design, "experiment_markdown", None):
            exp_md = await self._summarize_document(
                context.design.experiment_markdown,
                "experiment design",
                max_length=800,
            )
            experiment_md_block = f"\n## Experiment Design Document\n\n```markdown\n{exp_md}\n```\n"

        literature_block = ""
        if literature_summary and literature_summary.strip():
            lit = await self._summarize_document(
                literature_summary.strip(),
                "literature context",
                max_length=600,
            )
            literature_block = f"\n## Literature Context\n\n{lit}\n"

        data_block = ""
        if data_summary.schema_markdown:
            schema_md = await self._summarize_schema(
                data_summary.schema_markdown,
                data_summary.row_counts,
            )
            quick_stats = data_summary.quick_stats
            if len(quick_stats) > 1500:
                quick_stats = quick_stats[:1500] + "\n...[more stats available]"

            data_block = f"""
## Actual Data Structure

**CRITICAL - DATA-FIRST PRINCIPLE**:
- You MUST base your insights on the ACTUAL data structure below.
- Do NOT invent tables, columns, or values that are not shown here.
- If tables are empty or sparse, explicitly acknowledge this limitation.
- Reference actual table/column names and row counts in your insights.

{schema_md}

{quick_stats}

**Data Quality Notes**:
- Total tables: {len(data_summary.tables)}
- Non-empty tables: {sum(1 for t in data_summary.tables if data_summary.row_counts.get(t, 0) > 0)}
- Empty tables: {sum(1 for t in data_summary.tables if data_summary.row_counts.get(t, 0) == 0)}
"""

        custom_block = ""
        if custom_instructions:
            custom_block = f"\n## Custom Instructions\n\n{custom_instructions}\n"

        errors_text = "None"
        if context.error_messages:
            errors = [str(e)[:150] for e in context.error_messages[:3]]
            errors_text = "\n".join([f"- {e}" for e in errors])
            if len(context.error_messages) > 3:
                errors_text += f"\n... ({len(context.error_messages) - 3} more errors)"

        prompt = f"""## Experiment Context

**Experiment ID**: {context.experiment_id} | **Hypothesis ID**: {context.hypothesis_id}
**Hypothesis**: {context.design.hypothesis}
**Status**: {context.execution_status.value} | **Completion**: {context.completion_percentage:.1f}% | **Duration**: {f"{context.duration_seconds:.2f}s" if context.duration_seconds else "Unknown"}
**Errors**: {errors_text}

{hypothesis_md_block}{experiment_md_block}{data_block}{literature_block}{custom_block}

Based on the experiment context and **actual data structure above**, generate analysis insights.

**CRITICAL**: Your insights must be grounded in the actual data available. If tables are empty or data is limited, acknowledge this and provide appropriate caveats.

{analysis_xml_contract()}"""

        messages: List[AllMessageValues] = []
        skills = _system_with_skills(self.config)
        if skills:
            messages.append({"role": "system", "content": skills})
        messages.append({"role": "user", "content": prompt})

        parsed: Optional[Dict[str, Any]] = None
        error_history: list[str] = []  # 累積錯誤歷史
        
        for attempt in range(self.max_retries):
            self.logger.info(
                "生成分析結果 (第 %s/%s 次嘗試)",
                attempt + 1,
                self.max_retries,
            )
            try:
                response = await self.llm_router.acompletion(
                    model=self.model_name,
                    messages=messages,
                    temperature=self.temperature,
                )
                raw = response.choices[0].message.content or ""
                parsed = self._parse_analysis_response(raw)
                judgment = await self._judge_analysis_result(
                    parsed, context, data_summary
                )
            except XmlParseError as e:
                if attempt >= self.max_retries - 1:
                    self.logger.warning(
                        "XML解析失敗，嘗試 %s 次後失敗: %s", self.max_retries, e
                    )
                    raise
                # 用 LLM 總結錯誤資訊，生成有效反饋
                raw_content = getattr(e, 'raw_content', None)
                error_summary = await self._summarize_error(e, raw_content)
                error_history.append(f"解析錯誤: {error_summary}")
                
                # 構建累積錯誤的反饋
                history_note = ""
                if len(error_history) > 1:
                    history_note = "\n\n**之前的問題**（請避免重複）:\n" + "\n".join(f"- {err}" for err in error_history[:-1])
                
                feedback = (
                    f"Your previous output had a parsing error: {error_summary}\n"
                    f"{history_note}\n\n"
                    f"Please fix and return valid XML with this structure:\n"
                    f"<analysis>\n"
                    f"  <insights><item>...</item></insights>\n"
                    f"  <findings><item>...</item></findings>\n"
                    f"  <conclusions>...</conclusions>\n"
                    f"  <recommendations><item>...</item></recommendations>\n"
                    f"</analysis>\n\n"
                    f"Return ONLY the corrected XML."
                )
                messages.append({"role": "user", "content": feedback})
                continue

            if (
                judgment.success
                or not judgment.should_retry
                or attempt >= self.max_retries - 1
            ):
                break

            self.logger.info(
                "分析結果需要改進 (第 %s 次嘗試): %s",
                attempt + 1,
                judgment.reason,
            )
            
            # 累積錯誤歷史
            error_history.append(judgment.reason)
            
            # 構建包含歷史錯誤的具體反饋
            history_note = ""
            if len(error_history) > 1:
                history_note = (
                    "\n\n**之前的問題**（已修復或無需處理）:\n"
                    + "\n".join(f"- {err}" for err in error_history[:-1])
                )
            
            # 提取當前輸出的關鍵問題
            current_output_summary = ""
            if parsed:
                insights_count = len(parsed.get("insights", []))
                findings_count = len(parsed.get("findings", []))
                conclusions = parsed.get("conclusions", "")[:200]
                current_output_summary = (
                    f"\n\n**你上次的輸出**:\n"
                    f"- {insights_count} 條洞察\n"
                    f"- {findings_count} 條發現\n"
                    f"- 結論: {conclusions}...\n"
                    f"請針對上述問題進行改進，不要重複已有的內容。"
                )
            
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Previous output needs improvement: {judgment.reason}\n"
                        f"{judgment.retry_instruction}\n"
                        f"{history_note}"
                        f"{current_output_summary}\n\n"
                        f"Return corrected XML only."
                    ),
                }
            )

        if not parsed:
            parsed = {}

        return AnalysisResult(
            experiment_id=context.experiment_id,
            hypothesis_id=context.hypothesis_id,
            insights=parsed.get("insights", []),
            findings=parsed.get("findings", []),
            conclusions=parsed.get("conclusions", ""),
            recommendations=parsed.get("recommendations", []),
            generated_at=datetime.now(),
        )

    def _parse_analysis_response(self, content: str) -> Dict[str, Any]:
        """解析分析結果。使用 xenon 修復後的 XML 解析。"""
        from .utils import parse_llm_json_response
        
        # 嘗試 XML 解析（xenon 會自動修復）
        try:
            data = parse_llm_xml_response(content, root_tag="analysis")
        except XmlParseError as e:
            # XML 完全失敗，嘗試 JSON 解析
            try:
                data = parse_llm_json_response(content)
            except Exception as json_err:
                # 都失敗，丟擲詳細錯誤資訊供迭代反饋
                raise XmlParseError(
                    f"Both XML and JSON parsing failed. "
                    f"XML error: {e}. JSON error: {json_err}. "
                    f"Please ensure output is valid XML with <analysis> root tag "
                    f"containing: insights, findings, conclusions, recommendations.",
                    raw_content=content
                )
        
        insights = data.get("insights", [])
        findings = data.get("findings", [])
        recs = data.get("recommendations", [])

        if isinstance(insights, dict) and "item" in insights:
            insights = (
                insights["item"]
                if isinstance(insights["item"], list)
                else [insights["item"]]
            )
        if isinstance(findings, dict) and "item" in findings:
            findings = (
                findings["item"]
                if isinstance(findings["item"], list)
                else [findings["item"]]
            )
        if isinstance(recs, dict) and "item" in recs:
            recs = recs["item"] if isinstance(recs["item"], list) else [recs["item"]]

        return {
            "insights": (
                insights
                if isinstance(insights, list)
                else [insights] if insights else []
            ),
            "findings": (
                findings
                if isinstance(findings, list)
                else [findings] if findings else []
            ),
            "conclusions": data.get("conclusions", "") or "",
            "recommendations": (
                recs if isinstance(recs, list) else [recs] if recs else []
            ),
        }

    @staticmethod
    def _format_items_for_judgment(items: list, max_items: int = 5, max_len: int = 200) -> str:
        """格式化列表項供裁判檢視。

        限制數量和長度避免 prompt 過長。

        Args:
            items: 待格式化的列表項。
            max_items: 最大顯示數量，預設 5。
            max_len: 每項最大長度，預設 200。

        Returns:
            格式化後的字串，每項一行，帶序號。
        """
        if not items:
            return "(none)"
        formatted = []
        for i, item in enumerate(items[:max_items]):
            text = str(item)[:max_len]
            formatted.append(f"  {i+1}. {text}")
        if len(items) > max_items:
            formatted.append(f"  ... and {len(items) - max_items} more items")
        return "\n".join(formatted)

    @staticmethod
    def _format_tools_for_judgment(tools: list, max_tools: int = 10) -> str:
        """格式化工具列表供裁判檢視。

        Args:
            tools: 工具字典列表，每項包含 tool_name、tool_type、action。
            max_tools: 最大顯示數量，預設 10。

        Returns:
            格式化後的字串，每項一行，顯示工具型別和名稱。
        """
        if not tools:
            return "  (no tools)"
        formatted = []
        for i, tool in enumerate(tools[:max_tools]):
            tool_name = tool.get("tool_name", "unknown")
            tool_type = tool.get("tool_type", "unknown")
            action = str(tool.get("action", ""))[:150]
            formatted.append(f"  {i+1}. [{tool_type}] {tool_name}: {action}")
        if len(tools) > max_tools:
            formatted.append(f"  ... and {len(tools) - max_tools} more tools")
        return "\n".join(formatted)

    @staticmethod
    def _format_viz_plan_for_judgment(viz_plan: list, max_items: int = 8) -> str:
        """格式化視覺化計劃供裁判檢視。

        Args:
            viz_plan: 視覺化計劃字典列表。
            max_items: 最大顯示數量，預設 8。

        Returns:
            格式化後的字串，每項一行，顯示工具名和描述。
        """
        if not viz_plan:
            return "  (no visualization plan)"
        formatted = []
        for i, item in enumerate(viz_plan[:max_items]):
            tool_name = item.get("tool_name", "unknown")
            desc = str(item.get("tool_description", ""))[:150]
            use_tool = item.get("use_tool", "unknown")
            formatted.append(f"  {i+1}. [{use_tool}] {tool_name}: {desc}")
        if len(viz_plan) > max_items:
            formatted.append(f"  ... and {len(viz_plan) - max_items} more items")
        return "\n".join(formatted)

    @staticmethod
    def _build_strategy_feedback(
        error_history: list[str],
        current_reason: Optional[str],
        retry_instruction: Optional[str],
    ) -> str:
        """構建策略重試的反饋內容。

        將歷史錯誤、當前問題和重試指令組合成完整的反饋資訊，
        供 LLM 在下一次迭代時參考。

        Args:
            error_history: 累積的錯誤歷史列表。
            current_reason: 當前失敗原因。
            retry_instruction: 重試指令。

        Returns:
            格式化後的反饋字串。
        """
        parts = []
        if error_history:
            parts.append("Previous issues (avoid these):")
            for i, err in enumerate(error_history[-3:]):  # 只保留最近3個
                parts.append(f"  {i+1}. {err}")
        if current_reason:
            parts.append(f"Current issue: {current_reason}")
        if retry_instruction:
            parts.append(f"Instruction: {retry_instruction}")
        return "\n".join(parts) if parts else ""

    @staticmethod
    def _build_viz_feedback(error_history: list[str]) -> str:
        """構建視覺化重試的反饋內容。

        將累積的錯誤歷史格式化為反饋資訊，供 LLM 在下一次迭代時參考。

        Args:
            error_history: 累積的錯誤歷史列表。

        Returns:
            格式化後的反饋字串，最多顯示最近 3 個錯誤。
        """
        if not error_history:
            return ""
        parts = ["**Previous issues (avoid these)**:"]
        for i, err in enumerate(error_history[-3:]):
            parts.append(f"  {i+1}. {err}")
        return "\n".join(parts)

    async def _judge_analysis_result(
        self,
        parsed: Dict[str, Any],
        context: ExperimentContext,
        data_summary: DataSummary,
    ) -> AnalysisJudgment:
        """判斷分析結果是否合理。

        檢查 LLM 生成的洞察、發現、結論是否基於實際資料，是否與假設相關，
        是否存在幻覺（引用不存在的表/列）等問題。

        Args:
            parsed: 解析後的分析結果字典，包含 insights、findings、conclusions 等。
            context: 實驗上下文，包含假設資訊。
            data_summary: 資料摘要，包含 schema 和行數資訊。

        Returns:
            AnalysisJudgment 物件，包含 success、reason、should_retry 等欄位。

        Note:
            data_summary.schema_markdown 已在 _generate_insights_with_data 中被 LLM 總結過。
        """
        hypothesis_preview = (context.design.hypothesis or "")[:300]

        # schema_markdown 已被 LLM 總結，直接使用
        schema_preview = data_summary.schema_markdown or "No data available"
        if len(schema_preview) > 1000:
            # 如果仍然很長，說明原始資料量極大，保留關鍵部分
            schema_preview = schema_preview[:1000] + "\n...[schema summarized]"

        # 構建資料摘要
        total_rows = sum(data_summary.row_counts.values())
        non_empty_tables = [
            t for t in data_summary.tables if data_summary.row_counts.get(t, 0) > 0
        ]
        empty_tables = [
            t for t in data_summary.tables if data_summary.row_counts.get(t, 0) == 0
        ]

        prompt = f"""Evaluate the analysis for experiment {context.experiment_id}.

**Hypothesis**: {hypothesis_preview}

**Available Data Summary**:
- Total tables: {len(data_summary.tables)}
- Non-empty tables: {non_empty_tables}
- Empty tables: {empty_tables}
- Total rows: {total_rows}

**Schema**:
{schema_preview}

**Generated Analysis**:

Insights ({len(parsed.get("insights", []))} items):
{self._format_items_for_judgment(parsed.get("insights", []))}

Findings ({len(parsed.get("findings", []))} items):
{self._format_items_for_judgment(parsed.get("findings", []))}

Conclusions:
{(parsed.get("conclusions") or "(none)")[:500]}

Recommendations ({len(parsed.get("recommendations", []))} items):
{self._format_items_for_judgment(parsed.get("recommendations", []))}

**Checklist**:
1. Substantive content? (not generic placeholders like "need more data")
2. Relevant to hypothesis? (insights address the research question)
3. Data-grounded? (insights reference actual table/column names from schema above)
4. No hallucination? (no mention of tables/columns not in the schema)
5. Data limitations acknowledged? (if tables are empty/sparse, this should be noted)
6. Conclusions reasonable? (follows from the findings)

{judgment_prompt()}"""

        response = await self.llm_router.acompletion(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
        )
        content = response.choices[0].message.content or ""
        return parse_llm_xml_to_model(content, AnalysisJudgment, root_tag="judgment")

    # ========== 分析策略與視覺化方法 ==========

    async def _decide_analysis_strategy_with_judgment(
        self,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        data_summary: DataSummary,
        tool_executor: AnalysisRunner,
        on_progress: Optional[AnalysisProgressCallback],
    ) -> Dict[str, Any]:
        """決定分析策略，經 LLM 裁判透過後返回。

        支援重試機制，累積錯誤歷史以提高迭代效率。每次重試都會
        把之前的問題反饋給 LLM，避免重複錯誤。

        Args:
            context: 實驗上下文。
            analysis_result: 初始分析結果。
            data_summary: 資料摘要。
            tool_executor: 工具執行器。
            on_progress: 進度回撥函式，可選。

        Returns:
            分析計劃字典，包含 analysis_strategy 和 tools_to_use。

        Raises:
            XmlParseError: 重試次數耗盡仍未透過裁判。
        """
        max_retries = self.config.max_strategy_retries
        error_history: list[str] = []
        previous_feedback: Optional[str] = None
        
        for attempt in range(max_retries):
            try:
                analysis_plan = await self._decide_analysis_strategy(
                    context, analysis_result, data_summary, tool_executor,
                    previous_feedback=previous_feedback,
                )
                judgment = await self._judge_analysis_strategy(
                    analysis_plan, context, data_summary
                )
            except XmlParseError as e:
                if attempt >= max_retries - 1:
                    self.logger.warning("分析策略XML解析失敗: %s", e)
                    raise
                error_summary = f"XML解析錯誤: {str(e)[:200]}"
                error_history.append(error_summary)
                previous_feedback = self._build_strategy_feedback(
                    error_history, None, None
                )
                if on_progress:
                    await on_progress(f"Strategy XML parse failed, retrying: {e}")
                continue

            if (
                judgment.success
                or not judgment.should_retry
                or attempt >= max_retries - 1
            ):
                return analysis_plan

            error_history.append(judgment.reason)
            self.logger.info(
                "分析策略需要改進 (第 %s 次嘗試): %s",
                attempt + 1,
                judgment.reason,
            )
            if on_progress:
                await on_progress(f"Strategy needs improvement: {judgment.reason}")
            
            # 構建下一次生成的反饋
            previous_feedback = self._build_strategy_feedback(
                error_history, judgment.reason, judgment.retry_instruction
            )

        raise XmlParseError("Strategy retries exhausted", raw_content="")

    async def _decide_analysis_strategy(
        self,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        data_summary: DataSummary,
        tool_executor: AnalysisRunner,
        previous_feedback: Optional[str] = None,
    ) -> Dict[str, Any]:
        """決定分析策略，選表/選工具。

        使用 LLM 總結大型 schema 而非簡單截斷，確保策略基於完整的資料理解。

        Args:
            context: 實驗上下文。
            analysis_result: 初始分析結果，提供已有洞察。
            data_summary: 資料摘要，包含 schema 和行數資訊。
            tool_executor: 工具執行器，用於發現可用工具。
            previous_feedback: 上一次重試的反饋資訊，可選。

        Returns:
            分析計劃字典，包含:
            - analysis_strategy: 分析策略描述
            - tools_to_use: 要執行的工具列表
        """
        available_tools = tool_executor.discover_tools_with_schemas()
        builtin = {
            k: v for k, v in available_tools.items() if v.get("type") == "builtin"
        }
        tools_list = (
            self._format_tools_list(builtin) if builtin else "No built-in tools"
        )

        # 新增 EDA 工具說明
        if data_summary.db_path:
            eda_tools = [
                "- **eda_profile** (tool_type=eda_profile): Generate EDA report via ydata-profiling.",
                "- **eda_sweetviz** (tool_type=eda_sweetviz): Generate EDA via Sweetviz.",
            ]
            tools_list = tools_list + "\n\n**EDA tools**:\n" + "\n".join(eda_tools)

        # 使用 LLM 總結 schema（如果是大型 schema）
        schema_block = "(no schema)"
        if data_summary.schema_markdown:
            if len(data_summary.schema_markdown) > 2000:
                schema_block = await self._summarize_schema(
                    data_summary.schema_markdown,
                    data_summary.row_counts,
                )
            else:
                schema_block = data_summary.schema_markdown

        # 壓縮 insights（insights 相對結構化，可以直接截斷）
        insights_text = "None yet."
        if analysis_result.insights:
            insights = [str(i)[:200] for i in analysis_result.insights[:5]]
            insights_text = "\n".join([f"- {i}" for i in insights])

        prompt = f"""Decide how to analyze the experiment data and which tools to run.

**Hypothesis**: {context.design.hypothesis}
**Completion**: {context.completion_percentage:.1f}% | **Status**: {context.execution_status.value}

**Previous insights** (from data-aware analysis):
{insights_text}

**Database**: {data_summary.db_path}
**Database schema**:
{schema_block}

**Available tools**: {tools_list}
{f"**Previous feedback (FIX THESE ISSUES)**: {previous_feedback}" if previous_feedback else ""}

{strategy_xml_contract()}"""

        response = await self.llm_router.acompletion(
            model=self.model_name,
            messages=[
                {"role": "system", "content": _system_with_skills(self.config)},
                {"role": "user", "content": prompt},
            ],
            temperature=self.temperature,
        )

        data = parse_llm_xml_response(
            response.choices[0].message.content or "", root_tag="strategy"
        )
        tools = data.get("tools_to_use", [])

        if isinstance(tools, dict) and "tool" in tools:
            tools = (
                tools["tool"] if isinstance(tools["tool"], list) else [tools["tool"]]
            )
        if not isinstance(tools, list):
            tools = []

        normalized = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            params = t.get("parameters", {})
            if isinstance(params, str):
                try:
                    params = json_repair.loads(params) if params.strip() else {}
                except Exception:
                    params = {}
            normalized.append(
                {
                    "tool_name": t.get("tool_name", "code_executor"),
                    "tool_type": t.get("tool_type", "code_executor"),
                    "action": t.get("action", ""),
                    "parameters": params if isinstance(params, dict) else {},
                }
            )

        return {
            "analysis_strategy": data.get("analysis_strategy", ""),
            "tools_to_use": normalized,
        }

    async def _judge_analysis_strategy(
        self,
        analysis_plan: Dict[str, Any],
        context: ExperimentContext,
        data_summary: DataSummary,
    ) -> StrategyJudgment:
        """判斷分析策略是否合理。

        檢查策略是否與假設相關，工具是否引用了正確的表/列，
        是否考慮了資料稀疏情況等。

        Args:
            analysis_plan: 分析計劃字典，包含 analysis_strategy 和 tools_to_use。
            context: 實驗上下文。
            data_summary: 資料摘要，用於驗證工具引用的表/列是否存在。

        Returns:
            StrategyJudgment 物件，包含 success、reason、should_retry 等欄位。
        """
        # 構建資料摘要
        total_rows = sum(data_summary.row_counts.values())
        non_empty_tables = [
            t for t in data_summary.tables if data_summary.row_counts.get(t, 0) > 0
        ]
        empty_tables = [
            t for t in data_summary.tables if data_summary.row_counts.get(t, 0) == 0
        ]

        # 擷取 schema 資訊（裁判需要知道有哪些列）
        schema_preview = data_summary.schema_markdown or "No schema available"
        if len(schema_preview) > 1500:
            schema_preview = schema_preview[:1500] + "\n...[truncated]"

        # 格式化工具列表供裁判檢視
        tools_str = self._format_tools_for_judgment(analysis_plan.get("tools_to_use", []))

        prompt = f"""Evaluate the analysis strategy for experiment {context.experiment_id}.

**Hypothesis**: {context.design.hypothesis}

**Actual Data Summary**:
- Tables: {data_summary.tables}
- Non-empty: {non_empty_tables}
- Empty: {empty_tables}
- Total rows: {total_rows}

**Schema** (valid table/column names):
{schema_preview}

**Proposed strategy**:
- Analysis approach: {analysis_plan.get("analysis_strategy", "")}
- Tools to use:
{tools_str}

**CRITICAL CHECKS**:
1. **Relevance**: Strategy relevant to hypothesis?
2. **Schema Alignment**: Tools reference ONLY tables/columns that exist in schema above?
3. **Data Appropriateness**: If key tables are empty, does strategy adjust accordingly?
4. **EDA Usage**: EDA tools used when data overview needed?
5. **Hallucination Check**: Do tools reference tables/columns that do NOT exist in schema? If YES, must FAIL.

{judgment_prompt()}"""

        response = await self.llm_router.acompletion(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        content = response.choices[0].message.content or ""
        return parse_llm_xml_to_model(content, StrategyJudgment, root_tag="judgment")

    async def _execute_tools_with_feedback(
        self,
        tool_executor: AnalysisRunner,
        tools_to_use: List[Dict[str, Any]],
        db_path: Path,
        output_dir: Path,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        data_summary: DataSummary,
        on_progress: AnalysisProgressCallback = None,
    ) -> Dict[str, Any]:
        """執行工具，並根據反饋調整策略。使用上下文壓縮防止歷史膨脹。"""

        async def progress(msg: str) -> None:
            if on_progress:
                await on_progress(msg)

        results = {}
        conversation_history: List[Dict[str, Any]] = []
        context_summary: Optional[ContextSummary] = None
        max_iter = self.config.max_tool_iterations

        for iteration in range(max_iter):
            current_tools = tools_to_use if iteration == 0 else []
            if iteration > 0:
                adj = await self._adjust_strategy_based_on_results(
                    context,
                    analysis_result,
                    results,
                    context_summary,
                    iteration,
                )
                if not adj.get("tools_to_use"):
                    context_summary = await self._summarize_context(
                        conversation_history,
                        results,
                        iteration + 1,
                    )
                    self.logger.info("第 %d 輪無新增工具，停止迭代", iteration + 1)
                    break
                current_tools = adj.get("tools_to_use", [])

            for i, tool_spec in enumerate(current_tools):
                tool_name = tool_spec.get("tool_name", f"tool_{i}")
                tool_type = tool_spec.get("tool_type", "code_executor")
                parameters = tool_spec.get("parameters", {})

                await progress(f"Running tool: {tool_name}...")

                if tool_type in ("eda_profile", "eda_sweetviz", "eda_missingno", "eda_correlation") or tool_name in (
                    "eda_profile",
                    "eda_sweetviz",
                    "eda_missingno",
                    "eda_correlation",
                ):
                    result = await self._run_eda_tool(
                        tool_name, db_path, output_dir, on_progress=progress
                    )
                else:
                    exec_parameters = parameters.copy()
                    if tool_type == "code_executor":
                        exec_parameters["db_path"] = str(db_path)
                        exec_parameters["code_description"] = tool_spec.get(
                            "action", ""
                        )
                        exec_parameters["extra_files"] = collect_experiment_files(
                            db_path
                        )
                    result = await tool_executor.execute_tool(
                        tool_name=tool_name,
                        tool_type=tool_type,
                        parameters=exec_parameters,
                    )

                results[tool_name] = result
                conversation_history.append(
                    {
                        "tool": tool_name,
                        "success": result.get("success", False),
                        "iteration": iteration + 1,
                        "result": {
                            "success": result.get("success", False),
                            "error": result.get("error", "")[:100],
                        },
                    }
                )

            context_summary = await self._summarize_context(
                conversation_history,
                results,
                iteration + 1,
            )
            self.logger.info("第 %d 輪工具執行完成", iteration + 1)

        return results

    async def _run_eda_tool(
        self,
        tool_name: str,
        db_path: Path,
        output_dir: Path,
        on_progress=None,
    ) -> Dict[str, Any]:
        """執行 EDA 工具。

        支援的工具：
        - eda_profile: ydata-profiling 完整EDA報告
        - eda_sweetviz: Sweetviz EDA報告
        - eda_missingno: missingno 缺失值視覺化
        - eda_correlation: 相關性矩陣熱力圖
        """
        data_dir = output_dir / DIR_DATA
        data_dir.mkdir(parents=True, exist_ok=True)
        path = None
        gen = EDAGenerator(self.config)

        if tool_name == "eda_profile":
            path = gen.generate_ydata_profile(db_path, data_dir)
            if path and on_progress:
                await on_progress(f"EDA (ydata-profiling) generated: {path.name}")
        elif tool_name == "eda_sweetviz":
            path = gen.generate_sweetviz_profile(db_path, data_dir)
            if path and on_progress:
                await on_progress(f"EDA (Sweetviz) generated: {path.name}")
        elif tool_name == "eda_missingno":
            path = gen.generate_missingno_report(db_path, data_dir)
            if path and on_progress:
                await on_progress(f"Missing value analysis (missingno) generated: {path.name}")
        elif tool_name == "eda_correlation":
            path = gen.generate_correlation_report(db_path, data_dir)
            if path and on_progress:
                await on_progress(f"Correlation analysis generated: {path.name}")

        if path and path.exists():
            return {"success": True, "path": str(path), "tool_name": tool_name}
        return {
            "success": False,
            "error": "EDA generation failed or skipped",
            "tool_name": tool_name,
        }

    async def _adjust_strategy_based_on_results(
        self,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        current_results: Dict[str, Any],
        context_summary: Optional[ContextSummary],
        iteration: int,
    ) -> Dict[str, Any]:
        """根據工具執行結果，決定是否繼續執行工具或停止。使用 LLM 總結。"""

        # 構建簡潔的上下文資訊
        if context_summary:
            history_context = self._format_context_summary(context_summary)
        else:
            # 回退到簡單格式
            history_context = f"**Iteration**: {iteration}"

        # 使用 LLM 總結當前結果（如果有多個或者很長）
        if (
            current_results
            and sum(len(str(r)) for r in current_results.values()) > 1500
        ):
            results_summary = await self._summarize_tool_results(
                current_results,
                analysis_result.insights,
            )
        else:
            results_summary = self._format_tool_results(
                current_results, max_length=1500
            )

        prompt = f"""Decide whether to run more tools or stop.

**Hypothesis**: {context.design.hypothesis} | **Completion**: {context.completion_percentage:.1f}%

**Progress**:
{history_context}

**Latest results**:
{results_summary}

**Decision criteria**:
- If key analysis is complete and insights are sufficient → stop (empty tools_to_use)
- If more exploration needed → specify next tools
- If previous attempts failed → try alternative approach

{adjust_tools_xml_contract()}"""

        response = await self.llm_router.acompletion(
            model=self.model_name,
            messages=[
                {"role": "system", "content": _system_with_skills(self.config)},
                {"role": "user", "content": prompt},
            ],
            temperature=self.temperature,
        )

        data = parse_llm_xml_response(
            response.choices[0].message.content or "", root_tag="adjust"
        )
        tools = data.get("tools_to_use", [])

        if isinstance(tools, dict):
            t = tools.get("tool", [])
            tools = t if isinstance(t, list) else [t] if t else []
        elif not isinstance(tools, list):
            tools = []

        normalized = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            params = t.get("parameters", {})
            if isinstance(params, str):
                try:
                    params = json_repair.loads(params) if params.strip() else {}
                except Exception:
                    params = {}
            normalized.append(
                {
                    "tool_name": t.get("tool_name", "code_executor"),
                    "tool_type": t.get("tool_type", "code_executor"),
                    "action": t.get("action", ""),
                    "parameters": params if isinstance(params, dict) else {},
                }
            )

        return {"assessment": data.get("assessment", ""), "tools_to_use": normalized}

    async def _decide_and_generate_visualizations_with_judgment(
        self,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        data_summary: DataSummary,
        tool_results: Dict[str, Any],
        db_path: Path,
        output_dir: Path,
        tool_executor: AnalysisRunner,
        on_progress: AnalysisProgressCallback = None,
    ) -> Tuple[List[Dict[str, Any]], List[Path]]:
        """決定視覺化方案、生成圖表，經裁判透過後返回。

        支援重試機制，累積錯誤歷史以提高迭代效率。

        Args:
            context: 實驗上下文。
            analysis_result: 分析結果。
            data_summary: 資料摘要。
            tool_results: 工具執行結果。
            db_path: 資料庫路徑。
            output_dir: 輸出目錄。
            tool_executor: 工具執行器。
            on_progress: 進度回撥函式，可選。

        Returns:
            元組 (visualization_plan, generated_charts)。
        """
        max_retries = self.config.max_visualization_retries
        error_history: list[str] = []  # 累積錯誤歷史
        visualization_plan: List[Dict[str, Any]] = []
        generated_charts: List[Path] = []

        async def progress(msg: str) -> None:
            if on_progress:
                await on_progress(msg)

        for attempt in range(max_retries):
            # 構建包含歷史錯誤的反饋
            previous_feedback = self._build_viz_feedback(error_history)
            
            try:
                await progress("Deciding visualizations...")
                visualization_plan = await self._decide_visualizations(
                    context,
                    analysis_result,
                    data_summary,
                    tool_results,
                    previous_feedback,
                )
            except XmlParseError as e:
                if attempt >= max_retries - 1:
                    self.logger.warning("視覺化XML解析失敗: %s", e)
                    raise
                error_history.append(f"XML解析錯誤: {str(e)[:200]}")
                continue

            if not visualization_plan:
                self.logger.warning("視覺化計劃為空，跳過圖表生成")
                return visualization_plan, generated_charts

            await progress("Generating charts...")
            generated_charts, error_logs = await self._generate_visualizations(
                visualization_plan,
                db_path,
                output_dir,
                tool_executor,
                on_progress=on_progress,
            )

            try:
                judgment = await self._judge_visualizations(
                    visualization_plan,
                    generated_charts,
                    context,
                    tool_results,
                    error_logs,
                    data_summary=data_summary,
                )
            except XmlParseError as e:
                if attempt >= max_retries - 1:
                    raise
                error_history.append(f"裁判XML解析錯誤: {str(e)[:200]}")
                continue

            if (
                judgment.success
                or not judgment.should_retry
                or attempt >= max_retries - 1
            ):
                return visualization_plan, generated_charts

            error_history.append(judgment.reason)
            self.logger.info(
                "視覺化方案需要改進 (第 %s 次嘗試): %s",
                attempt + 1,
                judgment.reason,
            )

        return visualization_plan, generated_charts

    async def _decide_visualizations(
        self,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        data_summary: DataSummary,
        analysis_results: Dict[str, Any],
        previous_feedback: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """決定視覺化方案。

        根據分析結果和資料上下文，決定需要生成哪些視覺化圖表。
        使用 LLM 總結長內容以避免 prompt 過長。

        Args:
            context: 實驗上下文。
            analysis_result: 分析結果，包含洞察和發現。
            data_summary: 資料摘要。
            analysis_results: 工具執行結果字典。
            previous_feedback: 上一次重試的反饋資訊，可選。

        Returns:
            視覺化計劃列表，每項包含 tool_name、tool_description 等。
        """
        feedback_block = ""
        if previous_feedback:
            feedback_block = f"\n**Previous feedback**: {previous_feedback[:500]}\n"

        # 使用 LLM 總結 schema（如果是大型 schema）
        schema_block = "(no schema)"
        if data_summary.schema_markdown:
            if len(data_summary.schema_markdown) > 1500:
                schema_block = await self._summarize_schema(
                    data_summary.schema_markdown,
                    data_summary.row_counts,
                    max_tables=8,
                )
            else:
                schema_block = data_summary.schema_markdown

        # 壓縮 insights
        insights_text = "None."
        if analysis_result.insights:
            insights = [str(i)[:200] for i in analysis_result.insights[:5]]
            insights_text = "\n".join([f"- {i}" for i in insights])

        # 對於工具結果，如果有多個或者很長，使用 LLM 總結
        tool_results_text = self._format_tool_results(analysis_results, max_length=1500)
        if len(tool_results_text) > 1200 and analysis_results:
            tool_results_text = await self._summarize_tool_results(
                analysis_results,
                analysis_result.insights,
            )

        prompt = f"""Decide which charts to generate.

**Hypothesis**: {context.design.hypothesis} | **Completion**: {context.completion_percentage:.1f}%

**Insights** (from data-aware analysis):
{insights_text}

**Database**: {data_summary.db_path}
**Table row counts**: {data_summary.row_counts}

**Schema**:
{schema_block}

**Tool results**: {tool_results_text}
{feedback_block}

{visualization_xml_contract()}"""

        response = await self.llm_router.acompletion(
            model=self.model_name,
            messages=[
                {"role": "system", "content": _system_with_skills(self.config)},
                {"role": "user", "content": prompt},
            ],
            temperature=self.temperature,
        )

        data = parse_llm_xml_response(
            response.choices[0].message.content or "", root_tag="visualizations"
        )
        viz = data.get("viz", data.get("visualizations", []))

        if isinstance(viz, dict):
            viz = [viz]
        if not isinstance(viz, list):
            viz = []

        return [
            {
                "use_tool": v.get("use_tool", True) if isinstance(v, dict) else True,
                "tool_name": (
                    v.get("tool_name", "code_executor")
                    if isinstance(v, dict)
                    else "code_executor"
                ),
                "tool_description": (
                    v.get("tool_description", "") if isinstance(v, dict) else ""
                ),
            }
            for v in viz
        ]

    async def _judge_visualizations(
        self,
        visualization_plan: List[Dict[str, Any]],
        generated_charts: List[Path],
        context: ExperimentContext,
        tool_results: Dict[str, Any],
        error_logs: Optional[List[str]] = None,
        data_summary: Optional[DataSummary] = None,
    ) -> VisualizationJudgment:
        """判斷視覺化結果是否充分。

        檢查生成的圖表是否與假設相關，是否基於實際資料，
        是否足以支撐報告。

        Args:
            visualization_plan: 視覺化計劃列表。
            generated_charts: 已生成的圖表路徑列表。
            context: 實驗上下文。
            tool_results: 工具執行結果。
            error_logs: 執行錯誤日誌，可選。
            data_summary: 資料摘要，用於驗證資料上下文，可選。

        Returns:
            VisualizationJudgment 物件，包含 success、reason、should_retry 等欄位。
        """
        chart_names = [p.name for p in generated_charts]
        errors_block = ""
        if error_logs:
            errors_block = "\n**Execution Errors**:\n" + "\n".join(
                f"- {e[:200]}" for e in error_logs[:5]  # 限制錯誤資訊長度
            )

        # 資料摘要
        data_block = ""
        if data_summary:
            total_rows = sum(data_summary.row_counts.values())
            non_empty = [
                t for t in data_summary.tables if data_summary.row_counts.get(t, 0) > 0
            ]
            data_block = f"""
**Actual Data Context**:
- Tables with data: {non_empty}
- Total rows: {total_rows}
- Empty tables: {[t for t in data_summary.tables if data_summary.row_counts.get(t, 0) == 0]}
"""

        # 格式化視覺化計劃內容
        viz_plan_str = self._format_viz_plan_for_judgment(visualization_plan)

        prompt = f"""Evaluate the visualization output for experiment {context.experiment_id}.

**Hypothesis**: {context.design.hypothesis}

**Visualization Plan** ({len(visualization_plan)} items):
{viz_plan_str}

**Generated Charts**: {chart_names if chart_names else "(none)"}{errors_block}{data_block}

**CRITICAL CHECKS**:
1. **Relevance**: Charts relevant to hypothesis?
2. **Data Alignment**: Charts based on ACTUAL data (not hypothetical)?
3. **Adequacy**: Sufficient for report given data available?
4. **Failure Handling**: If key tables empty, are diagnostic charts generated?
5. **Quality**: Any failures that need retry?

{judgment_prompt()}"""

        response = await self.llm_router.acompletion(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        content = response.choices[0].message.content or ""
        return parse_llm_xml_to_model(
            content, VisualizationJudgment, root_tag="judgment"
        )

    async def _generate_visualizations(
        self,
        visualization_plan: List[Dict[str, Any]],
        db_path: Path,
        output_dir: Path,
        tool_executor: AnalysisRunner,
        on_progress: AnalysisProgressCallback = None,
    ) -> Tuple[List[Path], List[str]]:
        """執行視覺化生成。

        根據視覺化計劃，呼叫程式碼執行器生成圖表檔案。

        Args:
            visualization_plan: 視覺化計劃列表。
            db_path: 資料庫路徑。
            output_dir: 輸出目錄，用於儲存圖表。
            tool_executor: 工具執行器。
            on_progress: 進度回撥函式，可選。

        Returns:
            元組 (generated_charts, error_logs):
            - generated_charts: 生成的圖表路徑列表
            - error_logs: 錯誤日誌列表
        """

        async def progress(msg: str) -> None:
            if on_progress:
                await on_progress(msg)

        generated_charts: List[Path] = []
        error_logs: List[str] = []

        if not visualization_plan:
            return generated_charts, error_logs

        total = sum(
            1 for v in visualization_plan if v.get("use_tool") or v.get("tool_name")
        )
        done = 0

        for viz in visualization_plan:
            if not viz.get("use_tool") and not viz.get("tool_name"):
                continue
            done += 1
            await progress(
                f"Generating chart {done}/{total}..."
                if total > 1
                else "Generating chart..."
            )

            tool_description = (
                viz.get("tool_description") or viz.get("description") or ""
            )
            if not tool_description:
                continue

            result = await tool_executor.execute_tool(
                tool_name=viz.get("tool_name", "code_executor"),
                tool_type="code_executor",
                parameters={
                    "db_path": str(db_path),
                    "code_description": tool_description,
                    "extra_files": collect_experiment_files(db_path),
                },
            )

            if not result.get("success"):
                error_msg = result.get("error", "unknown")
                self.logger.warning("工具執行失敗: %s", error_msg)
                error_logs.append(f"Chart {done} failed: {error_msg}")
                continue

            generated_charts.extend(
                self._collect_generated_chart_paths(result, output_dir)
            )

        return generated_charts, error_logs

    def _collect_generated_chart_paths(
        self, tool_result: Dict[str, Any], output_dir: Path
    ) -> List[Path]:
        """收集工具產出的圖表檔案。"""
        chart_paths: List[Path] = []
        output_dir.mkdir(parents=True, exist_ok=True)
        for artifact_path_str in tool_result.get("artifacts", []):
            artifact_path = Path(artifact_path_str)
            if not artifact_path.exists() or not artifact_path.is_file():
                continue
            if artifact_path.suffix.lower() not in SUPPORTED_ASSET_FORMATS:
                continue
            dest_path = output_dir / artifact_path.name
            if artifact_path.resolve() != dest_path.resolve():
                shutil.copy2(artifact_path, dest_path)
            chart_paths.append(dest_path)
        return chart_paths

    def _format_tools_list(self, tools: Dict[str, Dict[str, Any]]) -> str:
        """格式化工具列表。"""
        if not tools:
            return "No built-in tools available"
        file_order = [
            "read_file",
            "write_file",
            "list_directory",
            "glob",
            "search_file_content",
        ]
        entries = []
        for name, info in tools.items():
            desc = info.get("description", "No description")
            params = info.get("parameters_description") or info.get("parameters")
            if params is not None and not isinstance(params, str):
                params = ", ".join(str(p) for p in params)
            line = f"- **{name}**: {desc}" + (
                f" Parameters: {params}" if params else ""
            )
            entries.append(
                (file_order.index(name) if name in file_order else 999, line)
            )
        entries.sort(key=lambda x: x[0])
        return "\n".join(e[1] for e in entries)

    def _format_tool_results(
        self, tool_results: Dict[str, Any], max_length: int = 2000
    ) -> str:
        """格式化工具執行結果。注意：這是格式化方法，總結由 _summarize_tool_results 負責。"""
        if not tool_results:
            return "No tool execution results"
        lines = []
        for name, result in tool_results.items():
            success = result.get("success", False)
            lines.append(f"\n**{name}**: {'✅ Success' if success else '❌ Failed'}")
            if success:
                if "path" in result:
                    lines.append(f"Output: {result['path']}")
                elif "stdout" in result:
                    stdout = result["stdout"]
                    # 保留較長輸出，後續由 LLM 總結
                    if len(stdout) > 800:
                        stdout = (
                            stdout[:800] + f"...[+{len(result['stdout'])-800} chars]"
                        )
                    lines.append(f"Output: {stdout}")
            else:
                error = result.get("error", "Unknown")
                if len(error) > 500:
                    error = error[:500] + "...[more]"
                lines.append(f"Error: {error}")
        result_str = "\n".join(lines) if lines else "No results"
        if len(result_str) > max_length:
            result_str = result_str[:max_length] + "\n...[see summary for key findings]"
        return result_str

    async def _summarize_tool_results(
        self,
        tool_results: Dict[str, Any],
        previous_insights: List[str],
    ) -> str:
        """
        使用 LLM 總結工具執行結果的關鍵發現。

        將長輸出壓縮為有意義的摘要，而非簡單截斷。
        """
        if not tool_results:
            return "No results to summarize."

        # 構建結果概覽
        results_overview = []
        for name, result in tool_results.items():
            if result.get("success"):
                if "path" in result:
                    results_overview.append(f"- {name}: Generated {result['path']}")
                elif "stdout" in result:
                    # 取輸出的關鍵部分
                    stdout = result["stdout"]
                    results_overview.append(f"- {name}: {len(stdout)} chars output")
            else:
                results_overview.append(
                    f"- {name}: FAILED - {str(result.get('error', ''))[:100]}"
                )

        prompt = f"""Summarize the key findings from these tool execution results.

**Results Overview**:
{chr(10).join(results_overview)}

**Previous Insights**:
{chr(10).join([f"- {i[:150]}" for i in previous_insights[:3]]) if previous_insights else "None yet"}

Provide a concise summary (2-3 sentences) of what was discovered or accomplished.
Focus on actionable findings, not just listing what ran.

Return ONLY the summary text, no XML needed."""

        try:
            response = await self.llm_router.acompletion(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            self.logger.warning("工具結果總結失敗: %s", e)
            return "; ".join(results_overview[:5])

    async def _summarize_document(
        self,
        document: str,
        document_type: str,
        max_length: int = 500,
    ) -> str:
        """使用 LLM 總結長文件。
        
        對於短文件直接返回，長文件呼叫 LLM 提取關鍵資訊。
        """
        if not document or len(document) <= max_length:
            return document or ""

        prompt = f"""Summarize this {document_type} document concisely.

Keep the key points, main arguments, and critical details.
Target length: around {max_length} characters.

**Document**:
{document[:3000]}

Return ONLY the summary, no additional text."""

        try:
            response = await self.llm_router.acompletion(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            summary = (response.choices[0].message.content or "").strip()
            self.logger.info(
                "文件總結: %s (%d -> %d chars)",
                document_type,
                len(document),
                len(summary),
            )
            return summary
        except Exception as e:
            self.logger.warning("文件總結失敗 (%s): %s", document_type, e)
            return document[:max_length] + "...[truncated]"

    async def _summarize_error(
        self,
        error: Exception,
        raw_content: Optional[str] = None,
    ) -> str:
        """使用 LLM 總結錯誤資訊，生成簡潔的反饋。

        對於長錯誤資訊，使用 LLM 提取關鍵問題，生成 2-3 句總結，
        供下一次迭代參考。

        Args:
            error: 原始異常物件。
            raw_content: 原始輸出內容，可選，用於提供更多上下文。

        Returns:
            簡潔的錯誤總結字串。
        """
        error_msg = str(error)
        
        # 如果錯誤資訊很短，直接返回
        if len(error_msg) <= 200 and (not raw_content or len(raw_content) <= 200):
            return error_msg
        
        # 構建總結提示
        prompt_parts = [
            "Summarize this error message concisely for an LLM to understand and fix.",
            "Focus on: what went wrong, what format is expected.\n",
            f"**Error**: {error_msg[:1000]}",
        ]
        
        if raw_content:
            prompt_parts.append(f"\n**Problematic Output** (first 500 chars):\n{raw_content[:500]}")
        
        prompt_parts.append("\nReturn a brief summary (2-3 sentences) explaining what needs to be fixed.")
        
        prompt = "\n".join(prompt_parts)
        
        try:
            response = await self.llm_router.acompletion(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            summary = (response.choices[0].message.content or "").strip()
            self.logger.info("錯誤總結: %d -> %d chars", len(error_msg), len(summary))
            return summary
        except Exception as e:
            self.logger.warning("錯誤總結失敗: %s", e)
            return error_msg[:300] + "...[error summary failed]"

    async def _summarize_schema(
        self,
        schema_markdown: str,
        row_counts: Dict[str, int],
        max_tables: int = 10,
    ) -> str:
        """使用 LLM 總結資料庫 schema，提取關鍵資訊。"""
        if not schema_markdown:
            return "(no schema)"

        if len(schema_markdown) <= 2000:
            return schema_markdown

        tables_with_data = [(t, c) for t, c in row_counts.items() if c > 0]
        tables_sorted = sorted(tables_with_data, key=lambda x: -x[1])[:max_tables]
        key_tables_info = "\n".join([f"- {t}: {c} rows" for t, c in tables_sorted])

        prompt = f"""Summarize this database schema for analysis purposes.

**Total tables**: {len(row_counts)}
**Tables with data**: {len(tables_with_data)}

**Key tables (by row count)**:
{key_tables_info}

**Full schema** (may be truncated):
{schema_markdown[:3000]}

Provide a concise schema summary that includes:
1. Most important tables and their purposes
2. Key columns in each important table
3. Any notable relationships or patterns

Keep it under 1500 characters. Return ONLY the summary."""

        try:
            response = await self.llm_router.acompletion(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            summary = (response.choices[0].message.content or "").strip()
            self.logger.info(
                "Schema 總結: %d -> %d chars",
                len(schema_markdown),
                len(summary),
            )
            return summary
        except Exception as e:
            self.logger.warning("Schema 總結失敗: %s", e)
            return f"Key tables:\n{key_tables_info}\n\n[schema truncated]\n{schema_markdown[:1500]}"

    async def _summarize_context(
        self,
        conversation_history: List[Dict[str, Any]],
        current_results: Dict[str, Any],
        iteration: int,
    ) -> ContextSummary:
        """壓縮歷史上下文為結構化摘要，避免上下文膨脹。"""
        if not conversation_history or len(conversation_history) <= 2:
            return ContextSummary(
                key_findings=[],
                failed_attempts=[],
                successful_tools=[
                    h["tool"]
                    for h in conversation_history
                    if h.get("result", {}).get("success")
                ],
                recommendations="",
                iteration_count=iteration,
            )

        history_text = "\n".join(
            [
                f"- Iter {h['iteration']}: {h['tool']} - {'OK' if h.get('result', {}).get('success') else 'FAILED'}"
                for h in conversation_history[-10:]
            ]
        )

        outputs_text = ""
        for name, result in list(current_results.items())[-3:]:
            if result.get("success") and result.get("stdout"):
                outputs_text += f"\n**{name}**: {result['stdout'][:300]}...\n"
            elif not result.get("success"):
                outputs_text += f"\n**{name}** FAILED: {str(result.get('error', ''))[:200]}\n"

        prompt = f"""Summarize the analysis execution history into a structured summary.

**Iteration**: {iteration}
**History**:
{history_text}

**Recent outputs**:
{outputs_text}

Extract:
1. key_findings: Important discoveries from tool outputs (max 3 items)
2. failed_attempts: Tools that failed and why (max 2 items)
3. successful_tools: Tools that completed successfully
4. recommendations: What to do next or what was learned

{summary_xml_contract()}"""

        try:
            response = await self.llm_router.acompletion(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            content = response.choices[0].message.content or ""
            data = parse_llm_xml_response(content, root_tag="summary")

            def _list(v: Any) -> List[str]:
                if isinstance(v, list):
                    return [str(i) for i in v[:3]]
                if isinstance(v, dict) and "item" in v:
                    items = v["item"]
                    return [
                        str(i)
                        for i in (items if isinstance(items, list) else [items])[:3]
                    ]
                return []

            return ContextSummary(
                key_findings=_list(data.get("key_findings", [])),
                failed_attempts=_list(data.get("failed_attempts", [])),
                successful_tools=_list(data.get("successful_tools", [])),
                recommendations=str(data.get("recommendations", "")),
                iteration_count=iteration,
            )
        except Exception as e:
            self.logger.warning("上下文摘要失敗: %s", e)
            return ContextSummary(
                key_findings=[],
                failed_attempts=[],
                successful_tools=[
                    h["tool"]
                    for h in conversation_history
                    if h.get("result", {}).get("success")
                ],
                recommendations="",
                iteration_count=iteration,
            )

    def _format_context_summary(self, summary: ContextSummary) -> str:
        """將上下文摘要格式化為 prompt 友好的文字。"""
        lines = [f"**Iteration {summary.iteration_count} Summary**:"]

        if summary.key_findings:
            lines.append("Key findings:")
            for f in summary.key_findings[:3]:
                lines.append(f"  - {f}")

        if summary.failed_attempts:
            lines.append("Failed attempts:")
            for f in summary.failed_attempts[:2]:
                lines.append(f"  - {f}")

        if summary.successful_tools:
            lines.append(f"Successful tools: {', '.join(summary.successful_tools[:5])}")

        if summary.recommendations:
            lines.append(f"Recommendations: {summary.recommendations}")

        return "\n".join(lines)

    async def close(self) -> None:
        """關閉智慧體"""
        pass
