---
name: cross-channel-history-retrieval
description: >-
  跨會話檢索聊天原文（記憶不足時再用）。在回答任何關於歷史事件、日期、人物、過去對話的問題時，如果記憶中沒有相關資訊或不足以回答，則需要使用跨會話檢索聊天原文。用 mcp_exec_command 執行 scripts/search_history.py，讀 ~/.jiuwenclaw/agent/sessions/*/history.json。支援 channel、session_id、關鍵詞、時間窗。如果搜尋結果不足，嘗試用不同的關鍵詞再次搜尋。
allowed_tools: [mcp_exec_command]
---

# 跨頻道歷史檢索

用於從 `~/.jiuwenclaw/agent/sessions/<session_id>/history.json` 中檢索歷史訊息，並把命中結果整理為可直接貼上進當前上下文的文字塊。

## 何時使用

- 使用者提到“其他頻道/會話”的聊天內容，或在 **A 頻道問自己在 B 頻道（如網頁）說過什麼**
- **「今天/剛才我問了什麼」「關於某某我提過什麼問題」** 且當前會話裡看不到原文
- 使用者給出關鍵詞，要求回溯某時間段對話
- 使用者要求把檢索結果“帶到當前上下文裡”

## 執行方式

必須使用 `mcp_exec_command` 執行指令碼，不要只口頭總結。

```bash
python ~/.jiuwenclaw/agent/skills/cross-channel-history-retrieval/scripts/search_history.py --channel feishu --query "報銷 審批" --start "2026-03-26 09:00" --end "2026-03-26 18:00" --limit 30
```

（Windows：**`--channel` / `--query` / 時間引數等與 Unix 相同**；預設 `mcp_exec_command` 走 **cmd**，**cmd 不會展開 `~`**，不要用 `~/.jiuwenclaw`，應寫 `python %USERPROFILE%\.jiuwenclaw\agent\skills\cross-channel-history-retrieval\scripts\search_history.py` 再接同樣引數。若整條命令在 PowerShell 裡執行，`~` 一般會展開，也可用 `$env:USERPROFILE\...`。）

## 引數說明

- `--channel`：按頻道過濾（如 `feishu` / `dingtalk` / `web`）。如果使用者在語言裡沒有指明 channel，則不要傳 `--channel`，指令碼會掃描所有會話。
- `--session-id`：只檢索指定會話（優先順序高於 `--channel`）
- `--query`：空格分詞關鍵詞（例如 `"合同 審批"`）
- `--keyword`：可重複傳入多個精確關鍵詞
- `--start`、`--end`：顯式時間範圍，格式支援
  - `YYYY-MM-DD`
  - `YYYY-MM-DD HH:MM`
  - `YYYY-MM-DD HH:MM:SS`
  - ISO8601（如 `2026-03-26T10:30:00+08:00`）
- `--at`：某個時間點，配合 `--window-minutes` 形成檢索窗
- `--window-minutes`：視窗大小（預設 120 分鐘）
- `--timezone`：預設 `Asia/Shanghai`
- `--limit`：返回命中上限（預設 20）
- `--max-sessions`：最多掃描會話數量（預設 200）
- `--auto-expand`：無命中時自動擴大時間窗重試（預設開啟）

## 時間策略

1. 若使用者明確給了開始/結束時間，優先使用。
2. 若僅給“某時刻”，使用 `--at + --window-minutes`。
3. 若使用者沒給時間，使用預設視窗（最近 24 小時）。
4. 若初次無結果且 `--auto-expand` 開啟，自動擴充套件為最近 72 小時再試一次。

## 輸出與上下文注入

指令碼**第一行**固定輸出 `SKILL=cross-channel-history-retrieval`，便於在 `mcp_exec_command` 回顯或日誌裡 `grep` 確認本 skill 的指令碼已執行。

指令碼會輸出兩個區塊：

- `HISTORY_SEARCH_SUMMARY`：統計資訊與最終時間窗
- `HISTORY_CONTEXT_BLOCK`：可直接放入當前對話上下文的命中訊息片段

拿到指令碼輸出後，你應當：

1. 在回覆中簡要說明檢索範圍和命中情況。
2. 把 `HISTORY_CONTEXT_BLOCK` 中的內容原樣（或輕度裁剪）貼到當前回覆裡，作為上下文依據。
3. 若無命中，明確說明已檢索的時間窗、頻道/會話和關鍵詞，並詢問是否放寬條件。
