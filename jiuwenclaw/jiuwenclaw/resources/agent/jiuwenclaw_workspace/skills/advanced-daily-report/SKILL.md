---
name: advanced-daily-report
version: 2.0.0
description: 進階版日報生成器，支援多資料來源採集、工作分析、趨勢對比、週報月報聚合
tags: [report, automation, productivity, daily, weekly, monthly, advanced]
allowed_tools: [read_memory, write_memory, bash, read_file, write_file]
---

# 進階版日報生成器

自動採集多源資料，智慧分析工作效率，生成日報/週報/月報並推送到飛書。

## 核心能力

### 1. 多資料來源採集

| 資料來源 | 採集內容 | 頻率 |
|--------|----------|------|
| **Git 倉庫** | 提交記錄、程式碼變更統計 | 實時 |
| **網易郵箱** | 收發郵件統計、未讀提醒 | 實時 |
| **記憶系統** | 今日工作記錄、長期記憶 | 實時 |
| **待辦事項** | 任務狀態、完成率 | 實時 |

### 2. 智慧工作分析

- **效率指標計算**
  - 任務完成率 = 已完成 / 總任務
  - 生產力得分（0-100）
  - 專注度得分（0-100）

- **趨勢對比**
  - 與昨日對比
  - 與上週同期對比
  - 周趨勢圖

- **關鍵詞提取**
  - 自動提取今日工作關鍵詞
  - 工作主題聚類

### 3. 多報告型別

| 型別 | 觸發方式 | 推送時間 |
|------|----------|----------|
| **日報** | 手動/定時 | 每天 18:00 |
| **週報** | 定時 | 每週五 18:00 |
| **月報** | 定時 | 每月最後一天 18:00 |

## 目錄結構

```
daily-report/
├── SKILL.md              # 技能定義（本檔案）
├── collectors/           # 資料採集模組
│   ├── __init__.py
│   ├── git_collector.py  # Git 提交採集
│   ├── email_collector.py # 郵件統計採集
│   ├── memory_collector.py # 記憶資料採集
│   ├── todo_collector.py  # 待辦事項採集
│   └── aggregator.py      # 資料聚合器
├── analyzers/            # 分析模組
│   ├── __init__.py
│   └── work_analyzer.py  # 工作分析引擎
├── generators/           # 報告生成模組
│   ├── __init__.py
│   └── report_generator.py # 報告生成器
└── report_helper.py      # 相容舊版指令碼
```

## 使用方式

### ⚠️ 重要：執行方式

本技能透過執行 Python 指令碼來採集資料（Git提交、郵箱郵件、記憶、待辦）。
**必須使用 `bash` 工具執行指令碼**，而不是直接回複使用者。

**指令碼會自動採集以下資料**：
- **Git 提交記錄**：透過 `git log` 命令讀取 `D:/Download/jiuwenclaw` 倉庫的提交歷史
- **郵箱郵件統計**：透過 IMAP 協議連線 `.env` 中配置的郵箱賬戶讀取郵件統計（需要郵箱授權碼）
- **記憶系統**：讀取 `~/.jiuwenclaw/agent/memory/` 目錄下的每日記憶檔案
- **待辦事項**：讀取 `~/.jiuwenclaw/agent/sessions/` 下各會話的 `todo.md` 檔案

### 手動觸發

當使用者請求生成日報/週報/月報時，**執行以下命令**：

```bash
# 生成今日日報（記憶/待辦/Git 等；Git 在倉庫根目錄統計）
python ~/.jiuwenclaw/agent/skills/daily-report/run_report.py daily --save

# 生成指定日期日報
python ~/.jiuwenclaw/agent/skills/daily-report/run_report.py daily --date 2026-03-06 --save

# 生成周報（聚合一週資料）
python ~/.jiuwenclaw/agent/skills/daily-report/run_report.py weekly --save

# 生成月報（聚合一月資料，包含每日Git提交統計）
python ~/.jiuwenclaw/agent/skills/daily-report/run_report.py monthly --save

# 生成月報（指定月份）
python ~/.jiuwenclaw/agent/skills/daily-report/run_report.py monthly --year 2026 --month 3 --save
```

### 執行步驟

1. 使用者傳送 "生成日報" / "生成周報" / "生成月報" 等指令
2. **使用 bash 執行上述命令**
3. 指令碼自動採集資料：
   - Git: 執行 `git log` 獲取提交記錄、程式碼變更統計
   - 郵箱: 透過 IMAP 連線獲取郵件統計（如果配置了郵箱）
   - 記憶: 讀取記憶檔案獲取工作記錄
   - 待辦: 解析 todo.md 獲取任務狀態
4. 指令碼執行完成後，輸出格式為 `REPORT_FILE:/path/to/report.md`
5. **⚠️ 重要：使用 read_file 工具讀取報告檔案，然後將完整內容傳送給使用者**
   - 指令碼輸出包含 `REPORT_FILE:` 字首，後面是檔案路徑
   - 必須讀取該檔案內容，不能只顯示檔案路徑
   - 要把完整的報告 Markdown 內容展示在對話方塊中

### 觸發關鍵詞

- 日報：生成今日日報、生成昨天日報、檢視今日工作、檢視程式碼提交
- 週報：生成本週週報、週報彙總、本週工作總結
- 月報：生成本月月報、月度總結、讀取郵箱中本月的內容整理成月報、本月程式碼提交統計

### 資料來源說明

| 資料來源 | 採集方式 | 配置位置 |
|--------|----------|----------|
| **Git 倉庫** | `git log` 命令 | 倉庫路徑: `D:/Download/jiuwenclaw` |
| **網易郵箱** | IMAP 協議 | `.env`: `EMAIL_ADDRESS`, `EMAIL_TOKEN` |
| **記憶系統** | 讀取 MD 檔案 | `~/.jiuwenclaw/agent/memory/YYYY-MM-DD.md` |
| **待辦事項** | 解析 todo.md | `~/.jiuwenclaw/agent/sessions/*/todo.md` |

### 定時觸發

透過 `HEARTBEAT.md` 配置定時執行：

```markdown
## 活躍的任務項
- 生成今日工作日報  # 每天執行
- 每週五生成周報    # 週報
- 每月末生成月報    # 月報
```

## 日報模板

```markdown
# 📋 工作日報 - 2026-03-06

## 📊 今日概覽

| 指標 | 數值 |
|------|------|
| 提交次數 | 5 |
| 任務完成 | 3/8 |
| 程式碼變更 | +350/-80 |
| 郵件處理 | 收 12 / 發 3 |
| 生產力得分 | 78.5 |

## ✅ 已完成任務
- 完成日報生成器技能開發
- 配置飛書頻道推送
- 測試心跳觸發功能

## 🔄 進行中任務
- 編寫開發文件
- 新增週報聚合功能

## 💻 程式碼提交

| 時間 | 提交資訊 | 變更 |
|------|----------|------|
| 09:30 | feat: 新增日報生成功能 | +120/-30 |
| 14:15 | fix: 修復郵件採集bug | +45/-12 |

## 📧 郵件概況
- 今日收件: 12 封
- 今日發件: 3 封
- 未讀郵件: 2 封

## 📈 趨勢對比
- 提交: ↑ 2 次
- 效率: ↑ 5.2 分

## 💡 工作建議
1. 專注度較低，建議減少干擾
2. 任務完成率有待提高

## 🔜 明日計劃
- 完善日報模板
- 新增週報聚合功能
```

## 配置說明

### Git 倉庫配置

本專案監控的 Git 倉庫（指令碼會自動讀取）：

```
倉庫路徑: D:/Download/jiuwenclaw
```

指令碼透過 `git log` 命令採集以下資料：
- 提交雜湊、提交資訊、作者、時間
- 每次提交的檔案變更數、新增行數、刪除行數

### 郵箱配置

在 `.env` 檔案中配置（本專案實際配置）：

```env
EMAIL_ADDRESS=
EMAIL_TOKEN=
EMAIL_PROVIDER=163
```

**注意**：`EMAIL_TOKEN` 是郵箱授權碼，不是登入密碼。
獲取方式：登入163郵箱 → 設定 → POP3/SMTP/IMAP → 開啟IMAP服務 → 獲取授權碼

### 心跳配置

```yaml
heartbeat:
  every: 3600
  target: feishu
  active_hours:
    start: 18:00
    end: 18:30
```

## API 參考

### 資料採集器

```python
from collectors import DataAggregator

aggregator = DataAggregator(
    workspace_dir="~/.jiuwenclaw/agent",
    git_repo="path/to/repo",
    email_config={
        "address": "xxx@163.com",
        "auth_code": "xxx",
        "provider": "163"
    }
)

# 採集今日資料
data = aggregator.collect()

# 採集一週資料
week_data = aggregator.collect_week()
```

### 工作分析器

```python
from analyzers import WorkAnalyzer

analyzer = WorkAnalyzer()
result = analyzer.analyze(data.to_dict())

print(f"生產力得分: {result.metrics.productivity_score}")
print(f"關鍵詞: {result.keywords}")
print(f"建議: {result.suggestions}")
```

### 報告生成器

```python
from generators import ReportGenerator

generator = ReportGenerator(aggregator)

# 生成日報
daily = generator.generate_daily()

# 生成周報
weekly = generator.generate_weekly()

# 生成月報
monthly = generator.generate_monthly(2026, 3)
```

## 注意事項

1. **Git 倉庫**: 確保倉庫路徑正確且有訪問許可權
2. **郵箱授權**: 使用授權碼而非登入密碼
3. **心跳時間**: 修改後需重啟服務
4. **資料儲存**: 報告儲存到 `~/.jiuwenclaw/agent/reports/`

## 更新日誌

- **v2.1.0** (2026-03-10): 新增 AI 智慧分析功能（智慧摘要、 明日計劃建議、 工作模式分析）
- **v2.0.0** (2026-03-06): 進階版，支援多資料來源、趨勢對比、週報月報
- **v1.0.0** (2026-03-06): 初始版本，基礎日報生成
