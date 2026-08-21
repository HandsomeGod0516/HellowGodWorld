"""
LightGCN 演算法配置
"""

from dataclasses import dataclass


@dataclass
class LightGCNConfig:
    """
    LightGCN 演算法配置引數

    引數說明:
    - embedding_dim: 嵌入維度
    - n_layers: 圖卷積層數
    - learning_rate: 學習率
    - reg: L2正則化引數
    - n_epochs: 訓練輪數
    - batch_size: 批次大小
    """

    embedding_dim: int = 64
    n_layers: int = 2
    learning_rate: float = 0.01
    reg: float = 1e-4
    n_epochs: int = 20
    batch_size: int = 2048

    def __post_init__(self):
        """驗證配置引數"""
        if self.embedding_dim <= 0:
            raise ValueError("embedding_dim 必須 > 0")
        if self.n_layers <= 0:
            raise ValueError("n_layers 必須 > 0")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate 必須 > 0")
        if self.reg < 0:
            raise ValueError("reg 必須 >= 0")
        if self.n_epochs <= 0:
            raise ValueError("n_epochs 必須 > 0")
        if self.batch_size <= 0:
            raise ValueError("batch_size 必須 > 0")

