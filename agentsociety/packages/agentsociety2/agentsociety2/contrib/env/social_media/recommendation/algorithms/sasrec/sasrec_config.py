"""
SASRec演算法配置類
"""

from dataclasses import dataclass


@dataclass
class SASRecConfig:
    """
    SASRec演算法配置

    模型超引數：
        hidden_units: 隱藏層維度（嵌入維度）
        maxlen: 最大序列長度（行為歷史的最大條數）
        num_blocks: Transformer塊數量
        num_heads: 多頭注意力的頭數
        dropout_rate: Dropout機率
        l2_emb: L2正則化係數（嵌入層）

    訓練超引數：
        learning_rate: 學習率
        weight_decay: 權重衰減（L2正則化）
        batch_size: 訓練批大小
        max_epochs: 最大訓練輪數
        patience: 早停耐心值（驗證集無改進的輪數）
        eval_interval: 評估間隔（每N個epoch評估一次）

    資料引數：
        user_num: 使用者數量（自動從資料集推斷）
        item_num: 物品數量（自動從資料集推斷）

    CoLLM最佳配置（MovieLens-1M）：
        - hidden_units: 64
        - maxlen: 25
        - learning_rate: 0.01
        - weight_decay: 0.01
        - batch_size: 2048
        - 最終AUC: ~0.71, UAUC: ~0.67
    """

    # 模型超引數
    hidden_units: int = 64          # 嵌入維度
    maxlen: int = 25                # 最大序列長度
    num_blocks: int = 2             # Transformer塊數量
    num_heads: int = 1              # 注意力頭數
    dropout_rate: float = 0.2       # Dropout率
    l2_emb: float = 1e-4            # L2正則化係數

    # 訓練超引數
    learning_rate: float = 0.01     # 學習率
    weight_decay: float = 0.01      # 權重衰減
    batch_size: int = 1024          # 批大小（根據資料集規模調整）
    max_epochs: int = 5000          # 最大訓練輪數
    patience: int = 100             # 早停耐心值
    eval_interval: int = 1          # 評估間隔

    # 資料引數（由資料集自動設定）
    user_num: int = 0               # 使用者數量
    item_num: int = 0               # 物品數量

    def __post_init__(self):
        """後處理：驗證配置有效性"""
        assert self.hidden_units > 0, "hidden_units必須大於0"
        assert self.maxlen > 0, "maxlen必須大於0"
        assert self.num_blocks > 0, "num_blocks必須大於0"
        assert self.num_heads > 0, "num_heads必須大於0"
        assert 0 <= self.dropout_rate < 1, "dropout_rate必須在[0, 1)範圍內"
        assert self.learning_rate > 0, "learning_rate必須大於0"
        assert self.batch_size > 0, "batch_size必須大於0"

    @classmethod
    def from_dict(cls, config_dict: dict) -> "SASRecConfig":
        """
        從字典建立配置物件

        Args:
            config_dict: 配置字典

        Returns:
            SASRecConfig例項
        """
        return cls(**{k: v for k, v in config_dict.items() if hasattr(cls, k)})

    def to_dict(self) -> dict:
        """
        轉換為字典

        Returns:
            配置字典
        """
        return {
            "hidden_units": self.hidden_units,
            "maxlen": self.maxlen,
            "num_blocks": self.num_blocks,
            "num_heads": self.num_heads,
            "dropout_rate": self.dropout_rate,
            "l2_emb": self.l2_emb,
            "learning_rate": self.learning_rate,
            "weight_decay": self.weight_decay,
            "batch_size": self.batch_size,
            "max_epochs": self.max_epochs,
            "patience": self.patience,
            "eval_interval": self.eval_interval,
            "user_num": self.user_num,
            "item_num": self.item_num,
        }

    def __str__(self) -> str:
        """字串表示"""
        return (
            f"SASRecConfig(\n"
            f"  Model: hidden={self.hidden_units}, maxlen={self.maxlen}, "
            f"blocks={self.num_blocks}, heads={self.num_heads}\n"
            f"  Training: lr={self.learning_rate}, wd={self.weight_decay}, "
            f"batch={self.batch_size}\n"
            f"  Data: users={self.user_num}, items={self.item_num}\n"
            f")"
        )
