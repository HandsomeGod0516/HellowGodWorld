# -*- coding: utf-8 -*-
"""
進階版日報生成器 - 分析模組

包含：
- WorkAnalyzer: 工作分析引擎
- EfficiencyMetrics: 效率指標
- TrendComparison: 趨勢對比
- AnalysisResult: 分析結果
- AIAnalyzer: AI 智慧分析器
- AIAnalysisResult: AI 分析結果
- WorkPatternResult: 工作模式分析結果
"""

from .work_analyzer import (
    WorkAnalyzer,
    EfficiencyMetrics,
    TrendComparison,
    AnalysisResult,
)
from .ai_analyzer import (
    AIAnalyzer,
    AIAnalysisResult,
    WorkPatternResult,
)

__all__ = [
    "WorkAnalyzer",
    "EfficiencyMetrics",
    "TrendComparison",
    "AnalysisResult",
    "AIAnalyzer",
    "AIAnalysisResult",
    "WorkPatternResult",
]
