# 推薦系統模組

AgentSociety2 推薦系統模組提供了基於協同過濾的推薦演算法實現,支援實時增量更新和靈活的服務組合。該模組採用簡化的分層架構,將演算法實現、推薦服務和訓練邏輯清晰分離,易於擴充套件和維護。

## 目錄

- [概述](#概述)
- [架構設計](#架構設計)
- [快速開始](#快速開始)
- [核心元件](#核心元件)
- [API 說明](#api-說明)
- [資料模型](#資料模型)
- [配置引數](#配置引數)
- [使用場景](#使用場景)
- [使用說明](#使用說明)

## 概述

推薦系統模組支援以下特性:

- **多演算法支援**: 當前實現 MF (矩陣分解),架構支援擴充套件其他演算法
- **可選增量訓練**: 透過 `IncrementalTrainer` 支援實時資料更新
- **非同步非阻塞**: 後臺訓練不影響推薦服務
- **併發安全**: 使用 `asyncio.Lock` 保證模型訪問安全
- **模型持久化**: 支援模型儲存和載入
- **冷啟動處理**: 新使用者/新物品的推薦策略
- **靈活組合**: 服務層和訓練層可獨立使用

## 架構設計

### 核心類

#### `RecommenderAlgorithm`

推薦演算法的統一抽象基類,所有演算法必須實現:

- `fit(data: RatingMatrix)`: 訓練模型
- `predict(user_id: int, item_id: int)`: 預測評分
- `recommend(user_id: int, n: int, exclude_ids: Set[int])`: 生成推薦
- `save(path: str)`: 儲存模型 (可選)
- `load(path: str)`: 載入模型 (可選)

#### `RatingMatrix`

評分資料的統一表示,支援多種格式轉換:

- `from_ratings(ratings: List[Rating])`: 從評分列表構建
- `to_dataframe()`: 轉換為 pandas DataFrame
- `to_sparse()`: 轉換為稀疏矩陣
- `to_numpy()`: 轉換為 NumPy 陣列

#### `RecommendationService`

核心推薦服務層,負責:

- 非同步轉換 (演算法是同步的)
- 推薦結果快取
- 批次推薦支援
- 模型訪問控制

#### `IncrementalTrainer`

可選的增量訓練元件,負責:

- 監聽新資料
- 觸發條件檢查
- 後臺非同步訓練
- 訓練任務管理

### 架構層次

![Draw by Gemini](https://webp-pic.yokumi.cn/2025/11/20251126212950223.png)

## 快速開始

### 場景 1: 簡單推薦 (無增量更新)

```python
from agentsociety2.contrib.env.social_media.recommendation import (
    MFRecommender,
    MFConfig,
    RecommendationService,
    RatingMatrix,
    Rating,
)

# 1. 建立演算法
config = MFConfig(n_latent_factors=50, n_iterations=100)
algorithm = MFRecommender(config)

# 2. 建立服務
service = RecommendationService(algorithm)

# 3. 準備資料並訓練
ratings = [
    Rating(user_id=1, item_id=1, rating=5.0),
    Rating(user_id=1, item_id=2, rating=4.0),
    # ...
]
data = RatingMatrix.from_ratings(ratings)
await service.fit(data)

# 4. 生成推薦
recommendations = await service.recommend(user_id=1, n=10)
# 返回: [(item_id, score), ...]
```

### 場景 2: 增量更新

```python
from agentsociety2.contrib.env.social_media.recommendation import (
    MFRecommender,
    MFConfig,
    RecommendationService,
    IncrementalTrainer,
    TrainerConfig,
)

# 1. 建立演算法和服務
algorithm = MFRecommender(MFConfig())
service = RecommendationService(algorithm)

# 2. 建立訓練器
trainer_config = TrainerConfig(
    retrain_threshold_ratings=100,     # 新評分數閾值
    retrain_threshold_time=300,        # 時間閾值(秒)
    enable_auto_retrain=True           # 啟用自動重訓練
)
trainer = IncrementalTrainer(service, trainer_config)

# 3. 載入初始資料
await trainer.load_initial_data(initial_ratings)

# 4. 新增新評分 (自動觸發重訓練)
await trainer.add_ratings(new_ratings)

# 5. 獲取推薦
recommendations = await service.recommend(user_id=1, n=10)
```

### 執行完整示例

```bash
cd packages/agentsociety2
python example_new_architecture.py
```

示例包含:
1. 簡單使用場景 (只需推薦)
2. 增量更新場景 (實時更新)
3. 批次推薦
4. 模型持久化

## 核心元件

### 1. 演算法層 (`algorithms/`)

#### MFRecommender

矩陣分解推薦演算法,基於 PyTorch 實現。

**特性**:
- 使用者和物品的嵌入向量學習
- SGD 最佳化 + L2 正則化
- 冷啟動處理 (基於熱門度)
- 模型持久化支援

**檔案結構**:
```
algorithms/
├── core.py           # RecommenderAlgorithm + RatingMatrix
└── mf/
    ├── config.py     # MFConfig 配置類
    ├── model.py      # MFRecommender 實現
    └── __init__.py
```

### 2. 服務層 (`service.py`)

#### RecommendationService

推薦服務的核心實現。

**關鍵方法**:
- `fit(data: RatingMatrix)`: 訓練模型 (非同步)
- `predict(user_id: int, item_id: int)`: 預測評分 (非同步)
- `recommend(user_id, n, exclude_rated, exclude_ids)`: 生成推薦 (帶快取)
- `batch_recommend(user_ids, n)`: 批次推薦 (併發)
- `save_model(path)` / `load_model(path)`: 模型持久化

**功能特性**:
- **非同步轉換**: 使用 `asyncio.to_thread()` 包裝同步演算法
- **快取管理**: TTL 快取,自動過期清理
- **併發控制**: `asyncio.Lock` 保護模型訪問
- **批次最佳化**: 併發執行多使用者推薦

### 3. 訓練層 (`trainer.py`)

#### IncrementalTrainer

可選的增量訓練元件。

**關鍵方法**:
- `load_initial_data(ratings)`: 載入初始資料並訓練
- `add_ratings(new_ratings)`: 新增新評分,檢查觸發條件
- `trigger_retrain()`: 手動觸發重訓練
- `get_trainer_info()`: 獲取訓練器狀態

**觸發機制**:
1. 評分數量閾值: `pending_ratings >= retrain_threshold_ratings`
2. 時間閾值: `time_since_last_train >= retrain_threshold_time`
3. 手動觸發: 呼叫 `trigger_retrain()`

**訓練流程**:
1. 拍攝所有評分的快照
2. 在後臺非同步訓練新模型
3. 訓練期間舊模型繼續服務
4. 訓練完成後原子替換模型

### 4. 資料模型 (`models.py`)

定義推薦系統的資料結構:

- `Item`: 物品資訊
- `Rating`: 使用者評分
- `UserPreference`: 使用者偏好
- `FeedCache`: Feed 快取
- `RecommendationHistory`: 推薦歷史

## API 說明

### RecommendationService API

#### `fit(data: RatingMatrix) -> None`

訓練推薦模型。

**引數**:
- `data`: 評分矩陣資料

**示例**:
```python
data = RatingMatrix.from_ratings(ratings)
await service.fit(data)
```

---

#### `predict(user_id: int, item_id: int) -> float`

預測使用者對物品的評分。

**引數**:
- `user_id`: 使用者 ID
- `item_id`: 物品 ID

**返回**: 預測評分 (1.0-5.0)

**示例**:
```python
score = await service.predict(user_id=1, item_id=100)
# 返回: 4.2
```

---

#### `recommend(user_id: int, n: int = 20, exclude_rated: bool = True, exclude_ids: Optional[Set[int]] = None) -> List[Tuple[int, float]]`

為使用者生成推薦列表。

**引數**:
- `user_id`: 使用者 ID
- `n`: 推薦數量
- `exclude_rated`: 是否排除已評分物品 (當前版本暫未實現,保留介面)
- `exclude_ids`: 額外要排除的物品 ID 集合

**返回**: `[(item_id, score), ...]` 按 score 降序排列

**示例**:
```python
recs = await service.recommend(user_id=1, n=10)
# 返回: [(101, 4.8), (203, 4.5), ...]
```

---

#### `batch_recommend(user_ids: List[int], n: int = 20, exclude_ids: Optional[Dict[int, Set[int]]] = None) -> Dict[int, List[Tuple[int, float]]]`

批次生成多個使用者的推薦。

**引數**:
- `user_ids`: 使用者 ID 列表
- `n`: 每個使用者的推薦數量
- `exclude_ids`: 每個使用者要排除的物品 ID 字典

**返回**: `{user_id: [(item_id, score), ...], ...}`

**示例**:
```python
results = await service.batch_recommend([1, 2, 3], n=5)
# 返回: {1: [(101, 4.8), ...], 2: [(205, 4.3), ...], ...}
```

---

#### `save_model(path: str) -> None`

儲存模型到檔案。

**引數**:
- `path`: 儲存路徑

**示例**:
```python
await service.save_model("/data/model.pkl")
```

---

#### `load_model(path: str) -> None`

從檔案載入模型。

**引數**:
- `path`: 模型檔案路徑

**示例**:
```python
await service.load_model("/data/model.pkl")
```

---

#### `get_algorithm_info() -> Dict[str, Any]`

獲取演算法資訊。

**返回**:
```python
{
    "name": str,           # 演算法名稱
    "config": dict,        # 演算法配置
    "is_trained": bool,    # 是否已訓練
    "num_users": int,      # 使用者數量
    "num_items": int       # 物品數量
}
```

---

#### `clear_cache() -> None`

清空推薦快取。

---

#### `get_cache_stats() -> Dict[str, Any]`

獲取快取統計資訊。

**返回**:
```python
{
    "total_entries": int,   # 快取總條目數
    "valid_entries": int,   # 有效條目數
    "cache_ttl": int        # 快取過期時間(秒)
}
```

### IncrementalTrainer API

#### `load_initial_data(ratings: List[Rating]) -> None`

載入初始資料並訓練模型。

**引數**:
- `ratings`: 初始評分列表

**示例**:
```python
await trainer.load_initial_data(initial_ratings)
```

---

#### `add_ratings(new_ratings: List[Rating]) -> None`

新增新評分,自動檢查是否觸發重訓練。

**引數**:
- `new_ratings`: 新評分列表

**示例**:
```python
await trainer.add_ratings(new_ratings)
```

---

#### `trigger_retrain() -> None`

手動觸發重訓練。

**示例**:
```python
await trainer.trigger_retrain()
```

---

#### `get_trainer_info() -> dict`

獲取訓練器狀態資訊。

**返回**:
```python
{
    "is_training": bool,           # 是否正在訓練
    "last_train_time": str,        # 上次訓練時間(ISO格式)
    "total_ratings": int,          # 總評分數
    "pending_ratings": int,        # 待訓練評分數
    "config": {
        "threshold_ratings": int,  # 評分閾值
        "threshold_time": int,     # 時間閾值(秒)
        "auto_retrain": bool       # 是否自動重訓練
    }
}
```

## 資料模型

### RatingMatrix

評分矩陣的統一表示。

**屬性**:
```python
user_ids: np.ndarray      # 使用者ID陣列
item_ids: np.ndarray      # 物品ID陣列
ratings: np.ndarray       # 評分陣列
user_map: Dict[int, int]  # 使用者ID對映
item_map: Dict[int, int]  # 物品ID對映
```

**方法**:
- `from_ratings(ratings: List[Rating])`: 從評分列表構建
- `to_dataframe()`: 轉換為 DataFrame
- `to_sparse()`: 轉換為稀疏矩陣
- `to_numpy()`: 轉換為 NumPy 陣列

### Rating

單個評分記錄。

**屬性**:
```python
user_id: int              # 使用者ID
item_id: int              # 物品ID
rating: float             # 評分 (1.0-5.0)
timestamp: datetime       # 評分時間
```

### Item

物品資訊。

**屬性**:
```python
item_id: int              # 物品ID
name: str                 # 物品名稱
category: str             # 分類
metadata: Dict[str, Any]  # 後設資料
```

## 配置引數

### MFConfig

MF 演算法配置引數。

```python
MFConfig(
    n_latent_factors: int = 50,      # 潛在因子數量
    learning_rate: float = 0.01,     # 學習率
    reg_param: float = 0.01,         # L2正則化引數
    n_iterations: int = 100,         # 訓練迭代次數
)
```

### ServiceConfig

推薦服務配置引數。

```python
ServiceConfig(
    cache_ttl: int = 300,            # 快取過期時間(秒)
    max_batch_size: int = 100,       # 最大批次大小
    timeout: float = 10.0,           # 請求超時時間(秒)
)
```

### TrainerConfig

訓練器配置引數。

```python
TrainerConfig(
    retrain_threshold_ratings: int = 100,   # 新評分數閾值
    retrain_threshold_time: int = 300,      # 時間閾值(秒)
    enable_auto_retrain: bool = True,       # 啟用自動重訓練
)
```

## 使用場景

### 場景 1: 離線批次訓練

適用於定期批次更新模型的場景。

```python
# 只使用 Service,不使用 Trainer
algorithm = MFRecommender(MFConfig())
service = RecommendationService(algorithm)

# 定期重新訓練
while True:
    # 收集一段時間的資料
    new_data = collect_ratings()
    data = RatingMatrix.from_ratings(new_data)

    # 重新訓練
    await service.fit(data)

    # 儲存模型
    await service.save_model(f"model_{timestamp}.pkl")

    # 等待下次更新
    await asyncio.sleep(3600)
```

### 場景 2: 實時增量更新

適用於需要實時響應使用者行為的場景。

```python
# 使用 Service + Trainer
algorithm = MFRecommender(MFConfig())
service = RecommendationService(algorithm)
trainer = IncrementalTrainer(
    service,
    TrainerConfig(
        retrain_threshold_ratings=50,   # 50條新評分觸發
        retrain_threshold_time=300,     # 或5分鐘觸發
    )
)

# 初始化
await trainer.load_initial_data(initial_ratings)

# 實時新增新評分
async def on_new_rating(rating: Rating):
    await trainer.add_ratings([rating])
    # 自動檢查是否需要重訓練
```

### 場景 3: 批次推薦服務

適用於需要為多個使用者同時生成推薦的場景。

```python
# 併發為多個使用者生成推薦
async def generate_feeds(user_ids: List[int]):
    results = await service.batch_recommend(user_ids, n=20)

    for user_id, recommendations in results.items():
        # 為每個使用者準備 Feed
        feed = prepare_feed(user_id, recommendations)
        await send_to_user(user_id, feed)
```

### 場景 4: A/B 測試

適用於對比不同演算法或引數的場景。

```python
# 建立兩個服務
service_a = RecommendationService(MFRecommender(MFConfig(n_latent_factors=50)))
service_b = RecommendationService(MFRecommender(MFConfig(n_latent_factors=100)))

# 同時訓練
await service_a.fit(data)
await service_b.fit(data)

# 對比推薦結果
recs_a = await service_a.recommend(user_id, n=10)
recs_b = await service_b.recommend(user_id, n=10)
```

## 使用說明

### 環境要求

- Python 3.11+
- PyTorch
- pandas, numpy
- pydantic

### 安裝依賴

```bash
pip install torch pandas numpy pydantic
```

### 冷啟動策略

**新使用者**:
- 返回熱門物品 (基於 `平均評分 × log(評分數+1)`)
- 累積評分後自動納入訓練

**新物品**:
- 返回預設評分 2.5
- 累積足夠評分後納入訓練

### 效能指標

**訓練效能**:

| 資料規模 | 使用者數 | 物品數 | 評分數 | 訓練時間 |
|---------|--------|--------|--------|---------|
| 小 | 100 | 1K | 10K | <10s |
| 中 | 1K | 5K | 100K | <30s |
| 大 | 5K | 10K | 500K | <2min |

**推薦延遲**:
- 單個推薦請求: <100ms (有快取: <10ms)
- 併發推薦 (100 QPS): 平均延遲 <150ms

### 最佳實踐

1. **模型持久化**: 定期儲存模型,避免重新訓練
2. **快取管理**: 合理設定 `cache_ttl`,平衡新鮮度和效能
3. **批次推薦**: 優先使用 `batch_recommend` 提高效率
4. **引數調優**: 根據資料規模調整 `n_latent_factors` 和 `n_iterations`
5. **增量訓練**: 根據資料流速度調整觸發閾值
6. **併發控制**: 避免同時訓練多個模型

### 限制和注意事項

- 所有 Service 和 Trainer API 都是非同步的,必須在 async 函式中呼叫
- 訓練期間推薦服務不會中斷,但使用的是舊模型
- 大資料集訓練時注意記憶體佔用
- 冷啟動使用者的推薦質量可能較低

### 故障排查

**訓練失敗**:
- 檢查評分資料是否為空
- 檢查 MFConfig 引數是否合理
- 檢視日誌獲取詳細錯誤資訊

**推薦為空**:
- 檢查模型是否已訓練 (`get_algorithm_info()`)
- 檢查使用者ID是否存在
- 新使用者會返回熱門物品

**重訓練不觸發**:
- 檢查 `enable_auto_retrain` 是否為 True
- 檢查是否達到觸發閾值
- 檢視 `get_trainer_info()` 中的 `pending_ratings`

**快取問題**:
- 使用 `clear_cache()` 清空快取
- 檢查 `cache_ttl` 設定是否合理
- 檢視 `get_cache_stats()` 瞭解快取狀態

## 擴充套件開發

### 新增新演算法

1. 繼承 `RecommenderAlgorithm` 類
2. 實現 5 個核心方法
3. 建立配置類 (可選)
4. 在 `__init__.py` 中匯出

**示例**:
```python
from .core import RecommenderAlgorithm, RatingMatrix

class MyRecommender(RecommenderAlgorithm):
    def fit(self, data: RatingMatrix) -> None:
        # 訓練邏輯
        pass

    def predict(self, user_id: int, item_id: int) -> float:
        # 預測邏輯
        pass

    def recommend(self, user_id: int, n: int, exclude_ids: Set[int]) -> List[Tuple[int, float]]:
        # 推薦邏輯
        pass

    def get_algorithm_name(self) -> str:
        return "MyRecommender"
```

### 自定義訓練策略

繼承 `IncrementalTrainer` 並重寫 `_should_retrain()`:

```python
class CustomTrainer(IncrementalTrainer):
    def _should_retrain(self) -> bool:
        # 自定義觸發邏輯
        if my_custom_condition():
            return True
        return super()._should_retrain()
```

---
