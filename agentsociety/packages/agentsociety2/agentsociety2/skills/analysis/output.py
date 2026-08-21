"""
输出层：EDA、资源管理、双语报告（ReportWriter / Reporter）、附属文件。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import pandas as pd
from pydantic import BaseModel

from agentsociety2.logger import get_logger
from litellm import AllMessageValues

from .models import (
    ExperimentContext,
    AnalysisResult,
    ReportContent,
    ReportAsset,
    AnalysisConfig,
    SUPPORTED_IMAGE_FORMATS,
    DIR_ARTIFACTS,
    DIR_DATA,
    DIR_REPORT_ASSETS,
    DIR_RUN,
    DIR_HYPOTHESIS_PREFIX,
    DIR_EXPERIMENT_PREFIX,
    FILE_README_MD,
    FILE_REPORT_MD,
    FILE_REPORT_HTML,
    FILE_REPORT_ZH_MD,
    FILE_REPORT_ZH_HTML,
    FILE_REPORT_EN_MD,
    FILE_REPORT_EN_HTML,
    FILE_ANALYSIS_SUMMARY_JSON,
)
from .llm_contracts import report_xml_instruction, judgment_prompt, report_judgment_prompt
from .utils import (
    XmlParseError,
    parse_llm_report_response,
    parse_llm_xml_to_model,
    get_analysis_skills,
    AnalysisProgressCallback,
    _sanitize_id,
)

if TYPE_CHECKING:
    from .agents import AnalysisAgent

logger = get_logger()


# ─────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class ReportPaths:
    """报告路径"""
    markdown: Path
    html: Path
    markdown_zh: Optional[Path] = None
    html_zh: Optional[Path] = None
    markdown_en: Optional[Path] = None
    html_en: Optional[Path] = None
    assets_dir: Optional[Path] = None


class ReportJudgment(BaseModel):
    """报告判断"""
    success: bool
    reason: str
    has_markdown: bool = False
    has_html: bool = False
    should_retry: bool = False
    retry_instruction: str = ""


# ─────────────────────────────────────────────────────────────────────────
# AssetManager: 资源管理器
# ─────────────────────────────────────────────────────────────────────────

class AssetManager:
    """资源管理器"""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)
        self.logger = logger

    def discover_assets(
        self,
        experiment_id: str,
        hypothesis_id: str,
    ) -> List[ReportAsset]:
        """发现 `run/artifacts` 下的可视化资源。"""
        hid = _sanitize_id(hypothesis_id)
        eid = _sanitize_id(experiment_id)
        asset_path = (
            self.workspace_path
            / f"{DIR_HYPOTHESIS_PREFIX}{hid}"
            / f"{DIR_EXPERIMENT_PREFIX}{eid}"
            / DIR_RUN
            / DIR_ARTIFACTS
        )

        assets: List[ReportAsset] = []
        if not asset_path.exists():
            return assets

        for file_path in asset_path.rglob("*"):
            if file_path.suffix.lower() not in SUPPORTED_IMAGE_FORMATS:
                continue
            assets.append(ReportAsset(
                asset_id=f"viz_{file_path.stem}",
                asset_type="visualization",
                title=self._format_title(file_path.stem),
                file_path=str(file_path),
                description=f"Generated visualization: {file_path.name}",
                file_size=file_path.stat().st_size,
            ))

        return assets

    def process_assets(
        self,
        assets: List[ReportAsset],
        output_dir: Path,
    ) -> Dict[str, Any]:
        """复制到 `assets/` 并生成可选 base64 嵌入数据。"""
        assets_dir = output_dir / DIR_REPORT_ASSETS
        assets_dir.mkdir(exist_ok=True)
        processed: Dict[str, Any] = {}

        for asset in assets:
            source_path = Path(asset.file_path)
            if not source_path.exists():
                continue

            dest_path = assets_dir / source_path.name
            if source_path.resolve() != dest_path.resolve():
                shutil.copy2(source_path, dest_path)

            # 生成 base64
            embedded = None
            if source_path.suffix.lower() in SUPPORTED_IMAGE_FORMATS:
                with open(source_path, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode("utf-8")
                    mime = (mimetypes.guess_type(source_path.name) or ("image/png", None))[0]
                    embedded = f"data:{mime};base64,{encoded}"

            processed[asset.asset_id] = {
                "title": asset.title,
                "local_path": str(dest_path),
                "relative_path": f"{DIR_REPORT_ASSETS}/{source_path.name}",
                "embedded_data": embedded,
                "description": asset.description,
            }

        return processed

    def _format_title(self, filename: str) -> str:
        """格式化文件名为标题"""
        title = filename.replace("_", " ").replace("-", " ")
        return " ".join(word.capitalize() for word in title.split())


# 历史名称兼容（与 AssetManager 同一实现）
AssetProcessor = AssetManager


# ─────────────────────────────────────────────────────────────────────────
# EDAGenerator: EDA 报告生成器
# ─────────────────────────────────────────────────────────────────────────

class EDAGenerator:
    """EDA 报告生成器 - 集成多种现成的自动分析工具"""

    def __init__(self, config: AnalysisConfig):
        self.config = config
        self.logger = logger

    def generate_quick_stats(
        self,
        db_path: Path,
        max_rows: int = 5000,
    ) -> Optional[str]:
        """生成快速统计摘要"""
        if not db_path.exists():
            return None

        from .data import DataReader
        reader = DataReader(db_path)
        schema = reader.read_schema()
        stats = reader.compute_stats(schema)

        return stats.quick_stats_md

    def generate_missingno_report(
        self,
        db_path: Path,
        output_dir: Path,
        max_rows: int = 50000,
    ) -> Optional[Path]:
        """生成 missingno 缺失值可视化报告

        missingno 是一个专门用于可视化缺失数据的工具，
        可以生成矩阵图、条形图、热力图等来展示数据缺失模式。
        """
        if not db_path.exists():
            return None

        from .data import DataReader
        reader = DataReader(db_path)
        sample = reader.read_sample_data(limit=max_rows)

        if not sample:
            return None

        # 合并所有表的数据进行缺失值分析
        all_dfs = []
        for table_name, data in sample.items():
            if data and len(data) > 0:
                df = pd.DataFrame(data)
                # 添加表名前缀避免列名冲突
                df.columns = [f"{table_name}.{col}" for col in df.columns]
                all_dfs.append(df)

        if not all_dfs:
            return None

        # 只取前几个表的列（避免太多列导致图表混乱）
        combined_df = pd.concat(all_dfs, axis=1)
        if len(combined_df.columns) > 50:
            # 选择缺失值最多的列
            missing_counts = combined_df.isnull().sum()
            top_missing = missing_counts.nlargest(50).index.tolist()
            combined_df = combined_df[top_missing]

        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            import missingno as msno
            import matplotlib.pyplot as plt

            out_file = output_dir / "eda_missingno.html"

            # 创建多子图
            fig, axes = plt.subplots(2, 2, figsize=(16, 12))

            # 1. 缺失值矩阵
            try:
                msno.matrix(combined_df, ax=axes[0, 0], fontsize=8)
                axes[0, 0].set_title("Missing Value Matrix", fontsize=12)
            except Exception:
                axes[0, 0].text(0.5, 0.5, "Matrix plot failed", ha='center', va='center')
                axes[0, 0].set_title("Missing Value Matrix (Error)")

            # 2. 缺失值条形图
            try:
                msno.bar(combined_df, ax=axes[0, 1], fontsize=8)
                axes[0, 1].set_title("Missing Value Bar Chart", fontsize=12)
            except Exception:
                axes[0, 1].text(0.5, 0.5, "Bar plot failed", ha='center', va='center')
                axes[0, 1].set_title("Missing Value Bar (Error)")

            # 3. 缺失值热力图
            try:
                if len(combined_df.columns) > 1:
                    msno.heatmap(combined_df, ax=axes[1, 0], fontsize=8)
                    axes[1, 0].set_title("Missing Value Correlation Heatmap", fontsize=12)
                else:
                    axes[1, 0].text(0.5, 0.5, "Need >1 columns", ha='center', va='center')
                    axes[1, 0].set_title("Correlation Heatmap (Skipped)")
            except Exception:
                axes[1, 0].text(0.5, 0.5, "Heatmap failed", ha='center', va='center')
                axes[1, 0].set_title("Correlation Heatmap (Error)")

            # 4. 缺失值树状图
            try:
                if len(combined_df.columns) > 1:
                    msno.dendrogram(combined_df, ax=axes[1, 1], fontsize=8)
                    axes[1, 1].set_title("Missing Value Dendrogram", fontsize=12)
                else:
                    axes[1, 1].text(0.5, 0.5, "Need >1 columns", ha='center', va='center')
                    axes[1, 1].set_title("Dendrogram (Skipped)")
            except Exception:
                axes[1, 1].text(0.5, 0.5, "Dendrogram failed", ha='center', va='center')
                axes[1, 1].set_title("Dendrogram (Error)")

            plt.tight_layout()
            plt.savefig(str(out_file).replace('.html', '.png'), dpi=150, bbox_inches='tight')
            plt.close()

            # 生成 HTML 包装
            html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Missing Value Analysis (missingno)</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 40px; }}
        h1 {{ color: #333; }}
        .summary {{ background: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0; }}
        .stats {{ margin-top: 10px; }}
        img {{ max-width: 100%; height: auto; margin: 20px 0; border: 1px solid #ddd; }}
    </style>
</head>
<body>
    <h1>Missing Value Analysis</h1>
    <div class="summary">
        <p><strong>Tool</strong>: missingno - Missing data visualization</p>
        <p><strong>Total Columns Analyzed</strong>: {len(combined_df.columns)}</p>
        <p><strong>Total Rows</strong>: {len(combined_df)}</p>
        <div class="stats">
            <p><strong>Total Missing Values</strong>: {combined_df.isnull().sum().sum()}</p>
            <p><strong>Columns with Missing</strong>: {(combined_df.isnull().sum() > 0).sum()}</p>
        </div>
    </div>
    <img src="eda_missingno.png" alt="Missing Value Visualization">
</body>
</html>"""
            out_file.write_text(html_content, encoding="utf-8")

            self.logger.info("生成 missingno 缺失值报告: %s", out_file)
            return out_file

        except ImportError:
            self.logger.warning("missingno 未安装，跳过缺失值可视化")
            return None
        except Exception as e:
            self.logger.warning("missingno 生成失败: %s", e)
            return None

    def generate_correlation_report(
        self,
        db_path: Path,
        output_dir: Path,
        max_rows: int = 50000,
    ) -> Optional[Path]:
        """生成相关性分析报告

        使用 pandas 和 seaborn 生成变量相关性矩阵热力图。
        """
        if not db_path.exists():
            return None

        from .data import DataReader
        reader = DataReader(db_path)
        sample = reader.read_sample_data(limit=max_rows)

        if not sample:
            return None

        output_dir.mkdir(parents=True, exist_ok=True)

        generated_files = []
        for table_name, data in sample.items():
            if not data or len(data) < 2:
                continue

            df = pd.DataFrame(data)
            numeric_df = df.select_dtypes(include=['number'])

            if len(numeric_df.columns) < 2:
                continue

            try:
                import matplotlib.pyplot as plt
                import seaborn as sns

                # 计算相关系数矩阵
                corr_matrix = numeric_df.corr()

                # 生成热力图
                fig, ax = plt.subplots(figsize=(12, 10))
                sns.heatmap(
                    corr_matrix,
                    annot=True,
                    fmt='.2f',
                    cmap='coolwarm',
                    center=0,
                    square=True,
                    linewidths=0.5,
                    ax=ax
                )
                ax.set_title(f"Correlation Matrix: {table_name}", fontsize=14)

                safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in table_name)
                out_file = output_dir / f"correlation_{safe_name}.png"
                plt.tight_layout()
                plt.savefig(str(out_file), dpi=150, bbox_inches='tight')
                plt.close()

                generated_files.append((table_name, str(out_file)))
                self.logger.info("生成相关性矩阵: %s (表: %s, %d 列)", out_file, table_name, len(numeric_df.columns))

            except Exception as e:
                self.logger.warning("生成表 %s 的相关性矩阵失败: %s", table_name, e)

        if not generated_files:
            return None

        # 创建索引页面
        index_file = output_dir / "correlation_index.html"
        rows = "\n".join(
            f'<tr><td>{name}</td><td><img src="{Path(path).name}" style="max-width:800px"></td></tr>'
            for name, path in generated_files
        )
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Correlation Analysis</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 40px; }}
        h1 {{ color: #333; }}
        table {{ border-collapse: collapse; width: 100%; }}
        td {{ padding: 20px; border-bottom: 1px solid #ddd; }}
        img {{ max-width: 100%; }}
    </style>
</head>
<body>
    <h1>Correlation Analysis Report</h1>
    <table>
        <tr><th>Table</th><th>Correlation Matrix</th></tr>
        {rows}
    </table>
</body>
</html>"""
        index_file.write_text(html_content, encoding="utf-8")

        return index_file

    def generate_ydata_profile(
        self,
        db_path: Path,
        output_dir: Path,
        max_rows: int = 10000,
    ) -> Optional[Path]:
        """生成 ydata-profiling EDA 报告

        为所有非空表生成 EDA 报告，合并为一个 HTML 文件。
        如果只有一张表，直接生成单表报告；多张表则生成索引页面。
        """
        if not db_path.exists():
            return None

        from .data import DataReader
        reader = DataReader(db_path)
        sample = reader.read_sample_data(limit=max_rows)

        if not sample:
            return None

        # 过滤出有数据的表
        non_empty_tables = {
            t: data for t, data in sample.items()
            if data and len(data) > 0
        }

        if not non_empty_tables:
            return None

        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            from ydata_profiling import ProfileReport

            if len(non_empty_tables) == 1:
                # 单表：直接生成报告
                table_name = next(iter(non_empty_tables.keys()))
                df = pd.DataFrame(non_empty_tables[table_name])
                out_file = output_dir / "eda_profile.html"
                profile = ProfileReport(df, title=f"EDA: {table_name}", minimal=True)
                profile.to_file(str(out_file))
                self.logger.info("生成 ydata-profiling 报告: %s (表: %s, %d 行)", out_file, table_name, len(df))
                return out_file

            # 多表：为每张表生成独立报告，并创建索引页
            generated_files = []
            for table_name, data in non_empty_tables.items():
                df = pd.DataFrame(data)
                if df.empty:
                    continue
                # 每张表的报告文件名
                safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in table_name)
                table_file = output_dir / f"eda_profile_{safe_name}.html"
                try:
                    profile = ProfileReport(df, title=f"EDA: {table_name}", minimal=True)
                    profile.to_file(str(table_file))
                    generated_files.append((table_name, table_file.name, len(df)))
                    self.logger.info("生成 ydata-profiling 表报告: %s (表: %s, %d 行)", table_file, table_name, len(df))
                except Exception as e:
                    self.logger.warning("生成表 %s 的 EDA 报告失败: %s", table_name, e)

            if not generated_files:
                return None

            # 创建索引页面
            index_file = output_dir / "eda_profile.html"
            index_content = self._build_eda_index_html(generated_files, "ydata-profiling")
            index_file.write_text(index_content, encoding="utf-8")
            self.logger.info("生成 EDA 索引页: %s (%d 张表)", index_file, len(generated_files))
            return index_file

        except Exception as e:
            self.logger.debug("ydata-profiling 生成失败: %s", e)
            return None

    def _build_eda_index_html(self, table_files: List[Tuple[str, str, int]], tool_name: str) -> str:
        """构建 EDA 索引页面 HTML"""
        rows = "\n".join(
            f'<tr><td><a href="{filename}">{name}</a></td><td>{rows}</td></tr>'
            for name, filename, rows in table_files
        )
        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>EDA Reports Index</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 40px; }}
        h1 {{ color: #333; }}
        table {{ border-collapse: collapse; width: 100%; max-width: 600px; margin-top: 20px; }}
        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
        th {{ background-color: #f5f5f5; }}
        tr:hover {{ background-color: #f9f9f9; }}
        a {{ color: #1890ff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .info {{ color: #666; margin-top: 10px; }}
    </style>
</head>
<body>
    <h1>EDA Reports Index ({tool_name})</h1>
    <p class="info">Click on a table name to view its EDA report.</p>
    <table>
        <tr><th>Table</th><th>Rows</th></tr>
{rows}
    </table>
</body>
</html>"""

    def generate_sweetviz_profile(
        self,
        db_path: Path,
        output_dir: Path,
        max_rows: int = 10000,
    ) -> Optional[Path]:
        """生成 Sweetviz EDA 报告

        为所有非空表生成 EDA 报告。
        如果只有一张表，直接生成单表报告；多张表则生成索引页面。
        """
        if not db_path.exists():
            return None

        from .data import DataReader
        reader = DataReader(db_path)
        sample = reader.read_sample_data(limit=max_rows)

        if not sample:
            return None

        # 过滤出有数据的表
        non_empty_tables = {
            t: data for t, data in sample.items()
            if data and len(data) > 0
        }

        if not non_empty_tables:
            return None

        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            import sweetviz as sv

            if len(non_empty_tables) == 1:
                # 单表：直接生成报告
                table_name = next(iter(non_empty_tables.keys()))
                df = pd.DataFrame(non_empty_tables[table_name])
                out_file = output_dir / "eda_sweetviz.html"
                report = sv.analyze(df)
                report.show_html(str(out_file), open_browser=False)
                if out_file.exists():
                    self.logger.info("生成 Sweetviz 报告: %s (表: %s, %d 行)", out_file, table_name, len(df))
                    return out_file
                return None

            # 多表：为每张表生成独立报告，并创建索引页
            generated_files = []
            for table_name, data in non_empty_tables.items():
                df = pd.DataFrame(data)
                if df.empty:
                    continue
                # 每张表的报告文件名
                safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in table_name)
                table_file = output_dir / f"eda_sweetviz_{safe_name}.html"
                try:
                    report = sv.analyze(df)
                    report.show_html(str(table_file), open_browser=False)
                    if table_file.exists():
                        generated_files.append((table_name, table_file.name, len(df)))
                        self.logger.info("生成 Sweetviz 表报告: %s (表: %s, %d 行)", table_file, table_name, len(df))
                except Exception as e:
                    self.logger.warning("生成表 %s 的 Sweetviz 报告失败: %s", table_name, e)

            if not generated_files:
                return None

            # 创建索引页面
            index_file = output_dir / "eda_sweetviz.html"
            index_content = self._build_eda_index_html(generated_files, "Sweetviz")
            index_file.write_text(index_content, encoding="utf-8")
            self.logger.info("生成 Sweetviz EDA 索引页: %s (%d 张表)", index_file, len(generated_files))
            return index_file

        except Exception as e:
            self.logger.debug("Sweetviz 生成失败: %s", e)

        return None


# ─────────────────────────────────────────────────────────────────────────
# ReportWriter: 报告生成器
# ─────────────────────────────────────────────────────────────────────────

class ReportWriter:
    """报告生成器"""

    def __init__(self, config: AnalysisConfig, llm_router, model_name: str):
        self.config = config
        self.llm_router = llm_router
        self.model_name = model_name
        self.logger = logger

    async def generate(
        self,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        processed_assets: Dict[str, Any],
        output_dir: Path,
        literature_summary: Optional[str] = None,
        eda_profile_path: Optional[Path] = None,
        eda_sweetviz_path: Optional[Path] = None,
        quick_stats_md: Optional[str] = None,
        data_summary: Optional[Any] = None,
    ) -> Tuple[ReportPaths, bool]:
        """生成报告"""
        max_retries = self.config.max_analysis_retries

        for attempt in range(max_retries):
            try:
                content = await self._generate_content(
                    context,
                    analysis_result,
                    processed_assets,
                    literature_summary,
                    eda_profile_path,
                    eda_sweetviz_path,
                    quick_stats_md,
                    data_summary,
                )
                paths = await self._save_report(content, output_dir)
                judgment = await self._judge(content, paths, processed_assets)

                if judgment.success:
                    return paths, True

                if not judgment.should_retry or attempt >= max_retries - 1:
                    return paths, False

            except XmlParseError as e:
                if attempt >= max_retries - 1:
                    self.logger.warning("报告生成失败: %s", e)
                    return await self._save_partial_report(context, output_dir), False

        return await self._save_partial_report(context, output_dir), False

    async def _generate_content(
        self,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        processed_assets: Dict[str, Any],
        literature_summary: Optional[str],
        eda_profile_path: Optional[Path],
        eda_sweetviz_path: Optional[Path],
        quick_stats_md: Optional[str],
        data_summary: Optional[Any],
    ) -> ReportContent:
        """生成报告内容"""
        prompt = self._build_prompt(
            context,
            analysis_result,
            processed_assets,
            literature_summary,
            eda_profile_path,
            eda_sweetviz_path,
            quick_stats_md,
            data_summary,
        )

        system = self._build_system_prompt()

        response = await self.llm_router.acompletion(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=self.config.temperature,
        )

        raw = response.choices[0].message.content or ""
        return self._parse_content(raw, context)

    def _build_system_prompt(self) -> str:
        """构建系统 prompt"""
        skills = get_analysis_skills(
            selected_names=self.config.analysis_skill_names,
            strict_selection=self.config.analysis_skill_strict_selection,
        )

        base = """You are a report expert. Based on analysis context, decide layout and generate a professional report.
HTML must be a complete document with proper styles.
Include charts where they support the narrative.

**Must return XML with ALL FOUR bilingual sections**:
- <markdown_zh><![CDATA[Chinese Markdown]]></markdown_zh>
- <html_zh><![CDATA[Chinese HTML]]></html_zh>
- <markdown_en><![CDATA[English Markdown]]></markdown_en>
- <html_en><![CDATA[English HTML]]></html_en>"""

        return f"{skills}\n\n---\n\n{base}" if skills else base

    def _build_prompt(
        self,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        processed_assets: Dict[str, Any],
        literature_summary: Optional[str],
        eda_profile_path: Optional[Path],
        eda_sweetviz_path: Optional[Path],
        quick_stats_md: Optional[str],
        data_summary: Optional[Any],
    ) -> str:
        """构建报告 prompt"""
        viz_block = self._format_viz_for_llm(processed_assets)
        literature_block = f"\n## Literature\n{literature_summary[:3000]}\n" if literature_summary else ""
        eda_block = self._format_eda_block(eda_profile_path, eda_sweetviz_path)
        stats_block = f"\n## Quick Stats\n{quick_stats_md[:2500]}\n" if quick_stats_md else ""
        data_block = self._format_data_context(data_summary)

        return f"""## Experiment Context

**Experiment ID**: {context.experiment_id}
**Hypothesis**: {context.design.hypothesis}
**Status**: {context.execution_status.value}
**Completion**: {context.completion_percentage:.1f}%

## Analysis Results

**Insights**: {self._format_list(analysis_result.insights[:8])}
**Findings**: {self._format_list(analysis_result.findings[:8])}
**Conclusions**: {analysis_result.conclusions[:1500]}
**Recommendations**: {self._format_list(analysis_result.recommendations[:6])}

## Visualizations (choose relevant ones)

{viz_block}
{literature_block}{eda_block}{stats_block}{data_block}

{report_xml_instruction()}"""

    def _format_viz_for_llm(self, assets: Dict[str, Any]) -> str:
        """格式化可视化资源"""
        if not assets:
            return "No visualizations available."

        lines = []
        for asset_id, data in assets.items():
            lines.append(f"### {data['title']}")
            lines.append(f"- Path: {data['relative_path']}")
            lines.append("")

        return "\n".join(lines)

    def _format_eda_block(
        self,
        eda_profile: Optional[Path],
        eda_sweetviz: Optional[Path],
    ) -> str:
        """格式化 EDA 信息"""
        files = []
        if eda_profile and eda_profile.exists():
            files.append("- **data/eda_profile.html** (ydata-profiling)")
        if eda_sweetviz and eda_sweetviz.exists():
            files.append("- **data/eda_sweetviz.html** (Sweetviz)")

        if not files:
            return ""

        return "\n## EDA Reports\n" + "\n".join(files) + "\n"

    def _format_data_context(self, data_summary: Optional[Any]) -> str:
        """格式化数据上下文"""
        if not data_summary:
            return ""

        tables = getattr(data_summary, "tables", [])
        row_counts = getattr(data_summary, "row_counts", {})
        total_rows = sum(row_counts.values()) if row_counts else 0
        non_empty = [t for t in tables if row_counts.get(t, 0) > 0] if tables else []

        return f"""
## Data Context

- **Tables**: {tables}
- **Non-empty**: {non_empty}
- **Total rows**: {total_rows}

**Note**: Ensure insights reference actual table/column names.
"""

    def _format_list(self, items: List[Any]) -> str:
        """格式化列表"""
        if not items:
            return "None"
        return "\n".join([f"- {str(i)[:200]}" for i in items[:8]])

    def _parse_content(self, content: str, context: ExperimentContext) -> ReportContent:
        """解析报告内容"""
        data = parse_llm_report_response(content)

        return ReportContent(
            title=f"Analysis: {context.design.hypothesis}",
            subtitle=f"Experiment {context.experiment_id}",
            format_preference="both",
            full_content_markdown_zh=data.get("markdown_zh"),
            full_content_html_zh=data.get("html_zh"),
            full_content_markdown_en=data.get("markdown_en"),
            full_content_html_en=data.get("html_en"),
        )

    async def _save_report(self, content: ReportContent, output_dir: Path) -> ReportPaths:
        """保存报告"""
        output_dir.mkdir(parents=True, exist_ok=True)
        assets_dir = output_dir / DIR_REPORT_ASSETS
        assets_dir.mkdir(exist_ok=True)

        # 保存中文报告
        md_zh, html_zh = None, None
        if content.full_content_markdown_zh:
            md_zh = output_dir / FILE_REPORT_ZH_MD
            md_zh.write_text(content.full_content_markdown_zh, encoding="utf-8")
            (output_dir / FILE_REPORT_MD).write_text(content.full_content_markdown_zh, encoding="utf-8")
        if content.full_content_html_zh:
            html_zh = output_dir / FILE_REPORT_ZH_HTML
            html_zh.write_text(content.full_content_html_zh, encoding="utf-8")
            (output_dir / FILE_REPORT_HTML).write_text(content.full_content_html_zh, encoding="utf-8")

        # 保存英文报告
        md_en, html_en = None, None
        if content.full_content_markdown_en:
            md_en = output_dir / FILE_REPORT_EN_MD
            md_en.write_text(content.full_content_markdown_en, encoding="utf-8")
            if not content.full_content_markdown_zh:
                (output_dir / FILE_REPORT_MD).write_text(content.full_content_markdown_en, encoding="utf-8")
        if content.full_content_html_en:
            html_en = output_dir / FILE_REPORT_EN_HTML
            html_en.write_text(content.full_content_html_en, encoding="utf-8")
            if not content.full_content_html_zh:
                (output_dir / FILE_REPORT_HTML).write_text(content.full_content_html_en, encoding="utf-8")

        return ReportPaths(
            markdown=output_dir / FILE_REPORT_MD,
            html=output_dir / FILE_REPORT_HTML,
            markdown_zh=md_zh,
            html_zh=html_zh,
            markdown_en=md_en,
            html_en=html_en,
            assets_dir=assets_dir,
        )

    async def _judge(
        self,
        content: ReportContent,
        paths: ReportPaths,
        assets: Dict[str, Any],
    ) -> ReportJudgment:
        """判断报告质量"""
        md_len = len(content.full_content_markdown or "")
        html_len = len(content.full_content_html or "")
        html_preview = (content.full_content_html or "")[:800]

        prompt = f"""Evaluate the report.

**Markdown length**: {md_len} chars
**HTML length**: {html_len} chars
**Charts available**: {len(assets)}

**HTML preview**: {html_preview}...

**Check**:
1. Both MD and HTML present?
2. HTML is complete document?
3. Charts embedded if needed?

{judgment_prompt()}"""

        response = await self.llm_router.acompletion(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )

        return parse_llm_xml_to_model(
            response.choices[0].message.content or "",
            ReportJudgment,
            root_tag="judgment",
        )

    async def _save_partial_report(
        self,
        context: ExperimentContext,
        output_dir: Path,
    ) -> ReportPaths:
        """保存部分报告"""
        output_dir.mkdir(parents=True, exist_ok=True)

        # 创建空报告
        md_path = output_dir / FILE_REPORT_MD
        html_path = output_dir / FILE_REPORT_HTML

        md_path.write_text(f"# Analysis Report\n\nExperiment: {context.experiment_id}\n", encoding="utf-8")
        html_path.write_text(f"<html><body><h1>Analysis Report</h1><p>Experiment: {context.experiment_id}</p></body></html>", encoding="utf-8")

        return ReportPaths(markdown=md_path, html=html_path)

class ReportGenerationResult(BaseModel):
    """报告生成结果判断"""

    success: bool
    reason: str
    has_markdown: bool
    has_html: bool
    should_retry: bool = False
    retry_instruction: str = ""

class Reporter:
    """报告子智能体：将洞察与图表组装成图文并茂的 Markdown/HTML 报告。"""

    def __init__(self, agent: AnalysisAgent, config: AnalysisConfig):
        """
        Args:
            agent: AnalysisAgent 实例（用于 LLM 生成内容）
            config: 分析配置（必须，用于 max_retries 等）
        """
        self.logger = get_logger()
        self.agent = agent
        self.config = config
        self.max_retries = config.max_analysis_retries
        self.logger.info("使用 %s 来生成报告", self.agent.model_name)

    @staticmethod
    def _build_retry_feedback(error_history: list[str]) -> str:
        """构建包含错误历史的反馈内容。

        将累积的错误历史格式化为反馈信息，供 LLM 在下一次迭代时参考，
        避免重复相同的错误。

        Args:
            error_history: 累积的错误历史列表。

        Returns:
            格式化后的反馈字符串，最多显示最近 3 个错误。
        """
        if not error_history:
            return ""
        parts = ["**Previous issues (avoid these mistakes)**:"]
        for i, err in enumerate(error_history[-3:]):  # 只保留最近3个
            parts.append(f"  {i+1}. {err}")
        return "\n".join(parts)

    async def generate(
        self,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        processed_assets: Dict[str, Any],
        output_dir: Path,
        literature_summary: Optional[str] = None,
        eda_profile_path: Optional[Path] = None,
        eda_sweetviz_path: Optional[Path] = None,
        quick_stats_md: Optional[str] = None,
        data_summary: Optional[Any] = None,
        on_progress: AnalysisProgressCallback = None,
    ) -> Tuple[Dict[str, str], bool]:
        """生成图文并茂的报告（Markdown + HTML）。

        支持重试机制，累积错误历史以提高迭代效率。

        Args:
            context: 实验上下文。
            analysis_result: 分析结果。
            processed_assets: 处理后的资产字典。
            output_dir: 输出目录。
            literature_summary: 文献摘要，可选。
            eda_profile_path: ydata-profiling EDA 概览路径，可选。
            eda_sweetviz_path: Sweetviz EDA 报告路径，可选。
            quick_stats_md: pandas describe 统计摘要，可选。
            data_summary: DataSummary 对象，用于交叉验证，可选。
            on_progress: 进度回调函数，可选。

        Returns:
            元组 (files, success):
            - files: 生成的文件路径字典
            - success: 报告是否成功生成
        """

        async def progress(msg: str) -> None:
            if on_progress:
                await on_progress(msg)

        max_retries = self.max_retries
        retry_count = 0
        error_history: list[str] = []  # 累积错误历史

        while retry_count < max_retries:
            # 构建包含历史错误的反馈
            combined_feedback = self._build_retry_feedback(error_history)
            
            try:
                await progress("Generating report content...")
                content = await self._generate_content(
                    context,
                    analysis_result,
                    processed_assets,
                    literature_summary,
                    eda_profile_path,
                    eda_sweetviz_path,
                    quick_stats_md,
                    data_summary,
                    previous_retry_instruction=combined_feedback,
                )
                files = {}
                await progress("Saving report (Markdown & HTML)...")
                md_path = await self._save_markdown(content, output_dir)
                md_path = self._embed_charts_in_markdown(
                    md_path, output_dir, processed_assets
                )
                files["markdown"] = str(md_path)
                html_path = await self._save_html(content, output_dir)
                html_path = self._embed_charts_in_html(
                    html_path, output_dir, processed_assets
                )
                html_path = self._embed_eda_in_html(
                    html_path, output_dir, eda_profile_path, eda_sweetviz_path
                )
                files["html"] = str(html_path)
                await progress("Checking report quality...")
                judgment = await self._judge_report_generation(
                    content, md_path, html_path, processed_assets
                )
            except XmlParseError as e:
                self.logger.warning("报告XML解析失败: %s", e)
                if retry_count >= max_retries - 1:
                    files = await self._save_supporting_files(
                        context, analysis_result, output_dir
                    )
                    files["markdown"] = str(output_dir / FILE_REPORT_MD)
                    files["html"] = str(output_dir / FILE_REPORT_HTML)
                    return (files, False)
                error_history.append(f"XML解析错误: {str(e)[:200]}")
                retry_count += 1
                self.logger.info(
                    "重试报告生成，XML解析错误 (%s/%s)", retry_count, max_retries
                )
                continue
            except Exception as e:
                self.logger.warning("报告生成异常: %s", e)
                judgment = await self._judge_exception(e, retry_count, max_retries)
                if not judgment.should_retry or retry_count >= max_retries - 1:
                    self.logger.error("报告生成异常后失败: %s", judgment.reason)
                    files = await self._save_supporting_files(
                        context, analysis_result, output_dir
                    )
                    files["markdown"] = str(output_dir / FILE_REPORT_MD)
                    files["html"] = str(output_dir / FILE_REPORT_HTML)
                    return (files, False)
                error_history.append(f"异常: {judgment.reason}")
                retry_count += 1
                self.logger.info(
                    "重试报告生成，异常 (%s/%s): %s",
                    retry_count,
                    max_retries,
                    judgment.retry_instruction,
                )
                continue

            if judgment.success:
                files.update(
                    await self._save_supporting_files(
                        context, analysis_result, output_dir
                    )
                )
                return (files, True)

            if not judgment.should_retry or retry_count >= max_retries - 1:
                self.logger.warning(
                    "报告生成失败: %s. 保存部分结果。",
                    judgment.reason,
                )
                files.update(
                    await self._save_supporting_files(
                        context, analysis_result, output_dir
                    )
                )
                return (files, False)

            error_history.append(judgment.reason)
            retry_count += 1
            self.logger.info(
                "重试报告生成 (%s/%s): %s",
                retry_count,
                max_retries,
                judgment.retry_instruction,
            )

        # 重试耗尽，保存部分结果
        self.logger.warning("报告生成重试耗尽，保存部分结果")
        files = await self._save_supporting_files(
            context, analysis_result, output_dir
        )
        files["markdown"] = str(output_dir / FILE_REPORT_MD)
        files["html"] = str(output_dir / FILE_REPORT_HTML)
        return (files, False)

    async def _generate_content(
        self,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        processed_assets: Dict[str, Any],
        literature_summary: Optional[str] = None,
        eda_profile_path: Optional[Path] = None,
        eda_sweetviz_path: Optional[Path] = None,
        quick_stats_md: Optional[str] = None,
        data_summary: Optional[Any] = None,
        previous_retry_instruction: Optional[str] = None,
    ) -> ReportContent:
        """构建报告生成 prompt 并调用 LLM 生成内容。

        Args:
            context: 实验上下文。
            analysis_result: 分析结果。
            processed_assets: 处理后的资产字典。
            literature_summary: 文献摘要，可选。
            eda_profile_path: ydata-profiling EDA 概览路径，可选。
            eda_sweetviz_path: Sweetviz EDA 报告路径，可选。
            quick_stats_md: pandas describe 统计摘要，可选。
            data_summary: DataSummary 对象，可选。
            previous_retry_instruction: 上一次重试的反馈信息，可选。

        Returns:
            ReportContent 对象，包含中英文 Markdown 和 HTML 内容。
        """
        prompt = self._build_prompt(
            context,
            analysis_result,
            processed_assets,
            literature_summary,
            eda_profile_path,
            eda_sweetviz_path,
            quick_stats_md,
            data_summary,
            previous_retry_instruction,
        )
        skills = get_analysis_skills(
            selected_names=self.config.analysis_skill_names,
            strict_selection=self.config.analysis_skill_strict_selection,
        )
        system = (
            f"{skills}\n\n---\n\n"
            "You are an experiment report expert. Based on analysis context and data, **decide** layout, structure, and which charts to include. "
            "Select charts that best support your findings; place them where they fit the narrative. You may include all, some, or none—based on relevance. "
            "HTML must be **professional and visually appealing**: proper layout, clear hierarchy, spacing and styling. "
            f"{report_xml_instruction()} "
            'For charts you include: HTML use <img src="assets/filename.png" alt="title">; Markdown use ![title](assets/filename.png).'
        )
        if not skills:
            system = (
                "Based on report content and charts, decide layout and generate a professional HTML report. "
                f"{report_xml_instruction()}"
            )
        messages: List[AllMessageValues] = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        self.logger.info("使用 %s 生成报告内容", self.agent.model_name)

        response = await self.agent.llm_router.acompletion(
            model=self.agent.model_name,
            messages=messages,
            temperature=self.agent.temperature,
        )

        llm_content = response.choices[0].message.content or ""
        self.logger.info(
            "报告生成 LLM 返回长度: %d 字符, 前 200 字符: %s",
            len(llm_content),
            llm_content[:200].replace("\n", " "),
        )
        return self._parse_content(llm_content, context)

    def _build_prompt(
        self,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        processed_assets: Dict[str, Any],
        literature_summary: Optional[str] = None,
        eda_profile_path: Optional[Path] = None,
        eda_sweetviz_path: Optional[Path] = None,
        quick_stats_md: Optional[str] = None,
        data_summary: Optional[Any] = None,
        previous_retry_instruction: Optional[str] = None,
    ) -> str:
        status_msg = self._get_status_message(context.execution_status.value)
        viz_block = self._format_viz_for_llm(processed_assets)
        retry_block = ""
        if previous_retry_instruction and previous_retry_instruction.strip():
            retry_block = f"\n## Previous feedback (must address)\n{previous_retry_instruction.strip()}\n\n"
        literature_block = ""
        if literature_summary and literature_summary.strip():
            lit = literature_summary.strip()
            if len(lit) > 3000:
                lit = lit[:3000] + "\n\n[... truncated for length ...]"
            literature_block = f"\n## Literature\n{lit}\n\n"
        eda_block = ""
        eda_files: List[str] = []
        if eda_profile_path and eda_profile_path.exists():
            eda_files.append(
                "**data/eda_profile.html** (ydata-profiling: stats, distributions, missing)"
            )
        if eda_sweetviz_path and eda_sweetviz_path.exists():
            eda_files.append(
                "**data/eda_sweetviz.html** (Sweetviz: correlations, target analysis)"
            )
        if eda_files:
            eda_block = (
                "\n## Data overview (EDA)\n"
                "Exploratory data analysis profiles generated:\n"
                + "\n".join(f"- {f}" for f in eda_files)
                + '\n\nInclude links or references to these in your report (e.g. a "Data Overview" section). '
                "Summarize key findings if relevant to the hypothesis.\n\n"
            )
        quick_stats_block = ""
        if quick_stats_md and quick_stats_md.strip():
            qs = quick_stats_md.strip()
            if len(qs) > 2500:
                qs = qs[:2500] + "\n\n[... truncated ...]"
            quick_stats_block = (
                "\n## Quick stats (pandas describe)\n"
                "Use these for reference when discussing data:\n\n"
                f"{qs}\n\n"
            )

        # 数据验证区块：确保洞察基于实际数据
        data_validation_block = ""
        if data_summary is not None:
            tables = getattr(data_summary, "tables", [])
            row_counts = getattr(data_summary, "row_counts", {})
            total_rows = sum(row_counts.values()) if row_counts else 0
            non_empty = (
                [t for t in tables if row_counts.get(t, 0) > 0] if tables else []
            )
            empty = [t for t in tables if row_counts.get(t, 0) == 0] if tables else []

            data_validation_block = f"""
## Data Context (For Cross-Validation)

**CRITICAL**: Ensure the insights below are grounded in ACTUAL data:
- **Tables**: {tables}
- **Non-empty tables**: {non_empty}
- **Empty tables**: {empty}
- **Total rows**: {total_rows}

**Verification Checklist**:
- Insights should reference tables/columns that exist in the schema
- If tables are empty, insights should acknowledge data limitations
- Numerical claims should match the data statistics

"""

        return f"""## Experiment Context

**Experiment ID**: {context.experiment_id}
**Hypothesis**: {context.design.hypothesis}
**Completion**: {context.completion_percentage:.1f}%
**Status**: {context.execution_status.value}
**Duration**: {f"{context.duration_seconds:.2f}s" if context.duration_seconds else "Not available"}

**Objectives**: {self._format_list(context.design.objectives) if context.design.objectives else "Not specified"}
**Success Criteria**: {self._format_list(context.design.success_criteria) if context.design.success_criteria else "Not specified"}
**Status context**: {status_msg}

## Analysis Results

**Key Insights** ({len(analysis_result.insights)}):
{self._format_list_truncated(analysis_result.insights, max_items=8, max_item_len=250)}

**Findings** ({len(analysis_result.findings)}):
{self._format_list_truncated(analysis_result.findings, max_items=8, max_item_len=250)}

**Conclusions**:
{self._truncate_text(analysis_result.conclusions or "", 1500)}

**Recommendations** ({len(analysis_result.recommendations)}):
{self._format_list_truncated(analysis_result.recommendations, max_items=6, max_item_len=200)}

## Visualizations (for layout)

{viz_block}
{retry_block}{literature_block}
{eda_block}
{quick_stats_block}
{data_validation_block}

Based on the above content, generate a professional report. **Decide** which visualizations (if any) support your analysis and embed them where they fit. HTML must be a complete document (DOCTYPE, head, body, styles).

**IMPORTANT**: Cross-validate insights against the Data Context section. Do NOT reference tables/columns that don't exist."""

    def _format_viz_for_llm(self, processed_assets: Dict[str, Any]) -> str:
        """将图表信息提供给 LLM，仅传路径和标题以控制 prompt 长度（不传 base64）。"""
        if not processed_assets:
            return "No visualizations available."
        lines = []
        for asset_id, data in processed_assets.items():
            title = data.get("title", asset_id)
            rel = data.get("relative_path", "")
            desc = (data.get("description") or "").strip()
            lines.append(f"### {title}")
            lines.append(f"- Path: {rel}")
            if desc:
                lines.append(f"- Description: {desc[:200]}")
            lines.append("")
        lines.append(
            "**Decide** which charts support your analysis and embed them where appropriate. "
            f'HTML: <img src="{DIR_REPORT_ASSETS}/filename.png" alt="title">. '
            f"Markdown: ![title]({DIR_REPORT_ASSETS}/filename.png)."
        )
        return "\n".join(lines)

    def _parse_content(self, content: str, context: ExperimentContext) -> ReportContent:
        """解析 LLM 返回的 XML，获取中英双语 markdown 与 html。"""
        try:
            data = parse_llm_report_response(content)
        except XmlParseError as e:
            self.logger.warning(
                "报告 XML 解析失败: %s, 原始内容前 500 字符: %s",
                e,
                (e.raw_content or content)[:500].replace("\n", "\\n"),
            )
            raise
        title = f"Analysis: {context.design.hypothesis}"
        subtitle = f"Experiment {context.experiment_id}"
        md_zh_len = len(data.get("markdown_zh") or "")
        html_zh_len = len(data.get("html_zh") or "")
        md_en_len = len(data.get("markdown_en") or "")
        html_en_len = len(data.get("html_en") or "")
        self.logger.info(
            "报告解析成功: markdown_zh=%d, html_zh=%d, markdown_en=%d, html_en=%d 字符",
            md_zh_len, html_zh_len, md_en_len, html_en_len,
        )
        return ReportContent(
            title=title,
            subtitle=subtitle,
            format_preference="both",
            full_content_markdown_zh=(data.get("markdown_zh") or "").strip() or None,
            full_content_html_zh=(data.get("html_zh") or "").strip() or None,
            full_content_markdown_en=(data.get("markdown_en") or "").strip() or None,
            full_content_html_en=(data.get("html_en") or "").strip() or None,
        )

    async def _judge_exception(
        self, exc: Exception, retry_count: int, max_retries: int
    ) -> ReportGenerationResult:
        """出现异常时由 LLM 裁判判断是否可重试及改进方向。"""
        prompt = f"""An exception occurred during report generation:

**Exception type**: {type(exc).__name__}
**Exception message**: {exc}

Please judge:
1. Can this exception be resolved by retry? (e.g. transient network failure, LLM output format issues)
2. If retryable, how to improve? (e.g. check dependencies, adjust prompt)
3. If not retryable (e.g. missing required dependency like markdown), state clearly.

Current retry count: {retry_count}/{max_retries}

{report_judgment_prompt()}"""
        try:
            response = await self.agent.llm_router.acompletion(
                model=self.agent.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            content = response.choices[0].message.content
            if content:
                return parse_llm_xml_to_model(
                    content, ReportGenerationResult, root_tag="judgment"
                )
        except Exception as judge_err:
            self.logger.warning("裁判异常失败: %s", judge_err)
        is_import = isinstance(exc, ImportError)
        return ReportGenerationResult(
            success=False,
            reason=str(exc),
            has_markdown=False,
            has_html=False,
            should_retry=not is_import and retry_count < max_retries - 1,
            retry_instruction=(
                "Install missing dependencies (e.g. pip install markdown) or fix the reported error."
                if is_import
                else "Retry report generation; check logs for details."
            ),
        )

    async def _judge_report_generation(
        self,
        content: ReportContent,
        md_path: Path,
        html_path: Path,
        processed_assets: Optional[Dict[str, Any]] = None,
    ) -> ReportGenerationResult:
        """裁判报告生成结果。

        检查生成的报告是否完整、格式是否正确，决定是否需要重试。

        Args:
            content: 报告内容对象。
            md_path: Markdown 文件路径。
            html_path: HTML 文件路径。
            processed_assets: 处理后的资产字典，可选。

        Returns:
            ReportGenerationResult 对象，包含 success、reason、should_retry 等字段。
        """
        md_exists = md_path.exists() and md_path.stat().st_size > 0
        html_exists = html_path.exists() and html_path.stat().st_size > 0
        md_text = content.full_content_markdown or ""
        html_text = content.full_content_html or ""
        has_markdown_content = bool(md_text.strip())
        has_html_content = bool(html_text.strip())
        html_preview = html_text[:800]
        num_assets = len(processed_assets) if processed_assets else 0

        self.logger.info(
            "报告裁判: md_exists=%s, html_exists=%s, md_len=%d, html_len=%d, assets=%d",
            md_exists, html_exists, len(md_text), len(html_text), num_assets,
        )

        report_summary = f"""## Report Generation Result

**Markdown Report**:
- File exists: {md_exists}
- Has content: {has_markdown_content}
- File size: {md_path.stat().st_size if md_exists else 0} bytes

**HTML Report**:
- File exists: {html_exists}
- Has content: {has_html_content}
- File size: {html_path.stat().st_size if html_exists else 0} bytes

**Visualizations**: {num_assets} assets available. Author may have chosen to include all, some, or none.

**HTML Preview** (first 800 chars):
{html_preview}

Evaluate:
1. Both Markdown and HTML must be present and meaningful.
2. HTML must be complete document (DOCTYPE, head, body) with proper layout.
3. If the report includes chart references, they should be properly embedded (img src, assets path).
4. success=true if content is coherent and HTML is properly formatted. Chart inclusion is the author's choice.

{report_judgment_prompt()}"""

        messages: List[AllMessageValues] = [{"role": "user", "content": report_summary}]

        response = await self.agent.llm_router.acompletion(
            model=self.agent.model_name,
            messages=messages,
            temperature=0.3,
        )

        response_content = response.choices[0].message.content
        if not response_content:
            self.logger.warning("报告裁判: LLM 返回空响应")
            return ReportGenerationResult(
                success=False,
                reason="LLM returned empty response",
                has_markdown=has_markdown_content,
                has_html=has_html_content,
                should_retry=True,
                retry_instruction="Regenerate report with complete content",
            )

        try:
            result = parse_llm_xml_to_model(
                response_content, ReportGenerationResult, root_tag="judgment"
            )
            self.logger.info(
                "报告裁判结果: success=%s, reason=%s, should_retry=%s",
                result.success, result.reason[:100] if result.reason else "", result.should_retry,
            )
            return result
        except XmlParseError as e:
            self.logger.warning("报告裁判 XML 解析失败: %s", e)
            return ReportGenerationResult(
                success=False,
                reason=f"Judge XML parse failed: {e}",
                has_markdown=has_markdown_content,
                has_html=has_html_content,
                should_retry=False,
                retry_instruction="",
            )

    async def _save_markdown(
        self,
        content: ReportContent,
        output_dir: Path,
    ) -> Path:
        """保存双语 Markdown 报告。中文版为主文件，英文版为 report_en.md。"""
        primary_path = output_dir / FILE_REPORT_MD

        if content.full_content_markdown_zh:
            (output_dir / FILE_REPORT_ZH_MD).write_text(
                content.full_content_markdown_zh, encoding="utf-8"
            )
            primary_path.write_text(content.full_content_markdown_zh, encoding="utf-8")
            self.logger.info("保存中文 Markdown 报告: %s", primary_path)

        if content.full_content_markdown_en:
            en_path = output_dir / FILE_REPORT_EN_MD
            en_path.write_text(content.full_content_markdown_en, encoding="utf-8")
            self.logger.info("保存英文 Markdown 报告: %s", en_path)
            if not content.full_content_markdown_zh:
                primary_path.write_text(content.full_content_markdown_en, encoding="utf-8")

        if not content.full_content_markdown_zh and not content.full_content_markdown_en:
            primary_path.write_text("", encoding="utf-8")

        return primary_path

    async def _save_html(
        self,
        content: ReportContent,
        output_dir: Path,
    ) -> Path:
        """保存双语 HTML 报告。中文版为主文件，英文版为 report_en.html。"""
        primary_path = output_dir / FILE_REPORT_HTML

        if content.full_content_html_zh:
            (output_dir / FILE_REPORT_ZH_HTML).write_text(
                content.full_content_html_zh, encoding="utf-8"
            )
            primary_path.write_text(content.full_content_html_zh, encoding="utf-8")
            self.logger.info("保存中文 HTML 报告: %s", primary_path)

        if content.full_content_html_en:
            en_path = output_dir / FILE_REPORT_EN_HTML
            en_path.write_text(content.full_content_html_en, encoding="utf-8")
            self.logger.info("保存英文 HTML 报告: %s", en_path)
            if not content.full_content_html_zh:
                primary_path.write_text(content.full_content_html_en, encoding="utf-8")

        if not content.full_content_html_zh and not content.full_content_html_en:
            primary_path.write_text("", encoding="utf-8")

        return primary_path

    def _embed_charts_in_html(
        self,
        html_path: Path,
        output_dir: Path,
        processed_assets: Dict[str, Any],
    ) -> Path:
        """
        图表嵌入由分析子智能体在生成报告时智能决定，此处不做硬编码补充。
        图表文件已由 process_assets 复制到 assets/，分析子智能体通过相对路径引用即可。
        """
        return html_path

    def _embed_charts_in_markdown(
        self,
        md_path: Path,
        output_dir: Path,
        processed_assets: Dict[str, Any],
    ) -> Path:
        """
        图表嵌入由分析子智能体在生成报告时智能决定，此处不做硬编码补充。
        """
        return md_path

    def _embed_eda_in_html(
        self,
        html_path: Path,
        output_dir: Path,
        eda_profile_path: Optional[Path] = None,
        eda_sweetviz_path: Optional[Path] = None,
    ) -> Path:
        """
        将 EDA 报告嵌入报告 HTML，形成统一入口。在 </body> 前插入 Data Overview 区块，
        含 iframe 嵌入 ydata/sweetviz 生成的 HTML。
        """
        eda_parts: List[Tuple[str, str]] = []
        for p, title in (
            (eda_profile_path, "ydata-profiling"),
            (eda_sweetviz_path, "Sweetviz"),
        ):
            if p and p.exists() and (p == output_dir or output_dir in p.parents):
                eda_parts.append((title, str(p.relative_to(output_dir))))

        if not eda_parts:
            return html_path

        html_content = html_path.read_text(encoding="utf-8")
        eda_section = self._build_eda_embed_section(eda_parts)
        if "</body>" in html_content:
            html_content = html_content.replace("</body>", f"{eda_section}\n</body>")
        else:
            html_content += eda_section
        html_path.write_text(html_content, encoding="utf-8")
        self.logger.info("将 EDA 报告嵌入报告 HTML")
        return html_path

    def _build_eda_embed_section(self, eda_parts: List[Tuple[str, str]]) -> str:
        """构建 EDA 报告嵌入区块 HTML。"""
        style = """
        .eda-embed-section { margin-top: 2em; padding: 1em; border-top: 1px solid #ddd; }
        .eda-embed-section h2 { font-size: 1.25em; margin-bottom: 0.5em; }
        .eda-embed-section iframe { width: 100%; height: 600px; border: 1px solid #ccc; margin-top: 0.5em; }
        """
        parts_html = ""
        for title, src in eda_parts:
            parts_html += f'<div class="eda-embed"><h3>{title}</h3><iframe src="{src}" title="{title}"></iframe></div>'
        return f"""
<section class="eda-embed-section" id="data-overview">
<style>{style}</style>
<h2>Data Overview (EDA)</h2>
<p>Exploratory data analysis profiles. Scroll within each frame to explore.</p>
{parts_html}
</section>"""

    async def _save_supporting_files(
        self,
        context: ExperimentContext,
        analysis_result: AnalysisResult,
        output_dir: Path,
    ) -> Dict[str, str]:
        """
        保存支持文件。

        Args:
            context: 实验上下文
            analysis_result: 分析结果
            output_dir: 输出目录

        Returns:
            文件路径字典
        """
        files = {}

        data_dir = output_dir / DIR_DATA
        data_dir.mkdir(exist_ok=True)
        result_file = data_dir / FILE_ANALYSIS_SUMMARY_JSON
        result_file.write_text(
            json.dumps(analysis_result.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        files["result_data"] = str(result_file)

        readme_file = output_dir / FILE_README_MD
        readme_content = f"""# Experiment Analysis Results

**Experiment ID:** {context.experiment_id}  
**Hypothesis ID:** {context.hypothesis_id}  
**Generated:** {analysis_result.generated_at.strftime("%Y-%m-%d %H:%M:%S")}

## Files

- `report.md` / `report.html` - Default report (Chinese preferred)
- `report_zh.md` / `report_zh.html` - Chinese report
- `report_en.md` / `report_en.html` - English report
- `data/analysis_summary.json` - Analysis summary
- `data/eda_profile.html` - EDA (ydata-profiling), when generated
- `data/eda_sweetviz.html` - EDA (Sweetviz), when generated
"""
        readme_file.write_text(readme_content, encoding="utf-8")
        files["readme"] = str(readme_file)

        return files

    def _format_list(self, items: List[Any]) -> str:
        """
        格式化列表用于提示词。

        Args:
            items: 要格式化的项目列表

        Returns:
            格式化的 Markdown 列表字符串
        """
        return "\n".join([f"- {item}" for item in items]) if items else "None"

    def _format_list_truncated(
        self, items: List[Any], max_items: int = 10, max_item_len: int = 300
    ) -> str:
        """格式化列表并截断，控制 prompt 长度。"""
        if not items:
            return "None"
        truncated = [
            (str(i)[:max_item_len] + ("..." if len(str(i)) > max_item_len else ""))
            for i in items[:max_items]
        ]
        suffix = (
            f"\n... ({len(items) - max_items} more)" if len(items) > max_items else ""
        )
        return "\n".join([f"- {t}" for t in truncated]) + suffix

    def _truncate_text(self, text: str, max_len: int) -> str:
        """截断文本到指定长度。"""
        if not text:
            return ""
        s = str(text).strip()
        return s[:max_len] + ("..." if len(s) > max_len else "")

    def _get_status_message(self, status: str) -> str:
        """与 ExperimentStatus 枚举值一致。"""
        messages = {
            "successful": "Experiment completed successfully. Focus on positive outcomes.",
            "partial_success": "Partial success. Discuss achievements and limitations.",
            "failed": "Experiment did not meet criteria. Analyze what went wrong.",
            "interrupted": "Experiment was interrupted. Discuss partial results.",
        }
        return messages.get(
            status, "Status uncertain. Present findings with limitations."
        )
