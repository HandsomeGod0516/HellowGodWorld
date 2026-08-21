"""
DeepFM 演算法配置
"""

from dataclasses import dataclass
from typing import List


@dataclass
class DeepFMConfig:
    """
    DeepFM 演算法配置引數

    引數說明:
    - embedding_dim: 嵌入維度
    - deep_layers: Deep網路層神經元數量列表
    - learning_rate: 學習率
    - batch_size: 批次大小
    - n_epochs: 訓練輪數
    - drop_rate: Dropout率
    """

    embedding_dim: int = 16
    deep_layers: List[int] = None
    learning_rate: float = 0.001
    batch_size: int = 256
    n_epochs: int = 50
    drop_rate: float = 0.5

    def __post_init__(self):
        """驗證配置引數"""
        if self.deep_layers is None:
            self.deep_layers = [64, 32]
        
        if self.embedding_dim <= 0:
            raise ValueError("embedding_dim 必須 > 0")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate 必須 > 0")
        if self.batch_size <= 0:
            raise ValueError("batch_size 必須 > 0")
        if self.n_epochs <= 0:
            raise ValueError("n_epochs 必須 > 0")
        if len(self.deep_layers) == 0:
            raise ValueError("deep_layers 不能為空")
        if any(layer <= 0 for layer in self.deep_layers):
            raise ValueError("deep_layers 中的所有值必須 > 0")
        if not 0 <= self.drop_rate <= 1:
            raise ValueError("drop_rate 必須在 [0, 1] 範圍內")

