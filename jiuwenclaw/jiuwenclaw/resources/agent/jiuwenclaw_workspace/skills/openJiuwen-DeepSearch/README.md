# openJiuwen-DeepSearch 技能

知識增強型深度檢索與研究引擎，支援查詢規劃、資訊收集、理解反思、報告生成等多 Agent 協同處理能力。

## 功能特性

- **深度研究**：基於使用者查詢自動規劃任務、收集資訊、分析並生成研究報告
- **知識增強**：融合本地知識庫與網頁搜尋，提升搜尋質量
- **結果溯源**：輸出結果包含引用資訊，支援片段級溯源
- **圖文並茂**：支援包含圖表的視覺化報告生成
- **多 Agent 協同**：查詢規劃、資訊收集、理解分析、報告生成全流程自動化

## 適用場景

- **金融分析研報**：投資分析、行業研究、市場趨勢分析
- **學術與政策研究**：政策影響分析、學術文獻綜述
- **企業級深度搜尋**：複雜資訊查詢、多源資料整合

## 快速開始

### 1. uv 環境準備

**安裝 uv（如未安裝）：**

```bash
# Windows (PowerShell)
pip install uv

# Linux/Mac
pip install uv
```

### 2. 配置環境

使用 uv 建立 Python 3.11 虛擬環境並安裝依賴（精確版本）：

```bash
# 使用 Python 3.11 建立虛擬環境並安裝精確版本的依賴
uv venv --python 3.11
uv pip install openjiuwen-deepsearch==0.1.1 python-dotenv pypandoc markdown markdown_mermaid_cli -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 配置 API Key

複製示例配置檔案並編輯：

```bash
cp .env.example .env
```

編輯 `.env` 檔案，填入你的 API Key：

```env
# LLM 配置
LLM_MODEL_NAME=gpt-4o
LLM_MODEL_TYPE=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your-actual-openai-api-key-here

# 搜尋引擎配置
WEB_SEARCH_ENGINE_NAME=tavily
WEB_SEARCH_API_KEY=tvly-your-actual-tavily-api-key-here
WEB_SEARCH_URL=https://api.tavily.com
```

### 4. 手動執行深度研究（可跳過，後續由Agent執行命令）

```bash
uv run scripts\main.py --mode query --query "待生成深度調研報告的主題"
```


## 輸出結果

- 日誌目錄：`./output/logs/`
- 結果目錄：openJiuwen-DeepSearch技能資料夾根目錄


## 許可證

Apache 2.0 License
