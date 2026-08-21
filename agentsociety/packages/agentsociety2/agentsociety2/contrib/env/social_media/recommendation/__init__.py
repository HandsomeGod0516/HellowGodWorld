"""
Recommendation Module for SocialMediaSpace

- RecommenderAlgorithm: 統一的演算法介面
- RatingMatrix: 統一的資料格式
- MFRecommender: MF演算法實現
- RecommendationService: 核心推薦服務
- IncrementalTrainer: 增量訓練器 (可選)
"""

from .models import Item, Rating, UserPreference, FeedCache, RecommendationHistory
from .storage import RecommendationStorageManager

from .algorithms.core import RecommenderAlgorithm, RatingMatrix
from .algorithms.mf import MFRecommender, MFConfig
from .service import RecommendationService, ServiceConfig
from .trainer import IncrementalTrainer, TrainerConfig

__all__ = [
    # 資料模型
    "Item",
    "Rating",
    "UserPreference",
    "FeedCache",
    "RecommendationHistory",
    "RecommendationStorageManager",

    "RecommenderAlgorithm",
    "RatingMatrix",
    "MFRecommender",
    "MFConfig",
    "RecommendationService",
    "ServiceConfig",
    "IncrementalTrainer",
    "TrainerConfig",
]
