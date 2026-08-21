# -*- coding: utf-8 -*-
"""
報告生成器

支援：
- 日報生成
- 週報生成（聚合一週資料）
- 月報生成（聚合一月資料）
- AI 智慧分析（智慧摘要、明日計劃建議、工作模式分析）
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

_REPORT_TZ = ZoneInfo("Asia/Shanghai")

from ..analyzers.work_analyzer import AnalysisResult, WorkAnalyzer
from ..analyzers.ai_analyzer import AIAnalyzer, AIAnalysisResult
from ..collectors.aggregator import CollectedData, DataAggregator


@dataclass
class ReportConfig:
    """報告配置"""

    report_type: str = "daily"  # daily, weekly, monthly
    date: str = ""  # 報告日期
    include_trends: bool = True  # 是否包含趨勢
    include_suggestions: bool = True  # 是否包含建議
    output_format: str = "markdown"  # markdown, json
    # AI 分析配置
    enable_ai_analysis: bool = False  # 是否啟用 AI 分析
    ai_auto_mode: bool = True  # AI 分析模式：True=自動，False=手動觸發


class ReportGenerator:
    """報告生成器"""

    def __init__(
        self,
        data_aggregator: DataAggregator,
        work_analyzer: Optional[WorkAnalyzer] = None,
        ai_analyzer: Optional[AIAnalyzer] = None,
    ):
        """
        初始化報告生成器

        Args:
            data_aggregator: 資料聚合器
            work_analyzer: 工作分析器（可選）
            ai_analyzer: AI 分析器（可選）
        """
        self.data_aggregator = data_aggregator
        self.work_analyzer = work_analyzer or WorkAnalyzer()
        self.ai_analyzer = ai_analyzer

    def generate_daily(self, date: Optional[str] = None, config: Optional[ReportConfig] = None) -> str:
        """
        生成日報

        Args:
            date: 日期，預設今天
            config: 報告配置

        Returns:
            str: Markdown 格式的日報
        """
        if date is None:
            date = datetime.now(_REPORT_TZ).strftime("%Y-%m-%d")

        if config is None:
            config = ReportConfig(report_type="daily", date=date)

        # 採集資料
        data = self.data_aggregator.collect(date, include_comparison=config.include_trends)

        # 分析資料
        analysis = self.work_analyzer.analyze(data.to_dict())

        # AI 分析（如果啟用）
        ai_result = None
        if config.enable_ai_analysis:
            ai_result = self._run_ai_analysis(data, config)

        # 生成報告
        return self._render_daily_report(data, analysis, config, ai_result)

    def _run_ai_analysis(self, data: CollectedData, config: ReportConfig) -> Optional[AIAnalysisResult]:
        """執行 AI 分析"""
        if self.ai_analyzer is None:
            try:
                self.ai_analyzer = AIAnalyzer()
            except Exception as e:
                print(f"[ReportGenerator] AI 分析器初始化失敗: {e}")
                return None

        try:
            # 採集工作模式分析資料
            pattern_data = self.data_aggregator.collect_for_pattern_analysis(days=7)

            # 執行完整分析
            return asyncio.run(self.ai_analyzer.analyze_full(data.to_dict(), pattern_data))
        except Exception as e:
            print(f"[ReportGenerator] AI 分析失敗: {e}")
            return None

    def generate_weekly(self, end_date: Optional[str] = None) -> str:
        """
        生成周報

        Args:
            end_date: 結束日期，預設今天

        Returns:
            str: Markdown 格式的週報
        """
        if end_date is None:
            end_date = datetime.now(_REPORT_TZ)
        else:
            end_date = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=_REPORT_TZ)

        # 計算本週日期範圍
        start_date = end_date - timedelta(days=6)
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        # 採集一週資料
        week_data = self.data_aggregator.collect_week(end_str)

        # 聚合週資料
        aggregated = self._aggregate_week_data(week_data)

        # 生成周報
        return self._render_weekly_report(aggregated, start_str, end_str)

    def generate_monthly(self, year: Optional[int] = None, month: Optional[int] = None) -> str:
        """
        生成月報

        Args:
            year: 年份，預設當前年
            month: 月份，預設當前月

        Returns:
            str: Markdown 格式的月報
        """
        now = datetime.now(_REPORT_TZ)
        if year is None:
            year = now.year
        if month is None:
            month = now.month

        # 採集一月資料
        month_data = self.data_aggregator.collect_month(year, month)

        # 聚合月資料
        aggregated = self._aggregate_month_data(month_data)

        # 生成月報
        return self._render_monthly_report(aggregated, year, month)

    @staticmethod
    def _aggregate_week_data(week_data: dict[str, CollectedData]) -> dict:
        """聚合一週資料"""
        aggregated = {
            "total_commits": 0,
            "total_files_changed": 0,
            "total_insertions": 0,
            "total_deletions": 0,
            "total_tasks_completed": 0,
            "total_tasks": 0,
            "total_emails_received": 0,
            "total_emails_sent": 0,
            "active_days": 0,
            "daily_data": [],
        }

        for date, data in week_data.items():
            day_summary = {
                "date": date,
                "commits": data.git.total_commits,
                "tasks_completed": data.todo.completed,
                "productivity": 0,
            }

            aggregated["total_commits"] += data.git.total_commits
            aggregated["total_files_changed"] += data.git.total_files_changed
            aggregated["total_insertions"] += data.git.total_insertions
            aggregated["total_deletions"] += data.git.total_deletions
            aggregated["total_tasks_completed"] += data.todo.completed
            aggregated["total_tasks"] += data.todo.total
            aggregated["total_emails_received"] += data.email.received_today
            aggregated["total_emails_sent"] += data.email.sent_today

            if data.git.total_commits > 0 or data.todo.completed > 0:
                aggregated["active_days"] += 1

            aggregated["daily_data"].append(day_summary)

        return aggregated

    def _aggregate_month_data(self, month_data: dict[str, CollectedData]) -> dict:
        """聚合一月資料"""
        aggregated = self._aggregate_week_data(month_data)
        aggregated["total_days"] = len(month_data)
        return aggregated

    @staticmethod
    def _render_daily_report(
         data: CollectedData, analysis: AnalysisResult, config: ReportConfig,
        ai_result: Optional[AIAnalysisResult] = None
    ) -> str:
        """渲染日報"""
        lines = [
            f"# 📋 工作日報 - {data.date}",
            "",
        ]

        # AI 智慧摘要（放在開頭）
        if ai_result and ai_result.summary:
            lines.extend([
                "## 🤖 AI 智慧摘要",
                "",
                f"> {ai_result.summary}",
                "",
            ])

        # 效率概覽
        lines.extend([
            "## 📊 今日概覽",
            "",
            "| 指標 | 數值 |",
            "|------|------|",
            f"| 提交次數 | {analysis.metrics.commit_count} |",
            f"| 任務完成 | {analysis.metrics.tasks_completed}/{analysis.metrics.tasks_total} |",
            f"| 程式碼變更 | +{analysis.metrics.lines_added}/-{analysis.metrics.lines_deleted} |",
            f"| 郵件處理 | 收 {analysis.metrics.emails_received} / 發 {analysis.metrics.emails_sent} |",
            f"| 生產力得分 | {analysis.metrics.productivity_score:.1f} |",
            "",
        ])

        # 已完成任務
        completed_tasks = [t for t in data.todo.tasks if t.status == "completed"]
        if completed_tasks:
            lines.extend([
                "## ✅ 已完成任務",
                "",
            ])
            for task in completed_tasks[:10]:
                lines.append(f"- {task.content}")
            lines.append("")

        # 進行中任務
        running_tasks = [t for t in data.todo.tasks if t.status == "running"]
        if running_tasks:
            lines.extend([
                "## 🔄 進行中任務",
                "",
            ])
            for task in running_tasks[:5]:
                lines.append(f"- {task.content}")
            lines.append("")

        # Git 提交記錄
        if data.git.commits:
            lines.extend([
                "## 💻 程式碼提交",
                "",
                "| 時間 | 提交資訊 | 變更 |",
                "|------|----------|------|",
            ])
            for commit in data.git.commits[:10]:
                time_str = commit.date.strftime("%H:%M") if commit.date else "-"
                lines.append(
                    f"| {time_str} | {commit.message[:40]} | "
                    f"+{commit.insertions}/-{commit.deletions} |"
                )
            lines.append("")

        # 今日工作記錄
        if data.memory.work_summaries:
            lines.extend([
                "## 📝 今日工作記錄",
                "",
            ])
            for summary in data.memory.work_summaries[:10]:
                lines.append(f"- {summary}")
            lines.append("")

        # 郵件概況
        if data.email.received_today > 0 or data.email.sent_today > 0:
            lines.extend([
                "## 📧 郵件概況",
                "",
                f"- 今日收件: {data.email.received_today} 封",
                f"- 今日發件: {data.email.sent_today} 封",
                f"- 未讀郵件: {data.email.unread} 封",
                "",
            ])

            # 未讀郵件
            if data.email.important_emails:
                lines.append("### 未讀郵件")
                lines.append("")
                for email in data.email.important_emails[:5]:
                    lines.append(f"- [{email.sender}] {email.subject}")
                lines.append("")

        # 趨勢對比
        if config.include_trends and analysis.trends.vs_yesterday:
            lines.extend([
                "## 📈 趨勢對比",
                "",
            ])
            vs_y = analysis.trends.vs_yesterday
            if "commits" in vs_y:
                change = vs_y["commits"]["change"]
                symbol = "↑" if change > 0 else "↓" if change < 0 else "→"
                lines.append(f"- 提交: {symbol} {abs(change)} 次")
            if "productivity_score" in vs_y:
                change = vs_y["productivity_score"]["change"]
                symbol = "↑" if change > 0 else "↓" if change < 0 else "→"
                lines.append(f"- 效率: {symbol} {abs(change):.1f} 分")
            lines.append("")

        # 工作建議與明日計劃
        lines.extend([
            "## 💡 工作建議與明日計劃",
            "",
        ])

        # 原有建議
        if config.include_suggestions and analysis.suggestions:
            lines.append("### 今日改進建議")
            lines.append("")
            for i, suggestion in enumerate(analysis.suggestions, 1):
                lines.append(f"{i}. {suggestion}")
            lines.append("")

        # AI 明日計劃建議
        if ai_result and ai_result.tomorrow_suggestions:
            lines.append("### 🔜 AI 明日計劃建議")
            lines.append("")
            for suggestion in ai_result.tomorrow_suggestions:
                lines.append(f"- {suggestion}")
            lines.append("")

        # 待辦任務
        waiting_tasks = [t for t in data.todo.tasks if t.status == "waiting"]
        if waiting_tasks:
            lines.append("### 📋 待辦任務")
            lines.append("")
            for task in waiting_tasks[:5]:
                lines.append(f"- {task.content}")
            lines.append("")

        # 工作模式分析
        if ai_result and ai_result.work_pattern and ai_result.work_pattern.get("description"):
            lines.extend([
                "## 📊 工作模式分析（近7天）",
                "",
                ai_result.work_pattern.get("description", ""),
                "",
            ])

            # 高峰時段
            peak_hours = ai_result.work_pattern.get("peak_hours", [])
            if peak_hours:
                lines.append(f"- **效率高峰時段**: {', '.join([f'{h}:00' for h in peak_hours])}")

            # 平均提交
            avg_commits = ai_result.work_pattern.get("avg_commits_per_day", 0)
            if avg_commits > 0:
                lines.append(f"- **平均每日提交**: {avg_commits:.1f} 次")

            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _render_weekly_report(data: dict, start_date: str, end_date: str) -> str:
        """渲染週報"""
        lines = [
            f"# 📋 工作週報 - {start_date} ~ {end_date}",
            "",
        ]

        # 本週概覽
        lines.extend([
            "## 📊 本週概覽",
            "",
            "| 指標 | 數值 |",
            "|------|------|",
            f"| 活躍天數 | {data['active_days']}/7 天 |",
            f"| 提交次數 | {data['total_commits']} 次 |",
            f"| 任務完成 | {data['total_tasks_completed']} 個 |",
            f"| 程式碼變更 | +{data['total_insertions']}/-{data['total_deletions']} |",
            f"| 郵件處理 | 收 {data['total_emails_received']} / 發 {data['total_emails_sent']} |",
            "",
        ])

        # 每日資料
        if data["daily_data"]:
            lines.extend([
                "## 📅 每日統計",
                "",
                "| 日期 | 提交 | 任務完成 |",
                "|------|------|----------|",
            ])
            for day in data["daily_data"]:
                lines.append(f"| {day['date']} | {day['commits']} | {day['tasks_completed']} |")
            lines.append("")

        # 本週亮點
        lines.extend([
            "## ⭐ 本週亮點",
            "",
            "- 本週完成多次程式碼提交",
            "- 保持了穩定的工作節奏",
            "",
        ])

        # 下週計劃
        lines.extend([
            "## 🔜 下週計劃",
            "",
            "- 繼續完善當前功能",
            "- 處理待辦事項",
            "",
        ])

        return "\n".join(lines)

    @staticmethod
    def _render_monthly_report(data: dict, year: int, month: int) -> str:
        """渲染月報"""
        lines = [
            f"# 📋 工作月報 - {year}年{month}月",
            "",
        ]

        # 本月概覽
        lines.extend([
            "## 📊 本月概覽",
            "",
            "| 指標 | 數值 |",
            "|------|------|",
            f"| 活躍天數 | {data['active_days']}/{data['total_days']} 天 |",
            f"| 提交次數 | {data['total_commits']} 次 |",
            f"| 任務完成 | {data['total_tasks_completed']} 個 |",
            f"| 程式碼變更 | +{data['total_insertions']}/-{data['total_deletions']} |",
            "",
        ])

        # 工作總結
        lines.extend([
            "## 📝 工作總結",
            "",
            "本月工作主要包括：",
            "- 日報生成器功能開發",
            "- 進階版資料採集模組",
            "- 多報告型別支援",
            "",
        ])

        # 下月計劃
        lines.extend([
            "## 🔜 下月計劃",
            "",
            "- 繼續最佳化報告生成功能",
            "- 新增更多資料來源支援",
            "",
        ])

        return "\n".join(lines)


def main():
    """測試入口"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python report_generator.py <workspace_dir> [git_repo]")
        sys.exit(1)

    workspace_dir = sys.argv[1]
    git_repo = sys.argv[2] if len(sys.argv) > 2 else None

    # 初始化
    aggregator = DataAggregator(workspace_dir=workspace_dir, git_repo=git_repo)
    generator = ReportGenerator(aggregator)

    # 生成日報
    print("=== 日報 ===\n")
    daily_report = generator.generate_daily()
    print(daily_report)


if __name__ == "__main__":
    main()
