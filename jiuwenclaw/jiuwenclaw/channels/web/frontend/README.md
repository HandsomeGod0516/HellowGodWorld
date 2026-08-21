# OpenJiuwen Web 前端

基於 React + TypeScript + Tailwind CSS 構建的 AI 程式設計助手 Web 介面，設計風格參考 JiuwenClaw。

## 功能特性

### 已實現功能

#### 💬 聊天互動
- **實時對話**：WebSocket 雙向通訊，支援流式輸出
- **Markdown 渲染**：支援程式碼高亮、列表、連結等格式
- **思考動畫**：AI 思考時顯示動態指示器
- **訊息歷史**：顯示使用者和助手的對話記錄

#### 🛠 工具呼叫
- **工具執行視覺化**：顯示 AI 呼叫的工具名稱和引數
- **執行結果展示**：顯示工具執行成功/失敗狀態和返回結果
- **可用工具列表**：右側面板顯示當前可用的工具

#### 📋 任務管理
- **Todo 列表**：顯示 AI 建立的任務列表
- **狀態分組**：按進行中、待處理、已完成分組顯示
- **實時更新**：任務狀態變化實時同步

#### 📂 會話管理
- **會話列表**：側邊欄顯示歷史會話
- **會話切換**：點選切換不同會話
- **會話刪除**：懸停顯示刪除按鈕，支援刪除會話
- **會話持久化**：重新整理頁面自動恢復上次會話

#### ⚙️ 模式切換
- **BUILD 模式**：預設編碼模式
- **PLAN 模式**：規劃模式
- **REVIEW 模式**：審查模式

#### 🎨 主題支援
- **淺色主題**：預設，藍色基調
- **深色主題**：深色背景，最佳化藍色可見度
- **系統跟隨**：可選跟隨系統主題

#### ⏯ 流程控制
- **暫停/繼續**：暫停和恢復 AI 處理
- **中斷**：中斷當前任務，可附加新指令

#### 🎤 語音互動
- **語音輸入**：點選麥克風按鈕進行語音輸入（STT）
- **語音朗讀**：滑鼠懸停在 AI 回覆上顯示朗讀按鈕（TTS）
- **打斷演示**：語音輸入時可隨時打斷 AI 處理

## 技術棧

- **框架**：React 18 + TypeScript
- **樣式**：Tailwind CSS + CSS Variables
- **狀態管理**：Zustand
- **構建工具**：Vite
- **通訊**：WebSocket + REST API

## 快速開始

### 環境要求

- Node.js 18+
- npm 或 pnpm

### 安裝依賴

```bash
cd jiuwenclaw/channels/web/frontend
npm install
```

### 配置後端地址

編輯 `vite.config.ts` 中的 proxy 配置：

```typescript
proxy: {
  '/api': {
    target: 'http://127.0.0.1:19000',  // 修改為你的後端地址
    changeOrigin: true,
  },
  '/ws': {
    target: 'http://127.0.0.1:19000',  // 修改為你的後端地址
    ws: true,
    changeOrigin: true,
  },
}
```

### 啟動開發伺服器

```bash
npm run dev
```

訪問 http://localhost:5173

### 啟動後端

```bash
cd jiuwenclaw
PORT=19000 python -m jiuwenclaw.api.app
```

## 後端 API 要求

前端依賴以下後端介面：

### REST API

| 端點 | 方法 | 說明 |
|------|------|------|
| `/api/config` | GET | 獲取服務配置（provider, model 等） |
| `/api/sessions` | GET | 獲取會話列表 |
| `/api/sessions/:id` | DELETE | 刪除會話 |

### WebSocket

連線地址：`/ws`

說明：
- 前端當前透過統一 WS 端點進行 RPC 通訊（`req/res/event`）
- `session_id` 透過請求體 `params.session_id` 傳遞，不在 URL 路徑中傳遞
- `provider` 預設由後端配置決定；僅在需要覆蓋後端預設配置時，才透過 query 傳遞

#### 客戶端 → 服務端訊息

統一請求幀（示例）：

```json
{
  "type": "req",
  "id": "req_xxx",
  "method": "chat.send",
  "params": {
    "session_id": "sess_xxx",
    "content": "hello"
  }
}
```

常用 `method`：

| method | 說明 |
|------|------|
| `chat.send` | 傳送聊天訊息 |
| `chat.interrupt` | 中斷/暫停任務 |
| `chat.resume` | 恢復任務 |
| `chat.user_answer` | 提交使用者問答結果 |
| `session.list` | 獲取會話列表 |
| `config.get` | 獲取服務配置 |

#### 服務端 → 客戶端訊息

請求響應幀（`res`）：

```json
{
  "type": "res",
  "id": "req_xxx",
  "ok": true,
  "payload": {}
}
```

事件推送幀（`event`）：

```json
{
  "type": "event",
  "event": "chat.delta",
  "payload": {
    "session_id": "sess_xxx"
  }
}
```

常見事件：
- `connection.ack`
- `chat.delta`
- `chat.final`
- `chat.tool_call`
- `chat.tool_result`
- `todo.updated`
- `chat.processing_status`
- `chat.interrupt_result`
- `chat.subtask_update`
- `chat.ask_user_question`

## Dev 模式 WS 日誌

`npm run dev` 時，前端會把 `/ws` 的請求與響應寫入本地日誌檔案，用於排查通訊問題：

- 日誌檔案：`jiuwenclaw/channels/web/frontend/logs/ws-dev.log`
- 每行一條 JSON（JSONL）
- 記錄方向：
  - `payload.direction = "outgoing"`：前端傳送的 `req`
  - `payload.direction = "incoming"`：後端返回的 `res/event`
  - `payload.direction = "lifecycle"`：連線生命週期（open/error/close）

示例：

```json
{"ts":"2026-02-12T08:10:05.120Z","payload":{"direction":"outgoing","messageType":"req","data":{"type":"req","id":"req_xxx","method":"chat.send","params":{"session_id":"sess_001","content":"你好"}},"at":"2026-02-12T08:10:05.119Z"}}
{"ts":"2026-02-12T08:10:05.150Z","payload":{"direction":"incoming","messageType":"res","data":{"type":"res","id":"req_xxx","ok":true,"payload":{"accepted":true}},"at":"2026-02-12T08:10:05.149Z"}}
{"ts":"2026-02-12T08:10:05.300Z","payload":{"direction":"incoming","messageType":"event","data":{"type":"event","event":"chat.delta","payload":{"session_id":"sess_001","content":"..."}},"at":"2026-02-12T08:10:05.299Z"}}
```

若只看到 `lifecycle error/close (code=1006)`，通常表示後端未啟動或 WS 埠不可達。

## 專案結構

```
jiuwenclaw/channels/web/frontend/
├── public/
│   └── logo.png           # 應用 Logo
├── src/
│   ├── components/
│   │   ├── ChatPanel/     # 聊天面板
│   │   ├── SessionSidebar/# 會話側邊欄
│   │   ├── StatusBar/     # 狀態列
│   │   ├── TodoList/      # 任務列表
│   │   └── ToolPanel/     # 工具面板
│   ├── hooks/
│   │   └── useWebSocket.ts# WebSocket Hook
│   ├── stores/
│   │   ├── chatStore.ts   # 聊天狀態
│   │   ├── sessionStore.ts# 會話狀態
│   │   └── todoStore.ts   # Todo 狀態
│   ├── types/             # TypeScript 型別定義
│   ├── utils/             # 工具函式
│   ├── App.tsx            # 主應用元件
│   ├── index.css          # 全域性樣式 + CSS 變數
│   └── main.tsx           # 入口檔案
├── index.html
├── tailwind.config.js
├── vite.config.ts
└── package.json
```

### 建議優先實現

1. **側邊欄詳情面板** - 檢視工具呼叫的完整輸出
2. **聊天附件** - 支援上傳檔案
3. **Config 配置** - 視覺化配置編輯
4. **Logs 日誌** - 除錯和問題排查必備
5. **Exec Approval** - 安全相關，防止危險命令執行

## 自定義配置

### 修改品牌

1. 替換 `public/logo.png`
2. 修改 `index.html` 中的 `<title>`
3. 修改 `src/App.tsx` 中的品牌文字

### 修改主題顏色

編輯 `src/index.css` 中的 CSS 變數：

```css
:root {
  --accent: #60a5fa;        /* 深色模式主色 */
  --accent-hover: #93c5fd;  /* 懸停色 */
}

:root[data-theme="light"] {
  --accent: #2563eb;        /* 淺色模式主色 */
  --accent-hover: #3b82f6;  /* 懸停色 */
}
```

## License

MIT
