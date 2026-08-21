"""
核心資料結構和演算法基類

- RatingMatrix: 評分矩陣表示
- RecommenderAlgorithm: 演算法介面
"""

from abc import ABC, abstractmethod
from typing import List, Tuple, Set, Dict
from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy import sparse

from ..models import Rating


@dataclass
class RatingMatrix:
    """
    統一的評分矩陣表示

    提供多種資料格式轉換,供不同演算法使用:
    - NumPy arrays: 原始資料
    - pandas DataFrame: MF等演算法需要
    - SciPy sparse matrix: 協同過濾等演算法需要
    """

    user_ids: np.ndarray      # [N] 使用者ID陣列
    item_ids: np.ndarray      # [N] 物品ID陣列
    ratings: np.ndarray       # [N] 評分值陣列
    user_map: Dict[int, int]  # 原始使用者ID → 內部索引
    item_map: Dict[int, int]  # 原始物品ID → 內部索引

    @classmethod
    def from_ratings(cls, ratings: List[Rating]) -> 'RatingMatrix':
        """
        從 Rating 列表構建 RatingMatrix

        Args:
            ratings: Rating 物件列表

        Returns:
            RatingMatrix 例項
        """
        if not ratings:
            raise ValueError("評分列表不能為空")

        # 提取資料
        user_ids = np.array([r.user_id for r in ratings])
        item_ids = np.array([r.item_id for r in ratings])
        rating_values = np.array([r.rating for r in ratings])

        # 構建對映 (原始ID → 內部索引)
        unique_users = np.unique(user_ids)
        unique_items = np.unique(item_ids)

        user_map = {uid: idx for idx, uid in enumerate(unique_users)}
        item_map = {iid: idx for idx, iid in enumerate(unique_items)}

        return cls(
            user_ids=user_ids,
            item_ids=item_ids,
            ratings=rating_values,
            user_map=user_map,
            item_map=item_map
        )

    def to_dataframe(self) -> pd.DataFrame:
        """
        轉換為 pandas DataFrame (pivot table 格式)

        用於 MF 等需要矩陣格式的演算法

        Returns:
            DataFrame with users as rows, items as columns
        """
        df = pd.DataFrame({
            'userId': self.user_ids,
            'itemId': self.item_ids,
            'rating': self.ratings
        })

        return df.pivot_table(
            values='rating',
            index='userId',
            columns='itemId',
            fill_value=np.nan
        )

    def to_sparse(self) -> sparse.csr_matrix:
        """
        轉換為稀疏矩陣 (CSR 格式)

        用於協同過濾等需要稀疏表示的演算法

        Returns:
            scipy.sparse.csr_matrix
        """
        n_users = len(self.user_map)
        n_items = len(self.item_map)

        # 將原始ID對映到索引
        row_indices = np.array([self.user_map[uid] for uid in self.user_ids])
        col_indices = np.array([self.item_map[iid] for iid in self.item_ids])

        return sparse.csr_matrix(
            (self.ratings, (row_indices, col_indices)),
            shape=(n_users, n_items)
        )

    def get_user_count(self) -> int:
        """獲取使用者數量"""
        return len(self.user_map)

    def get_item_count(self) -> int:
        """獲取物品數量"""
        return len(self.item_map)

    def get_rating_count(self) -> int:
        """獲取評分數量"""
        return len(self.ratings)

    def __str__(self) -> str:
        return (
            f"RatingMatrix("
            f"users={self.get_user_count()}, "
            f"items={self.get_item_count()}, "
            f"ratings={self.get_rating_count()})"
        )


class RecommenderAlgorithm(ABC):
    """
    推薦演算法統一介面

    - 只負責推薦邏輯，非同步由服務層處理
    - 可選功能: save/load 不是必需的
    """

    @abstractmethod
    def fit(self, data: RatingMatrix) -> None:
        """
        訓練模型

        Args:
            data: 評分矩陣
        """
        pass

    @abstractmethod
    def predict(self, user_id: int, item_id: int) -> float:
        """
        預測單個使用者對物品的評分

        Args:
            user_id: 使用者ID (原始ID,不是索引)
            item_id: 物品ID (原始ID,不是索引)

        Returns:
            預測評分 (1.0-5.0)
        """
        pass

    @abstractmethod
    def recommend(
        self,
        user_id: int,
        n: int,
        exclude_ids: Set[int]
    ) -> List[Tuple[int, float]]:
        """
        為使用者生成推薦列表

        Args:
            user_id: 使用者ID (原始ID,不是索引)
            n: 推薦數量
            exclude_ids: 要排除的物品ID集合 (原始ID)

        Returns:
            [(item_id, score), ...] 按 score 降序排列
        """
        pass

    def save(self, path: str) -> None:
        """
        儲存模型 (可選實現)

        Args:
            path: 模型儲存路徑

        Raises:
            NotImplementedError: 如果演算法不支援模型儲存
        """
        raise NotImplementedError(f"{self.__class__.__name__} 不支援模型儲存")

    def load(self, path: str) -> None:
        """
        載入模型 (可選實現)

        Args:
            path: 模型檔案路徑

        Raises:
            NotImplementedError: 如果演算法不支援模型載入
        """
        raise NotImplementedError(f"{self.__class__.__name__} 不支援模型載入")

    def get_algorithm_name(self) -> str:
        """
        獲取演算法名稱

        Returns:
            演算法名稱字串
        """
        return self.__class__.__name__

    def get_algorithm_info(self) -> Dict[str, any]:
        """
        獲取演算法資訊

        Returns:
            演算法資訊字典
        """
        return {
            'name': self.get_algorithm_name(),
            'supports_save': self._supports_save(),
            'supports_load': self._supports_load(),
        }

    def _supports_save(self) -> bool:
        """檢查是否支援儲存"""
        try:
            # 嘗試呼叫 save 方法簽名
            import inspect
            source = inspect.getsource(self.save)
            return 'NotImplementedError' not in source
        except Exception:
            return False

    def _supports_load(self) -> bool:
        """檢查是否支援載入"""
        try:
            import inspect
            source = inspect.getsource(self.load)
            return 'NotImplementedError' not in source
        except Exception:
            return False
