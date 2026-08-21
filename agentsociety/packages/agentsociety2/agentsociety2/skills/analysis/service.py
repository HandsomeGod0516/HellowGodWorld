"""编排层：单实验 `Analyzer`、多实验 `Synthesizer`、统一入口 `run_analysis_workflow`。"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import json_repair

from agentsociety2.logger import get_logger
from agentsociety2.config import get_llm_router_and_model

from .models import (
    ExperimentContext,
    ExperimentDesign,
    ExperimentStatus,
    AnalysisConfig,
    AnalysisResult,
    ExperimentSynthesis,
    HypothesisSummary,
    ReportAsset,
    DIR_DATA,
    DIR_HYPOTHESIS_PREFIX,
    DIR_EXPERIMENT_PREFIX,
    DIR_PRESENTATION,
    FILE_SQLITE,
    FILE_PID,
    FILE_HYPOTHESIS_MD,
    FILE_EXPERIMENT_MD,
    FILE_SYNTHESIS_REPORT_PREFIX,
)
from .agents import AnalysisAgent
from .output import EDAGenerator, Reporter, AssetProcessor
from .llm_contracts import judgment_prompt, report_xml_instruction
from .utils import (
    XmlParseError,
    parse_llm_xml_response,
    parse_llm_xml_to_model,
    parse_llm_report_response,
    AnalysisProgressCallback,
    experiment_paths,
    presentation_paths,
)


class Analyzer:
    """分析入口：编排统一 AnalysisAgent 与 Reporter，产出单实验图文报告。"""

    def __init__(self, config: AnalysisConfig):
        """
        初始化 Analyzer（单实验分析编排入口）。

        Args:
            config: 分析配置
        """
        self.config = config
        self.logger = get_logger()
        self.workspace_path = Path(config.workspace_path).resolve()
        self.presentation_path = self.workspace_path / DIR_PRESENTATION
        self.presentation_path.mkdir(parents=True, exist_ok=True)

        self.logger.info("分析智能体初始化完成")
        self.logger.info("  工作空间: %s", self.workspace_path)
        self.logger.info("  分析产物: %s", self.presentation_path)

        self.agent = AnalysisAgent(config=config, workspace_path=self.workspace_path)
        self.logger.info(
            "统一 AnalysisAgent 初始化完成，使用模型: %s", self.agent.model_name
        )

        self.asset_processor = AssetProcessor(self.workspace_path)

    def _find_database_path(
        self, hypothesis_id: str, experiment_id: str
    ) -> Optional[Path]:
        """查找数据库路径（按约定目录结构）。"""
        paths = experiment_paths(self.workspace_path, hypothesis_id, experiment_id)
        if paths.db_path.exists():
            self.logger.info("找到数据库: %s", paths.db_path)
            return paths.db_path
        self.logger.warning("数据库不存在: %s", paths.db_path)
        return None

    async def analyze(
        self,
        hypothesis_id: str,
        experiment_id: str,
        custom_instructions: Optional[str] = None,
        literature_summary: Optional[str] = None,
        on_progress: AnalysisProgressCallback = None,
    ) -> Dict[str, Any]:
        """
        分析单实验，生成图文报告。

        使用统一的 AnalysisAgent 执行数据优先的分析流程：
        1. 读取并理解数据结构
        2. 基于实际数据生成洞察
        3. 执行数据分析和可视化
        4. 生成报告

        Args:
            hypothesis_id: 假设ID
            experiment_id: 实验ID
            custom_instructions: 可选的自定义分析指令
            literature_summary: 可选的文献摘要
            on_progress: 可选的进度回调
        """
        self.logger.info("开始分析实验 %s", experiment_id)

        async def progress(msg: str) -> None:
            if on_progress:
                await on_progress(msg)

        await progress("Loading experiment context...")
        context = await self._load_context(hypothesis_id, experiment_id)

        await progress("Discovering assets...")
        assets = self.asset_processor.discover_assets(experiment_id, hypothesis_id)
        pres = presentation_paths(self.presentation_path, hypothesis_id, experiment_id)
        pres.output_dir.mkdir(parents=True, exist_ok=True)
        pres.charts_dir.mkdir(parents=True, exist_ok=True)
        db_path = self._find_database_path(hypothesis_id, experiment_id)

        # 使用统一的 AnalysisAgent 执行完整分析
        await progress("Running unified data-aware analysis...")
        analysis_result, data_analysis_result = await self.agent.analyze(
            context=context,
            db_path=db_path,
            output_dir=pres.charts_dir,
            custom_instructions=custom_instructions,
            literature_summary=literature_summary,
            on_progress=on_progress,
        )

        data_analyzed = db_path is not None and db_path.exists()

        # 收集生成的图表
        for chart_path in data_analysis_result.get("generated_charts", []):
            asset = ReportAsset(
                asset_id=f"gen_{chart_path.stem}",
                asset_type="visualization",
                title=chart_path.stem.replace("_", " ").title(),
                file_path=str(chart_path),
                description=f"Autonomously generated visualization: {chart_path.name}",
                file_size=chart_path.stat().st_size,
                embedded_content=None,
            )
            assets.append(asset)

        self.logger.info(
            "数据分析完成: %s 张图表生成",
            len(data_analysis_result.get("generated_charts", [])),
        )

        eda_profile_path = data_analysis_result.get("eda_profile_path")
        eda_sweetviz_path = data_analysis_result.get("eda_sweetviz_path")

        # 如果 EDA 未生成，尝试生成
        if db_path and (eda_profile_path is None or eda_sweetviz_path is None):
            data_dir = pres.output_dir / DIR_DATA
            data_dir.mkdir(parents=True, exist_ok=True)
            eda_gen = EDAGenerator(self.config)
            if eda_profile_path is None:
                eda_profile_path = eda_gen.generate_ydata_profile(db_path, data_dir)
                if eda_profile_path:
                    self.logger.info("EDA 概览备选生成: %s", eda_profile_path)
            if eda_sweetviz_path is None:
                eda_sweetviz_path = eda_gen.generate_sweetviz_profile(db_path, data_dir)
                if eda_sweetviz_path:
                    self.logger.info("Sweetviz EDA 概览备选生成: %s", eda_sweetviz_path)

        await progress("Processing assets...")
        processed_assets = self.asset_processor.process_assets(assets, pres.output_dir)

        quick_stats_md = None
        if db_path:
            quick_stats_md = EDAGenerator(self.config).generate_quick_stats(db_path)

        await progress("Writing report...")
        reporter = Reporter(agent=self.agent, config=self.config)
        generated_files, report_complete = await reporter.generate(
            context=context,
            analysis_result=analysis_result,
            processed_assets=processed_assets,
            output_dir=pres.output_dir,
            literature_summary=literature_summary,
            eda_profile_path=eda_profile_path,
            eda_sweetviz_path=eda_sweetviz_path,
            quick_stats_md=quick_stats_md,
            data_summary=data_analysis_result.get("data_summary"),
            on_progress=on_progress,
        )

        self.logger.info(
            "报告生成完成: report_complete=%s, files=%s",
            report_complete,
            list(generated_files.keys()),
        )

        if eda_profile_path:
            generated_files["eda_profile"] = str(eda_profile_path)
        if eda_sweetviz_path:
            generated_files["eda_sweetviz"] = str(eda_sweetviz_path)

        return {
            "success": True,
            "experiment_id": experiment_id,
            "hypothesis_id": hypothesis_id,
            "analysis_result": analysis_result,
            "generated_files": generated_files,
            "output_directory": str(pres.output_dir),
            "execution_status": context.execution_status,
            "completion_percentage": context.completion_percentage,
            "error_messages": context.error_messages,
            "data_analyzed": data_analyzed,
            "report_complete": report_complete,
        }

    async def _load_context(
        self,
        hypothesis_id: str,
        experiment_id: str,
    ) -> ExperimentContext:
        """从文件和数据库加载实验上下文。"""
        paths = experiment_paths(self.workspace_path, hypothesis_id, experiment_id)
        if not paths.experiment_path.exists():
            raise ValueError(
                f"Experiment path not found for experiment {experiment_id} in hypothesis {hypothesis_id}"
            )
        design = await self._load_design(
            paths.hypothesis_base,
            paths.experiment_path,
            hypothesis_id,
        )
        duration = await self._load_runtime_info(paths.run_path)
        status, completion, errors = await self._analyze_status(paths.run_path)
        return ExperimentContext(
            experiment_id=experiment_id,
            hypothesis_id=hypothesis_id,
            design=design,
            duration_seconds=duration,
            execution_status=status,
            completion_percentage=completion,
            error_messages=errors,
        )

    async def _load_design(
        self,
        hypothesis_base: Path,
        experiment_path: Path,
        hypothesis_id: str,
    ) -> ExperimentDesign:
        """从 HYPOTHESIS.md / EXPERIMENT.md 读取原始 Markdown 文本。"""
        design_data: Dict[str, Any] = {
            "hypothesis": "Hypothesis not specified",
            "objectives": [],
            "variables": {},
            "methodology": "",
            "success_criteria": [],
            "hypothesis_markdown": None,
            "experiment_markdown": None,
        }
        hypothesis_md_path = hypothesis_base / FILE_HYPOTHESIS_MD
        if hypothesis_md_path.exists():
            content = hypothesis_md_path.read_text(encoding="utf-8")
            design_data["hypothesis_markdown"] = content
            # 跳过标题行（以 # 开头），取第一条实质性内容作为 hypothesis
            for line in content.splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    design_data["hypothesis"] = s[:500]
                    break
            if not design_data.get("hypothesis"):
                first_non_empty = next(
                    (line.strip() for line in content.splitlines() if line.strip()),
                    "",
                )
                if first_non_empty:
                    design_data["hypothesis"] = first_non_empty[:500]
            self.logger.info("从 %s 加载假设", hypothesis_md_path)
        else:
            self.logger.warning("假设文件不存在: %s", hypothesis_md_path)
        experiment_md_path = experiment_path / FILE_EXPERIMENT_MD
        if experiment_md_path.exists():
            content = experiment_md_path.read_text(encoding="utf-8")
            design_data["experiment_markdown"] = content
            self.logger.info("从 %s 加载实验设计", experiment_md_path)
        return ExperimentDesign(
            hypothesis=design_data.get("hypothesis", "Hypothesis not specified"),
            objectives=design_data.get("objectives", []),
            variables=design_data.get("variables", {}),
            methodology=design_data.get("methodology", ""),
            success_criteria=design_data.get("success_criteria", []),
            hypothesis_markdown=design_data.get("hypothesis_markdown"),
            experiment_markdown=design_data.get("experiment_markdown"),
        )

    async def _load_runtime_info(self, run_path: Path) -> Optional[float]:
        """从 pid.json 读取开始/结束时间并计算 duration（以秒为单位）。"""
        pid_file = run_path / FILE_PID
        if not pid_file.exists():
            return None
        try:
            data = json_repair.loads(pid_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            return None
        start_s = data.get("start_time")
        end_s = data.get("end_time")
        start_dt = end_dt = None
        if start_s:
            try:
                start_dt = datetime.fromisoformat(start_s.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        if end_s:
            try:
                end_dt = datetime.fromisoformat(end_s.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        if start_dt and end_dt:
            return (end_dt - start_dt).total_seconds()
        return None

    async def _analyze_status(
        self,
        run_path: Path,
    ) -> Tuple[ExperimentStatus, float, List[str]]:
        """从 pid.json 和（可选）sqlite 读取实验状态与完成度；先读库结构再按表查询。"""
        db_path = run_path / FILE_SQLITE
        pid_file = run_path / FILE_PID
        errors: List[str] = []
        status = ExperimentStatus.UNKNOWN
        completion = 0.0
        if not db_path.exists():
            return ExperimentStatus.FAILED, 0.0, ["Database file not found"]
        if pid_file.exists():
            try:
                data = json_repair.loads(pid_file.read_text(encoding="utf-8"))
                pid_status = (data.get("status") or "").strip().lower()
                if pid_status in ("completed", "success", "done"):
                    status = ExperimentStatus.SUCCESSFUL
                elif pid_status in ("failed", "error"):
                    status = ExperimentStatus.FAILED
                elif pid_status in ("running", "in_progress"):
                    status = ExperimentStatus.UNKNOWN
                elif pid_status:
                    status = ExperimentStatus.UNKNOWN
            except (json.JSONDecodeError, ValueError, OSError) as e:
                errors.append(f"Failed to read pid.json: {e}")
        # 补充采集 run/artifacts 与 output.log 中的失败信号，使分析上下文更贴近真实实验运行结果。
        runtime_errors = self._collect_runtime_failures(run_path)
        for msg in runtime_errors:
            if msg not in errors:
                errors.append(msg)
        if runtime_errors and status == ExperimentStatus.SUCCESSFUL:
            status = ExperimentStatus.PARTIAL_SUCCESS

        return status, completion, errors

    def _collect_runtime_failures(self, run_path: Path) -> List[str]:
        """从 run 目录中提取运行期失败信号（ask artifacts / output.log）。"""
        failures: List[str] = []

        artifacts_dir = run_path / "artifacts"
        if artifacts_dir.exists():
            for md_path in artifacts_dir.glob("*.md"):
                try:
                    txt = md_path.read_text(encoding="utf-8")
                except OSError:
                    continue
                lowered = txt.lower()
                if "planning failed" in lowered or "invalid json response" in lowered:
                    failures.append(
                        f"Artifact failure in {md_path.name}: planning failed or invalid JSON response"
                    )

        log_path = run_path / "output.log"
        if log_path.exists():
            try:
                tail = log_path.read_text(encoding="utf-8", errors="ignore")[-8000:]
            except OSError:
                tail = ""
            tail_lower = tail.lower()
            if "traceback" in tail_lower:
                failures.append("Runtime log contains traceback")
            # Prefer structured error patterns over broad substring matching to avoid false positives.
            error_lines = [
                ln.strip()
                for ln in tail.splitlines()
                if re.search(r"\bERROR\b|\bCRITICAL\b", ln)
            ]
            non_ignorable = [
                ln
                for ln in error_lines
                if "litellm" not in ln.lower() and "deprecation" not in ln.lower()
            ]
            if non_ignorable:
                failures.append(
                    "Runtime log contains structured ERROR/CRITICAL entries"
                )

        return failures


class Synthesizer:
    """综合子智能体：对多实验/多假设先逐条分析再综合，产出综合报告。"""

    def __init__(
        self,
        workspace_path: str,
        config: Optional[AnalysisConfig] = None,
    ):
        self.logger = get_logger()
        self.workspace_path = Path(workspace_path).resolve()
        self.config = config or AnalysisConfig(workspace_path=str(self.workspace_path))
        self.temperature = self.config.temperature

        self.llm_router, self.model_name = get_llm_router_and_model(
            self.config.llm_profile_analysis
        )
        self.agent = AnalysisAgent(
            config=self.config,
            llm_router=self.llm_router,
            model_name=self.model_name,
            workspace_path=self.workspace_path,
        )

    def _discover_ids_by_prefix(self, base_path: Path, prefix: str) -> List[str]:
        """在 base_path 下按 prefix 发现子目录 id 列表。"""
        if not base_path.exists():
            return []
        ids: List[str] = []
        for p in base_path.iterdir():
            if not p.is_dir() or not p.name.startswith(prefix):
                continue
            parts = p.name.split(prefix, 1)
            if len(parts) == 2 and parts[1]:
                ids.append(parts[1])
        return sorted(set(ids))

    def _discover_hypotheses(self) -> List[str]:
        """自动发现工作区下的 hypothesis 目录。"""
        return self._discover_ids_by_prefix(self.workspace_path, DIR_HYPOTHESIS_PREFIX)

    def _discover_experiments(self, hypothesis_id: str) -> List[str]:
        """自动发现某个假设下的实验目录（路径经 experiment_paths 清洗）。"""
        paths = experiment_paths(self.workspace_path, hypothesis_id, "0")
        return self._discover_ids_by_prefix(
            paths.hypothesis_base, DIR_EXPERIMENT_PREFIX
        )

    async def _get_hypothesis_text(self, hypothesis_id: str) -> str:
        """从 HYPOTHESIS.md 抽取一段最关键的假设描述（路径经 experiment_paths 清理）。"""
        paths = experiment_paths(self.workspace_path, hypothesis_id, "0")
        hyp_file = paths.hypothesis_base / FILE_HYPOTHESIS_MD
        if not hyp_file.exists():
            return f"Hypothesis {hypothesis_id}"
        content = hyp_file.read_text(encoding="utf-8")

        for line in content.splitlines():
            if "hypothesis" in line.lower():
                line = line.strip()
                if line:
                    return line[:200]
        return content.strip()[:200] or f"Hypothesis {hypothesis_id}"

    async def _analyze_hypothesis(
        self,
        hypothesis_id: str,
        experiment_ids: Optional[List[str]],
        custom_instructions: Optional[str],
        literature_summary: Optional[str],
        service: Analyzer,
        on_progress: AnalysisProgressCallback = None,
    ) -> HypothesisSummary:
        """分析一个假设下的一组实验，并聚合出总结。"""
        exp_ids = experiment_ids or self._discover_experiments(hypothesis_id)
        if not exp_ids:
            return HypothesisSummary(
                hypothesis_id=hypothesis_id,
                hypothesis_text=await self._get_hypothesis_text(hypothesis_id),
                experiment_count=0,
            )

        experiment_results: List[Dict[str, Any]] = []
        successful_count = 0
        total_completion = 0.0
        all_insights: List[str] = []
        all_findings: List[str] = []
        total = len(exp_ids)

        for idx, exp_id in enumerate(exp_ids):
            if on_progress:
                await on_progress(
                    f"Analyzing hypothesis {hypothesis_id} experiment {exp_id} ({idx + 1}/{total})..."
                )
            try:
                result = await service.analyze(
                    hypothesis_id=hypothesis_id,
                    experiment_id=exp_id,
                    custom_instructions=custom_instructions,
                    literature_summary=literature_summary,
                    on_progress=on_progress,
                )
            except Exception as e:
                msg = f"analysis exception: {e}"
                self.logger.warning(
                    "实验分析异常，已记录并继续: hypothesis=%s, experiment=%s, error=%s",
                    hypothesis_id,
                    exp_id,
                    e,
                )
                experiment_results.append(
                    {
                        "experiment_id": exp_id,
                        "success": False,
                        "error": msg,
                    }
                )
                if on_progress:
                    await on_progress(
                        f"Experiment {exp_id} failed but synthesis will continue: {e}"
                    )
                continue

            if not result.get("success"):
                experiment_results.append(
                    {
                        "experiment_id": exp_id,
                        "success": False,
                        "error": result.get("error", "Unknown error"),
                    }
                )
                continue

            completion = float(result.get("completion_percentage", 0.0) or 0.0)
            analysis_result: AnalysisResult = result["analysis_result"]
            experiment_results.append(
                {
                    "experiment_id": exp_id,
                    "success": True,
                    "completion": completion,
                    "insights": analysis_result.insights,
                    "findings": analysis_result.findings,
                    "output_directory": result.get("output_directory", ""),
                    "generated_files": result.get("generated_files", {}),
                }
            )

            if completion >= self.config.completion_success_threshold:
                successful_count += 1
            total_completion += completion
            all_insights.extend(analysis_result.insights)
            all_findings.extend(analysis_result.findings)

        avg_completion = total_completion / len(exp_ids) if exp_ids else 0.0
        return HypothesisSummary(
            hypothesis_id=hypothesis_id,
            hypothesis_text=await self._get_hypothesis_text(hypothesis_id),
            experiment_count=len(exp_ids),
            successful_experiments=successful_count,
            total_completion=avg_completion,
            key_insights=all_insights[:10],
            main_findings=all_findings[:10],
            experiment_results=experiment_results,
        )

    async def _perform_synthesis(
        self,
        hypothesis_summaries: List[HypothesisSummary],
        custom_instructions: Optional[str],
        literature_summary: Optional[str] = None,
    ) -> ExperimentSynthesis:
        """让分析子智能体基于各假设总结做跨假设综合，可结合文献。"""
        synthesis_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        def _fmt_summary(i: int, s: HypothesisSummary) -> str:
            insights_str = (
                ", ".join(s.key_insights[:5]) if s.key_insights else "(none yet)"
            )
            findings_str = (
                ", ".join(s.main_findings[:5]) if s.main_findings else "(none yet)"
            )
            lines = [
                f"Hypothesis {i + 1} (ID: {s.hypothesis_id})",
                f"- Hypothesis text: {s.hypothesis_text}",
                f"- Experiments: {s.experiment_count}",
                f"- Successful experiments: {s.successful_experiments}",
                f"- Average completion: {s.total_completion:.1f}%",
                f"- Key insights: {insights_str}",
                f"- Main findings: {findings_str}",
            ]
            if not s.key_insights and not s.main_findings and s.experiment_results:
                exp_status = "; ".join(
                    f"exp_{r.get('experiment_id', '?')}: "
                    + ("ok" if r.get("success") else f"fail({r.get('error', '')[:50]})")
                    for r in s.experiment_results[:5]
                )
                lines.append(f"- Experiment status: {exp_status}")
            return "\n".join(lines)

        summaries_text = "\n\n".join(
            _fmt_summary(i, s) for i, s in enumerate(hypothesis_summaries)
        )

        instruction_block = (
            f"\nCustom instructions:\n{custom_instructions}\n"
            if custom_instructions
            else ""
        )
        literature_block = (
            f"\n## Literature context (incorporate into synthesis)\n{literature_summary}\n"
            if literature_summary and literature_summary.strip()
            else ""
        )

        prompt = f"""You are performing a cross-hypothesis synthesis of multiple experiment analyses.
You are given aggregated summaries for each hypothesis. Your goal is to compare them and produce an overall synthesis.
**IMPORTANT**: Even when key_insights or main_findings are empty (e.g. experiments still running, or analyses failed), you MUST still produce a complete synthesis. Include: (1) synthesis_strategy, (2) cross_hypothesis_analysis (narrative of hypothesis setup and experiment status), (3) comparative_insights (at least 1 item), (4) unified_conclusions, (5) recommendations (at least 1 item), (6) overall_assessment. Do not leave any tag empty.
{literature_block}
{instruction_block}

Hypothesis summaries:
{summaries_text}

Return only XML in this format:
<synthesis>
<synthesis_strategy>how you approached the synthesis</synthesis_strategy>
<cross_hypothesis_analysis>overall comparative narrative</cross_hypothesis_analysis>
<comparative_insights><item>insight 1</item><item>insight 2</item></comparative_insights>
<unified_conclusions>unified conclusions</unified_conclusions>
<recommendations><item>recommendation 1</item><item>recommendation 2</item></recommendations>
<best_hypothesis>hypothesis_id or empty</best_hypothesis>
<best_hypothesis_reason>reason for selection</best_hypothesis_reason>
<overall_assessment>overall assessment</overall_assessment>
</synthesis>"""

        response = await self.agent.llm_router.acompletion(
            model=self.agent.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
        )

        content = (response.choices[0].message.content or "").strip()
        data = parse_llm_xml_response(content, root_tag="synthesis")

        def _list(v: Any) -> list:
            if isinstance(v, list):
                return v
            if isinstance(v, dict) and "item" in v:
                i = v["item"]
                return i if isinstance(i, list) else [i] if i else []
            return [v] if v else []

        return ExperimentSynthesis(
            synthesis_id=synthesis_id,
            workspace_path=str(self.workspace_path),
            hypothesis_summaries=hypothesis_summaries,
            synthesis_strategy=str(data.get("synthesis_strategy", "")),
            cross_hypothesis_analysis=str(data.get("cross_hypothesis_analysis", "")),
            comparative_insights=_list(data.get("comparative_insights")),
            unified_conclusions=str(data.get("unified_conclusions", "")),
            recommendations=_list(data.get("recommendations")),
            best_hypothesis=data.get("best_hypothesis"),
            best_hypothesis_reason=str(data.get("best_hypothesis_reason", "")),
            overall_assessment=str(data.get("overall_assessment", "")),
        )

    def _generate_synthesis_charts(
        self, synthesis: ExperimentSynthesis, output_dir: Path
    ) -> List[Path]:
        """生成综合分析的可视化图表（跨假设对比），用于综合报告。"""
        assets_dir = output_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        generated: List[Path] = []
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        summaries = synthesis.hypothesis_summaries
        if not summaries:
            return generated

        try:
            ids = [f"H{s.hypothesis_id}" for s in summaries]
            completions = [s.total_completion for s in summaries]
            successes = [s.successful_experiments for s in summaries]
            fig, axes = plt.subplots(1, 2, figsize=(10, 4))
            axes[0].bar(ids, completions, color="steelblue")
            axes[0].set_title("Average Completion %")
            axes[0].set_ylabel("%")
            axes[1].bar(ids, successes, color="seagreen")
            axes[1].set_title("Successful Experiments")
            axes[1].set_ylabel("Count")
            plt.tight_layout()
            chart_path = assets_dir / "synthesis_comparison.png"
            plt.savefig(chart_path, dpi=150, bbox_inches="tight")
            plt.close()
            generated.append(chart_path)
        except Exception as e:
            self.logger.warning("综合图表生成失败: %s", e)
        return generated

    def _build_synthesis_report_context(self, synthesis: ExperimentSynthesis) -> str:
        """构建综合报告上下文供分析子智能体使用。"""
        lines: List[str] = []
        lines.append(f"# Synthesis Report ({synthesis.synthesis_id})")
        lines.append(f"- Workspace: {synthesis.workspace_path}")
        lines.append(f"- Generated at: {synthesis.synthesis_timestamp.isoformat()}")
        lines.append("")
        if synthesis.best_hypothesis:
            lines.append("## Best Hypothesis")
            lines.append(f"- Hypothesis ID: **{synthesis.best_hypothesis}**")
            if synthesis.best_hypothesis_reason:
                lines.append(f"- Reason: {synthesis.best_hypothesis_reason}")
            lines.append("")
        lines.append("## Cross-hypothesis Analysis")
        cross = synthesis.cross_hypothesis_analysis or ""
        if not cross.strip():
            cross = (
                "Data collection or analysis is ongoing. "
                "Summarize the hypothesis setup and experiment status from the per-hypothesis summaries below."
            )
        lines.append(cross)
        lines.append("")
        if synthesis.comparative_insights:
            lines.append("## Comparative Insights")
            for ins in synthesis.comparative_insights:
                lines.append(f"- {ins}")
            lines.append("")
        lines.append("## Unified Conclusions")
        concl = synthesis.unified_conclusions or ""
        if not concl.strip():
            concl = "Draw conclusions from the per-hypothesis summaries. If data is limited, state limitations and next steps."
        lines.append(concl)
        lines.append("")
        if synthesis.recommendations:
            lines.append("## Recommendations")
            for rec in synthesis.recommendations:
                lines.append(f"- {rec}")
            lines.append("")
        lines.append("## Per-hypothesis Summaries")
        for i, summary in enumerate(synthesis.hypothesis_summaries, 1):
            lines.append(f"### Hypothesis {i} (ID: {summary.hypothesis_id})")
            lines.append(f"- Hypothesis text: {summary.hypothesis_text}")
            lines.append(f"- Experiments: {summary.experiment_count}")
            lines.append(f"- Successful: {summary.successful_experiments}")
            lines.append(f"- Avg completion: {summary.total_completion:.1f}%")
            if summary.key_insights:
                lines.append("- Key insights: " + "; ".join(summary.key_insights[:5]))
            if summary.main_findings:
                lines.append("- Main findings: " + "; ".join(summary.main_findings[:5]))
            for r in summary.experiment_results or []:
                if r.get("success") and r.get("generated_files"):
                    gf = r.get("generated_files") or {}
                    md_p = gf.get("markdown")
                    html_p = gf.get("html")
                    if md_p or html_p:
                        paths = [p for p in (md_p, html_p) if p]
                        lines.append(
                            f"- Report (exp {r.get('experiment_id', '?')}): "
                            + "; ".join(str(p) for p in paths)
                        )
            lines.append("")
        lines.append("## Individual Reports (for reference & citation)")
        lines.append(
            "The above lists paths to each experiment's report.md and report.html. "
            "You SHOULD reference and cite these in your synthesis (e.g. 'See experiment 3 report for details', "
            "or link to specific sections). Combine insights from individual reports into the synthesis."
        )
        return "\n".join(lines)

    def _embed_synthesis_charts_in_markdown(
        self, md_path: Path, output_dir: Path, chart_paths: List[Path]
    ) -> None:
        """将综合报告图表引用嵌入 Markdown。若分析子智能体未生成 ![](assets/...) 则追加。"""
        if not chart_paths:
            return
        md_content = md_path.read_text(encoding="utf-8")
        if "](assets/" in md_content:
            return
        lines: List[str] = []
        for p in chart_paths:
            if not p.exists():
                continue
            rel = f"assets/{p.name}"
            title = p.stem.replace("_", " ").title()
            lines.append(f"![{title}]({rel})")
        if not lines:
            return
        section = "\n\n## Charts\n\n" + "\n\n".join(lines)
        md_path.write_text(md_content.rstrip() + section + "\n", encoding="utf-8")
        self.logger.info("已嵌入 %d 张图表到综合报告 Markdown", len(lines))

    def _embed_synthesis_charts_in_html(
        self, html_path: Path, output_dir: Path, chart_paths: List[Path]
    ) -> None:
        """将综合报告图表嵌入 HTML（base64），确保图表可见。"""
        if not chart_paths:
            return
        html_content = html_path.read_text(encoding="utf-8")
        if 'src="assets/' in html_content or "src='assets/" in html_content:
            return
        import base64
        import mimetypes

        imgs: List[Tuple[str, str]] = []
        for p in chart_paths:
            if not p.exists():
                continue
            with open(p, "rb") as f:
                enc = base64.b64encode(f.read()).decode("utf-8")
            mime = (mimetypes.guess_type(p.name) or ("image/png", None))[0]
            imgs.append((p.stem.replace("_", " ").title(), f"data:{mime};base64,{enc}"))
        if not imgs:
            return
        style = (
            ".synth-charts { margin-top:1em; } .synth-charts img { max-width:100%; }"
        )
        imgs_html = "\n".join(
            f'<figure><img src="{emb}" alt="{t}" /><figcaption>{t}</figcaption></figure>'
            for t, emb in imgs
        )
        section = f'<section class="synth-charts"><style>{style}</style><h2>Charts</h2>{imgs_html}</section>'
        if "</body>" in html_content:
            html_content = html_content.replace("</body>", f"{section}\n</body>")
        else:
            html_content += section
        html_path.write_text(html_content, encoding="utf-8")

    def _has_individual_reports(self, synthesis: ExperimentSynthesis) -> bool:
        """是否有可引用的单实验报告。"""
        for s in synthesis.hypothesis_summaries or []:
            for r in s.experiment_results or []:
                if r.get("success") and r.get("generated_files"):
                    gf = r.get("generated_files") or {}
                    if gf.get("markdown") or gf.get("html"):
                        return True
        return False

    async def _judge_synthesis_report(
        self,
        md_content: str,
        html_content: str,
        chart_count: int,
        synthesis: Optional[ExperimentSynthesis] = None,
    ) -> Any:
        """LLM 裁判：判断综合报告质量。"""
        from pydantic import BaseModel

        class SynthesisReportJudgment(BaseModel):
            success: bool
            reason: str
            should_retry: bool = False
            retry_instruction: str = ""

        ref_check = ""
        if synthesis and self._has_individual_reports(synthesis):
            ref_check = (
                " (4) When individual experiment reports exist, does the synthesis "
                "reference or cite them (e.g. 'see experiment X report', links)?"
            )

        prompt = f"""Evaluate the synthesis report.

**Markdown length**: {len(md_content)} chars
**HTML length**: {len(html_content)} chars
**Charts**: {chart_count}

**HTML preview**: {html_content[:600]}...

Check: (1) Both MD and HTML present and meaningful? (2) HTML is complete document? (3) Charts embedded if any?{ref_check}
{judgment_prompt()}"""
        response = await self.agent.llm_router.acompletion(
            model=self.agent.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        content = response.choices[0].message.content or ""
        return parse_llm_xml_to_model(
            content, SynthesisReportJudgment, root_tag="judgment"
        )

    async def _generate_synthesis_report(
        self, synthesis: ExperimentSynthesis
    ) -> Optional[Path]:
        """生成综合报告。

        由 LLM 根据综合内容与图表自主决定布局，直接生成 Markdown 与 HTML，
        经裁判校验。支持重试机制，累积错误历史避免重复问题。

        Args:
            synthesis: 实验综合结果对象。

        Returns:
            生成的报告路径，失败时返回 None。
        """
        output_dir = self.workspace_path / self.config.synthesis_output_dir_name
        output_dir.mkdir(parents=True, exist_ok=True)
        sid = synthesis.synthesis_id
        report_md = output_dir / f"{FILE_SYNTHESIS_REPORT_PREFIX}{sid}.md"
        report_html = output_dir / f"{FILE_SYNTHESIS_REPORT_PREFIX}{sid}.html"

        chart_paths = self._generate_synthesis_charts(synthesis, output_dir)
        chart_block = ""
        if chart_paths:
            chart_block = "\n## Visualizations (for layout)\n"
            for p in chart_paths:
                if p.exists():
                    chart_block += f"\n### {p.stem}\n- Path: assets/{p.name}\n"
            chart_block += '\nEmbed in BOTH HTML and Markdown. HTML: <img src="assets/filename.png" alt="title">. Markdown: ![title](assets/filename.png).\n'

        context = self._build_synthesis_report_context(synthesis)
        if len(context) > 12000:
            context = context[:12000] + "\n\n[... truncated for length ...]"
        if not context.strip() or len(context) < 100:
            context = (
                "# Synthesis Report\n\nNo hypothesis summaries available. "
                "Include at least: Overview, Hypothesis Summary, Current Status, and Recommendations."
            )
        max_retries = self.config.max_synthesis_report_retries
        error_history: list[str] = []  # 累积错误历史

        for attempt in range(max_retries):
            # 构建包含历史错误的反馈
            feedback_block = ""
            if error_history:
                feedback_block = (
                    "\n**Previous issues (avoid these)**:\n"
                    + "\n".join(f"  {i+1}. {err}" for i, err in enumerate(error_history[-3:]))
                    + "\n"
                )

            prompt = f"""You are writing a synthesis report. Below is the synthesis content and available charts.

{context}
{chart_block}
{feedback_block}

Based on this content and charts, produce a **beautiful, professional** synthesis report. **YOU decide the layout** and structure.
**IMPORTANT**: Even with minimal or sparse data, you MUST produce a complete report with: (1) Executive Overview, (2) Hypothesis Summary, (3) Cross-experiment Analysis or Current Status, (4) Conclusions, (5) Recommendations. Do not leave sections empty.
**REFERENCE INDIVIDUAL REPORTS**: When individual experiment reports are listed above, you SHOULD reference and cite them (e.g. "See experiment 3 report for details", or link to report paths). Combine insights from individual reports into the synthesis.
{report_xml_instruction()}
Use CDATA to wrap content. HTML must be a complete, well-styled document. If charts exist, embed in BOTH: HTML <img src=\"assets/filename.png\" alt=\"title\"> and Markdown ![title](assets/filename.png)."""

            try:
                response = await self.agent.llm_router.acompletion(
                    model=self.agent.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self.temperature,
                )
                raw = (response.choices[0].message.content or "").strip()
                data = parse_llm_report_response(raw)
            except XmlParseError as e:
                if attempt >= max_retries - 1:
                    self.logger.warning("综合报告 XML 解析失败: %s", e)
                    return report_md
                error_history.append(f"XML解析错误: {str(e)[:200]}")
                continue
            # 双语解析：优先取中文；markdown/html 为解析器计算出的聚合字段
            md_content = (data.get("markdown_zh") or data.get("markdown") or "").strip()
            html_content = (data.get("html_zh") or data.get("html") or "").strip()
            md_content_en = (data.get("markdown_en") or "").strip()
            html_content_en = (data.get("html_en") or "").strip()

            MIN_CONTENT_LEN = 150
            is_too_short = (
                len(md_content) < MIN_CONTENT_LEN
                and len(html_content) < MIN_CONTENT_LEN
            )
            if is_too_short:
                error_history.append(
                    "报告内容过短 (< 150 chars)，必须包含完整章节"
                )
                self.logger.warning(
                    "综合报告内容过短 (md=%d, html=%d chars)，正在重试 (%s/%s)",
                    len(md_content),
                    len(html_content),
                    attempt + 1,
                    max_retries,
                )
                if attempt >= max_retries - 1:
                    self.logger.warning(
                        "综合报告在 %d 次尝试后仍为空，保存部分结果。",
                        max_retries,
                    )
                    if md_content or html_content:
                        report_md.write_text(md_content, encoding="utf-8")
                        self._embed_synthesis_charts_in_markdown(
                            report_md, output_dir, chart_paths
                        )
                        if html_content:
                            report_html.write_text(html_content, encoding="utf-8")
                            self._embed_synthesis_charts_in_html(
                                report_html, output_dir, chart_paths
                            )
                            synthesis.synthesis_report_html_path = str(report_html)
                    return report_md
                continue

            if md_content or html_content:
                report_md.write_text(md_content, encoding="utf-8")
                self._embed_synthesis_charts_in_markdown(
                    report_md, output_dir, chart_paths
                )
                if html_content:
                    report_html.write_text(html_content, encoding="utf-8")
                    self._embed_synthesis_charts_in_html(
                        report_html, output_dir, chart_paths
                    )
                    synthesis.synthesis_report_html_path = str(report_html)
                # 保存英文版（如存在且与中文不同）
                if md_content_en and md_content_en != md_content:
                    en_md = output_dir / f"{FILE_SYNTHESIS_REPORT_PREFIX}{sid}_en.md"
                    en_md.write_text(md_content_en, encoding="utf-8")
                if html_content_en and html_content_en != html_content:
                    en_html = output_dir / f"{FILE_SYNTHESIS_REPORT_PREFIX}{sid}_en.html"
                    en_html.write_text(html_content_en, encoding="utf-8")

            judgment = await self._judge_synthesis_report(
                md_content, html_content, len(chart_paths), synthesis
            )
            if judgment.success:
                self.logger.info("综合报告已保存: %s", report_md)
                return report_md
            if attempt >= max_retries - 1:
                self.logger.warning(
                    "综合报告裁判失败，已尝试 %d 次，保留最后输出。",
                    max_retries,
                )
                return report_md
            error_history.append(f"{judgment.reason}. {judgment.retry_instruction}")
            self.logger.info("综合报告需要改进: %s，正在重试", judgment.reason)

        return report_md

    async def synthesize(
        self,
        hypothesis_ids: Optional[List[str]] = None,
        experiment_ids: Optional[List[str]] = None,
        custom_instructions: Optional[str] = None,
        literature_summary: Optional[str] = None,
        on_progress: AnalysisProgressCallback = None,
    ) -> ExperimentSynthesis:
        """
        执行多假设/多实验综合分析，可结合文献。

        Args:
            hypothesis_ids: 指定 hypothesis_id 列表；不传则自动发现
            experiment_ids: 指定 experiment_id 列表；不传则每个 hypothesis 自动发现
            custom_instructions: 额外定制说明
            literature_summary: 可选，文献调研摘要，会纳入单实验分析与综合报告
            on_progress: 进度回调
        """

        async def progress(msg: str) -> None:
            if on_progress:
                await on_progress(msg)

        await progress("Discovering hypotheses and experiments...")
        hyp_ids = hypothesis_ids or self._discover_hypotheses()
        if not hyp_ids:
            raise ValueError("No hypotheses found in workspace")

        service = Analyzer(self.config)
        summaries: List[HypothesisSummary] = []
        for idx, hid in enumerate(hyp_ids):
            await progress(f"Analyzing hypothesis {hid} ({idx + 1}/{len(hyp_ids)})...")
            summaries.append(
                await self._analyze_hypothesis(
                    hypothesis_id=hid,
                    experiment_ids=experiment_ids,
                    custom_instructions=custom_instructions,
                    literature_summary=literature_summary,
                    service=service,
                    on_progress=on_progress,
                )
            )

        await progress("Running cross-hypothesis synthesis...")
        synthesis = await self._perform_synthesis(
            summaries, custom_instructions, literature_summary
        )
        await progress("Writing synthesis report...")
        report_path = await self._generate_synthesis_report(synthesis)
        synthesis.synthesis_report_path = str(report_path) if report_path else None
        return synthesis


async def run_analysis(
    workspace_path: str,
    hypothesis_id: str,
    experiment_id: str,
    custom_instructions: Optional[str] = None,
    literature_summary: Optional[str] = None,
    on_progress: AnalysisProgressCallback = None,
) -> Dict[str, Any]:
    """
    触发单实验分析，由分析子智能体产出图文报告。可选 literature_summary 结合文献。

    Returns:
        分析结果字典（含 success、generated_files、analysis_result 等）
    """
    config = AnalysisConfig(workspace_path=workspace_path)
    service = Analyzer(config)
    return await service.analyze(
        hypothesis_id,
        experiment_id,
        custom_instructions,
        literature_summary,
        on_progress=on_progress,
    )


def _discover_experiment_ids(workspace_path: Path, hypothesis_id: str) -> List[str]:
    """在 hypothesis_<id> 下自动发现 experiment id 列表（仅目录名，不读文件内容）。"""
    paths = experiment_paths(workspace_path, hypothesis_id, "0")
    base = paths.hypothesis_base
    if not base.exists():
        return []
    ids: List[str] = []
    for p in base.iterdir():
        if not p.is_dir():
            continue
        if not p.name.startswith(DIR_EXPERIMENT_PREFIX):
            continue
        raw_id = p.name.split(DIR_EXPERIMENT_PREFIX, 1)[-1]
        ids.append(raw_id)

    def _sort_key(x: str) -> tuple[int, str]:
        try:
            return (0, f"{int(x):09d}")
        except ValueError:
            return (1, x)

    # 统一与路径规则一致的清洗逻辑
    from .utils import _sanitize_id as _sanitize

    return sorted({_sanitize(eid) for eid in ids}, key=_sort_key)


async def run_analysis_many(
    workspace_path: str,
    hypothesis_id: str,
    experiment_ids: Optional[List[str]] = None,
    custom_instructions: Optional[str] = None,
    literature_summary: Optional[str] = None,
    on_progress: AnalysisProgressCallback = None,
) -> Dict[str, Any]:
    """按 hypothesis 批量执行单实验分析（互不覆盖），输出到 `presentation/`。

    说明：
    - `run_analysis` 是单实验入口；本函数用于一次性分析一个 hypothesis 下的多个实验。
    - 若不传 experiment_ids，则自动发现 hypothesis_<id>/experiment_*。
    - 每个实验的产物固定落在：`presentation/hypothesis_<hid>/experiment_<eid>/`（报告、data、charts、assets）。
    """
    config = AnalysisConfig(workspace_path=workspace_path)
    service = Analyzer(config)
    wp = Path(workspace_path).resolve()
    eids = experiment_ids or _discover_experiment_ids(wp, hypothesis_id)
    if not eids:
        return {
            "success": False,
            "error": f"No experiments found under hypothesis_{hypothesis_id}",
            "hypothesis_id": hypothesis_id,
            "results": [],
        }

    async def progress(msg: str) -> None:
        if on_progress:
            await on_progress(msg)

    results: List[Dict[str, Any]] = []
    for i, eid in enumerate(eids):
        await progress(f"Analyzing experiment {eid} ({i + 1}/{len(eids)})...")
        results.append(
            await service.analyze(
                hypothesis_id=hypothesis_id,
                experiment_id=eid,
                custom_instructions=custom_instructions,
                literature_summary=literature_summary,
                on_progress=on_progress,
            )
        )

    return {
        "success": True,
        "hypothesis_id": hypothesis_id,
        "experiment_ids": eids,
        "count": len(results),
        "results": results,
    }


async def run_analysis_workflow(
    workspace_path: str,
    mode: str,
    hypothesis_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    experiment_ids: Optional[List[str]] = None,
    hypothesis_ids: Optional[List[str]] = None,
    custom_instructions: Optional[str] = None,
    literature_summary: Optional[str] = None,
    config: Optional[AnalysisConfig] = None,
    on_progress: AnalysisProgressCallback = None,
) -> Dict[str, Any]:
    """统一分析入口：单实验 / 指定多实验 / 综合（跨 hypothesis）。

    - `mode="single"`: 需要 `hypothesis_id` + `experiment_id`，输出到 `presentation/hypothesis_<hid>/experiment_<eid>/`
    - `mode="batch"`: 需要 `hypothesis_id`；`experiment_ids` 可选（不传则自动发现），逐个输出到各自的 presentation 目录
    - `mode="synthesize"`: `hypothesis_ids` / `experiment_ids` 可选（不传则自动发现），输出到 `synthesis/`
    """
    m = (mode or "").strip().lower()
    if m not in {"single", "batch", "synthesize"}:
        raise ValueError("mode must be one of: single|batch|synthesize")

    cfg = config or AnalysisConfig(workspace_path=workspace_path)

    if m == "single":
        if not hypothesis_id or not experiment_id:
            raise ValueError("single mode requires hypothesis_id and experiment_id")
        return {
            "mode": "single",
            **(
                await run_analysis(
                    workspace_path=workspace_path,
                    hypothesis_id=hypothesis_id,
                    experiment_id=experiment_id,
                    custom_instructions=custom_instructions,
                    literature_summary=literature_summary,
                    on_progress=on_progress,
                )
            ),
        }

    if m == "batch":
        if not hypothesis_id:
            raise ValueError("batch mode requires hypothesis_id")
        return {
            "mode": "batch",
            **(
                await run_analysis_many(
                    workspace_path=workspace_path,
                    hypothesis_id=hypothesis_id,
                    experiment_ids=experiment_ids,
                    custom_instructions=custom_instructions,
                    literature_summary=literature_summary,
                    on_progress=on_progress,
                )
            ),
        }

    synth = Synthesizer(workspace_path=workspace_path, config=cfg)
    out = await synth.synthesize(
        hypothesis_ids=hypothesis_ids,
        experiment_ids=experiment_ids,
        custom_instructions=custom_instructions,
        literature_summary=literature_summary,
        on_progress=on_progress,
    )
    return {"mode": "synthesize", "success": True, "synthesis": out.model_dump(mode="json")}

async def run_synthesis(
    workspace_path: str,
    hypothesis_ids: Optional[List[str]] = None,
    experiment_ids: Optional[List[str]] = None,
    custom_instructions: Optional[str] = None,
    literature_summary: Optional[str] = None,
    config: Optional[AnalysisConfig] = None,
    on_progress: AnalysisProgressCallback = None,
) -> ExperimentSynthesis:
    """多实验/多假设综合分析便捷函数；可选 literature_summary 结合文献。"""
    synth = Synthesizer(
        workspace_path=workspace_path,
        config=config or AnalysisConfig(workspace_path=workspace_path),
    )
    return await synth.synthesize(
        hypothesis_ids,
        experiment_ids,
        custom_instructions,
        literature_summary=literature_summary,
        on_progress=on_progress,
    )
