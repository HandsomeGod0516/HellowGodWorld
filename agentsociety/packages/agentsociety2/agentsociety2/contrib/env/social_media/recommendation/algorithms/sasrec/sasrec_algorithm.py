"""
SASRec推薦演算法包裝類
"""

import pickle
from typing import List, Tuple, Set, Dict, Optional
from collections import defaultdict
import numpy as np
import torch
import torch.nn as nn

from agentsociety2.logger import get_logger
from ..core import RecommenderAlgorithm, RatingMatrix
from .sasrec_config import SASRecConfig
from .sasrec_model import SASRec


class SASRecRecommender(RecommenderAlgorithm):
    """
    SASRec (Self-Attentive Sequential Recommendation) 推薦演算法
    """

    def __init__(self, config: SASRecConfig = SASRecConfig()):
        """
        初始化SASRec推薦器

        Args:
            config: SASRec演算法配置
        """
        self.config = config
        self.model: Optional[SASRec] = None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # ID對映（原始ID ↔ 內部索引）
        self._user_map: Dict[int, int] = {}
        self._item_map: Dict[int, int] = {}

        # 使用者行為序列（儲存每個使用者的歷史物品ID列表）
        self._user_sequences: Dict[int, List[int]] = {}

        # 熱門物品（用於冷啟動）
        self._popular_items: List[Tuple[int, float]] = []

        get_logger().info(
            f"SASRecRecommender 初始化: hidden={config.hidden_units}, "
            f"maxlen={config.maxlen}, blocks={config.num_blocks}, "
            f"lr={config.learning_rate}, device={self.device}"
        )

    def fit(self, data: RatingMatrix) -> None:
        """
        訓練SASRec模型

        流程：
        1. 構建使用者序列（按時間戳排序）
        2. 建立訓練資料（序列 + 目標物品）
        3. 訓練Transformer模型
        4. 計算熱門物品（冷啟動用）

        Args:
            data: 評分矩陣（需要包含時間戳資訊）
        """
        get_logger().info(
            f"開始訓練 SASRec 模型: {data.get_user_count()} 使用者, "
            f"{data.get_item_count()} 物品, {data.get_rating_count()} 評分"
        )

        # 1. 構建ID對映
        self._user_map = data.user_map.copy()
        self._item_map = data.item_map.copy()

        # 2. 更新配置中的使用者/物品數量
        self.config.user_num = len(self._user_map)
        self.config.item_num = len(self._item_map) + 1  # +1 for padding (ID=0)

        # 3. 構建使用者序列（按使用者分組，按時間排序）
        self._build_user_sequences(data)

        # 4. 建立PyTorch模型
        model_config = self.config  # 直接使用config物件
        self.model = SASRec(model_config).to(self.device)

        # 5. 準備訓練資料
        train_loader = self._prepare_training_data()

        # 6. 訓練迴圈
        optimizer = torch.optim.Adam(
            self.model.parameters(),
            lr=self.config.learning_rate,
            weight_decay=self.config.weight_decay
        )
        criterion = nn.BCEWithLogitsLoss()

        best_loss = float('inf')
        patience_counter = 0

        self.model.train()
        for epoch in range(self.config.max_epochs):
            total_loss = 0.0
            batch_count = 0

            for seqs, targets, labels in train_loader:
                seqs = seqs.to(self.device)
                targets = targets.to(self.device)
                labels = labels.to(self.device).float()

                # 前向傳播
                logits = self.model(seqs, targets)
                loss = criterion(logits, labels)

                # 反向傳播
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                total_loss += loss.item()
                batch_count += 1

            avg_loss = total_loss / batch_count if batch_count > 0 else 0.0

            # 評估和早停
            if (epoch + 1) % self.config.eval_interval == 0:
                get_logger().debug(
                    f"Epoch {epoch + 1}/{self.config.max_epochs}, "
                    f"Loss: {avg_loss:.4f}"
                )

                if avg_loss < best_loss:
                    best_loss = avg_loss
                    patience_counter = 0
                else:
                    patience_counter += 1

                if patience_counter >= self.config.patience:
                    get_logger().info(f"早停觸發於 epoch {epoch + 1}")
                    break

        self.model.eval()

        # 7. 計算熱門物品
        self._compute_popular_items(data)

        get_logger().info(f"SASRec 模型訓練完成, 最終損失: {best_loss:.4f}")

    def predict(self, user_id: int, item_id: int) -> float:
        """
        預測使用者對物品的評分

        使用使用者的歷史序列編碼 + 目標物品嵌入計算匹配分數

        Args:
            user_id: 使用者ID（原始ID）
            item_id: 物品ID（原始ID）

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

        # 獲取使用者歷史序列
        user_seq = self._get_user_sequence(user_id)

        # 獲取物品內部索引（+1因為0是padding）
        item_idx = self._item_map[item_id] + 1

        # 預測
        with torch.no_grad():
            seq_tensor = torch.LongTensor([user_seq]).to(self.device)
            item_tensor = torch.LongTensor([item_idx]).to(self.device)
            logit = self.model.forward_eval(
                user_ids=None,  # SASRec不使用user_id
                target_item=item_tensor,
                log_seqs=seq_tensor
            )

        # Sigmoid轉換到[0,1]，然後對映到[1,5]
        score = torch.sigmoid(logit).item()
        rating = 1.0 + score * 4.0

        return max(1.0, min(5.0, rating))

    def recommend(
        self,
        user_id: int,
        n: int,
        exclude_ids: Set[int]
    ) -> List[Tuple[int, float]]:
        """
        為使用者生成推薦列表

        使用predict_all()計算所有物品的分數，返回Top-N

        Args:
            user_id: 使用者ID（原始ID）
            n: 推薦數量
            exclude_ids: 要排除的物品ID集合

        Returns:
            [(item_id, score), ...] 按 score 降序排列
        """
        if self.model is None:
            raise RuntimeError("模型尚未訓練,請先呼叫 fit()")

        # 冷啟動：新使用者返回熱門物品
        if user_id not in self._user_map:
            return [
                (item_id, score)
                for item_id, score in self._popular_items
                if item_id not in exclude_ids
            ][:n]

        # 獲取使用者歷史序列
        user_seq = self._get_user_sequence(user_id)

        # 預測所有物品的分數
        with torch.no_grad():
            seq_tensor = torch.LongTensor([user_seq]).to(self.device)
            logits = self.model.predict_all(
                user_ids=None,
                log_seqs=seq_tensor
            )  # [1, item_num]

            # Sigmoid轉換
            scores = torch.sigmoid(logits).squeeze(0).cpu().numpy()

        # 構建候選列表（排除已互動物品）
        all_scores: List[Tuple[int, float]] = []
        for original_item_id, internal_idx in self._item_map.items():
            if original_item_id in exclude_ids:
                continue
            # internal_idx+1 因為模型中0是padding
            score = scores[internal_idx + 1]
            # 對映到[1,5]
            rating = 1.0 + float(score) * 4.0
            all_scores.append((original_item_id, rating))

        # 按分數降序排序
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
            'user_sequences': self._user_sequences,
            'popular_items': self._popular_items,
            'config': self.config
        }

        with open(path, 'wb') as f:
            pickle.dump(checkpoint, f)

        get_logger().info(f"SASRec 模型已儲存到 {path}")

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
        self._user_sequences = checkpoint['user_sequences']
        self._popular_items = checkpoint['popular_items']

        # 重建模型
        self.model = SASRec(self.config).to(self.device)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()

        get_logger().info(f"SASRec 模型已從 {path} 載入")

    def _build_user_sequences(self, data: RatingMatrix) -> None:
        """
        從評分矩陣構建使用者行為序列

        假設data按時間戳排序，相同使用者的互動已經按順序排列

        Args:
            data: 評分矩陣
        """
        user_sequences = defaultdict(list)

        # 按使用者分組收集物品
        for user_id, item_id in zip(data.user_ids, data.item_ids):
            # 轉換為內部索引 (+1因為0是padding)
            item_idx = self._item_map[item_id] + 1
            user_sequences[user_id].append(item_idx)

        # 截斷到maxlen（保留最近的N個）
        for user_id, seq in user_sequences.items():
            if len(seq) > self.config.maxlen:
                user_sequences[user_id] = seq[-self.config.maxlen:]

        self._user_sequences = dict(user_sequences)

        get_logger().debug(
            f"構建了 {len(self._user_sequences)} 個使用者序列, "
            f"平均長度: {np.mean([len(s) for s in self._user_sequences.values()]):.1f}"
        )

    def _get_user_sequence(self, user_id: int) -> List[int]:
        """
        獲取使用者的行為序列（padding到maxlen）

        Args:
            user_id: 使用者ID（原始ID）

        Returns:
            填充後的序列 [maxlen]
        """
        if user_id not in self._user_sequences:
            return [0] * self.config.maxlen

        seq = self._user_sequences[user_id]

        # 左側填充0（padding）
        if len(seq) < self.config.maxlen:
            padding_len = self.config.maxlen - len(seq)
            padded_seq = [0] * padding_len + seq
        else:
            # 取最近的maxlen個
            padded_seq = seq[-self.config.maxlen:]

        return padded_seq

    def _prepare_training_data(self) -> List[Tuple[torch.Tensor, torch.Tensor, torch.Tensor]]:
        """
        準備訓練資料：序列 → 下一個物品

        使用滑動視窗生成訓練樣本：
        - 正樣本：實際下一個物品
        - 負樣本：隨機取樣物品

        Returns:
            [(seqs, targets, labels), ...] 的批次列表
        """
        train_data = []

        for user_id, seq in self._user_sequences.items():
            if len(seq) < 2:
                continue  # 序列太短，跳過

            # 為序列中的每個位置生成訓練樣本
            for i in range(1, len(seq)):
                # 輸入序列：[0, ..., item_i-1]
                input_seq = [0] * (self.config.maxlen - i) + seq[:i]

                # 正樣本：下一個物品
                pos_item = seq[i]
                train_data.append((input_seq, pos_item, 1.0))

                # 負樣本：隨機物品（不在使用者序列中）
                neg_item = np.random.randint(1, self.config.item_num)
                while neg_item in seq:
                    neg_item = np.random.randint(1, self.config.item_num)
                train_data.append((input_seq, neg_item, 0.0))

        # 轉換為批次
        batch_size = self.config.batch_size
        batches = []
        for i in range(0, len(train_data), batch_size):
            batch = train_data[i:i + batch_size]
            seqs = torch.LongTensor([x[0] for x in batch])
            targets = torch.LongTensor([x[1] for x in batch])
            labels = torch.FloatTensor([x[2] for x in batch])
            batches.append((seqs, targets, labels))

        get_logger().debug(
            f"生成了 {len(train_data)} 個訓練樣本, "
            f"{len(batches)} 個批次"
        )

        return batches

    def _compute_popular_items(self, data: RatingMatrix) -> None:
        """
        計算熱門物品（用於冷啟動）

        基於 平均評分 × log(評分數+1) 計算熱門度

        Args:
            data: 評分矩陣
        """
        item_ratings: Dict[int, List[float]] = defaultdict(list)

        for item_id, rating in zip(data.item_ids, data.ratings):
            item_ratings[item_id].append(rating)

        popular_scores = []
        for item_id, ratings in item_ratings.items():
            avg_rating = np.mean(ratings)
            count = len(ratings)
            popularity = avg_rating * np.log(count + 1)
            popular_scores.append((item_id, popularity))

        # 按熱門度降序排序
        popular_scores.sort(key=lambda x: x[1], reverse=True)

        # 歸一化到[1.0, 5.0]
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
