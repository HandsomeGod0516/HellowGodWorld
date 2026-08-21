"""
LightGCN 推薦演算法實現

基於 PyTorch 的輕量級圖卷積網路演算法
"""

import pickle
from typing import List, Tuple, Set, Dict, Optional
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset

from agentsociety2.logger import get_logger
from ..core import RecommenderAlgorithm, RatingMatrix
from .config import LightGCNConfig


class LightGCNModel(nn.Module):
    """
    簡化版 LightGCN：
    - 使用者、物品各一套 embedding
    - 使用歸一化後的使用者-物品二分圖鄰接矩陣進行多層傳播
    - 終端使用者/物品表示為各層 embedding 的平均
    """
    
    def __init__(
        self,
        n_users: int,
        n_items: int,
        embedding_dim: int = 64,
        n_layers: int = 2,
    ):
        super().__init__()
        self.n_users = n_users
        self.n_items = n_items
        self.embedding_dim = embedding_dim
        self.n_layers = n_layers
        
        self.embedding_user = nn.Embedding(n_users, embedding_dim)
        self.embedding_item = nn.Embedding(n_items, embedding_dim)
        
        self._init_weights()
        
        # 圖結構（訓練時由 Recommender 透過 set_graph 注入）
        self.Graph = None  # torch.sparse.FloatTensor
    
    def _init_weights(self):
        """初始化權重"""
        nn.init.normal_(self.embedding_user.weight, std=0.1)
        nn.init.normal_(self.embedding_item.weight, std=0.1)
    
    def set_graph(self, graph: torch.Tensor):
        """
        設定歸一化後的稀疏鄰接矩陣 Graph（形狀為 [n_users+n_items, n_users+n_items]）
        """
        self.Graph = graph
    
    def propagate(self) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        進行 LightGCN 的多層傳播，返回最終的 (user_emb, item_emb)
        """
        assert self.Graph is not None, "Graph 未設定，請先呼叫 set_graph"
        
        users_emb = self.embedding_user.weight
        items_emb = self.embedding_item.weight
        all_emb = torch.cat([users_emb, items_emb], dim=0)  # [N, D]
        
        embs = [all_emb]
        g = self.Graph
        
        for _ in range(self.n_layers):
            all_emb = torch.sparse.mm(g, all_emb)
            embs.append(all_emb)
        
        embs = torch.stack(embs, dim=1)  # [N, K+1, D]
        out = torch.mean(embs, dim=1)  # [N, D]
        
        out_users, out_items = torch.split(out, [self.n_users, self.n_items], dim=0)
        return out_users, out_items
    
    def forward(self, users: torch.Tensor, items: torch.Tensor) -> torch.Tensor:
        """
        輸入使用者索引和物品索引，輸出匹配得分（未過 sigmoid）
        """
        all_users, all_items = self.propagate()
        users_emb = all_users[users.long()]
        items_emb = all_items[items.long()]
        inner_pro = torch.mul(users_emb, items_emb).sum(dim=1)
        return inner_pro


class LightGCNRecommender(RecommenderAlgorithm):
    """
    LightGCN 推薦演算法
    
    基於圖卷積網路的推薦演算法，使用使用者-物品二分圖進行資訊傳播
    """
    
    def __init__(self, config: LightGCNConfig = LightGCNConfig()):
        """
        初始化LightGCN推薦器
        
        Args:
            config: LightGCN演算法配置
        """
        self.config = config
        self.model: Optional[LightGCNModel] = None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        
        # ID對映
        self.user_index_map: Optional[Dict[int, int]] = None
        self.item_index_map: Optional[Dict[int, int]] = None
        self.index_user_map: Optional[Dict[int, int]] = None
        self.index_item_map: Optional[Dict[int, int]] = None
        self.n_users: int = 0
        self.n_items: int = 0
        
        # 熱門物品（用於冷啟動）
        self._popular_items: List[Tuple[int, float]] = []
        
        get_logger().info(
            f"LightGCNRecommender 初始化: embedding_dim={config.embedding_dim}, "
            f"n_layers={config.n_layers}, n_epochs={config.n_epochs}, device={self.device}"
        )
    
    def fit(self, data: RatingMatrix) -> None:
        """
        訓練LightGCN模型
        
        Args:
            data: 評分矩陣
        """
        get_logger().info(
            f"開始訓練 LightGCN 模型: {data.get_user_count()} 使用者, "
            f"{data.get_item_count()} 物品, {data.get_rating_count()} 評分"
        )
        
        # 1. 構建ID對映
        self.user_index_map = data.user_map.copy()
        self.item_index_map = data.item_map.copy()
        self.index_user_map = {idx: uid for uid, idx in self.user_index_map.items()}
        self.index_item_map = {idx: iid for iid, idx in self.item_index_map.items()}
        
        self.n_users = len(self.user_index_map)
        self.n_items = len(self.item_index_map)
        
        # 2. 構建圖結構
        graph = self._build_graph(data)
        
        # 3. 建立模型並設定圖
        self.model = LightGCNModel(
            n_users=self.n_users,
            n_items=self.n_items,
            embedding_dim=self.config.embedding_dim,
            n_layers=self.config.n_layers,
        ).to(self.device)
        self.model.set_graph(graph)
        
        # 4. 準備訓練資料（使用rating >= 3.0作為正樣本）
        train_users = []
        train_items = []
        train_labels = []
        
        for user_id, item_id, rating in zip(data.user_ids, data.item_ids, data.ratings):
            if user_id in self.user_index_map and item_id in self.item_index_map:
                train_users.append(self.user_index_map[user_id])
                train_items.append(self.item_index_map[item_id])
                train_labels.append(1.0 if rating >= 3.0 else 0.0)  # 使用rating >= 3.0作為正樣本
        
        train_dataset = TensorDataset(
            torch.tensor(train_users, dtype=torch.long),
            torch.tensor(train_items, dtype=torch.long),
            torch.tensor(train_labels, dtype=torch.float32)
        )
        train_loader = DataLoader(train_dataset, batch_size=self.config.batch_size, shuffle=True)
        
        # 5. 最佳化器和損失函式
        optimizer = optim.Adam(self.model.parameters(), lr=self.config.learning_rate, weight_decay=self.config.reg)
        criterion = nn.BCEWithLogitsLoss()
        
        # 6. 訓練迴圈
        self.model.train()
        for epoch in range(self.config.n_epochs):
            total_loss = 0.0
            for batch_users, batch_items, batch_labels in train_loader:
                batch_users = batch_users.to(self.device)
                batch_items = batch_items.to(self.device)
                batch_labels = batch_labels.to(self.device)
                
                optimizer.zero_grad()
                
                # 計算預測logits（內積）
                pred_logits = self.model.forward(batch_users, batch_items)
                
                # 計算損失（BCEWithLogitsLoss包含sigmoid，直接使用logits）
                loss = criterion(pred_logits, batch_labels)
                
                loss.backward()
                optimizer.step()
                
                total_loss += loss.item()
            
            avg_loss = total_loss / len(train_loader)
            if (epoch + 1) % 10 == 0 or epoch == 0:
                get_logger().debug(
                    f"Epoch {epoch + 1}/{self.config.n_epochs}, Loss: {avg_loss:.4f}"
                )
        
        self.model.eval()
        
        # 7. 計算熱門物品
        self._compute_popular_items(data)
        
        get_logger().info("LightGCN 模型訓練完成")
    
    def _build_graph(self, data: RatingMatrix) -> torch.Tensor:
        """
        從評分矩陣構建 LightGCN 的歸一化鄰接矩陣（稀疏）
        
        使用rating >= 3.0作為正反饋構建圖
        """
        # 所有正樣本互動（rating >= 3.0）
        user_indices = []
        item_indices = []
        
        for user_id, item_id, rating in zip(data.user_ids, data.item_ids, data.ratings):
            if rating >= 3.0:  # 使用rating >= 3.0作為正反饋
                if user_id in self.user_index_map and item_id in self.item_index_map:
                    user_indices.append(self.user_index_map[user_id])
                    item_indices.append(self.item_index_map[item_id])
        
        user_indices = np.array(user_indices)
        item_indices = np.array(item_indices)
        
        num_nodes = self.n_users + self.n_items
        
        # 構建無向圖的邊：user -> item', item' -> user
        rows = np.concatenate([user_indices, item_indices + self.n_users])
        cols = np.concatenate([item_indices + self.n_users, user_indices])
        
        indices = torch.tensor(
            np.vstack([rows, cols]), dtype=torch.long, device=self.device
        )  # [2, E]
        
        # 計算度並進行 D^{-1/2} A D^{-1/2} 歸一化
        deg = torch.bincount(indices[0], minlength=num_nodes).float().to(self.device)
        deg_inv_sqrt = torch.pow(deg + 1e-8, -0.5).to(self.device)
        deg_inv_sqrt[torch.isinf(deg_inv_sqrt)] = 0.0
        
        values = (deg_inv_sqrt[indices[0]] * deg_inv_sqrt[indices[1]]).to(self.device)
        
        graph = torch.sparse_coo_tensor(
            indices,
            values,
            size=(num_nodes, num_nodes),
            device=self.device,
        )
        return graph.coalesce()
    
    def _get_final_embeddings(self) -> Tuple[torch.Tensor, torch.Tensor]:
        """獲取最終的使用者和物品嵌入"""
        assert self.model is not None
        self.model.eval()
        with torch.no_grad():
            user_emb, item_emb = self.model.propagate()
        return user_emb, item_emb
    
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
        
        u_idx = self.user_index_map[user_id]
        i_idx = self.item_index_map[item_id]
        
        user_emb, item_emb = self._get_final_embeddings()
        u_vec = user_emb[u_idx]
        i_vec = item_emb[i_idx]
        
        # 計算內積（logits），然後sigmoid並對映到1-5評分
        score = torch.dot(u_vec, i_vec).item()
        prob = torch.sigmoid(torch.tensor(score)).item()
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
        
        u_idx = self.user_index_map[user_id]
        user_emb, item_emb = self._get_final_embeddings()
        u_vec = user_emb[u_idx]  # [D]
        
        # 計算所有物品的分數
        scores = torch.matmul(item_emb, u_vec)  # [n_items]
        scores = torch.sigmoid(scores).cpu().numpy()  # sigmoid後對映到0-1
        ratings = 1.0 + scores * 4.0  # 對映到 [1, 5]
        
        recommendations = [
            (self.index_item_map[i_idx], float(rating))
            for i_idx, rating in enumerate(ratings)
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
            'popular_items': self._popular_items,
            'config': self.config,
            'n_users': self.n_users,
            'n_items': self.n_items,
        }
        
        with open(path, 'wb') as f:
            pickle.dump(checkpoint, f)
        
        get_logger().info(f"LightGCN 模型已儲存到 {path}")
    
    def load(self, path: str) -> None:
        """從檔案載入模型"""
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
        
        # 重建模型（需要重新構建圖，但這裡簡化處理，假設圖會在fit時重建）
        self.model = LightGCNModel(
            n_users=self.n_users,
            n_items=self.n_items,
            embedding_dim=self.config.embedding_dim,
            n_layers=self.config.n_layers,
        ).to(self.device)
        
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()
        
        get_logger().info(f"LightGCN 模型已從 {path} 載入")
        get_logger().warning("注意：載入的模型需要重新設定圖結構，建議重新訓練")
    
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

