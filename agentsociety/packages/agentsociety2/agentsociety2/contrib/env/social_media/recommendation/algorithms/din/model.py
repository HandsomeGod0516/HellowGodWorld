"""
DIN (Deep Interest Network) 推薦演算法實現

"""

import pickle
import math
from typing import List, Tuple, Set, Dict, Optional
from collections import defaultdict
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

from agentsociety2.logger import get_logger
from ..core import RecommenderAlgorithm, RatingMatrix
from .config import DINConfig


# ========== DIN 模型元件 ==========

class Dice(nn.Module):
    """Data Adaptive Activation Function in DIN"""
    
    def __init__(self, emb_size: int, dim: int = 2, epsilon: float = 1e-8):
        super().__init__()
        assert dim in (2, 3)
        self.bn = nn.BatchNorm1d(emb_size, eps=epsilon)
        self.sigmoid = nn.Sigmoid()
        self.dim = dim
        
        if self.dim == 2:
            self.alpha = nn.Parameter(torch.zeros((emb_size,)))
        else:
            self.alpha = nn.Parameter(torch.zeros((emb_size, 1)))
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        assert x.dim() == self.dim
        if self.dim == 2:
            x_p = self.sigmoid(self.bn(x))
            out = self.alpha * (1 - x_p) * x + x_p * x
        else:
            x = torch.transpose(x, 1, 2)
            x_p = self.sigmoid(self.bn(x))
            out = self.alpha * (1 - x_p) * x + x_p * x
            out = torch.transpose(out, 1, 2)
        return out


class FullyConnectedLayer(nn.Module):
    """多層感知機模組"""
    
    def __init__(
        self,
        input_size: int,
        hidden_unit: List[int],
        batch_norm: bool = False,
        activation: str = "relu",
        sigmoid: bool = False,
        dropout: Optional[float] = None,
        dice_dim: Optional[int] = None,
    ):
        super().__init__()
        assert len(hidden_unit) >= 1
        self.sigmoid = sigmoid
        
        layers = []
        layers.append(nn.Linear(input_size, hidden_unit[0]))
        
        for i, _ in enumerate(hidden_unit[:-1]):
            if batch_norm:
                layers.append(nn.BatchNorm1d(hidden_unit[i]))
            
            act = activation.lower()
            if act == "relu":
                layers.append(nn.ReLU(inplace=True))
            elif act == "tanh":
                layers.append(nn.Tanh())
            elif act == "leakyrelu":
                layers.append(nn.LeakyReLU())
            elif act == "dice":
                assert dice_dim is not None
                layers.append(Dice(hidden_unit[i], dim=dice_dim))
            else:
                raise NotImplementedError(f"Unknown activation: {activation}")
            
            if dropout is not None:
                layers.append(nn.Dropout(dropout))
            
            layers.append(nn.Linear(hidden_unit[i], hidden_unit[i + 1]))
        
        self.fc = nn.Sequential(*layers)
        if self.sigmoid:
            self.output_layer = nn.Sigmoid()
        else:
            self.output_layer = None
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.fc(x)
        if self.output_layer is not None:
            x = self.output_layer(x)
        return x


class LocalActivationUnit(nn.Module):
    """DIN 中的區域性啟用單元，建模 target item 與歷史行為的細粒度互動"""
    
    def __init__(self, hidden_unit: List[int] = None, embedding_dim: int = 4, batch_norm: bool = False):
        super().__init__()
        if hidden_unit is None:
            hidden_unit = [80, 40]
        self.fc1 = FullyConnectedLayer(
            input_size=4 * embedding_dim,
            hidden_unit=hidden_unit,
            batch_norm=batch_norm,
            sigmoid=False,
            activation="dice",
            dice_dim=3,
        )
        self.fc2 = nn.Linear(hidden_unit[-1], 1)
    
    def forward(self, query: torch.Tensor, user_behavior: torch.Tensor) -> torch.Tensor:
        """
        query: [B, 1, D]
        user_behavior: [B, T, D]
        """
        user_behavior_len = user_behavior.size(1)
        queries = query.expand(-1, user_behavior_len, -1)  # [B,T,D]
        att_input = torch.cat(
            [queries, user_behavior, queries - user_behavior, queries * user_behavior],
            dim=-1,
        )  # [B,T,4D]
        att_out = self.fc1(att_input)
        att_score = self.fc2(att_out)  # [B,T,1]
        return att_score


class AttentionSequencePoolingLayer(nn.Module):
    """基於注意力的序列池化層"""
    
    def __init__(self, embedding_dim: int = 4):
        super().__init__()
        self.local_att = LocalActivationUnit(
            hidden_unit=[64, 16], embedding_dim=embedding_dim, batch_norm=False
        )
    
    def forward(
        self,
        query_ad: torch.Tensor,
        user_behavior: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        query_ad: [B,1,D]
        user_behavior: [B,T,D]
        mask: [B,T] ，為 True 的位置會被置 0
        return: [B,1,D]
        """
        att_score = self.local_att(query_ad, user_behavior)  # [B,T,1]
        att_score = torch.transpose(att_score, 1, 2)  # [B,1,T]
        
        if mask is not None:
            att_score = att_score.masked_fill(mask.unsqueeze(1), torch.tensor(0.0, device=att_score.device))
        
        output = torch.matmul(att_score, user_behavior)  # [B,1,D]
        return output


class DINModel(nn.Module):
    """DIN 模型"""
    
    def __init__(
        self,
        n_users: int,
        n_items: int,
        embedding_dim: int = 192,
        hidden_units: List[int] = None,
        drop: float = 0.2,
    ):
        super().__init__()
        if hidden_units is None:
            hidden_units = [200, 80]
        
        self.n_users = n_users
        self.n_items = n_items
        self.embedding_dim = embedding_dim
        
        # 將總 embedding_dim 分成 3 份（user, item, history）
        self.emb_dim = embedding_dim // 3
        
        # 使用者、目標物品、歷史物品的 embedding
        self.user_embedding = nn.Embedding(n_users, self.emb_dim)
        # item 以 0 作為 padding id，所以尺寸為 n_items + 1
        self.item_embedding = nn.Embedding(n_items + 1, self.emb_dim, padding_idx=0)
        self.history_embedding = nn.Embedding(n_items + 1, self.emb_dim, padding_idx=0)
        
        # 注意力池化層
        self.attn = AttentionSequencePoolingLayer(embedding_dim=self.emb_dim)
        
        # 全連線層（Dice 啟用）
        self.fc_layer = FullyConnectedLayer(
            input_size=self.emb_dim * 3,
            hidden_unit=hidden_units + [1],
            batch_norm=False,
            sigmoid=True,  # 輸出機率值（0-1之間）
            activation="dice",
            dropout=drop,
            dice_dim=2,
        )
        
        self._init_weights()
    
    def _init_weights(self):
        """初始化權重"""
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_normal_(m.weight.data)
                if m.bias is not None:
                    nn.init.zeros_(m.bias.data)
            elif isinstance(m, nn.Embedding):
                nn.init.xavier_normal_(m.weight.data)
            elif isinstance(m, nn.Parameter):
                nn.init.xavier_normal_(m)
    
    def forward(self, user_ids: torch.Tensor, item_ids: torch.Tensor, histories: torch.Tensor):
        """
        Args:
            user_ids: [B]
            item_ids: [B]
            histories: [B, L]  歷史物品 id 序列，0 為 padding
        Returns:
            probs: [B]，經過 sigmoid 的機率值（0-1之間）
        """
        # 基礎 embedding
        user_emb = self.user_embedding(user_ids)  # [B, D]
        item_emb = self.item_embedding(item_ids)  # [B, D]
        
        # 歷史物品嵌入 [B, L, D]
        hist_emb = self.history_embedding(histories)
        
        # mask：歷史為 0 的位置視為 padding
        mask = histories.eq(0)  # [B,L]
        
        # 注意力池化
        browse_atten = self.attn(item_emb.unsqueeze(1), hist_emb, mask=mask)  # [B,1,D]
        
        # 拼接特徵 [item_emb, att_hist, user_emb]
        concat_feature = torch.cat(
            [item_emb, browse_atten.squeeze(1), user_emb],
            dim=-1,
        )  # [B, 3D]
        
        # 返回機率值（經過sigmoid）
        out = self.fc_layer(concat_feature).squeeze(-1)
        return out


class DINDataset(Dataset):
    """DIN 訓練資料集"""
    
    def __init__(
        self,
        user_ids: np.ndarray,
        item_ids: np.ndarray,
        labels: np.ndarray,
        histories: np.ndarray,
    ):
        assert (
            user_ids.shape[0]
            == item_ids.shape[0]
            == labels.shape[0]
            == histories.shape[0]
        )
        self.user_ids = user_ids.astype("int64")
        self.item_ids = item_ids.astype("int64")
        self.labels = labels.astype("float32")
        self.histories = histories.astype("int64")
    
    def __len__(self):
        return self.user_ids.shape[0]
    
    def __getitem__(self, idx):
        return (
            self.user_ids[idx],
            self.item_ids[idx],
            self.labels[idx],
            self.histories[idx],
        )


# ========== DIN Recommender ==========

class DINRecommender(RecommenderAlgorithm):
    """
    DIN (Deep Interest Network) 推薦演算法
    
    使用注意力機制建模使用者歷史行為與目標物品的互動
    """
    
    def __init__(self, config: DINConfig = DINConfig()):
        """
        初始化DIN推薦器
        
        Args:
            config: DIN演算法配置
        """
        self.config = config
        self.model: Optional[DINModel] = None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        
        # ID對映
        self.user_index_map: Optional[Dict[int, int]] = None
        self.item_index_map: Optional[Dict[int, int]] = None
        self.index_user_map: Optional[Dict[int, int]] = None
        self.index_item_map: Optional[Dict[int, int]] = None
        self.n_users: int = 0
        self.n_items: int = 0
        
        # 使用者歷史序列（儲存每個使用者的正反饋歷史，用 item index 表示）
        self.user_history: Dict[int, List[int]] = {}
        
        # 熱門物品（用於冷啟動）
        self._popular_items: List[Tuple[int, float]] = []
        
        get_logger().info(
            f"DINRecommender 初始化: embedding_dim={config.embedding_dim}, "
            f"hidden_units={config.hidden_units}, max_history_len={config.max_history_len}, "
            f"device={self.device}"
        )
    
    def fit(self, data: RatingMatrix) -> None:
        """
        訓練DIN模型
        
        Args:
            data: 評分矩陣
        """
        get_logger().info(
            f"開始訓練 DIN 模型: {data.get_user_count()} 使用者, "
            f"{data.get_item_count()} 物品, {data.get_rating_count()} 評分"
        )
        
        # 1. 構建ID對映
        self.user_index_map = data.user_map.copy()
        self.item_index_map = {iid: idx + 1 for idx, iid in enumerate(sorted(data.item_map.keys()))}  # item從1開始，0是padding
        self.index_user_map = {idx: uid for uid, idx in self.user_index_map.items()}
        self.index_item_map = {idx: iid for iid, idx in self.item_index_map.items()}
        
        self.n_users = len(self.user_index_map)
        self.n_items = len(self.item_index_map)
        
        # 2. 構建使用者歷史序列（使用rating >= 3.0作為正反饋）
        # 假設資料順序可以作為時間順序的近似
        self._build_user_sequences(data)
        
        # 3. 構建訓練資料集
        train_dataset = self._build_train_dataset(data)
        train_loader = DataLoader(
            train_dataset, batch_size=self.config.batch_size, shuffle=True
        )
        
        # 4. 建立模型
        self.model = DINModel(
            n_users=self.n_users,
            n_items=self.n_items,
            embedding_dim=self.config.embedding_dim,
            hidden_units=self.config.hidden_units,
            drop=self.config.drop,
        ).to(self.device)
        
        # 5. 最佳化器和損失函式
        optimizer = optim.Adam(self.model.parameters(), lr=self.config.learning_rate, weight_decay=1e-3)
        criterion = nn.BCELoss()
        
        # 6. 訓練迴圈
        self.model.train()
        iters_per_epoch = len(train_loader)
        warmup_steps = 200
        init_lr = self.config.learning_rate
        min_lr = 8e-5
        warmup_start_lr = 1e-5
        
        for epoch in range(self.config.n_epochs):
            total_loss = 0.0
            n_batches = 0
            
            for batch_idx, batch in enumerate(train_loader):
                # 學習率排程
                cur_step = epoch * iters_per_epoch + batch_idx
                if cur_step < warmup_steps:
                    lr = warmup_start_lr + (init_lr - warmup_start_lr) * cur_step / max(warmup_steps, 1)
                else:
                    total_steps = self.config.n_epochs * iters_per_epoch
                    progress = (cur_step - warmup_steps) / max(total_steps - warmup_steps, 1)
                    lr = (init_lr - min_lr) * 0.5 * (1.0 + math.cos(math.pi * progress)) + min_lr
                
                for param_group in optimizer.param_groups:
                    param_group['lr'] = lr
                
                user_ids, item_ids, labels, histories = batch
                user_ids = user_ids.to(self.device)
                item_ids = item_ids.to(self.device)
                labels = labels.to(self.device)
                histories = histories.to(self.device)
                
                # 前向傳播
                probs = self.model(user_ids, item_ids, histories)
                loss = criterion(probs, labels)
                
                # 反向傳播
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                
                total_loss += loss.item()
                n_batches += 1
            
            avg_loss = total_loss / max(n_batches, 1)
            if (epoch + 1) % 10 == 0 or epoch == 0:
                get_logger().debug(
                    f"Epoch {epoch + 1}/{self.config.n_epochs}, Loss: {avg_loss:.4f}"
                )
        
        self.model.eval()
        
        # 7. 計算熱門物品
        self._compute_popular_items(data)
        
        get_logger().info("DIN 模型訓練完成")
    
    def _build_user_sequences(self, data: RatingMatrix) -> None:
        """從評分矩陣構建使用者歷史序列（使用rating >= 3.0作為正反饋）"""
        self.user_history = {}
        user_sequences = defaultdict(list)
        
        # 按使用者分組，收集正反饋（rating >= 3.0）
        for user_id, item_id, rating in zip(data.user_ids, data.item_ids, data.ratings):
            if rating >= 3.0:  # 使用rating >= 3.0作為正反饋閾值
                if user_id in self.user_index_map and item_id in self.item_index_map:
                    item_idx = self.item_index_map[item_id]
                    user_sequences[user_id].append(item_idx)
        
        # 截斷到max_history_len（保留最近的N個）
        for user_id, seq in user_sequences.items():
            if len(seq) > self.config.max_history_len:
                self.user_history[user_id] = seq[-self.config.max_history_len:]
            else:
                self.user_history[user_id] = seq
    
    def _build_train_dataset(self, data: RatingMatrix) -> DINDataset:
        """構建訓練資料集"""
        user_list = []
        item_list = []
        label_list = []
        history_list = []
        
        # 為每個使用者維護一個正反饋歷史
        user_current_history = {uid: hist.copy() for uid, hist in self.user_history.items()}
        
        # 遍歷所有互動，構建訓練樣本
        for user_id, item_id, rating in zip(data.user_ids, data.item_ids, data.ratings):
            if user_id not in self.user_index_map or item_id not in self.item_index_map:
                continue
            
            user_idx = self.user_index_map[user_id]
            item_idx = self.item_index_map[item_id]
            
            # 獲取當前歷史（在新增當前互動之前）
            history = user_current_history.get(user_id, [])
            
            # 構建歷史序列（padding到max_history_len）
            if len(history) == 0:
                hist_indices = [0] * self.config.max_history_len
            else:
                hist_trunc = history[-self.config.max_history_len:]
                hist_indices = [0] * (self.config.max_history_len - len(hist_trunc)) + hist_trunc
            
            user_list.append(user_idx)
            item_list.append(item_idx)
            label_list.append(1.0 if rating >= 3.0 else 0.0)  # 使用rating >= 3.0作為正樣本
            history_list.append(hist_indices)
            
            # 如果當前是正反饋，則加入歷史
            if rating >= 3.0:
                if user_id not in user_current_history:
                    user_current_history[user_id] = []
                user_current_history[user_id].append(item_idx)
                # 保持歷史長度不超過max_history_len
                if len(user_current_history[user_id]) > self.config.max_history_len:
                    user_current_history[user_id] = user_current_history[user_id][-self.config.max_history_len:]
        
        user_arr = np.array(user_list, dtype="int64")
        item_arr = np.array(item_list, dtype="int64")
        label_arr = np.array(label_list, dtype="float32")
        hist_arr = np.array(history_list, dtype="int64")
        
        return DINDataset(user_arr, item_arr, label_arr, hist_arr)
    
    def _get_user_sequence(self, user_id: int) -> np.ndarray:
        """獲取使用者的歷史序列（padding到max_history_len）"""
        history = self.user_history.get(user_id, [])
        
        if len(history) == 0:
            return np.zeros((self.config.max_history_len,), dtype="int64")
        
        hist_trunc = history[-self.config.max_history_len:]
        hist_indices = [0] * (self.config.max_history_len - len(hist_trunc)) + hist_trunc
        return np.array(hist_indices, dtype="int64")
    
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
        if user_id not in self.user_index_map:
            return 2.5
        if item_id not in self.item_index_map:
            return 2.5
        
        user_idx = self.user_index_map[user_id]
        item_idx = self.item_index_map[item_id]
        hist_np = self._get_user_sequence(user_id)
        
        user_tensor = torch.tensor([user_idx], dtype=torch.long, device=self.device)
        item_tensor = torch.tensor([item_idx], dtype=torch.long, device=self.device)
        hist_tensor = torch.tensor(hist_np.reshape(1, -1), dtype=torch.long, device=self.device)
        
        self.model.eval()
        with torch.no_grad():
            # 模型輸出機率值（0-1），對映到1-5評分
            prob = self.model(user_tensor, item_tensor, hist_tensor).item()
            rating = 1.0 + prob * 4.0  # 對映到 [1, 5]
        
        return max(1.0, min(5.0, rating))
    
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
        if user_id not in self.user_index_map:
            return [
                (item_id, score)
                for item_id, score in self._popular_items
                if item_id not in exclude_ids
            ][:n]
        
        user_idx = self.user_index_map[user_id]
        hist_np = self._get_user_sequence(user_id)
        
        user_tensor = torch.tensor([user_idx], dtype=torch.long, device=self.device)
        hist_tensor = torch.tensor(hist_np.reshape(1, -1), dtype=torch.long, device=self.device)
        
        # 對所有物品打分
        all_item_indices = np.arange(1, self.n_items + 1, dtype="int64")
        item_tensor = torch.tensor(all_item_indices, dtype=torch.long, device=self.device)
        user_batch = user_tensor.repeat(item_tensor.size(0))
        hist_batch = hist_tensor.repeat(item_tensor.size(0), 1)
        
        self.model.eval()
        with torch.no_grad():
            # 模型輸出機率值（0-1），對映到1-5評分
            probs = self.model(user_batch, item_tensor, hist_batch).cpu().numpy()
            scores = 1.0 + probs * 4.0  # 對映到 [1, 5]
        
        recommendations = [
            (self.index_item_map[i_idx], float(score))
            for i_idx, score in zip(all_item_indices, scores)
            if self.index_item_map[i_idx] not in exclude_ids
        ]
        
        # 按評分降序排序
        recommendations.sort(key=lambda x: x[1], reverse=True)
        return recommendations[:n]
    
    def save(self, path: str) -> None:
        """儲存模型到檔案"""
        if self.model is None:
            raise RuntimeError("模型尚未訓練,無法儲存")
        
        checkpoint = {
            'model_state_dict': self.model.state_dict(),
            'user_index_map': self.user_index_map,
            'item_index_map': self.item_index_map,
            'index_user_map': self.index_user_map,
            'index_item_map': self.index_item_map,
            'user_history': self.user_history,
            'popular_items': self._popular_items,
            'config': self.config,
            'n_users': self.n_users,
            'n_items': self.n_items,
        }
        
        with open(path, 'wb') as f:
            pickle.dump(checkpoint, f)
        
        get_logger().info(f"DIN 模型已儲存到 {path}")
    
    def load(self, path: str) -> None:
        """從檔案載入模型"""
        with open(path, 'rb') as f:
            checkpoint = pickle.load(f)
        
        self.config = checkpoint['config']
        self.user_index_map = checkpoint['user_index_map']
        self.item_index_map = checkpoint['item_index_map']
        self.index_user_map = checkpoint['index_user_map']
        self.index_item_map = checkpoint['index_item_map']
        self.user_history = checkpoint['user_history']
        self._popular_items = checkpoint['popular_items']
        self.n_users = checkpoint['n_users']
        self.n_items = checkpoint['n_items']
        
        # 重建模型
        self.model = DINModel(
            n_users=self.n_users,
            n_items=self.n_items,
            embedding_dim=self.config.embedding_dim,
            hidden_units=self.config.hidden_units,
            drop=self.config.drop,
        ).to(self.device)
        
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()
        
        get_logger().info(f"DIN 模型已從 {path} 載入")
    
    def _compute_popular_items(self, data: RatingMatrix) -> None:
        """計算熱門物品（用於冷啟動）"""
        item_ratings: Dict[int, List[float]] = {}
        
        for item_id, rating in zip(data.item_ids, data.ratings):
            if item_id not in item_ratings:
                item_ratings[item_id] = []
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

