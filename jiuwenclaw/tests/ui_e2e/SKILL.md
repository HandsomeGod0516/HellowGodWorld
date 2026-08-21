---
name: ui_e2e
description: 執行 JiuwenClaw Web UI 端到端測試並收集截圖、日誌、report.md、report.json。用於驗證 Todo 和 Cron Web UI 流程、復現瀏覽器互動問題、選擇執行直譯器、準備 Playwright 環境，或返回可操作的失敗證據時。
---

# UI E2E

複用本目錄現成指令碼，不要臨時重寫瀏覽器測試流程。

## 使用指令碼

- `todo_ui_report.py`：驗證待辦建立、狀態更新、Tool Panel 展示。
- `cron_ui_report.py`：驗證定時任務面板、結構化提醒、預覽、立即執行、開關、刪除。
- `run_suite.py`：順序執行多個場景並彙總結果。

## 準備環境

- 選擇用於啟動 `jiuwenclaw.app` 和 `jiuwenclaw.app_web` 的 Python 直譯器。
- 在該直譯器裡安裝專案依賴和 `.[e2e]`。
- 確保 `jiuwenclaw/channels/web/frontend` 已安裝前端依賴。
- 確保本機可用 Chrome/Chromium；沒有時再安裝 Playwright 瀏覽器。

常用命令：

```bash
export JIUWENCLAW_E2E_PYTHON=.venv/bin/python
"$JIUWENCLAW_E2E_PYTHON" -m pip install -e ".[e2e]"
"$JIUWENCLAW_E2E_PYTHON" -m playwright install chromium
```

## 直譯器選擇

1. `--runtime-python`
2. 環境變數 `JIUWENCLAW_E2E_PYTHON`
3. `./.venv/bin/python`
4. 當前直譯器

優先使用倉庫自己的虛擬環境，不要硬編碼個人機器路徑。

## 執行

執行完整套件：

```bash
python3 -m tests.ui_e2e.run_suite --build
```

執行單個場景：

```bash
python3 tests/ui_e2e/todo_ui_report.py --build
python3 tests/ui_e2e/cron_ui_report.py --build
```

指定直譯器或輸出目錄時，顯式傳參：

```bash
python3 -m tests.ui_e2e.run_suite \
  --build \
  --runtime-python "$JIUWENCLAW_E2E_PYTHON" \
  --report-root /tmp/ui-e2e-suite
```

```bash
python3 tests/ui_e2e/cron_ui_report.py \
  --build \
  --runtime-python "$JIUWENCLAW_E2E_PYTHON" \
  --report-dir /tmp/cron-ui-report
```

預設使用臨時 `HOME` 做冒煙驗證；只有確認真實工作區行為時，再顯式傳入真實 `--home`。

## 產物

- `report.md`
- `report.json`
- `backend.log`
- `ui.log`
- 若干截圖

預設產物目錄在 `tests/ui_e2e/artifacts/`。

## 場景

- `todo_ui_report.py`：啟動真實 `jiuwenclaw.app`，驗證待辦工具鏈和 Tool Panel。
- `cron_ui_report.py`：啟動真實 `jiuwenclaw.app`，驗證 Cron 面板和結構化提醒。

## 輸出結論

- 實際執行的命令
- 使用的執行時直譯器
- 報告目錄
- 每個場景的透過或失敗狀態
- 第一處可操作的失敗資訊
- 對應證據檔名，例如截圖或日誌
