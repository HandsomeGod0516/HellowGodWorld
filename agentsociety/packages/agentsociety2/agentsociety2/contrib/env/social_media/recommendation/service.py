"""
推薦服務層
"""

import asyncio
import time
from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass

from agentsociety2.logger import get_logger
from .algorithms.core import RecommenderAlgorithm, RatingMatrix


@dataclass
class ServiceConfig:
    """
    推薦服務配置

    Args:
        cache_ttl: 快取過期時間 (秒),預設 300秒
        max_batch_size: 最大批次推薦大小,預設 100
        timeout: 請求超時時間 (秒),預設 10秒
    """
    cache_ttl: int = 300
    max_batch_size: int = 100
    timeout: float = 10.0


class RecommendationService:
    """
    推薦服務
    """

    def __init__(
        self,
        algorithm: RecommenderAlgorithm,
        config: ServiceConfig = ServiceConfig()
    ):
        """
        初始化推薦服務

        Args:
            algorithm: 推薦演算法例項
            config: 服務配置
        """
        self._algorithm = algorithm
        self._config = config

        # 快取: cache_key -> (timestamp, result)
        self._cache: Dict[str, Tuple[float, List[Tuple[int, float]]]] = {}

        # 鎖保護模型訪問
        self._lock = asyncio.Lock()

        get_logger().info(
            f"RecommendationService 初始化: "
            f"algorithm={algorithm.get_algorithm_name()}, "
            f"cache_ttl={config.cache_ttl}s"
        )

    async def fit(self, data: RatingMatrix) -> None:
        """
        訓練模型 (非同步包裝)

        Args:
            data: 評分矩陣
        """
        get_logger().info(f"開始訓練模型: {data}")

        async with self._lock:
            # 線上程池中執行同步訓練
            await asyncio.to_thread(self._algorithm.fit, data)

        # 訓練後清空快取
        self._cache.clear()

        get_logger().info("模型訓練完成")

    async def predict(self, user_id: int, item_id: int) -> float:
        """
        預測評分 (非同步包裝)

        Args:
            user_id: 使用者ID
            item_id: 物品ID

        Returns:
            預測評分 (1.0-5.0)
        """
        return await asyncio.to_thread(
            self._algorithm.predict,
            user_id,
            item_id
        )

    async def recommend(
        self,
        user_id: int,
        n: int = 20,
        exclude_rated: bool = True,
        exclude_ids: Optional[Set[int]] = None
    ) -> List[Tuple[int, float]]:
        """
        生成推薦 (帶快取)

        Args:
            user_id: 使用者ID
            n: 推薦數量
            exclude_rated: 是否排除已評分物品 (暫未實現,保留介面)
            exclude_ids: 額外要排除的物品ID集合

        Returns:
            [(item_id, score), ...] 按 score 降序排列
        """
        # 檢查快取
        exclude_set = exclude_ids or set()
        cache_key = f"{user_id}:{n}:{len(exclude_set)}"

        if cache_key in self._cache:
            timestamp, cached_result = self._cache[cache_key]
            if time.time() - timestamp < self._config.cache_ttl:
                get_logger().debug(f"快取命中: user={user_id}")
                return cached_result

        # 呼叫演算法生成推薦
        result = await asyncio.to_thread(
            self._algorithm.recommend,
            user_id,
            n,
            exclude_set
        )

        # 更新快取
        self._cache[cache_key] = (time.time(), result)

        get_logger().debug(
            f"為使用者 {user_id} 生成 {len(result)} 條推薦"
        )

        return result

    async def batch_recommend(
        self,
        user_ids: List[int],
        n: int = 20,
        exclude_ids: Optional[Dict[int, Set[int]]] = None
    ) -> Dict[int, List[Tuple[int, float]]]:
        """
        批次生成推薦

        Args:
            user_ids: 使用者ID列表
            n: 每個使用者的推薦數量
            exclude_ids: 每個使用者要排除的物品ID字典

        Returns:
            {user_id: [(item_id, score), ...], ...}
        """
        exclude_dict = exclude_ids or {}

        # 併發執行推薦
        tasks = [
            self.recommend(
                user_id=uid,
                n=n,
                exclude_ids=exclude_dict.get(uid)
            )
            for uid in user_ids
        ]

        results = await asyncio.gather(*tasks)

        return {
            user_id: result
            for user_id, result in zip(user_ids, results)
        }

    async def save_model(self, path: str) -> None:
        """
        儲存模型 (非同步包裝)

        Args:
            path: 模型儲存路徑
        """
        async with self._lock:
            await asyncio.to_thread(self._algorithm.save, path)

        get_logger().info(f"模型已儲存到 {path}")

    async def load_model(self, path: str) -> None:
        """
        載入模型 (非同步包裝)

        Args:
            path: 模型檔案路徑
        """
        async with self._lock:
            await asyncio.to_thread(self._algorithm.load, path)

        # 載入後清空快取
        self._cache.clear()

        get_logger().info(f"模型已從 {path} 載入")

    def get_algorithm_info(self) -> Dict[str, any]:
        """
        獲取演算法資訊

        Returns:
            演算法資訊字典
        """
        return self._algorithm.get_algorithm_info()

    def clear_cache(self) -> None:
        """清空快取"""
        self._cache.clear()
        get_logger().info("推薦快取已清空")

    def get_cache_stats(self) -> Dict[str, any]:
        """
        獲取快取統計

        Returns:
            快取統計資訊
        """
        current_time = time.time()
        valid_entries = sum(
            1 for timestamp, _ in self._cache.values()
            if current_time - timestamp < self._config.cache_ttl
        )

        return {
            'total_entries': len(self._cache),
            'valid_entries': valid_entries,
            'cache_ttl': self._config.cache_ttl
        }
