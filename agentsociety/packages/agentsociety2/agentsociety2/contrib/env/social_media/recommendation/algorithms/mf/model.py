"""
MF (矩陣分解) 推薦演算法實現

基於 PyTorch 的矩陣分解演算法,使用 SGD 最佳化
"""

import pickle
from typing import List, Tuple, Set, Dict, Optional
import numpy as np
import torch
import torch.nn as nn

from agentsociety2.logger import get_logger
from ..core import RecommenderAlgorithm, RatingMatrix
from .config import MFConfig


class MFModel(nn.Module):
    """
    PyTorch 矩陣分解模型

    使用使用者和物品的 embedding 表示,透過點積預測評分
    """

    def __init__(self, n_users: int, n_items: int, n_factors: int):
        super().__init__()
        self.user_embedding = nn.Embedding(n_users, n_factors)
        self.item_embedding = nn.Embedding(n_items, n_factors)

        # Xavier 初始化
        nn.init.xavier_uniform_(self.user_embedding.weight)
        nn.init.xavier_uniform_(self.item_embedding.weight)

    def forward(self, users: torch.Tensor, items: torch.Tensor) -> torch.Tensor:
        """
        前向傳播: 計算使用者-物品評分預測

        Args:
            users: 使用者索引張量 [batch_size]
            items: 物品索引張量 [batch_size]

        Returns:
            預測評分張量 [batch_size]
        """
        user_emb = self.user_embedding(users)  # [batch_size, n_factors]
        item_emb = self.item_embedding(items)  # [batch_size, n_factors]

        # 點積
        return (user_emb * item_emb).sum(dim=-1)


class MFRecommender(RecommenderAlgorithm):
    """
    MF (矩陣分解) 推薦演算法

    直接實現 RecommenderAlgorithm 介面,不需要額外的 Adapter 層

    特性:
    - PyTorch 實現,支援 GPU 加速
    - SGD 最佳化 + L2 正則化
    - 冷啟動處理: 新使用者返回熱門物品,新物品返回預設評分
    - 模型持久化: 支援儲存/載入
    """

    def __init__(self, config: MFConfig = MFConfig()):
        """
        初始化 MF 推薦器

        Args:
            config: MF 演算法配置
        """
        self.config = config
        self.model: Optional[MFModel] = None
        self._user_map: Dict[int, int] = {}
        self._item_map: Dict[int, int] = {}
        self._popular_items: List[Tuple[int, float]] = []

        get_logger().info(
            f"MFRecommender 初始化: n_factors={config.n_latent_factors}, "
            f"lr={config.learning_rate}, reg={config.reg_param}, "
            f"iters={config.n_iterations}"
        )

    def fit(self, data: RatingMatrix) -> None:
        """
        訓練 MF 模型

        Args:
            data: 評分矩陣
        """
        get_logger().info(
            f"開始訓練 MF 模型: {data.get_user_count()} 使用者, "
            f"{data.get_item_count()} 物品, {data.get_rating_count()} 評分"
        )

        # 儲存對映
        self._user_map = data.user_map.copy()
        self._item_map = data.item_map.copy()

        # 構建 PyTorch 模型
        n_users = len(self._user_map)
        n_items = len(self._item_map)

        self.model = MFModel(
            n_users=n_users,
            n_items=n_items,
            n_factors=self.config.n_latent_factors
        )

        # 準備訓練資料
        user_indices = torch.tensor(
            [self._user_map[uid] for uid in data.user_ids],
            dtype=torch.long
        )
        item_indices = torch.tensor(
            [self._item_map[iid] for iid in data.item_ids],
            dtype=torch.long
        )
        ratings = torch.tensor(data.ratings, dtype=torch.float32)

        # 最佳化器 (SGD + L2正則化)
        optimizer = torch.optim.SGD(
            self.model.parameters(),
            lr=self.config.learning_rate,
            weight_decay=self.config.reg_param
        )

        # 損失函式
        criterion = nn.MSELoss()

        # 訓練迴圈
        self.model.train()
        for iteration in range(self.config.n_iterations):
            # 前向傳播
            predictions = self.model(user_indices, item_indices)

            # 計算損失
            loss = criterion(predictions, ratings)

            # 反向傳播
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            if (iteration + 1) % 20 == 0 or iteration == 0:
                get_logger().debug(
                    f"Iteration {iteration + 1}/{self.config.n_iterations}, "
                    f"Loss: {loss.item():.4f}"
                )

        self.model.eval()

        # 計算熱門物品 (用於冷啟動)
        self._compute_popular_items(data)

        get_logger().info(f"MF 模型訓練完成, 最終損失: {loss.item():.4f}")

    def predict(self, user_id: int, item_id: int) -> float:
        """
        預測使用者對物品的評分

        Args:
            user_id: 使用者ID
            item_id: 物品ID

        Returns:
            預測評分 (1.0-5.0)
        """
        if self.model is None:
            raise RuntimeError("模型尚未訓練,請先呼叫 fit()")

        # 冷啟動處理
        if user_id not in self._user_map:
            return 2.5  # 新使用者預設評分
        if item_id not in self._item_map:
            return 2.5  # 新物品預設評分

        # 獲取內部索引
        user_idx = self._user_map[user_id]
        item_idx = self._item_map[item_id]

        # 預測
        with torch.no_grad():
            user_tensor = torch.tensor([user_idx], dtype=torch.long)
            item_tensor = torch.tensor([item_idx], dtype=torch.long)
            prediction = self.model(user_tensor, item_tensor)

        # 限制到 [1.0, 5.0] 範圍
        score = float(prediction.item())
        return max(1.0, min(5.0, score))

    def recommend(
        self,
        user_id: int,
        n: int,
        exclude_ids: Set[int]
    ) -> List[Tuple[int, float]]:
        """
        為使用者生成推薦列表

        Args:
            user_id: 使用者ID
            n: 推薦數量
            exclude_ids: 要排除的物品ID集合

        Returns:
            [(item_id, score), ...] 按 score 降序排列
        """
        if self.model is None:
            raise RuntimeError("模型尚未訓練,請先呼叫 fit()")

        # 冷啟動: 新使用者返回熱門物品
        if user_id not in self._user_map:
            return [
                (item_id, score)
                for item_id, score in self._popular_items
                if item_id not in exclude_ids
            ][:n]

        # 計算所有物品的預測評分
        user_idx = self._user_map[user_id]
        all_scores: List[Tuple[int, float]] = []

        with torch.no_grad():
            user_tensor = torch.tensor([user_idx], dtype=torch.long)

            for item_id, item_idx in self._item_map.items():
                # 跳過要排除的物品
                if item_id in exclude_ids:
                    continue

                item_tensor = torch.tensor([item_idx], dtype=torch.long)
                score = self.model(user_tensor, item_tensor)
                all_scores.append((item_id, float(score.item())))

        # 按評分降序排序
        all_scores.sort(key=lambda x: x[1], reverse=True)

        return all_scores[:n]

    def save(self, path: str) -> None:
        """
        儲存模型到檔案

        Args:
            path: 模型儲存路徑
        """
        if self.model is None:
            raise RuntimeError("模型尚未訓練,無法儲存")

        checkpoint = {
            'model_state_dict': self.model.state_dict(),
            'user_map': self._user_map,
            'item_map': self._item_map,
            'popular_items': self._popular_items,
            'config': self.config
        }

        with open(path, 'wb') as f:
            pickle.dump(checkpoint, f)

        get_logger().info(f"MF 模型已儲存到 {path}")

    def load(self, path: str) -> None:
        """
        從檔案載入模型

        Args:
            path: 模型檔案路徑
        """
        with open(path, 'rb') as f:
            checkpoint = pickle.load(f)

        self.config = checkpoint['config']
        self._user_map = checkpoint['user_map']
        self._item_map = checkpoint['item_map']
        self._popular_items = checkpoint['popular_items']

        # 重建模型
        n_users = len(self._user_map)
        n_items = len(self._item_map)

        self.model = MFModel(
            n_users=n_users,
            n_items=n_items,
            n_factors=self.config.n_latent_factors
        )

        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()

        get_logger().info(f"MF 模型已從 {path} 載入")

    def _compute_popular_items(self, data: RatingMatrix) -> None:
        """
        計算熱門物品 (用於冷啟動)

        基於 平均評分 × log(評分數+1) 計算熱門度

        Args:
            data: 評分矩陣
        """
        item_ratings: Dict[int, List[float]] = {}

        for item_id, rating in zip(data.item_ids, data.ratings):
            if item_id not in item_ratings:
                item_ratings[item_id] = []
            item_ratings[item_id].append(rating)

        popular_scores = []
        for item_id, ratings in item_ratings.items():
            avg_rating = np.mean(ratings)
            count = len(ratings)
            # 熱門度 = 平均評分 × log(評分數+1)
            popularity = avg_rating * np.log(count + 1)
            popular_scores.append((item_id, popularity))

        # 按熱門度降序排序
        popular_scores.sort(key=lambda x: x[1], reverse=True)

        # 歸一化到 [1.0, 5.0]
        if popular_scores:
            max_score = popular_scores[0][1]
            min_score = popular_scores[-1][1]
            if max_score > min_score:
                self._popular_items = [
                    (item_id, 1.0 + 4.0 * (score - min_score) / (max_score - min_score))
                    for item_id, score in popular_scores
                ]
            else:
                self._popular_items = [(item_id, 3.0) for item_id, _ in popular_scores]
        else:
            self._popular_items = []

        get_logger().debug(f"計算了 {len(self._popular_items)} 個熱門物品")
