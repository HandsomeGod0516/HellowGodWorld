"""
增量訓練器
"""

import asyncio
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass

from agentsociety2.logger import get_logger
from .models import Rating
from .algorithms.core import RatingMatrix
from .service import RecommendationService


@dataclass
class TrainerConfig:
    """
    訓練器配置

    Args:
        retrain_threshold_ratings: 觸發重訓練的新評分數量閾值
        retrain_threshold_time: 觸發重訓練的時間閾值 (秒)
        enable_auto_retrain: 是否啟用自動重訓練
    """
    retrain_threshold_ratings: int = 100
    retrain_threshold_time: int = 300
    enable_auto_retrain: bool = True


class IncrementalTrainer:
    """
    增量訓練器 (可選元件)
    """

    def __init__(
        self,
        service: RecommendationService,
        config: TrainerConfig = TrainerConfig()
    ):
        """
        初始化增量訓練器

        Args:
            service: 推薦服務例項
            config: 訓練器配置
        """
        self._service = service
        self._config = config

        # 資料管理
        self._all_ratings: List[Rating] = []
        self._pending_ratings: List[Rating] = []

        # 訓練狀態
        self._is_training = False
        self._last_train_time: Optional[datetime] = None
        self._training_task: Optional[asyncio.Task] = None

        get_logger().info(
            f"IncrementalTrainer 初始化: "
            f"threshold_ratings={config.retrain_threshold_ratings}, "
            f"threshold_time={config.retrain_threshold_time}s, "
            f"auto_retrain={config.enable_auto_retrain}"
        )

    async def load_initial_data(self, ratings: List[Rating]) -> None:
        """
        載入初始資料並訓練

        Args:
            ratings: 初始評分列表
        """
        if not ratings:
            raise ValueError("初始評分列表不能為空")

        get_logger().info(f"載入初始資料: {len(ratings)} 條評分")

        # 儲存資料
        self._all_ratings = ratings.copy()
        self._pending_ratings.clear()

        # 訓練模型
        data = RatingMatrix.from_ratings(ratings)
        await self._service.fit(data)

        self._last_train_time = datetime.now()

        get_logger().info("初始資料載入和訓練完成")

    async def add_ratings(self, new_ratings: List[Rating]) -> None:
        """
        新增新評分

        Args:
            new_ratings: 新評分列表
        """
        if not new_ratings:
            return

        # 新增到所有評分
        self._all_ratings.extend(new_ratings)

        # 新增到待訓練評分
        self._pending_ratings.extend(new_ratings)

        get_logger().info(
            f"新增 {len(new_ratings)} 條新評分, "
            f"待訓練評分數: {len(self._pending_ratings)}"
        )

        # 檢查是否需要自動重訓練
        if self._config.enable_auto_retrain and self._should_retrain():
            get_logger().info("觸發自動重訓練")
            await self.trigger_retrain()

    async def trigger_retrain(self) -> None:
        """
        觸發重訓練
        """
        # 等待現有訓練任務完成
        if self._training_task and not self._training_task.done():
            get_logger().info("等待現有訓練任務完成...")
            try:
                await self._training_task
            except Exception as e:
                get_logger().error(f"現有訓練任務失敗: {e}")

        # 啟動新的後臺訓練任務
        self._training_task = asyncio.create_task(
            self._retrain_async()
        )

        get_logger().info("已啟動後臺重訓練任務")

    def get_trainer_info(self) -> dict:
        """
        獲取訓練器狀態資訊

        Returns:
            訓練器資訊字典
        """
        return {
            'is_training': self._is_training,
            'last_train_time': self._last_train_time.isoformat() if self._last_train_time else None,
            'total_ratings': len(self._all_ratings),
            'pending_ratings': len(self._pending_ratings),
            'config': {
                'threshold_ratings': self._config.retrain_threshold_ratings,
                'threshold_time': self._config.retrain_threshold_time,
                'auto_retrain': self._config.enable_auto_retrain
            }
        }

    def _should_retrain(self) -> bool:
        """
        判斷是否需要重訓練

        Returns:
            True 如果滿足重訓練條件
        """
        # 如果正在訓練,不觸發新的訓練
        if self._is_training:
            return False

        # 如果沒有待訓練的評分,不需要重訓練
        if not self._pending_ratings:
            return False

        # 檢查評分數量閾值
        if len(self._pending_ratings) >= self._config.retrain_threshold_ratings:
            get_logger().info(
                f"達到評分數量閾值: {len(self._pending_ratings)} >= "
                f"{self._config.retrain_threshold_ratings}"
            )
            return True

        # 檢查時間閾值
        if self._last_train_time is not None:
            time_since_last_train = (
                datetime.now() - self._last_train_time
            ).total_seconds()
            time_threshold = self._config.retrain_threshold_time

            if time_since_last_train >= time_threshold:
                get_logger().info(
                    f"達到時間閾值: {time_since_last_train:.0f}s >= {time_threshold}s"
                )
                return True

        return False

    async def _retrain_async(self) -> None:
        """
        非同步後臺重訓練
        """
        if self._is_training:
            get_logger().warning("已有訓練任務在進行中,跳過重訓練")
            return

        self._is_training = True

        try:
            get_logger().info(
                f"開始後臺重訓練 (總評分: {len(self._all_ratings)}, "
                f"新增評分: {len(self._pending_ratings)})"
            )

            # 1. 拍攝快照
            ratings_snapshot = self._all_ratings.copy()

            # 2. 構建資料並訓練
            data = RatingMatrix.from_ratings(ratings_snapshot)
            await self._service.fit(data)

            # 3. 清空待訓練評分
            self._pending_ratings.clear()

            # 4. 更新訓練時間
            self._last_train_time = datetime.now()

            get_logger().info("後臺重訓練完成")

        except Exception as e:
            get_logger().error(f"後臺重訓練失敗: {e}")
            raise

        finally:
            self._is_training = False
