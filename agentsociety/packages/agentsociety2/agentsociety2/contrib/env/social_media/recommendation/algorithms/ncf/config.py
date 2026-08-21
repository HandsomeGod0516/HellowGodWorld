"""
NCF (Neural Collaborative Filtering) 演算法配置
"""

from dataclasses import dataclass
from typing import List


@dataclass
class NCFConfig:
    """
    NCF 演算法配置引數

    引數說明:
    - embedding_dim: 嵌入維度
    - mlp_layers: MLP層的神經元數量列表
    - learning_rate: 學習率
    - batch_size: 批次大小
    - n_epochs: 訓練輪數
    """

    embedding_dim: int = 32
    mlp_layers: List[int] = None
    learning_rate: float = 0.001
    batch_size: int = 256
    n_epochs: int = 50

    def __post_init__(self):
        """驗證配置引數"""
        if self.mlp_layers is None:
            self.mlp_layers = [64, 32, 16]
        
        if self.embedding_dim <= 0:
            raise ValueError("embedding_dim 必須 > 0")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate 必須 > 0")
        if self.batch_size <= 0:
            raise ValueError("batch_size 必須 > 0")
        if self.n_epochs <= 0:
            raise ValueError("n_epochs 必須 > 0")
        if len(self.mlp_layers) == 0:
            raise ValueError("mlp_layers 不能為空")
        if any(layer <= 0 for layer in self.mlp_layers):
            raise ValueError("mlp_layers 中的所有值必須 > 0")

