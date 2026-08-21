# 單元測試指南

## 📋 目錄

- [安裝測試依賴](#安裝測試依賴)
- [本地執行測試](#本地執行測試)
- [測試覆蓋率](#測試覆蓋率)

---

## 🔧 安裝測試依賴

### 安裝測試依賴

```bash
# 方式 1: 使用 pip
pip install -e ".[test]"

# 方式 2: 直接安裝測試包
pip install pytest pytest-asyncio pytest-cov pytest-mock coverage freezegun
```

---

## 🏃 本地執行測試

### 執行所有測試

```bash
# 執行所有測試
pytest

# 或者指定目錄
pytest tests/
```

### 執行特定測試檔案

```bash
# 執行單個測試檔案
pytest tests/unit_tests/test_config.py

# 執行特定目錄的測試
pytest tests/unit_tests/
```

### 執行特定測試用例

```bash
# 執行特定測試函式
pytest tests/unit_tests/test_config.py::TestResolveEnvVars::test_resolve_string_with_env_var

# 執行特定測試類
pytest tests/unit_tests/test_config.py::TestResolveEnvVars
```

### 常用測試選項

```bash
# 詳細輸出
pytest -v

# 顯示列印輸出
pytest -s

# 顯示錯誤堆疊
pytest --tb=long

# 只執行失敗的測試
pytest --lf

# 遇到第一個失敗就停止
pytest -x

# 並行執行測試（需要安裝 pytest-xdist）
pytest -n auto
```

### 執行帶標記的測試

```bash
# 只執行單元測試
pytest -m unit

# 只執行整合測試
pytest -m integration

# 只執行慢速測試
pytest -m slow

# 排除慢速測試
pytest -m "not slow"
```

---

## 📊 測試覆蓋率

### 生成覆蓋率報告

```bash
# 生成終端報告
pytest --cov=jiuwenclaw --cov-report=term-missing

# 生成 HTML 報告
pytest --cov=jiuwenclaw --cov-report=html

# 生成 XML 報告（用於 CI）
pytest --cov=jiuwenclaw --cov-report=xml
```

### 檢視覆蓋率報告

```bash
# 生成 HTML 報告後在瀏覽器中開啟
pytest --cov=jiuwenclaw --cov-report=html
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```
