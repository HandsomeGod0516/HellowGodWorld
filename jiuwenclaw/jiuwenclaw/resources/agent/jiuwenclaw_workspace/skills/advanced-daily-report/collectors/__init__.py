# -*- coding: utf-8 -*-
"""
進階版日報生成器 - 資料採集模組

包含：
- GitCollector: Git 提交記錄採集
- EmailCollector: 郵件統計採集
- MemoryCollector: 記憶資料採集
- TodoCollector: 待辦事項採集
- DataAggregator: 資料聚合器
"""

from .git_collector import GitCollector, GitCommit
from .email_collector import EmailCollector, EmailStats
from .memory_collector import MemoryCollector
from .todo_collector import TodoCollector
from .aggregator import DataAggregator, CollectedData

__all__ = [
    "GitCollector",
    "GitCommit",
    "EmailCollector",
    "EmailStats",
    "MemoryCollector",
    "TodoCollector",
    "DataAggregator",
    "CollectedData",
]
