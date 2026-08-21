<div align="center">

# JiuwenClaw

> 隨叫隨到的智慧管家，讓AI觸手可及

[![Python Version](https://img.shields.io/badge/python-3.11%2C3.12%2C3.13-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)
[![華為雲MaaS](https://img.shields.io/badge/華為雲-MaaS-red)](https://www.huaweicloud.com/)

</div>

## 🌟 專案簡介

**JiuwenClaw** 是一款基於Python開發的智慧AI Agent，正如其名——"Claw"象徵著精準的抓取與連線。它能夠將大語言模型的強大能力，透過你日常使用的各類通訊應用，直接延伸至你的指尖。

### ✨ 核心特色

- **生態相容**：完美支援**華為雲MaaS**等主流模型平臺
- **無縫對接**：與**小藝開放平臺**無縫接入，華為手機使用者可透過小藝直接喚醒
- **靈活部署**：支援自託管部署，資料完全自主可控
- **多端接入**：支援Web端、聊天軟體等多種互動方式

## 🎯 核心理念

> **懂你所想，自主演進**

### 🤝 貼身任務管家
面對複雜的輸入場景——任務追加、指令打斷、需求修改，JiuwenClaw都能精準理解，為你智慧排期，有條不紊地完成任務。

### 🔄 自主演進
當你表達不滿或執行出錯時，它會根據你的反饋自動調整相應技能，持續演進，全心全意為你服務。

<p align="center">
  <strong>⚡ 一個始終線上、資料自主的專屬AI助理 ⚡</strong>
</p>

## ⚠️ 版本升級提醒

如果您從舊版本升級，請檢視更新日誌確認是否有重大變更。如有重大變更，升級後**必須**重新初始化 JiuwenClaw，否則服務將無法啟動。

### 升級前備份資料

| 資料型別 | 原路徑 | 說明 |
|---------|--------|------|
| 記憶資料 | `.jiuwenclaw/workspace/agent/memory` | 所有對話記憶 |
| 自定義技能 | `.jiuwenclaw/workspace/agent/skills` | 您的自定義技能 |
| 配置檔案 | `.jiuwenclaw/config` | 應用設定 |

### 資料遷移步驟

升級並執行 `jiuwenclaw-init` 後，請手動遷移資料：

1. **遷移記憶資料**：將原目錄下的 `.jiuwenclaw/workspace/agent/memory` 複製到 `.jiuwenclaw/agent/memory`

2. **遷移技能資料**：將原目錄下的 `.jiuwenclaw/workspace/agent/skills` 複製到 `.jiuwenclaw/agent/skills`

## 🚀 快速上手

### 📦 安裝

```bash
# 安裝 JiuwenClaw
pip install jiuwenclaw

# 初始化 JiuwenClaw (首次啟動)
jiuwenclaw-init

# 啟動 JiuwenClaw
jiuwenclaw-start

# 安裝 JiuwenClaw-tui
pip install jiuwenclaw-tui

# 啟動 JiuwenClaw-tui
jiuwenclaw-tui
```

### 💬 使用方式

#### 1️⃣ 對話模式

| 方式 | 說明                                        |
|------|-------------------------------------------|
| **Web前端** | 啟動服務後訪問 `http://localhost:5173`，透過瀏覽器直接對話 |
| **小藝頻道** | 華為手機使用者可直接喚醒小藝，與JiuwenClaw對話               |
| **飛書頻道** | 完成渠道配置後，在飛書中與JiuwenClaw暢聊                 |

#### 2️⃣ 配置模型

在 Web 頁面左側找到「配置資訊」，進入配置頁面：

![](docs/assets/images/jiuwenclaw_configuration_Info.png)

完善以下四項基本配置，完成後點選右上角「儲存」：

![](docs/assets/images/jiuwenclaw_config_api.png)

#### 3️⃣ 開始對話

在 Web 頁面左側找到「對話」，輸入問題即可開始：

![](docs/assets/images/jiuwenclaw_example.png)

#### 4️⃣ 會話管理

點選下方的「+」號，可清空當前會話並開啟新會話：

![](docs/assets/images/jiuwenclaw_new_session.png)

清理後頁面顯示：

![](docs/assets/images/jiuwenclaw_clear_session.png)

#### 5️⃣ 定時任務

設定心跳任務，填寫待辦事項，JiuwenClaw即可定時被喚醒，自動執行預設任務。讓你的日程管理更加智慧高效！

#### 6️⃣ 清空記憶

當你需要讓 JiuwenClaw 忘記之前的所有對話歷史和使用者資訊時，可以清空記憶檔案。

**適用場景：**
- **隱私保護**：清除包含敏感資訊的歷史記錄
- **全新開始**：開始一個完全不同的專案或話題，避免歷史資訊干擾
- **除錯排錯**：記憶檔案損壞或內容異常時重置
- **使用者切換**：多使用者共用環境時，清除上一個使用者的資訊

**清空記憶操作步驟：**

記憶檔案儲存在 `{workspace_dir}/memory/` 目錄下：

**方式一：透過 Agent 刪除**
直接告訴 JiuwenClaw："請刪除所有記憶檔案" 或 "清空我的記憶"，Agent 會呼叫檔案工具刪除 memory 目錄下的檔案。
![](docs/assets/images/jiuwenclaw_delete_memory.png)

**方式二：手動刪除**
停止 JiuwenClaw 服務後，直接刪除 `memory/` 目錄下的所有 Markdown 檔案即可。
![](docs/assets/images/jiuwenclaw_memory.png)

> ⚠️ **注意**：清空記憶後無法恢復，請謹慎操作。建議定期備份重要的記憶檔案。

## 📚 文件導航

| 文件 | 核心內容 |
|:-----|:---------|
| [📖 安裝指南](docs/zh/安裝指南.md) | 從零安裝（pip、原始碼、conda、Docker 等） |
| [📖 快速開始](docs/zh/Quickstart.md) | 5分鐘上手JiuwenClaw |
| [📖 快速開始(TUI)](docs/zh/Quickstart_tui.md) | 5分鐘上手JiuwenClaw-tui |
| [⚙️ 配置與工作空間](docs/zh/配置資訊.md) | 環境配置與工作區管理 |
| [📁 工作區結構](docs/zh/智慧體.md) | workspace 目錄說明，預置與動態生成內容 |
| [🔄 模式系統](docs/zh/模式系統.md) | PLAN / AGENT / CODE / TEAM 模式切換與配置 |
| [🛠️ 技能系統](docs/zh/技能.md) | 自定義技能開發指南 |
| [🔄 Skill自演進](docs/zh/Skill自演進.md) | Skill自演進機制 |
| [📱 頻道配置](docs/zh/頻道.md) | 飛書、小藝等頻道接入 |
| [💬 Discord](docs/zh/Discord.md) | Discord頻道配置與使用 |
| [💬 WhatsApp](docs/zh/whatsapp.md) | WhatsApp頻道配置與使用 |
| [⌨️ 命令列指令](docs/zh/命令列指令.md) | 命令列工具使用指南 |
| [⏰ 定時任務](docs/zh/定時任務.md) | 定時任務管理 |
| [💓 心跳](docs/zh/心跳.md) | 心跳機制與配置 |
| [🧠 記憶功能](docs/zh/記憶.md) | 智慧記憶與學習 |
| [💡 經驗記憶](docs/zh/經驗記憶.md) | 任務級經驗檢索與沉澱 |
| [📦 上下文壓縮](docs/zh/上下文壓縮解除安裝.md) | 上下文壓縮與解除安裝 |
| [💻 編碼記憶](docs/zh/編碼記憶.md) | Code模式專屬記憶系統 |
| [📋 任務規劃](docs/zh/任務規劃.md) | 任務規劃與待辦事項 |
| [🌐 瀏覽器相關](docs/zh/瀏覽器.md) | 自動化瀏覽功能 |
| [🔌 MCP配置](docs/zh/MCP配置.md) | MCP服務接入與配置 |
| [🔒 工具許可權與安全](docs/zh/工具許可權與安全防護.md) | 許可權模型與安全配置 |
| [📝 Slash命令](docs/zh/Slash命令表.md) | Slash命令速查 |
| [🏗️ Slash命令架構](docs/zh/SLASH_COMMAND_ARCHITECTURE.md) | Slash命令內部機制與擴充套件 |
| [📨 E2A協議](docs/zh/E2A-protocol.md) | Gateway ↔ Agent 請求信封規範 |
| [🤝 A2A接入](docs/zh/A2A.md) | A2A協議接入說明 |
| [🔌 ACP外掛配置](docs/zh/ACP外掛使用.md) | ACP客戶端外掛配置 |
| [👥 分散式Team](docs/zh/分散式Team.md) | 多程序分散式團隊模式 |
| [🔀 單機多例項](docs/zh/單機多例項執行.md) | 同一機器執行多個獨立例項 |
| [📦 打包桌面應用](docs/zh/打包exe指南.md) | 打包獨立桌面可執行檔案 |
| [🚀 開發實踐](docs/zh/開發實踐/) | 開發實踐與經驗分享 |

## 🤝 參與貢獻

我們熱烈歡迎社群貢獻！無論是提交Bug、提出新功能建議，還是完善文件，都是對專案的寶貴支援。

1. Fork 本倉庫
2. 建立您的特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交您的改動 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 開啟一個 Pull Request

## 📄 開源協議

本專案採用 **Apache License 2.0** 開源協議，詳情請參閱 [LICENSE](LICENSE) 檔案。

---

<p align="center">
  <strong>讓智慧觸手可及，讓生活更加簡單</strong><br>
  <sub>✨ JiuwenClaw —— 您的專屬AI助理 ✨</sub>
</p>