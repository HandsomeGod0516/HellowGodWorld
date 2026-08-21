# -*- coding: utf-8 -*-
"""
AI 智慧分析器

功能：
- 智慧工作摘要：使用 LLM 生成自然語言摘要
- 明日計劃建議：基於今日工作生成明日計劃
- 工作模式分析：分析提交時間分佈，識別效率高峰
"""

import asyncio
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

# 嘗試匯入 LLM 相關模組
try:
    from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig, UserMessage, SystemMessage
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False
    Model = None

# 嘗試匯入配置
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


@dataclass
class AIAnalysisResult:
    """AI 分析結果"""

    # 智慧摘要
    summary: str = ""

    # 明日計劃建議
    tomorrow_suggestions: list[str] = field(default_factory=list)

    # 工作模式分析
    work_pattern: dict[str, Any] = field(default_factory=dict)

    # 原始 LLM 響應（除錯用）
    raw_response: str = ""

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "tomorrow_suggestions": self.tomorrow_suggestions,
            "work_pattern": self.work_pattern,
        }


@dataclass
class WorkPatternResult:
    """工作模式分析結果"""

    # 效率高峰時段
    peak_hours: list[int] = field(default_factory=list)

    # 各時段提交分佈
    hourly_distribution: dict[int, int] = field(default_factory=dict)

    # 工作日分佈
    weekday_distribution: dict[str, int] = field(default_factory=dict)

    # 平均每日提交數
    avg_commits_per_day: float = 0.0

    # 分析描述
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "peak_hours": self.peak_hours,
            "hourly_distribution": self.hourly_distribution,
            "weekday_distribution": self.weekday_distribution,
            "avg_commits_per_day": round(self.avg_commits_per_day, 2),
            "description": self.description,
        }


class AIAnalyzer:
    """AI 智慧分析器"""

    def __init__(self, config_path: Optional[str | Path] = None):
        """
        初始化 AI 分析器

        Args:
            config_path: 配置檔案路徑，預設為專案根目錄的 config/config.yaml
        """
        self.config_path = Path(config_path) if config_path else None
        self.llm_config = self._load_llm_config()
        self._model = None

    def _load_llm_config(self) -> dict:
        """從配置檔案載入 LLM 配置"""
        config = {
            "model_name": "glm-4.7",
            "api_base": "https://open.bigmodel.cn/api/paas/v4",
            "api_key": "",
        }

        # 嘗試從配置檔案讀取
        config_path = self.config_path
        if not config_path:
            config_path = Path(__file__).parent.parent.parent.parent.parent / "config" / "config.yaml"

        if config_path and config_path.exists():
            try:
                if YAML_AVAILABLE:
                    with open(config_path, "r", encoding="utf-8") as f:
                        yaml_config = yaml.safe_load(f)
                        # 優先讀取 ai_analysis 配置
                        if yaml_config and "ai_analysis" in yaml_config:
                            ai_config = yaml_config["ai_analysis"]
                            config["model_name"] = ai_config.get("model_name", config["model_name"])
                            config["api_base"] = ai_config.get("api_base", config["api_base"])
                            config["api_key"] = ai_config.get("api_key", config["api_key"])
                        # 其次讀取 react 配置
                        elif yaml_config and "react" in yaml_config:
                            react_config = yaml_config["react"]
                            config["model_name"] = react_config.get("model_name", config["model_name"])
                            if "model_client_config" in react_config:
                                client_config = react_config["model_client_config"]
                                config["api_base"] = client_config.get("api_base", config["api_base"])
                                config["api_key"] = client_config.get("api_key", config["api_key"])
            except Exception:
                print(f"[AIAnalyzer] 載入配置檔案失敗")

        return config

    def _get_model(self):
        """獲取 LLM 模型例項"""
        if self._model is None:
            if not LLM_AVAILABLE:
                raise RuntimeError("LLM 模組未安裝，請檢查 openjiuwen 包")

            if not self.llm_config.get("api_key"):
                raise RuntimeError("未配置 API Key，請檢查環境變數或配置檔案")

            client_config = ModelClientConfig(
                client_provider="OpenAI",
                api_base=self.llm_config["api_base"],
                api_key=self.llm_config["api_key"],
                verify_ssl=False,
            )

            model_config = ModelRequestConfig(
                model=self.llm_config["model_name"],
            )

            self._model = Model(
                model_client_config=client_config,
                model_config=model_config,
            )

        return self._model

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """
        呼叫 LLM

        Args:
            system_prompt: 系統提示詞
            user_prompt: 使用者提示詞

        Returns:
            str: LLM 響應文字
        """
        model = self._get_model()

        messages = [
            SystemMessage(content=system_prompt),
            UserMessage(content=user_prompt),
        ]

        try:
            response = await model.invoke(messages=messages)
            return response.content if hasattr(response, "content") else str(response)
        except Exception:
            print(f"[AIAnalyzer] LLM 呼叫失敗")
            return ""

    def generate_summary_sync(self, data: dict) -> str:
        """同步版本的智慧摘要生成"""
        return asyncio.run(self.generate_summary(data))

    async def generate_summary(self, data: dict) -> str:
        """
        生成智慧工作摘要

        Args:
            data: 採集的工作資料

        Returns:
            str: 智慧摘要文字
        """
        # 提取關鍵資料
        git_data = data.get("git", {})
        todo_data = data.get("todo", {})
        email_data = data.get("email", {})

        commit_count = git_data.get("total_commits", 0)
        insertions = git_data.get("total_insertions", 0)
        deletions = git_data.get("total_deletions", 0)
        commit_messages = "\n".join([
            f"- {c.get('message', '')}" for c in git_data.get("commits", [])[:5]
        ])

        completed = todo_data.get("completed_count", 0)
        total = todo_data.get("total_count", 0)

        received = email_data.get("received_count", 0)
        sent = email_data.get("sent_count", 0)

        system_prompt = """你是一個專業的工作分析助手。你的任務是根據工作資料生成簡潔、專業的摘要。
要求：
1. 用 2-3 句話總結今日工作重點
2. 突出最重要的成果和進展
3. 語氣積極專業
4. 不要使用列表格式，用連貫的段落"""

        user_prompt = f"""請根據以下工作資料生成今日工作摘要：

## 今日資料
- 日期：{data.get('date', '未知')}
- Git 提交：{commit_count} 次
- 程式碼變更：+{insertions} / -{deletions} 行
- 任務完成：{completed}/{total} 項
- 郵件處理：收 {received} 封，發 {sent} 封

## 提交記錄
{commit_messages if commit_messages else '無提交記錄'}

請生成今日工作摘要："""

        response = await self._call_llm(system_prompt, user_prompt)
        return response.strip() if response else self._generate_fallback_summary(data)

    @staticmethod
    def _generate_fallback_summary(data: dict) -> str:
        """生成備用摘要（當 LLM 不可用時）"""
        git_data = data.get("git", {})
        todo_data = data.get("todo", {})

        commit_count = git_data.get("total_commits", 0)
        completed = todo_data.get("completed_count", 0)

        if commit_count > 0 and completed > 0:
            return f"今日完成了 {commit_count} 次程式碼提交和 {completed} 項任務，工作進展順利。"
        elif commit_count > 0:
            return f"今日專注於程式碼開發，完成了 {commit_count} 次提交。"
        elif completed > 0:
            return f"今日完成了 {completed} 項任務，穩步推進工作。"
        else:
            return "今日工作資料較少，建議記錄更多工作內容。"

    async def suggest_tomorrow(self, data: dict) -> list[str]:
        """
        生成明日計劃建議

        Args:
            data: 採集的工作資料

        Returns:
            list[str]: 明日計劃建議列表
        """
        todo_data = data.get("todo", {})
        memory_data = data.get("memory", {})

        # 待處理任務
        pending_tasks = todo_data.get("pending_items", [])
        in_progress = todo_data.get("in_progress_items", [])

        # 今日工作記錄
        work_notes = memory_data.get("content", "")

        system_prompt = """你是一個專業的工作規劃助手。你的任務是根據今日工作情況和待辦事項，給出具體的明日工作建議。
要求：
1. 給出 3-5 條具體、可執行的建議
2. 優先處理緊急和重要的任務
3. 考慮工作連續性
4. 每條建議簡潔明瞭，不超過 20 字
5. 直接輸出建議列表，每行一條，不要編號"""

        pending_str = "\n".join([f"- {t}" for t in pending_tasks[:5]]) if pending_tasks else "無"
        in_progress_str = "\n".join([f"- {t}" for t in in_progress[:5]]) if in_progress else "無"

        user_prompt = f"""請根據以下資訊，建議明日的重點工作：

## 待處理任務
{pending_str}

## 進行中任務
{in_progress_str}

## 今日工作記錄
{work_notes[:500] if work_notes else '無記錄'}

請給出明日工作建議（每行一條）："""

        response = await self._call_llm(system_prompt, user_prompt)

        if response:
            # 解析響應為列表
            suggestions = [
                line.strip().lstrip("- ").lstrip("• ")
                for line in response.strip().split("\n")
                if line.strip()
            ]
            return suggestions[:5]

        # 備用建議
        return self._generate_fallback_suggestions(pending_tasks, in_progress)

    @staticmethod
    def _generate_fallback_suggestions(
        pending: list[str], in_progress: list[str]
    ) -> list[str]:
        """生成備用建議"""
        suggestions = []

        if in_progress:
            suggestions.append(f"繼續推進：{in_progress[0][:20]}")
        if pending:
            suggestions.append(f"處理待辦：{pending[0][:20]}")

        suggestions.extend([
            "整理今日工作筆記",
            "檢查郵件和訊息",
        ])

        return suggestions[:5]

    def analyze_work_pattern(self, commits_data: list[dict]) -> WorkPatternResult:
        """
        分析工作模式

        Args:
            commits_data: 近期提交資料列表，每個元素包含 date, time, message 等欄位

        Returns:
            WorkPatternResult: 工作模式分析結果
        """
        result = WorkPatternResult()

        if not commits_data:
            result.description = "暫無足夠資料進行工作模式分析"
            return result

        # 統計各時段提交分佈
        hourly_counts = Counter()
        weekday_counts = Counter()
        date_set = set()

        for commit in commits_data:
            # 解析時間
            commit_time = commit.get("time", "")
            commit_date = commit.get("date", "")

            if commit_date:
                date_set.add(commit_date)

            if commit_time:
                try:
                    hour = int(commit_time.split(":")[0])
                    hourly_counts[hour] += 1
                except (ValueError, IndexError):
                    pass

            # 統計工作日分佈
            if commit_date:
                try:
                    dt = datetime.strptime(commit_date, "%Y-%m-%d")
                    weekday_name = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][dt.weekday()]
                    weekday_counts[weekday_name] += 1
                except ValueError:
                    pass

        # 計算高峰時段（提交最多的 2-3 個小時）
        if hourly_counts:
            sorted_hours = hourly_counts.most_common(3)
            result.peak_hours = [h[0] for h in sorted_hours]
            result.hourly_distribution = dict(sorted(hourly_counts.items()))

        # 工作日分佈
        if weekday_counts:
            # 按週一到週日排序
            weekday_order = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
            result.weekday_distribution = {
                day: weekday_counts.get(day, 0) for day in weekday_order
            }

        # 平均每日提交數
        if date_set:
            result.avg_commits_per_day = len(commits_data) / len(date_set)

        # 生成描述
        result.description = self._generate_pattern_description(result)

        return result

    @staticmethod
    def _generate_pattern_description(pattern: WorkPatternResult) -> str:
        """生成工作模式描述"""
        parts = []

        # 高峰時段描述
        if pattern.peak_hours:
            peak_str = "、".join([f"{h}:00" for h in pattern.peak_hours[:2]])
            parts.append(f"你的工作效率高峰時段在 **{peak_str}** 左右")

        # 工作日描述
        if pattern.weekday_distribution:
            # 找出提交最多的工作日
            top_days = sorted(
                pattern.weekday_distribution.items(),
                key=lambda x: x[1],
                reverse=True
            )[:2]
            if top_days and top_days[0][1] > 0:
                days_str = "、".join([d[0] for d in top_days])
                parts.append(f"提交最活躍的日子是 **{days_str}**")

        # 平均提交描述
        if pattern.avg_commits_per_day > 0:
            parts.append(f"平均每日 **{pattern.avg_commits_per_day:.1f}** 次提交")

        if parts:
            return "。".join(parts) + "。建議在高峰時段處理重要任務，提高工作效率。"
        else:
            return "暫無足夠資料進行分析，建議持續記錄工作資料。"

    async def analyze_full(self, data: dict, pattern_data: Optional[list[dict]] = None) -> AIAnalysisResult:
        """
        執行完整的 AI 分析

        Args:
            data: 今日工作資料
            pattern_data: 近期提交資料（用於工作模式分析）

        Returns:
            AIAnalysisResult: 完整分析結果
        """
        result = AIAnalysisResult()

        # 生成智慧摘要
        result.summary = await self.generate_summary(data)

        # 生成明日計劃
        result.tomorrow_suggestions = await self.suggest_tomorrow(data)

        # 分析工作模式
        if pattern_data:
            pattern_result = self.analyze_work_pattern(pattern_data)
            result.work_pattern = pattern_result.to_dict()

        return result


# 同步版本的便捷函式
def analyze_sync(data: dict, config_path: Optional[str] = None) -> AIAnalysisResult:
    """同步版本的完整分析"""
    analyzer = AIAnalyzer(config_path)
    return asyncio.run(analyzer.analyze_full(data))
