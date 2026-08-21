"""
增強版MF配置
"""

from pydantic import BaseModel, Field, ConfigDict


class EnhancedMFConfig(BaseModel):
    """
    增強版MF演算法配置
    1. 偏差項（Biases）：捕捉使用者和物品的系統性評分偏差
    2. 隱式反饋（Implicit Feedback）：整合使用者瀏覽/互動歷史
    3. 時間動態性（Temporal Dynamics）：捕捉評分隨時間的變化
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "n_latent_factors": 50,
                "learning_rate": 0.005,
                "reg_param": 0.02,
                "n_iterations": 100,
                "use_biases": True,
                "use_implicit_feedback": False,
                "use_temporal_dynamics": False
            }
        }
    )

    # 基礎引數
    n_latent_factors: int = Field(
        default=50,
        description="潛在因子數量",
        ge=1
    )

    learning_rate: float = Field(
        default=0.005,
        description="學習率（降低以提高穩定性）",
        gt=0
    )

    reg_param: float = Field(
        default=0.02,
        description="L2正則化引數",
        ge=0
    )

    n_iterations: int = Field(
        default=100,
        description="訓練迭代次數",
        ge=1
    )

    # 增強特性開關
    use_biases: bool = Field(
        default=True,
        description="是否使用偏差項（推薦開啟）"
    )

    use_implicit_feedback: bool = Field(
        default=False,
        description="是否使用隱式反饋（需要額外資料）"
    )

    use_temporal_dynamics: bool = Field(
        default=False,
        description="是否使用時間動態性（需要時間戳資料）"
    )

    # 隱式反饋引數
    implicit_weight: float = Field(
        default=0.4,
        description="隱式反饋權重",
        ge=0,
        le=1
    )

    # 時間動態性引數
    temporal_bins: int = Field(
        default=10,
        description="時間分箱數量",
        ge=1
    )

    temporal_weight: float = Field(
        default=0.3,
        description="時間動態性權重",
        ge=0,
        le=1
    )


__all__ = ["EnhancedMFConfig"]
