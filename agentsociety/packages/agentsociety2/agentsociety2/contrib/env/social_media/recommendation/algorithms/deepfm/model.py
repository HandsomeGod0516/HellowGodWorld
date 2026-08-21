"""
DeepFM (Deep Factorization Machine) 推薦演算法實現

"""

import pickle
from typing import List, Tuple, Set, Dict, Optional
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

from agentsociety2.logger import get_logger
from ..core import RecommenderAlgorithm, RatingMatrix
from .config import DeepFMConfig


class DeepFMModel(nn.Module):
    """Deep Factorization Machine 模型"""
    
    def __init__(self, n_users: int, n_items: int, embedding_dim: int = 16, 
                 deep_layers: List[int] = None, drop_rate: float = 0.5):
        """
        初始化DeepFM模型
        
        Args:
            n_users: 使用者數量
            n_items: 物品數量
            embedding_dim: 嵌入維度
            deep_layers: 深度網路層神經元數量列表
            drop_rate: Dropout率
        """
        super(DeepFMModel, self).__init__()
        
        if deep_layers is None:
            deep_layers = [64, 32]
        
        self.n_users = n_users
        self.n_items = n_items
        self.embedding_dim = embedding_dim
        
        # 使用者和物品嵌入層
        self.user_embedding = nn.Embedding(n_users, embedding_dim)
        self.item_embedding = nn.Embedding(n_items, embedding_dim)
        
        # FM部分：一階線性項
        self.user_bias = nn.Embedding(n_users, 1)
        self.item_bias = nn.Embedding(n_items, 1)
        self.global_bias = nn.Parameter(torch.zeros(1))
        
        # Deep部分
        deep_input_dim = embedding_dim * 2
        deep_layers_list = []
        for i, layer_size in enumerate(deep_layers):
            if i == 0:
                deep_layers_list.append(nn.Linear(deep_input_dim, layer_size))
            else:
                deep_layers_list.append(nn.Linear(deep_layers[i-1], layer_size))
            deep_layers_list.append(nn.BatchNorm1d(layer_size))
            deep_layers_list.append(nn.ReLU())
            deep_layers_list.append(nn.Dropout(drop_rate))
        
        self.deep_network = nn.Sequential(*deep_layers_list)
        
        # 輸出層
        # 最終的輸出層輸入維度：線性項(1) + FM二階項(1) + Deep輸出(deep_layers[-1])
        final_input_dim = 2 + deep_layers[-1]
        self.output_layer = nn.Linear(final_input_dim, 1)
        
        # 初始化權重
        self._init_weights()
    
    def _init_weights(self):
        """初始化模型權重"""
        nn.init.normal_(self.user_embedding.weight, std=0.01)
        nn.init.normal_(self.item_embedding.weight, std=0.01)
        nn.init.normal_(self.user_bias.weight, std=0.01)
        nn.init.normal_(self.item_bias.weight, std=0.01)
        nn.init.zeros_(self.global_bias)
        
        for layer in self.deep_network:
            if isinstance(layer, nn.Linear):
                nn.init.xavier_uniform_(layer.weight)
                nn.init.zeros_(layer.bias)
        
        nn.init.xavier_uniform_(self.output_layer.weight)
        nn.init.zeros_(self.output_layer.bias)
    
    def forward(self, user_ids: torch.Tensor, item_ids: torch.Tensor) -> torch.Tensor:
        """
        前向傳播
        
        Args:
            user_ids: 使用者ID張量
            item_ids: 物品ID張量
            
        Returns:
            預測評分
        """
        # 獲取嵌入向量
        user_emb = self.user_embedding(user_ids)
        item_emb = self.item_embedding(item_ids)
        
        # FM一階線性項
        user_bias = self.user_bias(user_ids)
        item_bias = self.item_bias(item_ids)
        linear_term = user_bias + item_bias + self.global_bias
        
        # FM二階互動項：計算使用者嵌入和物品嵌入的內積
        fm_second_order = torch.sum(user_emb * item_emb, dim=1, keepdim=True)  # [batch_size, 1]
        
        # Deep部分：使用拼接後的嵌入向量
        concat_emb = torch.cat([user_emb, item_emb], dim=1)  # [batch_size, embedding_dim * 2]
        deep_output = self.deep_network(concat_emb)
        
        # 合併所有特徵
        concat_features = torch.cat([linear_term, fm_second_order, deep_output], dim=1)
        output = self.output_layer(concat_features)
        
        # 使用sigmoid將輸出對映到1-5的評分範圍
        output = torch.sigmoid(output) * 4 + 1  # 對映到 [1, 5]
        
        return output.squeeze()


class DeepFMRecommender(RecommenderAlgorithm):
    """
    DeepFM 推薦演算法
    """
    
    def __init__(self, config: DeepFMConfig = DeepFMConfig()):
        """
        初始化DeepFM推薦系統
        
        Args:
            config: DeepFM演算法配置
        """
        self.config = config
        self.model: Optional[DeepFMModel] = None
        
        # 設定裝置
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        
        self.user_index_map: Optional[Dict[int, int]] = None
        self.item_index_map: Optional[Dict[int, int]] = None
        self.index_user_map: Optional[Dict[int, int]] = None
        self.index_item_map: Optional[Dict[int, int]] = None
        self.n_users: int = 0
        self.n_items: int = 0
        
        # 熱門物品（用於冷啟動）
        self._popular_items: List[Tuple[int, float]] = []
        
        get_logger().info(
            f"DeepFMRecommender 初始化: embedding_dim={config.embedding_dim}, "
            f"deep_layers={config.deep_layers}, n_epochs={config.n_epochs}, "
            f"device={self.device}"
        )
    
    def fit(self, data: RatingMatrix) -> None:
        """
        訓練DeepFM模型
        
        Args:
            data: 評分矩陣
        """
        get_logger().info(
            f"開始訓練 DeepFM 模型: {data.get_user_count()} 使用者, "
            f"{data.get_item_count()} 物品, {data.get_rating_count()} 評分"
        )
        
        # 儲存對映
        self.user_index_map = data.user_map.copy()
        self.item_index_map = data.item_map.copy()
        self.index_user_map = {idx: uid for uid, idx in self.user_index_map.items()}
        self.index_item_map = {idx: iid for iid, idx in self.item_index_map.items()}
        
        self.n_users = len(self.user_index_map)
        self.n_items = len(self.item_index_map)
        
        # 建立模型
        self.model = DeepFMModel(
            n_users=self.n_users,
            n_items=self.n_items,
            embedding_dim=self.config.embedding_dim,
            deep_layers=self.config.deep_layers,
            drop_rate=self.config.drop_rate
        ).to(self.device)
        
        # 準備訓練資料
        train_data = []
        for user_id, item_id, rating in zip(data.user_ids, data.item_ids, data.ratings):
            user_idx = self.user_index_map[user_id]
            item_idx = self.item_index_map[item_id]
            train_data.append((user_idx, item_idx, rating))
        
        train_data = np.array(train_data, dtype=np.float32)
        get_logger().info(f"訓練樣本數: {len(train_data)}")
        
        # 最佳化器和損失函式
        optimizer = optim.Adam(self.model.parameters(), lr=self.config.learning_rate, weight_decay=0.0001)
        criterion = nn.MSELoss()
        
        # 訓練模型
        get_logger().info("正在訓練模型...")
        self.model.train()
        
        for epoch in range(self.config.n_epochs):
            # 隨機打亂資料
            np.random.shuffle(train_data)
            
            total_loss = 0
            n_batches = 0
            
            # 批次訓練
            for batch_start in range(0, len(train_data), self.config.batch_size):
                batch_end = min(batch_start + self.config.batch_size, len(train_data))
                batch_data = train_data[batch_start:batch_end]
                
                user_ids_batch = torch.LongTensor(batch_data[:, 0]).to(self.device)
                item_ids_batch = torch.LongTensor(batch_data[:, 1]).to(self.device)
                ratings_batch = torch.FloatTensor(batch_data[:, 2]).to(self.device)
                
                # 前向傳播
                predictions = self.model(user_ids_batch, item_ids_batch)
                loss = criterion(predictions, ratings_batch)
                
                # 反向傳播
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                
                total_loss += loss.item()
                n_batches += 1
            
            # 每10個epoch列印一次損失
            if (epoch + 1) % 10 == 0 or epoch == 0:
                avg_loss = total_loss / n_batches if n_batches > 0 else 0
                rmse = np.sqrt(avg_loss)
                get_logger().debug(
                    f"Epoch {epoch + 1}/{self.config.n_epochs}, Loss: {avg_loss:.4f}, RMSE: {rmse:.4f}"
                )
        
        self.model.eval()
        
        # 計算熱門物品（用於冷啟動）
        self._compute_popular_items(data)
        
        get_logger().info("DeepFM 模型訓練完成")
    
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
            return 2.5  # 新使用者預設評分
        if item_id not in self.item_index_map:
            return 2.5  # 新物品預設評分
        
        user_idx = self.user_index_map[user_id]
        item_idx = self.item_index_map[item_id]
        
        self.model.eval()
        with torch.no_grad():
            user_tensor = torch.LongTensor([user_idx]).to(self.device)
            item_tensor = torch.LongTensor([item_idx]).to(self.device)
            prediction = self.model(user_tensor, item_tensor)
            return float(prediction.cpu().item())
    
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
        
        # 計算使用者對所有物品的預測評分
        self.model.eval()
        recommendations = []
        
        with torch.no_grad():
            user_tensor = torch.LongTensor([user_idx] * self.n_items).to(self.device)
            item_tensor = torch.LongTensor(list(range(self.n_items))).to(self.device)
            predictions = self.model(user_tensor, item_tensor)
            
            for item_idx, score in enumerate(predictions.cpu().numpy()):
                item_id = self.index_item_map[item_idx]
                if item_id not in exclude_ids:
                    recommendations.append((item_id, float(score)))
        
        # 按評分降序排序，返回Top-N
        recommendations.sort(key=lambda x: x[1], reverse=True)
        return recommendations[:n]
    
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
            'user_index_map': self.user_index_map,
            'item_index_map': self.item_index_map,
            'index_user_map': self.index_user_map,
            'index_item_map': self.index_item_map,
            'popular_items': self._popular_items,
            'config': self.config,
            'n_users': self.n_users,
            'n_items': self.n_items,
        }
        
        with open(path, 'wb') as f:
            pickle.dump(checkpoint, f)
        
        get_logger().info(f"DeepFM 模型已儲存到 {path}")
    
    def load(self, path: str) -> None:
        """
        從檔案載入模型
        
        Args:
            path: 模型檔案路徑
        """
        with open(path, 'rb') as f:
            checkpoint = pickle.load(f)
        
        self.config = checkpoint['config']
        self.user_index_map = checkpoint['user_index_map']
        self.item_index_map = checkpoint['item_index_map']
        self.index_user_map = checkpoint['index_user_map']
        self.index_item_map = checkpoint['index_item_map']
        self._popular_items = checkpoint['popular_items']
        self.n_users = checkpoint['n_users']
        self.n_items = checkpoint['n_items']
        
        # 重建模型
        self.model = DeepFMModel(
            n_users=self.n_users,
            n_items=self.n_items,
            embedding_dim=self.config.embedding_dim,
            deep_layers=self.config.deep_layers,
            drop_rate=self.config.drop_rate
        ).to(self.device)
        
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()
        
        get_logger().info(f"DeepFM 模型已從 {path} 載入")
    
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

