---
name: ascend-moe-optimizer-trace-analyzer
description: 在使用者提供 Chrome/Perfetto trace.json、或排查 Ascend 上 MoE/FusedDeepMoe 等運算元效能時使用。按 phase、category、core group、tid 統計耗時、overlap、bubble，輸出 CSV、Markdown 報告與確定性診斷；可選外部 LLM 擴寫分析。預設 phase 對映面向 UMDK FusedDeepMoe，其它 trace 需替換或擴充套件 config/phase_map.yaml。
---

# Ascend MoE 效能 Trace 分析

分析 Chrome/Perfetto 風格的 `trace.json`，把原始 trace event 轉換為結構化統計表、圖表和 Markdown 報告，用於替代人工在 Perfetto 中做第一輪耗時分佈和瓶頸定位。本 skill 的內建名稱為 `ascend-moe-optimizer-trace-analyzer`；當前目錄為 `ascend-moe-optimizer-trace-analyzer`。

## 何時使用

- 使用者需要分析 **運算元或 runtime 打點** 匯出的 **Chrome/Perfetto `trace.json`**，關注 **phase 分佈、category、Ascend core group、執行緒 tid、overlap、bubble**。
- 調優 **Ascend 上 MoE / FusedDeepMoe（如 `fused_deep_moe`）** 或需沿用本倉庫預設 `config/phase_map.yaml` 的場景。
- 需要 **確定性自動診斷**，或可選的 **`--llm-analysis`** 二次解讀。

## 指令碼位置

- 使用者安裝後的 skill 根目錄：`<ASCEND_MOE_OPTIMIZER_SKILL>` = `~/.jiuwenclaw/agent/jiuwenclaw_workspace/skills/ascend-moe-optimizer-trace-analyzer`
- 入口：`<ASCEND_MOE_OPTIMIZER_SKILL>/app.py`
- 從本倉庫資源執行時，將上述路徑換為 `jiuwenclaw/resources/agent/jiuwenclaw_workspace/skills/ascend-moe-optimizer-trace-analyzer`（相對倉庫根目錄）。

執行命令前請先 `cd` 到 `<ASCEND_MOE_OPTIMIZER_SKILL>`，或使用下文絕對路徑形式的 `python3 .../app.py`。

## 能力概覽

本 skill 面向的核心物件是 `trace.json`，不是某一個固定運算元。它本身負責：
- 解析 trace 中的完整區間事件。
- 將原始 trace name 對映為可穩定統計的 phase。
- 按 phase、category、core group、tid、raw name 聚合耗時。
- 計算 phase overlap 和外層階段 bubble。
- 生成統計圖、文字化統計摘要和 Markdown 報告。
- 生成穩定、可復現的自動診斷。
- 可選呼叫外部 LLM，把統計上下文擴寫成專家分析段落。

當前倉庫預設攜帶的 `config/phase_map.yaml` 和部分診斷規則來自 UMDK FusedDeepMoe trace 的實踐經驗。因此，預設配置對 FusedDeepMoe 最友好；如果要分析其他來源的 trace，應替換或擴充套件 phase/category 對映配置，並逐步沉澱對應領域的診斷規則。

## Agent 執行原則
執行本 skill 時，agent 不應把文件中的示例路徑當成固定輸入。應先從使用者請求或當前工作區中確認以下上下文，並把它們替換到命令中：

- `TRACE_JSON`：必需，使用者要分析的 trace 檔案。
- `OUTPUT_DIR`：必需或由 agent 選擇，建議按本次任務命名，例如 `output/<case_name>`。
- `PHASE_MAP`：可選，phase/category 對映配置。若使用者指定運算元或已有對應配置，應使用對應配置；否則使用預設 `config/phase_map.yaml`。
- `SOURCE_ROOT`：可選，運算元原始碼工程目錄，例如某個 UMDK 工程。當前 CLI 尚未消費該引數，但 agent 可以用它閱讀原始碼、理解打點語義和輔助維護 phase map。
- `OPERATOR`：可選，使用者指定的運算元名，例如 `fused_deep_moe`。當前 CLI 尚未消費該引數，但 agent 應用它選擇或維護對應的 phase/category 規則和診斷上下文。

如果使用者只提供 `trace.json`，按 trace-only 模式分析。如果使用者同時提供原始碼目錄和運算元名，agent 應先閱讀相關原始碼打點，再決定是否需要補充或調整 `PHASE_MAP`。

## 執行命令

在 `<ASCEND_MOE_OPTIMIZER_SKILL>` 目錄下執行（以下 `<ASCEND_MOE_OPTIMIZER_SKILL>` 含義見「指令碼位置」）：

基礎命令模板：

```bash
cd <ASCEND_MOE_OPTIMIZER_SKILL>
python3 app.py \
  --trace <TRACE_JSON> \
  --phase-map <PHASE_MAP> \
  --output-dir <OUTPUT_DIR>
```

常用引數：
- `--trace PATH`：輸入 trace JSON，必填。
- `--phase-map PATH`：phase/category 對映配置，預設 `config/phase_map.yaml`。
- `--output-dir DIR`：輸出目錄，預設 `output`。
- `--top-n 20`：控制 `report.md` 中各表展示的行數。
- `--llm-analysis`：啟用 LLM Analysis 章節。
- `--llm-command "<cmd>"`：外部 LLM 命令，命令從 stdin 讀取 prompt，並把分析文字寫到 stdout。
- `--llm-timeout 120`：LLM 命令超時時間，單位秒。

如果使用預設 phase map，可以省略 `--phase-map`：

```bash
cd <ASCEND_MOE_OPTIMIZER_SKILL>
python3 app.py \
  --trace <TRACE_JSON> \
  --output-dir <OUTPUT_DIR>
```

如果本機安裝了 `matplotlib`，執行時會預設生成統計分析總圖 `analysis_charts.png`，並嵌入 `report.md`。未安裝時會跳過圖表，其他輸出不受影響。

LLM 命令也可以用環境變數配置：

```bash
export TRACE_ANALYSIS_LLM_CMD="<your-llm-cli>"
cd <ASCEND_MOE_OPTIMIZER_SKILL>
python3 app.py \
  --trace <TRACE_JSON> \
  --phase-map <PHASE_MAP> \
  --output-dir <OUTPUT_DIR> \
  --llm-analysis
```

如果未啟用 `--llm-analysis`，仍會生成 `llm_prompt.md`，方便後續手動交給 Codex 或其他模型複核。

## 輸入要求
支援兩種 trace 檔案外層格式：
- `{ "traceEvents": [...] }`
- 直接以事件陣列 `[...]` 作為檔案內容

支援的事件型別：
- `ph == "X"`：完整區間事件，直接使用 `ts + dur` 得到結束時間。
- `ph == "B" / "E"`：按 `(pid, tid, name)` 棧式配對為完整區間。

每個可分析事件至少應包含：
- `name`：事件名稱。
- `ts`：開始時間或 B/E 時間戳。
- `dur`：僅 `X` 事件需要。
- `pid` / `tid`：程序和執行緒維度，建議保留。
- `args`：可選，若包含 `core_type/core_id/rank_id/extra_id/event_id` 等欄位，報告會一併保留。

不匹配 `--phase-map` 的事件當前不會進入 phase 統計表。分析非預設 trace 時，最重要的適配工作就是維護一份能覆蓋目標 trace name 的 phase mapping。

分析時會同時保留：
- `name`：原始 trace name。
- `normalized_name`：去掉 `[extra:x] #seq` 後的歸一化名稱，便於把同一類事件合併統計。

## Phase 和 Category

本 skill 透過 `--phase-map` 指定的 YAML 配置把原始 trace name 對映到穩定 phase。配置包含兩類資訊：
- `phases`：phase 到正則 pattern 列表的對映。
- `phase_categories`：phase 到 category 的歸因。

正則命中多個 phase 時，優先選擇 pattern 字串最長的更具體規則。

預設 category 包括：
- `container`
- `wait`
- `sync`
- `compute`
- `epilogue`
- `communication`
- `quant`
- `init`
- `cleanup`
- `other`

對於 UMDK FusedDeepMoe，預設配置已經覆蓋 `processing`、`dispatch_gmm1`、`gmm2_combine` 及其子階段。對於其他 trace，可以保留這套統計框架，只替換 phase/category 對映。

## Core Group

本 skill 會盡量為每個已對映事件補充：
- `core_type`
- `core_group`
- `core_kind`
- `core_id`

當前內建的核組解釋來自 UMDK 1C2V trace：
- `type0 -> cube`
- `type1 -> vector_recv`
- `type2 -> vector_send`

如果 trace event args 中沒有 `core_type/core_id`，本 skill 會嘗試從 `tid` 推斷，例如 `type1_core003 -> vector_recv/core_id=3`。

對於其他來源的 trace，如果沒有這類 `core_type` 約定，事件會落到 `unknown` 核組。後續若要支援更多硬體或 runtime，可以把 core group 規則從當前內建邏輯中抽成配置。

## 指標口徑
- `total_us`：同類事件時長直接求和，會重複累計並行 tid/core。
- `union_us`：同類事件時間區間並集長度，更接近 wall time 覆蓋。
- `ratio_to_total_wall = union_us / trace_wall_time`。
- `ratio_to_core_group_wall = union_us / 當前 core_group 的 union_us`，用於判斷某類耗時在該核組內部的覆蓋比例。
- `ratio_to_core_group_wall` 是覆蓋率，不是互斥佔比；不同 category/phase 可以在同一時間重疊，因此同一核組下的百分比不要求加和為 100%。
- `overlap_summary.csv` 的 overlap 基於 phase 區間並集兩兩求交，避免逐事件重複累計。
- `bubble_summary.csv` 表示外層階段中未被已知子階段覆蓋的時間空洞。這是“未歸因時間”，不一定代表硬體空閒。

## 輸出檔案
- `phase_instances.csv`：每個已對映區間事件，包含 phase/category/name/core_group/core_id/timing。
- `phase_summary.csv`：按 phase 聚合。
- `category_summary.csv`：按 category 聚合。
- `core_group_summary.csv`：按 core group 聚合。
- `phase_core_group_summary.csv`：按 `(core_group, phase)` 聚合。
- `category_core_group_summary.csv`：按 `(core_group, category)` 聚合。
- `name_summary.csv`：按原始 trace name 聚合。
- `phase_tid_summary.csv`：按 `(phase, pid, tid)` 聚合，用於看單執行緒或單核長尾。
- `overlap_summary.csv`：phase 兩兩 overlap。
- `bubble_summary.csv`：外層階段內部 bubble。
- `summary.json`：整體概覽。
- `diagnosis.json`：確定性自動診斷結果。
- `statistical_summary.md`：確定性統計摘要，文字化說明圖表和關鍵統計訊號。
- `llm_prompt.md`：交給 LLM 的完整統計上下文，總是生成。
- `llm_analysis_meta.json`：LLM 呼叫狀態、命令和錯誤資訊，總是生成。
- `llm_analysis.md`：啟用 LLM 且命令成功時生成。
- `report.md`：可讀報告，包含 Overview、Visualizations、Statistical Highlights、Automatic Diagnosis、可選 LLM Analysis 和各類彙總表。
- `analysis_charts.png`：安裝 `matplotlib` 時預設生成。單圖包含 core group wall 覆蓋、非 container category 的 `total_us` 餅圖和 top phase。完整 trace 時間線建議繼續使用 Perfetto UI 檢視。

## 診斷策略
報告優先回答：
1. 哪些 phase 覆蓋 wall time 最多。
2. 耗時型別更偏 wait、sync、compute、epilogue、communication 還是 quant。
3. 耗時主要落在哪些 core group 或 tid。
4. 關鍵 phase 之間的 overlap 是否不足。
5. 外層階段內部是否存在明顯未歸因 bubble。
6. top raw names 中哪些原始事件應優先回查。

當前確定性診斷仍包含一部分 UMDK FusedDeepMoe 經驗規則，例如 `dispatch_gmm1` 與 `gmm2_combine` 的 overlap 判斷。分析其他 trace 時，這些規則可能只具備參考價值；通用統計表和圖表仍然是主要輸出。

## 依賴和驗證
預設執行只使用 Python 標準庫，不需要安裝第三方包。

可選能力：
- `matplotlib`：用於自動生成 `analysis_charts.png`。
- 外部 LLM CLI：用於 `--llm-analysis`，協議是 stdin 輸入 prompt、stdout 輸出分析文字。

基礎驗證：

```bash
cd <ASCEND_MOE_OPTIMIZER_SKILL>
python3 app.py --trace <TRACE_JSON> --phase-map <PHASE_MAP> --output-dir <OUTPUT_DIR> --top-n 20
```

## 當前限制
- 預設只分析單個 trace 檔案，不做多 trace 對比。
- 當前沒有顯式 `--profile` 機制；不同 trace 來源主要透過 `--phase-map` 適配。
- 未對映到 phase 的事件會被過濾，通用 fallback 統計仍有改進空間。
- core group 規則目前仍以內建 UMDK 1C2V 約定為主，尚未完全配置化。
- 部分自動診斷規則仍偏 FusedDeepMoe，需要繼續拆分為通用規則和領域規則。
- LLM Analysis 是可選外部命令，不內建具體模型、API key 或網路呼叫。
