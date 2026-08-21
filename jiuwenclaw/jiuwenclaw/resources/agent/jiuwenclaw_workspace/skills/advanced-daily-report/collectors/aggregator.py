# -*- coding: utf-8 -*-
"""
資料聚合器

功能：
- 整合所有采集器的資料
- 統一時間視窗過濾
- 提供統一的資料訪問介面
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

# 與 run_report / work_analyzer 一致：日曆日與採集時間戳使用東八區
_REPORT_TZ = ZoneInfo("Asia/Shanghai")

from .email_collector import EmailCollector, EmailStats
from .git_collector import GitCollector, GitStats
from .memory_collector import MemoryCollector, MemoryData
from .todo_collector import TodoCollector, TodoStats


@dataclass
class CollectedData:
    """聚合後的資料"""

    date: str  # 日期
    collected_at: datetime  # 採集時間

    # Git 資料
    git: GitStats = field(default_factory=GitStats)

    # 郵件資料
    email: EmailStats = field(default_factory=EmailStats)

    # 記憶資料
    memory: MemoryData = field(default_factory=MemoryData)

    # 待辦資料
    todo: TodoStats = field(default_factory=TodoStats)

    # 歷史對比資料
    comparison: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "collected_at": self.collected_at.isoformat(),
            "git": self.git.to_dict(),
            "email": self.email.to_dict(),
            "memory": self.memory.to_dict(),
            "todo": self.todo.to_dict(),
            "comparison": self.comparison,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


class DataAggregator:
    """資料聚合器"""

    def __init__(
        self,
        workspace_dir: str | Path,
        git_repo: Optional[str | Path] = None,
        email_config: Optional[dict] = None,
    ):
        """
        初始化資料聚合器

        Args:
            workspace_dir: workspace 目錄
            git_repo: Git 倉庫路徑
            email_config: 郵箱配置 {"address": str, "auth_code": str, "provider": str}
        """
        self.workspace_dir = Path(workspace_dir)

        # 初始化各採集器
        self.memory_collector = MemoryCollector(self.workspace_dir)
        self.todo_collector = TodoCollector(self.workspace_dir)

        # Git 採集器（可選）
        self.git_collector = None
        if git_repo:
            self.git_collector = GitCollector(git_repo)

        # 郵件採集器（可選）
        self.email_collector = None
        self.email_config = email_config

    def collect(self, date: Optional[str] = None, include_comparison: bool = True) -> CollectedData:
        """
        聚合採集資料

        Args:
            date: 日期字串，預設今天
            include_comparison: 是否包含歷史對比

        Returns:
            CollectedData: 聚合後的資料
        """
        if date is None:
            date = datetime.now(_REPORT_TZ).strftime("%Y-%m-%d")

        data = CollectedData(
            date=date,
            collected_at=datetime.now(_REPORT_TZ),
        )

        # 採集記憶資料
        data.memory = self.memory_collector.collect(date)

        # 採集待辦資料
        data.todo = self.todo_collector.collect()

        # 採集 Git 資料
        if self.git_collector:
            data.git = self.git_collector.get_commits(date)

        # 採集郵件資料
        if self.email_config and self.email_collector is None:
            try:
                self.email_collector = EmailCollector(
                    email_address=self.email_config["address"],
                    auth_code=self.email_config["auth_code"],
                    provider=self.email_config.get("provider", "163"),
                )
            except Exception as e:
                print(f"郵件採集器初始化失敗: {e}")

        if self.email_collector:
            try:
                with self.email_collector:
                    data.email = self.email_collector.get_stats(date)
            except Exception as e:
                print(f"郵件資料採集失敗: {e}")

        # 歷史對比
        if include_comparison:
            data.comparison = self._generate_comparison(data, date)

        return data

    def _generate_comparison(self, current_data: CollectedData, date: str) -> dict:
        """生成歷史對比資料"""
        comparison = {}

        try:
            current_date = datetime.strptime(date, "%Y-%m-%d")

            # 與昨日對比
            yesterday = (current_date - timedelta(days=1)).strftime("%Y-%m-%d")
            yesterday_data = self._collect_light(yesterday)

            comparison["yesterday"] = {
                "git_commits": {
                    "current": current_data.git.total_commits,
                    "previous": yesterday_data.git.total_commits,
                    "change": current_data.git.total_commits - yesterday_data.git.total_commits,
                },
                "todo_completed": {
                    "current": current_data.todo.completed,
                    "previous": yesterday_data.todo.completed,
                    "change": current_data.todo.completed - yesterday_data.todo.completed,
                },
            }

            # 與上週同期對比
            last_week = (current_date - timedelta(days=7)).strftime("%Y-%m-%d")
            last_week_data = self._collect_light(last_week)

            comparison["last_week"] = {
                "git_commits": {
                    "current": current_data.git.total_commits,
                    "previous": last_week_data.git.total_commits,
                    "change": current_data.git.total_commits - last_week_data.git.total_commits,
                },
            }

        except Exception:
            pass

        return comparison

    def _collect_light(self, date: str) -> CollectedData:
        """輕量採集（僅 Git 和記憶）"""
        data = CollectedData(
            date=date,
            collected_at=datetime.now(_REPORT_TZ),
        )

        if self.git_collector:
            data.git = self.git_collector.get_commits(date)

        data.memory = self.memory_collector.collect(date)

        return data

    def collect_week(self, end_date: Optional[str] = None) -> dict[str, CollectedData]:
        """採集一週的資料"""
        if end_date is None:
            end_date = datetime.now(_REPORT_TZ)
        else:
            end_date = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=_REPORT_TZ)

        result = {}
        for i in range(7):
            date = (end_date - timedelta(days=i)).strftime("%Y-%m-%d")
            result[date] = self.collect(date, include_comparison=False)

        return result

    def collect_month(self, year: int, month: int) -> dict[str, CollectedData]:
        """採集一月的資料"""
        import calendar

        _, days_in_month = calendar.monthrange(year, month)
        result = {}

        for day in range(1, days_in_month + 1):
            date = f"{year:04d}-{month:02d}-{day:02d}"
            result[date] = self.collect(date, include_comparison=False)

        return result

    def collect_for_pattern_analysis(self, days: int = 7) -> list[dict]:
        """
        採集用於工作模式分析的資料

        Args:
            days: 天數，預設 7 天

        Returns:
            包含日期、時間、提交資訊的字典列表
        """
        if self.git_collector:
            return self.git_collector.get_commits_for_pattern_analysis(days)
        return []


def main():
    """測試入口"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python aggregator.py <workspace_dir> [git_repo] [email] [auth_code]")
        sys.exit(1)

    workspace_dir = sys.argv[1]
    git_repo = sys.argv[2] if len(sys.argv) > 2 else None
    email_config = None

    if len(sys.argv) > 4:
        email_config = {
            "address": sys.argv[3],
            "auth_code": sys.argv[4],
            "provider": "163",
        }

    aggregator = DataAggregator(
        workspace_dir=workspace_dir,
        git_repo=git_repo,
        email_config=email_config,
    )

    print("採集資料中...")
    data = aggregator.collect()

    print(f"\n=== 資料採集結果 ({data.date}) ===\n")

    print("Git 統計:")
    print(f"  提交次數: {data.git.total_commits}")
    print(f"  修改檔案: {data.git.total_files_changed}")
    print(f"  程式碼變更: +{data.git.total_insertions}/-{data.git.total_deletions}")

    print("\n郵件統計:")
    print(f"  今日收件: {data.email.received_today}")
    print(f"  今日發件: {data.email.sent_today}")
    print(f"  未讀郵件: {data.email.unread}")

    print("\n待辦統計:")
    print(f"  總數: {data.todo.total}")
    print(f"  已完成: {data.todo.completed}")
    print(f"  完成率: {data.todo.completion_rate:.1%}")

    print("\n記憶資料:")
    print(f"  今日記錄: {len(data.memory.work_summaries)} 條")

    if data.comparison:
        print("\n歷史對比:")
        if "yesterday" in data.comparison:
            y = data.comparison["yesterday"]
            print(f"  vs 昨日: 提交 {y['git_commits']['change']:+d}, 任務 {y['todo_completed']['change']:+d}")


if __name__ == "__main__":
    main()
