"""
推薦系統評估指標模組 
"""

import numpy as np
from typing import List, Tuple
from dataclasses import dataclass

from sklearn.metrics import roc_auc_score


@dataclass
class RecommendationMetrics:
    """推薦系統評估指標"""

    # 準確性指標
    rmse: float = 0.0
    mae: float = 0.0

    # 排序指標
    ndcg: float = 0.0          # NDCG@K (預設K=10)
    auc: float = 0.0           # 全域性AUC
    uauc: float = 0.0          # User-wise AUC

    # 使用者互動指標
    view_rate: float = 0.0
    rating_rate: float = 0.0
    avg_rating: float = 0.0


class MetricsCalculator:
    """評估指標計算器"""

    def __init__(self):
        """初始化計算器"""
        pass

    def calculate_rmse_mae(
        self,
        predictions: List[float],
        ground_truth: List[float]
    ) -> Tuple[float, float]:
        """
        計算RMSE和MAE

        Args:
            predictions: 預測評分列表
            ground_truth: 真實評分列表

        Returns:
            (rmse, mae)
        """
        if not predictions or not ground_truth:
            return 0.0, 0.0

        predictions = np.array(predictions)
        ground_truth = np.array(ground_truth)

        # RMSE
        rmse = np.sqrt(np.mean((predictions - ground_truth) ** 2))

        # MAE
        mae = np.mean(np.abs(predictions - ground_truth))

        return float(rmse), float(mae)

    def calculate_ndcg(
        self,
        user_ids: List[int],
        predictions: List[float],
        labels: List[int],
        k: int = 10
    ) -> Tuple[float, int]:
        """
        計算User-wise NDCG@K

        Args:
            user_ids: 使用者ID列表
            predictions: 預測評分列表
            labels: 真實標籤列表（0或1）
            k: NDCG@K的K值，預設10

        Returns:
            (ndcg, computed_users): NDCG值和成功計算的使用者數
        """
        if len(user_ids) == 0 or len(predictions) == 0 or len(labels) == 0:
            return 0.0, 0

        user_ids = np.array(user_ids)
        predictions = np.array(predictions)
        labels = np.array(labels)

        # 按使用者分組
        unique_users, inverse, counts = np.unique(
            user_ids, return_inverse=True, return_counts=True
        )
        index = np.argsort(inverse)

        # 為每個使用者計算DCG/IDCG
        ndcg_list = []
        computed_users = 0

        total_num = 0
        for k_idx, user_id in enumerate(unique_users):
            start_id = total_num
            end_id = total_num + counts[k_idx]
            user_indices = index[start_id:end_id]

            # 跳過只有1個互動的使用者
            if counts[k_idx] == 1:
                total_num += counts[k_idx]
                continue

            user_preds = predictions[user_indices]
            user_labels = labels[user_indices]

            # 檢查標籤唯一性
            if len(np.unique(user_labels)) < 2:
                total_num += counts[k_idx]
                continue

            # 計算DCG
            pos_num = user_labels.sum()
            if pos_num == 0 or pos_num == len(user_labels):
                total_num += counts[k_idx]
                continue

            # 按預測分數降序排序
            ranked_id = np.argsort(-user_preds)
            ranked_label = user_labels[ranked_id]

            # DCG計算
            flag = 1.0 / np.log2(np.arange(len(ranked_label)) + 2.0)
            dcg = (ranked_label * flag).sum()

            # IDCG計算（理想情況）
            idcg = flag[:int(pos_num)].sum()

            if idcg > 0:
                ndcg = dcg / idcg
                ndcg_list.append(ndcg)
                computed_users += 1

            total_num += counts[k_idx]

        if computed_users > 0:
            return float(np.mean(ndcg_list)), computed_users
        else:
            return 0.0, 0

    def calculate_auc(
        self,
        predictions: List[float],
        labels: List[int]
    ) -> float:
        """
        計算全域性AUC

        Args:
            predictions: 預測評分列表
            labels: 真實標籤列表（0或1）

        Returns:
            全域性AUC值
        """
        if len(predictions) == 0 or len(labels) == 0:
            return 0.0

        predictions = np.array(predictions)
        labels = np.array(labels)

        # 檢查標籤唯一性
        if len(np.unique(labels)) < 2:
            return 0.0

        auc = roc_auc_score(labels, predictions)
        return float(auc)

    def calculate_uauc(
        self,
        user_ids: List[int],
        predictions: List[float],
        labels: List[int]
    ) -> Tuple[float, int]:
        """
        計算User-wise AUC

        Args:
            user_ids: 使用者ID列表
            predictions: 預測評分列表
            labels: 真實標籤列表（0或1）

        Returns:
            (uauc, computed_users): UAUC值和成功計算的使用者數
        """
        if len(user_ids) == 0 or len(predictions) == 0 or len(labels) == 0:
            return 0.0, 0

        user_ids = np.array(user_ids)
        predictions = np.array(predictions)
        labels = np.array(labels)

        # 按使用者分組
        unique_users, inverse, counts = np.unique(
            user_ids, return_inverse=True, return_counts=True
        )
        index = np.argsort(inverse)

        # 為每個使用者計算AUC
        user_aucs = []
        computed_users = 0

        total_num = 0
        for k, user_id in enumerate(unique_users):
            start_id = total_num
            end_id = total_num + counts[k]
            user_indices = index[start_id:end_id]

            # 跳過只有一個互動的使用者
            if counts[k] == 1:
                total_num += counts[k]
                continue

            user_preds = predictions[user_indices]
            user_labels = labels[user_indices]

            # 檢查標籤唯一性
            if len(np.unique(user_labels)) < 2:
                total_num += counts[k]
                continue

            try:
                user_auc = roc_auc_score(user_labels, user_preds)
                user_aucs.append(user_auc)
                computed_users += 1
            except Exception:
                pass

            total_num += counts[k]

        if computed_users > 0:
            uauc = np.mean(user_aucs)
            return float(uauc), computed_users
        else:
            return 0.0, 0


__all__ = [
    "RecommendationMetrics",
    "MetricsCalculator"
]
