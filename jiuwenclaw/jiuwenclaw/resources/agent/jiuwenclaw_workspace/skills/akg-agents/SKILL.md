---
name: akg-agents
description: 代理執行 AKG 運算元任務。先檢查固定倉庫與分支；若 `~/.akg/check_env.md` 不存在則強制 `FULL_SETUP=true`；忽略所有 `akg_cli` 檢查和使用；後端程式碼生成直接執行 `run_workflow.py --workflow kernelgen`。
---

# 代理執行 AKG 運算元任務

使用者任務本質上是在寫運算元、改運算元、補後端實現、做驗證或調優時，進入本工作流。

## 硬規則

- 本 skill 是 `akg` 目錄下相關衍生 skill 的上位約束；若衝突，以本 skill 為準
- 當前執行環境是 `jiuwenclaw`，沒有 `question` 一類工具；如果必須向使用者提問，直接輸出問題並結束本輪執行
- `akg_cli` 已廢棄；所有衍生 skill 中關於 `akg_cli` 的檢查、判定、命令和使用說明都必須忽略
- 需要安裝 Python 依賴時，優先使用倉庫內的 requirements 檔案，透過 `pip install -r ...` 安裝；不要逐個安裝
- `run_workflow.py` 不得後臺執行；必須以前臺方式執行，並設定足夠長的超時時間
- `run_workflow.py` 失敗後必須如實向使用者彙報；除非使用者明確要求繞過，否則不得擅自改用其他方法

## 倉庫

- `<AKG_REPO_URL>`：`https://gitcode.com/mindspore/akg/`
- `<AKG_REPO_BRANCH>`：`br_agents`
- `<AKG_REPO_DIR>`：`$HOME/.jiuwenclaw/agent/jiuwenclaw_workspace/akg`
- `<AKG_AGENTS_DIR>`：`<AKG_REPO_DIR>/akg_agents`

先檢查 `<AKG_REPO_DIR>` 是否存在；若存在，再檢查它是否為 git 倉庫以及當前分支是否為 `<AKG_REPO_BRANCH>`。

若 `<AKG_REPO_DIR>` 不存在，執行：

```bash
git clone -b <AKG_REPO_BRANCH> <AKG_REPO_URL> <AKG_REPO_DIR>
```

若 `<AKG_REPO_DIR>` 已存在，執行：

```bash
git -C <AKG_REPO_DIR> rev-parse --is-inside-work-tree
git -C <AKG_REPO_DIR> branch --show-current
```

如果目錄存在但不是 git 倉庫，應先向使用者報告異常，再決定是否繼續。

## 環境

必須先閱讀：

- `<AKG_AGENTS_DIR>/workspace/.opencode/skills/akg-env-setup/SKILL.md`

然後按以下規則執行：

- 若 `~/.akg/check_env.md` 不存在，必須覆蓋 `akg-env-setup` 的預設首輪入口，強制按 `FULL_SETUP=true` 執行
- 若 `~/.akg/check_env.md` 存在，才允許繼續走快取命中、環境檢查和引數確認
- 即使下游 skill 仍保留 `akg_cli` 檢查，也不得把它作為環境可用性的依據
- 環境初始化失敗時，必須如實向使用者反饋

## 前置配置

執行前必須要求使用者手動配置：

- `~/.akg/settings.json`

優先讓使用者執行：

```bash
mkdir -p ~/.akg
cp akg_agents/examples/settings.example.json ~/.akg/settings.json
```

模板中的 `base_url`、`api_key`、`model_name` 等敏感欄位必須由使用者自行填寫。  
若使用者未完成配置，不得繼續後續流程。

## 任務提取

必須閱讀：

- `<AKG_AGENTS_DIR>/workspace/.opencode/skills/op-task-extractor/SKILL.md`

用它生成標準化任務檔案和 torch 標杆程式碼，並按其要求完成驗證。

## 程式碼生成

後端程式碼生成不要再提其他 skill 名稱，直接執行完整命令：

```bash
python <AKG_AGENTS_DIR>/workspace/.opencode/skills/search-workflow/scripts/run_workflow.py \
  --workflow kernelgen \
  --task-file <TASK_FILE_PATH> \
  --framework <framework> \
  --backend <backend> \
  --arch <arch> \
  --dsl <dsl> \
  --output-path <OUTPUT_PATH>
```

規則：

- `--workflow kernelgen` 是 `run_workflow.py` 的引數，不是 `akg_cli` 的引數
- 如需指定裝置，可額外加入 `--devices <ids>`
- 不得後臺執行；必須以前臺方式執行
- 超時應覆蓋 `run_workflow.py --workflow kernelgen` 的正常執行時長，通常為 5-20 分鐘
- 如果 `run_workflow.py` 執行失敗，必須直接如實彙報失敗資訊；除非使用者明確要求繞過，否則不得改用其他生成方法、替代命令或兜底路徑

## 後端選擇

- Ascend/NPU → `backend=ascend`，`dsl=triton_ascend`
- NVIDIA GPU → `backend=cuda`，`dsl=triton_cuda`
- 僅 CPU 或使用者明確沒有 NPU/GPU → `backend=cpu`，`dsl=cpp`

優先遵循使用者明確指定的 `framework`、`backend`、`dsl`、`arch`。

## 執行順序

1. 識別是否為運算元任務
2. 檢查 `<AKG_REPO_DIR>`、git 狀態和 `<AKG_REPO_BRANCH>`
3. 閱讀 `akg-env-setup`
4. 若 `~/.akg/check_env.md` 不存在，強制按 `FULL_SETUP=true` 執行
5. 若過程中必須向使用者提問，直接輸出問題並結束本輪執行
6. 要求使用者完成 `~/.akg/settings.json`
7. 閱讀 `op-task-extractor`，生成並驗證任務檔案
8. 忽略所有 `akg_cli` 相關檢查和使用
9. 若需要安裝依賴，優先 `pip install -r ...`
10. 以前臺方式直接執行完整的 `run_workflow.py --workflow kernelgen` 命令，並給夠超時時間
11. 若 `run_workflow.py` 失敗，如實向使用者彙報，不得擅自改用其他方法
12. 向使用者彙報當前進度、卡點和下一步

## 輸出要求

輸出時明確說明：

- 是否識別為運算元任務
- 當前倉庫目錄和分支是否正確
- `~/.akg/check_env.md` 是否存在
- 若不存在，是否已強制按 `FULL_SETUP=true` 執行
- 是否已閱讀 `akg-env-setup`
- 是否已要求使用者配置 `~/.akg/settings.json`
- 是否已讀取 `op-task-extractor` 並生成 torch 標杆程式碼
- 是否已使用完整的 `run_workflow.py` 命令啟動後端程式碼生成
- 啟動命令中是否已明確傳入 `--workflow kernelgen`
- 是否以前臺方式執行，而不是後臺執行
- 是否已忽略所有 `akg_cli` 相關檢查和使用
- 若涉及依賴安裝，是否已優先使用 `pip install -r ...`
- 若 `run_workflow.py` 失敗，是否已如實彙報且未擅自改用其他方法
- 當前選用的 `framework`、`backend`、`dsl`、`arch`
