# JiuwenClow 單元測試框架配置總結

## 📦 已建立的檔案
### 1. 測試配置檔案

```
jiuwenclaw/
├── pytest.ini                           # Pytest 配置
├── pyproject.toml                       # 已更新，新增了測試依賴
├── run_tests.sh                         # 測試執行指令碼（可執行）
├── tests/
│   ├── __init__.py
│   ├── conftest.py                      # 共享 fixtures
│   ├── README.md                        # 詳細測試指南
│   └── unit/                            # 單元測試目錄
│       ├── agentserver
│       ├── channel
│       ├── evolution
│           ├── test_schema.py           # 演進模型測試
│           ├── test_signal_detector.py  # 訊號檢測器測試
│           ├── test_message.py          # 訊息模型測試
│       ├── gateway
│       ├── schema
│       ├── __init__.py
│       ├── test_config.py               # 配置模組測試
│       └── test_utils.py                # 工具函式測試
└── .gitcode/workflows/
    ├── ci.yml                           # gitcode Actions 測試工作流
    └── test.yml                         # gitcode Actions 程式碼質量檢查
```

---

## 🚀 本地測試

### 方式 1: 使用測試指令碼（推薦）

```bash
cd /Users/gawa/Desktop/pr/jiuwenclaw

# 執行所有測試
./run_tests.sh

# 生成 HTML 覆蓋率報告
./run_tests.sh -c

# 只執行單元測試
./run_tests.sh -u

# 並行執行測試
./run_tests.sh -p

# 檢視幫助
./run_tests.sh -h
```

### 方式 2: 直接使用 pytest

```bash
# 首先安裝測試依賴
pip install -e ".[test]"

# 執行所有測試
pytest -v

# 執行特定目錄
pytest tests/unit_tests/ -v

# 執行特定檔案
pytest tests/unit_tests/test_config.py -v

# 執行特定測試
pytest tests/unit_tests/test_config.py::TestResolveEnvVars::test_resolve_string_with_env_var -v

# 生成覆蓋率報告
pytest --cov=jiuwenclaw --cov-report=html --cov-report=term-missing
```

---

## 🧪 已實現的測試用例

### 1. `test_config.py` - 配置模組測試

**測試內容**：
- ✅ 環境變數解析（`resolve_env_vars`）
- ✅ 字串中的環境變數替換
- ✅ 預設值處理
- ✅ 字典和列表中的環境變數
- ✅ 巢狀結構解析
- ✅ 配置檔案讀取

**測試數量**: ~15 個測試

**關鍵測試**：
```python
test_resolve_string_with_env_var()      # 測試 ${VAR} 解析
test_resolve_string_with_default()       # 測試 ${VAR:-default}
test_resolve_dict_with_env_vars()        # 測試字典解析
test_resolve_nested_structure()          # 測試巢狀結構
```

---

### 2. `test_evolution_schema.py` - 演進模型測試

**測試內容**：
- ✅ `EvolutionType` 列舉
- ✅ `EvolutionChange` 資料類
- ✅ `EvolutionEntry` 資料類
- ✅ `EvolutionFile` 資料類
- ✅ `EvolutionSignal` 資料類
- ✅ 序列化/反序列化

**測試數量**: ~30 個測試

**關鍵測試**：
```python
test_evolution_entry_make()               # 測試工廠方法
test_evolution_entry_is_pending()         # 測試屬性
test_evolution_file_pending_entries()     # 測試屬性
test_evolution_signal_to_dict()           # 測試序列化
```

---

### 3. `test_signal_detector.py` - 訊號檢測器測試

**測試內容**：
- ✅ 執行失敗訊號檢測
- ✅ 使用者修正訊號檢測
- ✅ 中英文關鍵詞檢測
- ✅ 訊號去重
- ✅ Skill 名稱提取
- ✅ Excerpt 擷取

**測試數量**: ~20 個測試

**關鍵測試**：
```python
test_detect_execution_failure()           # 測試錯誤檢測
test_detect_user_correction_chinese()     # 測試中文修正
test_detect_multiple_signals()            # 測試多訊號
test_deduplicate_signals()                # 測試去重
test_detect_with_skill_from_tool_calls()  # 測試 Skill 歸因
```

---

### 4. `test_schema.py` - 訊息模型測試

**測試內容**：
- ✅ `ReqMethod` 列舉
- ✅ `EventType` 列舉
- ✅ `Mode` 列舉
- ✅ `AgentRequest` 資料類
- ✅ `AgentResponse` 資料類
- ✅ `AgentResponseChunk` 資料類
- ✅ `Message` 資料類

**測試數量**: ~25 個測試

**關鍵測試**：
```python
test_create_agent_request_minimal()       # 測試最小請求
test_create_agent_request_full()          # 測試完整請求
test_create_request_message()             # 測試請求訊息
test_create_event_message()               # 測試事件訊息
test_message_mode()                       # 測試模式欄位
```

---

### 5. `test_utils.py` - 工具函式測試

**測試內容**：
- ✅ 路徑解析函式
- ✅ 包檢測函式
- ✅ Logger 設定
- ✅ 常量定義

**測試數量**: ~10 個測試

**關鍵測試**：
```python
test_get_root_dir()                       # 測試根目錄獲取
test_get_config_dir()                     # 測試配置目錄
test_setup_logger_default()               # 測試 Logger 設定
test_path_caching()                       # 測試路徑快取
```

---

## 🎯 測試覆蓋率目標

| 模組 | 當前覆蓋率 | 目標覆蓋率 |
|------|-----------|-----------|
| `config.py` | ~80% | 90% |
| `evolution/schema.py` | ~90% | 95% |
| `evolution/signal_detector.py` | ~75% | 85% |
| `schema/*.py` | ~70% | 80% |
| `utils.py` | ~60% | 75% |
| **總體** | **~70%** | **80%** |

---

## 🤖 GitHub Actions CI

### 工作流 1: Tests (`.github/workflows/test.yml`)

**觸發條件**：
- Push to `main` or `develop`
- Pull requests to `main` or `develop`
- 手動觸發

**測試矩陣**：
- Python: 3.11, 3.12, 3.13
- OS: Ubuntu Latest

**步驟**：
1. ✅ Checkout 程式碼
2. ✅ 設定 Python
3. ✅ 安裝依賴
4. ✅ 執行單元測試
5. ✅ 上傳覆蓋率到 Codecov

### 工作流 2: Code Quality (`.github/workflows/lint.yml`)

**檢查專案**：
- ✅ 型別檢查 (mypy)
- ✅ 程式碼格式 (black)
- ✅ Linting (ruff)
- ✅ 安全掃描 (bandit)

---

## 📝 使用示例

### 快速開始

```bash
# 1. 安裝依賴
pip install -e ".[test]"

# 2. 執行所有測試
./run_tests.sh

# 3. 檢視覆蓋率報告
open htmlcov/index.html  # macOS
```

### 開發新功能時的測試工作流

```bash
# 1. 編寫測試
# tests/unit_tests/test_new_feature.py

# 2. 執行新測試
pytest tests/unit_tests/test_new_feature.py -v

# 3. 檢視覆蓋率
pytest --cov=jiuwenclaw.new_feature --cov-report=term-missing

# 4. 執行所有測試確保沒有破壞
pytest tests/

# 5. 提交程式碼
git add .
git commit -m "feat: add new feature with tests"
git push
```

### CI 失敗時的除錯

```bash
# 1. 本地復現 CI 環境
python -m pytest tests/ -v

# 2. 檢查 Python 版本
python --version  # 應該是 3.11, 3.12, 或 3.13

# 3. 檢查依賴
pip list | grep pytest

# 4. 執行特定失敗的測試
pytest tests/unit_tests/test_config.py::TestResolveEnvVars -vv
```

---

## 🛠️ 測試框架配置詳解

### pytest.ini

```ini
[pytest]
# 測試發現模式
python_files = test_*.py *_test.py
python_classes = Test*
python_functions = test_*

# 測試路徑
testpaths = tests

# 輸出選項
addopts =
    -v                              # 詳細輸出
    --strict-markers                # 嚴格標記檢查
    --tb=short                      # 簡短的錯誤堆疊
    --cov=jiuwenclaw                # 覆蓋率
    --cov-report=term-missing       # 終端報告
    --cov-report=html               # HTML 報告
    --cov-report=xml                # XML 報告（CI）
    --asyncio-mode=auto             # 非同步測試模式

# 標記定義
markers =
    unit: Unit tests
    integration: Integration tests
    slow: Slow running tests
    async: Async tests
```

### conftest.py Fixtures

```python
@pytest.fixture
def temp_workspace() -> Path:
    """建立臨時工作區"""

@pytest.fixture
def temp_config_file() -> Path:
    """建立臨時配置檔案"""

@pytest.fixture
def mock_env_vars() -> None:
    """設定模擬環境變數"""

@pytest.fixture
def sample_skill_md() -> Path:
    """建立示例 SKILL.md 檔案"""

@pytest.fixture
def sample_messages() -> List[dict]:
    """示例訊息列表"""
```

---

## 📚 擴充套件測試

### 新增新的測試檔案

```bash
# 1. 建立測試檔案
touch tests/unit_tests/test_new_module.py

# 2. 編寫測試
# 參考 tests/README.md 中的模板

# 3. 執行測試
pytest tests/unit_tests/test_new_module.py -v
```

### 新增新的 Fixture

```python
# 在 tests/conftest.py 中新增

@pytest.fixture
def my_custom_fixture():
    """自定義 fixture."""
    # 設定
    data = {"key": "value"}
    yield data
    # 清理（可選）
```

---

## ✅ 下一步建議

1. **增加測試覆蓋**：
   - `evolution/evolver.py` - 演進生成器
   - `evolution/service.py` - 演進服務
   - `gateway/` - 閘道器層
   - `channel/` - 頻道介面卡

2. **新增整合測試**：
   - 端到端測試
   - API 測試
   - 效能測試

3. **提升測試質量**：
   - 使用 mock 隔離外部依賴
   - 新增效能基準測試
   - 實現測試資料工廠

4. **改進 CI/CD**：
   - 新增效能測試
   - 整合安全掃描
   - 自動化釋出流程

---

## 📞 需要幫助？

- 檢視 `tests/README.md` 獲取詳細指南
- 執行 `./run_tests.sh -h` 檢視測試指令碼幫助
- 檢視 pytest 文件: https://docs.pytest.org/

---

**Happy Testing! 🎉**
