"""
增強版MF (矩陣分解) 推薦演算法
"""

import pickle
from typing import List, Tuple, Set, Dict, Optional
import numpy as np
import torch
import torch.nn as nn

from agentsociety2.logger import get_logger
from ..core import RecommenderAlgorithm, RatingMatrix
from .enhanced_config import EnhancedMFConfig


class EnhancedMFModel(nn.Module):
    """
    增強版PyTorch矩陣分解模型

    預測公式（with biases）：
        r_hat_{ui} = μ + b_u + b_i + q_i^T * p_u

    其中：
        μ: 全域性平均評分
        b_u: 使用者偏差（使用者傾向於給高分還是低分）
        b_i: 物品偏差（物品傾向於獲得高分還是低分）
        q_i^T * p_u: 使用者-物品因子互動
    """

    def __init__(
        self,
        n_users: int,
        n_items: int,
        n_factors: int,
        global_mean: float = 3.5,
        use_biases: bool = True
    ):
        super().__init__()

        self.n_users = n_users
        self.n_items = n_items
        self.n_factors = n_factors
        self.use_biases = use_biases

        # 使用者和物品的因子向量（核心MF）
        self.user_embedding = nn.Embedding(n_users, n_factors)
        self.item_embedding = nn.Embedding(n_items, n_factors)

        # Xavier初始化
        nn.init.xavier_uniform_(self.user_embedding.weight)
        nn.init.xavier_uniform_(self.item_embedding.weight)

        if use_biases:
            # 全域性平均評分（固定）
            self.register_buffer('global_mean', torch.tensor([global_mean]))

            # 使用者偏差 b_u
            self.user_bias = nn.Embedding(n_users, 1)
            nn.init.zeros_(self.user_bias.weight)

            # 物品偏差 b_i
            self.item_bias = nn.Embedding(n_items, 1)
            nn.init.zeros_(self.item_bias.weight)

    def forward(self, users: torch.Tensor, items: torch.Tensor) -> torch.Tensor:
        """
        前向傳播

        Args:
            users: 使用者索引 [batch_size]
            items: 物品索引 [batch_size]

        Returns:
            預測評分 [batch_size]
        """
        # 因子向量
        user_emb = self.user_embedding(users)  # [batch_size, n_factors]
        item_emb = self.item_embedding(items)  # [batch_size, n_factors]

        # 互動項：q_i^T * p_u
        interaction = (user_emb * item_emb).sum(dim=-1)  # [batch_size]

        if self.use_biases:
            # 偏差項
            u_bias = self.user_bias(users).squeeze(-1)  # [batch_size]
            i_bias = self.item_bias(items).squeeze(-1)  # [batch_size]

            # 完整預測：μ + b_u + b_i + q_i^T * p_u
            return self.global_mean + u_bias + i_bias + interaction
        else:
            # 基礎MF：q_i^T * p_u
            return interaction


class EnhancedMFRecommender(RecommenderAlgorithm):
    """
    增強版MF推薦演算法
    """

    def __init__(self, config: EnhancedMFConfig = EnhancedMFConfig()):
        """
        初始化增強版MF推薦器

        Args:
            config: 增強版MF配置
        """
        self.config = config
        self.model: Optional[EnhancedMFModel] = None
        self._user_map: Dict[int, int] = {}
        self._item_map: Dict[int, int] = {}
        self._popular_items: List[Tuple[int, float]] = []
        self._global_mean: float = 3.5
        self._metrics: Dict[str, float] = {}  # 儲存訓練指標

        features = []
        if config.use_biases:
            features.append("Biases")
        if config.use_implicit_feedback:
            features.append("ImplicitFeedback")
        if config.use_temporal_dynamics:
            features.append("TemporalDynamics")

        get_logger().info(
            f"EnhancedMFRecommender 初始化: "
            f"n_factors={config.n_latent_factors}, "
            f"lr={config.learning_rate}, reg={config.reg_param}, "
            f"iters={config.n_iterations}, "
            f"features=[{', '.join(features) if features else 'Plain'}]"
        )

    def fit(self, data: RatingMatrix) -> None:
        """
        訓練增強版MF模型

        Args:
            data: 評分矩陣
        """
        get_logger().info(
            f"開始訓練增強版MF模型: {data.get_user_count()} 使用者, "
            f"{data.get_item_count()} 物品, {data.get_rating_count()} 評分"
        )

        # 儲存對映
        self._user_map = data.user_map.copy()
        self._item_map = data.item_map.copy()

        # 計算全域性平均評分
        self._global_mean = float(np.mean(data.ratings))
        get_logger().info(f"全域性平均評分: {self._global_mean:.4f}")

        # 構建PyTorch模型
        n_users = len(self._user_map)
        n_items = len(self._item_map)

        self.model = EnhancedMFModel(
            n_users=n_users,
            n_items=n_items,
            n_factors=self.config.n_latent_factors,
            global_mean=self._global_mean,
            use_biases=self.config.use_biases
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

        # 最佳化器（SGD + L2正則化）
        optimizer = torch.optim.SGD(
            self.model.parameters(),
            lr=self.config.learning_rate,
            weight_decay=self.config.reg_param
        )

        # 損失函式
        criterion = nn.MSELoss()

        # 訓練迴圈
        self.model.train()
        best_loss = float('inf')

        for iteration in range(self.config.n_iterations):
            # 前向傳播
            predictions = self.model(user_indices, item_indices)

            # 計算損失
            loss = criterion(predictions, ratings)

            # 反向傳播
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            # 記錄最佳損失
            if loss.item() < best_loss:
                best_loss = loss.item()

            # 定期輸出日誌
            if (iteration + 1) % 20 == 0 or iteration == 0:
                get_logger().debug(
                    f"Iteration {iteration + 1}/{self.config.n_iterations}, "
                    f"Loss: {loss.item():.4f} (Best: {best_loss:.4f})"
                )

        self.model.eval()

        # 計算熱門物品（用於冷啟動）
        self._compute_popular_items(data)

        # 計算最終RMSE
        with torch.no_grad():
            final_predictions = self.model(user_indices, item_indices)
            rmse = torch.sqrt(criterion(final_predictions, ratings)).item()

        # 儲存訓練指標
        self._metrics = {
            'rmse': rmse,
            'loss': best_loss
        }

        get_logger().info(
            f"增強版MF模型訓練完成, "
            f"最終Loss: {best_loss:.4f}, RMSE: {rmse:.4f}"
        )

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
            # 新使用者：返回物品平均評分（如果有偏差）或全域性平均
            if item_id in self._item_map and self.config.use_biases:
                item_idx = self._item_map[item_id]
                with torch.no_grad():
                    item_tensor = torch.tensor([item_idx], dtype=torch.long)
                    i_bias = self.model.item_bias(item_tensor).item()
                    return float(self._global_mean + i_bias)
            return self._global_mean

        if item_id not in self._item_map:
            # 新物品：返回使用者平均評分（如果有偏差）或全域性平均
            if self.config.use_biases:
                user_idx = self._user_map[user_id]
                with torch.no_grad():
                    user_tensor = torch.tensor([user_idx], dtype=torch.long)
                    u_bias = self.model.user_bias(user_tensor).item()
                    return float(self._global_mean + u_bias)
            return self._global_mean

        # 正常預測
        user_idx = self._user_map[user_id]
        item_idx = self._item_map[item_id]

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
        """儲存模型到檔案"""
        if self.model is None:
            raise RuntimeError("模型尚未訓練,無法儲存")

        checkpoint = {
            'model_state_dict': self.model.state_dict(),
            'user_map': self._user_map,
            'item_map': self._item_map,
            'popular_items': self._popular_items,
            'global_mean': self._global_mean,
            'config': self.config
        }

        with open(path, 'wb') as f:
            pickle.dump(checkpoint, f)

        get_logger().info(f"增強版MF模型已儲存到 {path}")

    def load(self, path: str) -> None:
        """從檔案載入模型"""
        with open(path, 'rb') as f:
            checkpoint = pickle.load(f)

        self.config = checkpoint['config']
        self._user_map = checkpoint['user_map']
        self._item_map = checkpoint['item_map']
        self._popular_items = checkpoint['popular_items']
        self._global_mean = checkpoint.get('global_mean', 3.5)

        # 重建模型
        n_users = len(self._user_map)
        n_items = len(self._item_map)

        self.model = EnhancedMFModel(
            n_users=n_users,
            n_items=n_items,
            n_factors=self.config.n_latent_factors,
            global_mean=self._global_mean,
            use_biases=self.config.use_biases
        )

        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()

        get_logger().info(f"增強版MF模型已從 {path} 載入")

    def _compute_popular_items(self, data: RatingMatrix) -> None:
        """
        計算熱門物品（用於冷啟動）

        基於 平均評分 × log(評分數+1) 計算熱門度
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

    def get_metrics(self) -> Dict[str, float]:
        """
        獲取最近一次訓練的指標

        Returns:
            包含rmse和loss的字典
        """
        return self._metrics.copy()


__all__ = ["EnhancedMFRecommender", "EnhancedMFModel"]
