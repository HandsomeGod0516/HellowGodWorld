"""
DIN (Deep Interest Network) 演算法配置
"""

from dataclasses import dataclass
from typing import List


@dataclass
class DINConfig:
    """
    DIN 演算法配置引數

    引數說明:
    - embedding_dim: 嵌入維度（會被分成3份：user, item, history）
    - hidden_units: 全連線層神經元數量列表
    - learning_rate: 學習率
    - batch_size: 批次大小
    - n_epochs: 訓練輪數
    - max_history_len: 最大歷史序列長度
    - drop: Dropout率
    """

    embedding_dim: int = 192
    hidden_units: List[int] = None
    learning_rate: float = 0.001
    batch_size: int = 16
    n_epochs: int = 10
    max_history_len: int = 10
    drop: float = 0.2

    def __post_init__(self):
        """驗證配置引數"""
        if self.hidden_units is None:
            self.hidden_units = [200, 80]
        
        if self.embedding_dim <= 0:
            raise ValueError("embedding_dim 必須 > 0")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate 必須 > 0")
        if self.batch_size <= 0:
            raise ValueError("batch_size 必須 > 0")
        if self.n_epochs <= 0:
            raise ValueError("n_epochs 必須 > 0")
        if self.max_history_len <= 0:
            raise ValueError("max_history_len 必須 > 0")
        if len(self.hidden_units) == 0:
            raise ValueError("hidden_units 不能為空")
        if any(unit <= 0 for unit in self.hidden_units):
            raise ValueError("hidden_units 中的所有值必須 > 0")
        if not 0 <= self.drop <= 1:
            raise ValueError("drop 必須在 [0, 1] 範圍內")

