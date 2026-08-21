"""
MF (矩陣分解) 演算法配置
"""

from dataclasses import dataclass


@dataclass
class MFConfig:
    """
    MF 演算法配置引數

    引數說明:
    - n_latent_factors: 潛在因子數量
    - learning_rate: 學習率
    - reg_param: L2正則化參
    - n_iterations: 訓練迭代次數
    """

    n_latent_factors: int = 50
    learning_rate: float = 0.01
    reg_param: float = 0.01
    n_iterations: int = 100

    def __post_init__(self):
        """驗證配置引數"""
        if self.n_latent_factors <= 0:
            raise ValueError("n_latent_factors 必須 > 0")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate 必須 > 0")
        if self.reg_param < 0:
            raise ValueError("reg_param 必須 >= 0")
        if self.n_iterations <= 0:
            raise ValueError("n_iterations 必須 > 0")
