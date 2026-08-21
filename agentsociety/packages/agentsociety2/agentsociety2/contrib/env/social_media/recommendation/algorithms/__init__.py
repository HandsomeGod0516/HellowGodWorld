"""
Recommendation Algorithms

統一演算法介面和實現:
- RecommenderAlgorithm: 統一演算法抽象基類
- RatingMatrix: 統一評分資料格式
- MFRecommender: MF (矩陣分解) 演算法實現
- SASRecRecommender: SASRec (序列推薦) 演算法實現
- NCFRecommender: NCF (神經協同過濾) 演算法實現
- DeepFMRecommender: DeepFM (深度因子分解機) 演算法實現
- DINRecommender: DIN (深度興趣網路) 演算法實現
- LightGCNRecommender: LightGCN (輕量級圖卷積網路) 演算法實現
"""

from .core import RecommenderAlgorithm, RatingMatrix
from .mf import MFRecommender, MFConfig
from .sasrec import SASRecRecommender, SASRecConfig
from .ncf import NCFRecommender, NCFConfig
from .deepfm import DeepFMRecommender, DeepFMConfig
from .din import DINRecommender, DINConfig
from .lightgcn import LightGCNRecommender, LightGCNConfig

__all__ = [
    "RecommenderAlgorithm",
    "RatingMatrix",
    "MFRecommender",
    "MFConfig",
    "SASRecRecommender",
    "SASRecConfig",
    "NCFRecommender",
    "NCFConfig",
    "DeepFMRecommender",
    "DeepFMConfig",
    "DINRecommender",
    "DINConfig",
    "LightGCNRecommender",
    "LightGCNConfig",
]
