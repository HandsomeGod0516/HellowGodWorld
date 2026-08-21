---
name: openJiuwen-DeepSearch
description: 知識增強型深度檢索與深度研究，支援查詢規劃、資訊收集、理解反思、研究報告生成等多 Agent 協同。使用場景：金融分析研報、學術與政策研究、企業級深度搜尋等複雜推理任務，可以生成Markdown、Doc和Html格式的研究報告。**每次使用該技能之前都先完整閱讀一遍SKILL.md學習技能。**


---

# openJiuwen-DeepSearch 技能使用指南

1. 每次使用該技能之前都先**完整閱讀一遍SKILL.md學習技能**
2. **無需配置環境**
3. **切換到openJiuwen-DeepSearch技能資料夾路徑**
4. 在該路徑下**直接執行**`uv run "scripts\main.py" --mode query --query "研究報告標題"`命令即可，**不需要**`uv sync`等操作。
5. 執行命令後會啟動子程序在後臺執行，請確保**後臺子程序正常執行**，你需要給出**openJiuwen-DeepSearch技能資料夾絕對路徑**作為報告輸出目錄並提示使用者等待約15分鐘直至報告檔案輸出。
6. 技能執行時間約15分鐘，執行完上述命令後，該程式會拉起一個後臺子程序完成報告生成任務，並且在openJiuwen-DeepSearch技能資料夾絕對路徑下的PID.info中會輸出該子程序的PID。你必須確保後臺子程序正常執行並**讀取PID.info中的PID**，之後直接結束當前輪次對話並提示使用者等待，當使用者詢問報告是否完成生成時，你需要**透過對應子程序PID的程序任務和openJiuwen-DeepSearch技能資料夾路徑中的Markdown/Doc/Html檔案列表判斷是否完成本次研究報告的生成**。
7. 你需要**讀取openJiuwen-DeepSearch技能資料夾下的PID.info檔案來獲取子程序的PID**，然後建立一個定時任務（**執行週期每20分鐘**）監測子程序的PID來定期檢查對應子程序的存活狀態，**當子程序結束執行後，你要檢查本次研究對應的報告是否生成**，並通知使用者結果，然後**必須**清理當前的定時任務，**必須**檢查並更新當前的todo list。

## 執行深度研究

### 命令列執行（推薦）

```bash
uv run "scripts\main.py" --mode query --query "AI手機行業研究報告"
```

### 示例場景

#### 金融分析研報

```bash
uv run "scripts\main.py" --mode query --query "美聯儲2025年降息對A股科技板塊的影響"
```

#### 學術與政策研究

```bash
uv run "scripts\main.py" --mode query --query "中國'新質生產力'政策對製造業中小企業的影響"
```

#### 行業分析

```bash
uv run "scripts\main.py" --mode query --query "2025年新能源汽車行業發展趨勢分析"
```

## 可選環境變數

| 變數名                      | 說明            | 預設值        |
| ------------------------ | ------------- | ---------- |
| `MAX_WEB_SEARCH_RESULTS` | 單次搜尋最大返回結果數   | `5`        |
| `EXECUTION_METHOD`       | workflow 執行方式 | `parallel` |

### 執行方式

- **parallel**：並行執行（預設，推薦）
- **dependency_driving**：依賴驅動執行

## 輸出結果

### 日誌輸出

- 日誌目錄：`./output/logs/`
- 結果目錄：openJiuwen-DeepSearch技能資料夾根目錄

### 報告輸出

最終研究報告會以流式的方式輸出到到控制檯，包含：

- 查詢規劃結果
- 資訊收集過程
- 理解分析內容
- 最終生成的報告

## 錯誤處理

### 常見錯誤

1. **缺少必需的環境變數**
   
   ```
   缺少必需的環境變數: LLM_API_KEY, WEB_SEARCH_API_KEY
   ```
   
    **解決方案**：檢查 `.env` 檔案是否正確配置

2. **API Key 無效**
   
   ```
   Error: Invalid API key
   ```
   
    **解決方案**：檢查 `.env` 檔案中的 API Key 是否正確

## 注意事項

1. **無需配置環境**：**切換到在openJiuwen-DeepSearch技能資料夾路徑**後直接使用`uv run`命令執行，該命令會使用技能資料夾根目錄的`.venv`環境
2. **查詢內容**：查詢內容支援空格，無需額外引號
3. **技能移植性**：技能支援任意位置複製，無路徑硬編碼依賴

## 技術架構

openJiuwen-DeepSearch 基於 openJiuwen agent-core 框架構建，包含：

- **管理器**：Agent 建立、編排流程管理、配置管理
- **查詢規劃**：意圖識別、查詢路由、結構規劃、任務分解
- **知識檢索**：關鍵詞檢索、向量檢索、知識圖譜檢索、融合檢索
- **理解分析**：搜尋結果評估、精煉、擴充套件、融合
- **結果生成**：答案生成、報告生成、互動式編輯、結果溯源
