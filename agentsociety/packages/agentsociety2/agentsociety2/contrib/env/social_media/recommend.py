import random
import math
from datetime import datetime
from typing import List, Dict, Optional, Set

from .models import Post


def _get_pretrained_algorithm(algorithm_name: str, model_path: Optional[str]):
    """
    根據演算法名和模型路徑載入預訓練推薦模型。
    預訓練方式：使用 recommendation 子模組訓練後 save(path)，此處 load(path) 即可接入。

    Args:
        algorithm_name: 如 "mf", "ncf", "lightgcn" 等，需與 recommendation.algorithms 中實現一致。
        model_path: 預訓練模型檔案路徑（如 .pkl 或 目錄）。

    Returns:
        已 load 的 RecommenderAlgorithm 例項，若 model_path 為空則返回 None。
    """
    if not model_path:
        return None
    algo = None
    if algorithm_name == "mf":
        from .recommendation.algorithms.mf import MFRecommender, MFConfig
        algo = MFRecommender(config=MFConfig())
    # 可在此擴充套件: "ncf" -> NCFRecommender, "lightgcn" -> LightGCNRecommender 等
    if algo is None:
        return None
    try:
        algo.load(model_path)
    except Exception:
        return None
    return algo


class RecommendationEngine:
    """
    推薦演算法引擎。支援規則演算法（時間序、熱度、Twitter 排序、隨機）與預訓練模型（如 MF）。
    預訓練模型：在 recommendation 子模組中訓練並 save(path)，構造時傳入 model_path 與 algorithm 即可接入。
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        recommendation_algorithm: str = "mf",
    ):
        """
        Args:
            model_path: 預訓練模型路徑。非空時載入對應演算法並可在 refresh_feed 中透過 algorithm="mf" 使用。
            recommendation_algorithm: 演算法名，如 "mf"。需與 recommendation.algorithms 中實現一致。
        """
        self._model_algorithm = _get_pretrained_algorithm(recommendation_algorithm, model_path)
        self._model_algorithm_name = recommendation_algorithm if self._model_algorithm else None

    def get_model_algorithm_name(self) -> Optional[str]:
        """若已載入預訓練模型，返回演算法名（如 'mf'），否則返回 None。"""
        return self._model_algorithm_name

    def model_recommend(
        self,
        posts: List[Post],
        user_id: int,
        limit: int = 20,
        exclude_post_ids: Optional[Set[int]] = None,
    ) -> List[Post]:
        """
        使用預訓練模型對候選帖子排序。未載入模型時回退為時間序。

        Args:
            posts: 候選帖子列表（item_id = post_id）
            user_id: 使用者 ID
            limit: 返回條數
            exclude_post_ids: 需要排除的 post_id 集合（如已讀）

        Returns:
            按模型打分排序的帖子列表
        """
        if not posts:
            return []
        exclude = set(exclude_post_ids or [])
        post_by_id = {p.post_id: p for p in posts}
        candidate_ids = [p.post_id for p in posts]

        if self._model_algorithm is None:
            return self.chronological(posts, user_id, limit)

        try:
            rec_list = self._model_algorithm.recommend(user_id, limit, exclude)
        except Exception:
            return self.chronological(posts, user_id, limit)

        out: List[Post] = []
        added_ids: Set[int] = set()
        for item_id, _score in rec_list:
            if item_id in post_by_id and item_id not in exclude and item_id not in added_ids:
                out.append(post_by_id[item_id])
                added_ids.add(item_id)
            if len(out) >= limit:
                break
        # 若模型返回不足或含大量未在候選中的 id，用候選集順序補足
        for pid in candidate_ids:
            if len(out) >= limit:
                break
            if pid not in added_ids:
                out.append(post_by_id[pid])
                added_ids.add(pid)
        return out[:limit]

    @staticmethod
    def chronological(
        posts: List[Post],
        user_id: int,
        limit: int = 20
    ) -> List[Post]:
        """
        時間序列排序演算法（基線）
        
        Args:
            posts: 所有候選帖子列表
            user_id: 當前使用者ID
            limit: 返回帖子數量
            
        Returns:
            按時間倒序排列的帖子列表
        """
        # 按建立時間降序排序
        sorted_posts = sorted(posts, key=lambda p: p.created_at, reverse=True)
        return sorted_posts[:limit]
    
    @staticmethod
    def reddit_hot(
        posts: List[Post],
        user_id: int,
        limit: int = 20
    ) -> List[Post]:
        """
        Reddit熱度演算法
        
        基於點贊數和時間衰減計算熱度分數。
        公式: score = sign * log10(|likes|) + (created_at_seconds - epoch) / 45000
        
        參考: https://medium.com/hacking-and-gonzo/how-reddit-ranking-algorithms-work-ef111e33d0d9
        
        Args:
            posts: 所有候選帖子列表
            user_id: 當前使用者ID
            limit: 返回帖子數量
            
        Returns:
            按熱度分數排序的帖子列表
        """
        def calculate_hot_score(post: Post) -> float:
            """
            計算帖子的Reddit熱度分數
            
            注意：這裡簡化了公式，只考慮點贊數
            """
            # 點贊數（沒有dislike，所以s = num_likes）
            s = post.likes_count
            
            # log10(|s|) with minimum of 1 to avoid log(0)
            order = math.log10(max(abs(s), 1))
            
            sign = 1 if s > 0 else 0 if s == 0 else -1
            
            # 時間部分
            epoch = datetime(1970, 1, 1)
            td = post.created_at - epoch
            epoch_seconds = td.days * 86400 + td.seconds + (td.microseconds / 1e6)
            
            # Reddit epoch offset (2005-12-08)
            reddit_epoch = 1134028003
            seconds = epoch_seconds - reddit_epoch
            
            # 最終分數
            score = sign * order + seconds / 45000
            
            return round(score, 7)
        
        # 計算所有帖子的熱度分數
        post_scores = [(post, calculate_hot_score(post)) for post in posts]
        
        # 按熱度分數降序排序
        post_scores.sort(key=lambda x: x[1], reverse=True)
        
        # 返回top N
        return [post for post, score in post_scores[:limit]]
    
    @staticmethod
    def twitter_ranking(
        posts: List[Post],
        user_id: int,
        limit: int = 20,
        follows: Dict[int, List[int]] = None,
        likes: Dict[int, List[int]] = None,
        weights: Dict[str, float] = None
    ) -> List[Post]:
        """
        Twitter排序演算法
        
        綜合多個因素的加權排序：
        - 是否關注作者（following_weight）
        - 互動數（likes + reposts + comments）（engagement_weight）
        - 新鮮度（時間）（recency_weight）
        - 互動率（engagement / views）（engagement_rate_weight）
        
        Args:
            posts: 所有候選帖子列表
            user_id: 當前使用者ID
            limit: 返回帖子數量
            follows: 關注關係 {follower_id: [followee_ids]}
            likes: 點贊記錄 {post_id: [user_ids]}
            weights: 權重配置
            
        Returns:
            按綜合分數排序的帖子列表
        """
        # 預設權重
        default_weights = {
            "following": 0.4,      # 關注權重
            "engagement": 0.3,     # 互動數權重
            "recency": 0.2,        # 新鮮度權重
            "engagement_rate": 0.1 # 互動率權重
        }
        
        if weights:
            default_weights.update(weights)
        
        follows = follows or {}
        likes = likes or {}
        
        # 使用者關注的人
        following_ids = follows.get(user_id, [])
        
        # 當前時間
        max_time = max((p.created_at for p in posts), default=datetime.now())
        
        def calculate_twitter_score(post: Post) -> float:
            """計算Twitter排序分數"""
            score = 0.0
            
            # 1. 關注因素
            if post.author_id in following_ids:
                score += default_weights["following"]
            
            # 2. 互動數因素（歸一化到0-1）
            total_engagement = post.likes_count + post.reposts_count + post.comments_count
            # 假設最大互動數為100
            engagement_normalized = min(total_engagement / 100.0, 1.0)
            score += default_weights["engagement"] * engagement_normalized
            
            # 3. 新鮮度因素（時間差越小，分數越高）
            time_diff_hours = (max_time - post.created_at).total_seconds() / 3600
            # 假設24小時內的帖子有新鮮度加成
            recency_score = max(0, 1 - time_diff_hours / 24.0)
            score += default_weights["recency"] * recency_score
            
            # 4. 互動率因素
            if post.view_count > 0:
                engagement_rate = total_engagement / post.view_count
                engagement_rate_normalized = min(engagement_rate, 1.0)
                score += default_weights["engagement_rate"] * engagement_rate_normalized
            
            return score
        
        # 計算所有帖子的分數
        post_scores = [(post, calculate_twitter_score(post)) for post in posts]
        
        # 按分數降序排序
        post_scores.sort(key=lambda x: x[1], reverse=True)
        
        # 返回top N
        return [post for post, score in post_scores[:limit]]
    
    @staticmethod
    def random_recommend(
        posts: List[Post],
        user_id: int,
        limit: int = 20
    ) -> List[Post]:
        """
        隨機推薦演算法（基線/對照組）
        
        Args:
            posts: 所有候選帖子列表
            user_id: 當前使用者ID
            limit: 返回帖子數量
            
        Returns:
            隨機選擇的帖子列表
        """
        if len(posts) <= limit:
            return list(posts)
        else:
            return random.sample(posts, limit)


__all__ = ["RecommendationEngine"]

