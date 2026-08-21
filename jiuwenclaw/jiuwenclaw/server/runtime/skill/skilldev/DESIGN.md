# SkillDev 模式設計文件

> 版本：v1.0 

---

## 1. 定位與目標

SkillDev 是 JiuWenClaw 平臺的一種**執行模式**，專門用於輔助開發者端到端地建立、測試、最佳化並打包一個 Agent Skill（`.skill` 包）。

它不是一個對話式 Agent，而是一條**確定性工程流水線**：接受使用者需求描述，依次經過規劃、程式碼生成、格式校驗、測試、評測、改進、打包、描述最佳化等階段，最終輸出可以直接安裝到 JiuWenClaw 的 Skill 產物。

**三個入口模式**（系統自動識別，無需前端傳入標誌位）：

| 模式 | 觸發條件 | 場景 |
|---|---|---|
| `create` | 僅有 `query` | 從零建立新 Skill |
| `create_with_resources` | `query` + `resources` | 攜帶參考資料（文件/程式碼）建立 |
| `modify` | `query` + `existing_skill` | 修改/升級已有 Skill |

---

## 2. 整體架構

### 2.1 在 JiuWenClaw 中的位置

```
前端（對話方塊 + 彈窗 + Todo列表 + 產物列表）
    ↕ WebSocket / HTTP（E2A 協議，AgentResponseChunk 流）
Gateway 層（路由層保證同一 task_id 的請求到同一例項）
    ↓
JiuWenClaw.process_message_stream()
    ├── 普通 chat 請求  → ReActAgent
    ├── skills.* 請求   → SkillManager
    └── skilldev.* 請求 → SkillDevService   ← 本文件的範圍
```

SkillDev 與主 ReActAgent **完全隔離**，不共享對話上下文、記憶體、會話狀態，僅複用：
- 模型配置（`model_name` + `model_client_config`）
- MCP 工具工廠函式（`mcp_tools_factory`）
- 檔案系統訪問配置（`sysop_config`）

### 2.2 模組劃分

```
jiuwenclaw/agentserver/skilldev/
├── schema.py          # 資料模型層：列舉、狀態、事件、掛起點配置、評測資料結構
├── pipeline.py        # 編排層：確定性狀態機（執行 & 恢復邏輯）
├── service.py         # 服務層：無狀態請求處理器，Method 路由
├── context.py         # 上下文層：階段執行環境（emit + create_stage_agent）
├── deps.py            # 依賴注入：最小外部依賴集合
├── store.py           # 基礎設施：狀態持久化（checkpoint）
├── workspace.py       # 基礎設施：任務工作區管理
└── stages/            # 階段處理器層
    ├── base.py              # StageHandler 抽象基類 + StageResult
    ├── init_stage.py        # INIT：資源預處理
    ├── plan_stage.py        # PLAN：需求分析與規劃
    ├── generate_stage.py    # GENERATE：SKILL.md 生成
    ├── validate_stage.py    # VALIDATE：格式校驗
    ├── test_design_stage.py # TEST_DESIGN：測試用例設計
    ├── test_run_stage.py    # TEST_RUN：測試執行
    ├── evaluate_stage.py    # EVALUATE：評分 + 聚合 + 分析
    ├── improve_stage.py     # IMPROVE：根據反饋改進
    ├── package_stage.py     # PACKAGE：打包 .skill
    └── desc_optimize_stage.py # DESC_OPTIMIZE：描述最佳化迴圈
```

**分層依賴關係**（只允許上層依賴下層）：

```
service.py
    → pipeline.py
        → stages/*.py
            → context.py
                → deps.py
                    → store.py
                    → workspace.py
    → schema.py（所有層均可依賴）
```

---

## 3. Pipeline 狀態機

### 3.1 完整階段流程

```
INIT → PLAN → PLAN_CONFIRM* → GENERATE → VALIDATE
    → TEST_DESIGN → TEST_RUN → EVALUATE → REVIEW*
    → IMPROVE → (迴圈回 TEST_RUN)
    → PACKAGE → DESC_OPTIMIZE_CONFIRM* → DESC_OPTIMIZE → COMPLETED

標註 * 的為掛起點（Suspension Point）：Pipeline 在此暫停，等待前端使用者確認
```

| 階段 | 型別 | 職責 |
|---|---|---|
| `INIT` | 執行 | 資源解壓、已有 Skill 載入、狀態初始化 |
| `PLAN` | 執行 | ReActAgent 分析需求，輸出結構化開發計劃 |
| `PLAN_CONFIRM` | **掛起點** | 等待使用者審閱並確認（或修改）plan |
| `GENERATE` | 執行 | ReActAgent 按 plan 生成 SKILL.md |
| `VALIDATE` | 執行 | 靜態校驗 SKILL.md 格式（frontmatter 合法性、命名規範） |
| `TEST_DESIGN` | 執行 | ReActAgent 設計測試用例集（EvalSet） |
| `TEST_RUN` | 執行 | 執行測試用例，採集 GradingResult + RunTiming |
| `EVALUATE` | 執行 | Grader 評分 → 聚合 Benchmark → Analyst 生成分析報告 |
| `REVIEW` | **掛起點** | 等待使用者決定：繼續改進 or 透過打包 |
| `IMPROVE` | 執行 | ReActAgent 根據 feedback_history 最佳化 SKILL.md |
| `PACKAGE` | 執行 | 打包為 `.skill` 壓縮包 |
| `DESC_OPTIMIZE_CONFIRM` | **掛起點** | 詢問使用者是否需要描述最佳化 |
| `DESC_OPTIMIZE` | 執行 | 描述最佳化迴圈（train/test 分組，迭代擬合） |
| `COMPLETED` | 終態 | 流程結束 |
| `ERROR` | 終態 | 不可恢復錯誤 |

### 3.2 Pipeline 生命週期

Pipeline **不長駐記憶體**。每次請求的處理流程：

```
收到請求
  → StateStore 載入狀態（或建立新狀態）
  → new SkillDevPipeline(state, deps)
  → pipeline.run() 或 pipeline.resume()
  → 執行到掛起點或終態
  → StateStore 儲存狀態（checkpoint）
  → Pipeline 物件釋放
```

這意味著即使服務重啟，任務也能從上次 checkpoint 恢復繼續執行。

### 3.3 run() 的內部邏輯

```python
while stage not in (COMPLETED, ERROR):
    if stage in SUSPENSION_POINTS:       # 命中掛起點
        emit TODOS_UPDATE                # 更新左側 Todo 列表
        emit CONFIRM_REQUEST             # 驅動前端彈出確認框
        checkpoint()
        break                            # 暫停，等待下次 resume()

    handler = STAGE_HANDLERS[stage]      # 查詢處理器
    emit STAGE_CHANGED                   # 通知前端階段變更
    emit TODOS_UPDATE                    # 同步 Todo 狀態
    result = await handler.execute(ctx)  # 執行階段邏輯
    state.stage = result.next_stage      # 跳轉下一階段
    checkpoint()
```

### 3.4 resume() 的內部邏輯

```python
def resume(data: dict):
    suspension = SUSPENSION_POINTS[state.stage]  # 當前必須是掛起點
    suspension.on_resume(state, data)             # 更新狀態（寫入使用者的 plan/反饋）
    next_stage = suspension.next_stage            # 計算下一階段
    if callable(next_stage):
        next_stage = next_stage(data)             # REVIEW 的下一階段由使用者 action 決定
    state.stage = next_stage
    yield from run()                              # 繼續執行
```

---

## 4. 掛起點（Suspension Points）機制

掛起點是 Pipeline 的**結構化暫停**：Pipeline 到達該階段時不執行任何 Agent 邏輯，而是向前端推送確認請求，然後等待使用者響應。

### 4.1 SuspensionConfig 結構

```python
@dataclass
class SuspensionConfig:
    confirm_type: str           # 標識確認型別（前端用於選擇彈框樣式）
    title: str                  # 彈框標題
    message: str                # 彈框描述文字
    actions: list[dict]         # 按鈕列表：[{"id": "confirm", "label": "確認", "style": "primary"}]
    extract_data: Callable      # (state) → dict，從 state 提取要展示的資料
    on_resume: Callable         # (state, data) → None，根據使用者響應更新 state
    next_stage: Stage | Callable # 下一階段（REVIEW 的下一階段取決於使用者選擇）
```

### 4.2 三個掛起點配置

**PLAN_CONFIRM（計劃確認）**
- 推送事件：`CONFIRM_REQUEST { confirm_type: "plan_confirm", data: { plan: {...} } }`
- 使用者操作："確認" → 寫入 `state.plan`，跳轉 GENERATE；"修改" → 前端在對話方塊中提出修改意見，透過 `skilldev.respond` 帶入新的 plan 重新提交
- 狀態變更：`state.plan = data["plan"]`，`state.plan_confirmed_at = 時間戳`

**REVIEW（評測審閱）**
- 推送事件：`CONFIRM_REQUEST { confirm_type: "review", data: { benchmark, report, iteration } }`
- 使用者操作："透過，進入打包" → 跳轉 PACKAGE；"繼續改進" → `feedback_history` 追加記錄，跳轉 IMPROVE
- 狀態變更：`state.feedback_history.append({ iteration, feedback })`

**DESC_OPTIMIZE_CONFIRM（描述最佳化確認）**
- 推送事件：`CONFIRM_REQUEST { confirm_type: "desc_optimize_confirm", data: { current_description } }`
- 使用者操作："最佳化" → 跳轉 DESC_OPTIMIZE；"跳過" → 跳轉 COMPLETED
- 狀態變更：無（純路由決策）

---

## 5. 事件系統

後端透過 WebSocket 流式推送 `AgentResponseChunk`，前端根據 `event_type` 直接對映 UI 動作。

### 5.1 事件分類

| 事件型別 | 觸發時機 | 前端響應 |
|---|---|---|
| `skilldev.stage_changed` | 每次階段切換 | 內部標識，可用於除錯 |
| `skilldev.progress` | 階段內進度說明 | 對話流中顯示文字提示 |
| `skilldev.agent_thinking` | Agent 推理 token 流 | 對話流中實時顯示思考過程 |
| `skilldev.test_progress` | 測試執行中 | 對話流中顯示測試進度 |
| `skilldev.todos_update` | 每次階段切換 & 掛起點 | **更新右側 Todo 列表** |
| `skilldev.confirm_request` | 命中掛起點 | **彈出確認框** |
| `skilldev.artifact_ready` | 生成檔案/打包完成 | **更新右側產物/附件列表** |
| `skilldev.eval_ready` | EVALUATE 完成 | 對話流中展示評測詳情 |
| `skilldev.validate_result` | VALIDATE 完成 | 對話流中展示校驗報告 |
| `skilldev.desc_opt_ready` | DESC_OPTIMIZE 完成 | 對話流中展示 before/after |
| `skilldev.error` | 不可恢復錯誤 | 顯示錯誤，停止流程 |

### 5.2 關鍵事件 Payload 結構

**`skilldev.confirm_request`**（驅動前端彈窗的核心事件）：
```json
{
  "event_type": "skilldev.confirm_request",
  "task_id": "sd_xxx",
  "confirm_type": "plan_confirm",
  "title": "請審閱開發計劃",
  "message": "以下是生成的開發計劃，請確認或修改",
  "actions": [
    {"id": "confirm", "label": "確認", "style": "primary"},
    {"id": "modify",  "label": "修改", "style": "secondary"}
  ],
  "data": {
    "plan": { "skill_name": "...", "description": "...", ... }
  }
}
```

**`skilldev.todos_update`**（驅動前端 Todo 列表）：
```json
{
  "event_type": "skilldev.todos_update",
  "task_id": "sd_xxx",
  "todos": [
    {"id": "plan",         "label": "需求分析與規劃", "status": "completed"},
    {"id": "generate",     "label": "技能生成與校驗", "status": "in_progress"},
    {"id": "test",         "label": "測試與評測",     "status": "pending"},
    {"id": "improve",      "label": "最佳化改進",       "status": "pending"},
    {"id": "package",      "label": "打包",           "status": "pending"},
    {"id": "desc_optimize","label": "描述最佳化",       "status": "pending"}
  ]
}
```

**`skilldev.artifact_ready`**（驅動前端產物列表）：
```json
{
  "event_type": "skilldev.artifact_ready",
  "task_id": "sd_xxx",
  "artifact": {
    "id": "skill_package",
    "name": "my_skill.skill",
    "type": "skill_package",
    "size_bytes": 12345,
    "browsable": true,
    "downloadable": true
  }
}
```

### 5.3 後端驅動原則

**Todo 列表的計算完全由後端控制**，前端只做渲染。`compute_todos()` 根據 `current_stage` 和 `mode` 動態計算每個分組的狀態（`completed` / `in_progress` / `pending`）：

```python
_STAGE_GROUPS = [
    _StageGroup(id="plan",         stages={INIT, PLAN, PLAN_CONFIRM}),
    _StageGroup(id="generate",     stages={GENERATE, VALIDATE}),
    _StageGroup(id="test",         stages={TEST_DESIGN, TEST_RUN, EVALUATE, REVIEW}),
    _StageGroup(id="improve",      stages={IMPROVE}),
    _StageGroup(id="package",      stages={PACKAGE}),
    _StageGroup(id="desc_optimize",stages={DESC_OPTIMIZE_CONFIRM, DESC_OPTIMIZE}),
]
```

---

## 6. 外部 API 介面

前端透過以下 7 個 Method 與 SkillDev 互動，所有請求統一走 `JiuWenClaw.process_message_stream()`，由 `_SKILLDEV_METHODS` 字首匹配自動路由到 `SkillDevService`。

### 6.1 介面總覽

| Method | 型別 | 說明 |
|---|---|---|
| `skilldev.start` | 流式 | 發起新任務（或升級已有 Skill） |
| `skilldev.respond` | 流式 | 統一確認入口，後端按當前階段自動路由 |
| `skilldev.status` | 一次性 | 查詢單任務狀態 / 列出所有任務 |
| `skilldev.download` | 一次性 | 下載打包產物（Base64） |
| `skilldev.cancel` | 一次性 | 取消任務 |
| `skilldev.file.list` | 一次性 | 獲取工作區檔案樹（產物瀏覽） |
| `skilldev.file.read` | 一次性 | 讀取工作區檔案內容 |

### 6.2 介面詳情

#### `skilldev.start` — 發起新任務

**請求引數（params）**：
```json
{
  "query": "幫我建立一個能搜尋和下載 arXiv 論文的 Skill",
  "tools": ["web_search", "file_write"],
  "resources": ["/path/to/api_docs.pdf"],
  "existing_skill": null
}
```
- `existing_skill` 不為 null 時，系統判定為 `modify` 模式

**響應事件流**：
```
→ {event_type: "skilldev.started",       task_id: "sd_xxx"}          # 立即返回 task_id
→ {event_type: "skilldev.stage_changed", stage: "init"}
→ {event_type: "skilldev.todos_update",  todos: [...]}
→ {event_type: "skilldev.stage_changed", stage: "plan"}
→ {event_type: "skilldev.agent_thinking", delta: "...", status: "thinking"}
→ ... (plan 階段 Agent 推理流)
→ {event_type: "skilldev.stage_changed",  stage: "plan_confirm"}
→ {event_type: "skilldev.todos_update",   todos: [...]}
→ {event_type: "skilldev.confirm_request", confirm_type: "plan_confirm", data: {plan: {...}}}
→ {event_type: "skilldev.suspended",      stage: "plan_confirm"}      # 流結束，等待使用者
```

#### `skilldev.respond` — 統一確認入口

**請求引數（params）**：
```json
{
  "task_id": "sd_xxx",
  "action": "confirm",
  "plan": { ... }
}
```
- `action` 欄位的合法值由 `CONFIRM_REQUEST` 事件的 `actions` 列表定義
- `plan` / `feedback` 等附加欄位由具體掛起點的 `on_resume` 消費

**REVIEW 階段的響應示例（使用者選擇繼續改進）**：
```json
{
  "task_id": "sd_xxx",
  "action": "improve",
  "feedback": "測試用例 2 的邊界條件處理有問題，請修復"
}
```

**響應事件流**（與 `start` 類似，從恢復點繼續）：
```
→ ...各階段事件...
→ {event_type: "skilldev.completed" | "skilldev.suspended", stage: "..."}
```

#### `skilldev.status` — 查詢狀態

**請求引數**：
- 查單個任務：`{ "task_id": "sd_xxx" }`
- 列所有任務：`{}`（不傳 task_id）

**響應（單任務）**：
```json
{
  "ok": true,
  "task_id": "sd_xxx",
  "stage": "review",
  "mode": "create",
  "iteration": 1,
  "plan": { ... },
  "eval_results": { ... },
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T01:00:00Z"
}
```

#### `skilldev.download` — 下載產物

**請求引數**：`{ "task_id": "sd_xxx" }`

**響應**：
```json
{
  "ok": true,
  "filename": "arxiv_searcher.skill",
  "content_base64": "UEsDB...",
  "size_bytes": 12345
}
```

#### `skilldev.file.list` — 獲取檔案樹

**請求引數**：`{ "task_id": "sd_xxx" }`

**響應**：
```json
{
  "ok": true,
  "tree": [
    {"path": "SKILL.md", "type": "file", "size": 2048},
    {"path": "tools/",   "type": "dir",  "children": [
      {"path": "tools/search.py", "type": "file", "size": 512}
    ]}
  ]
}
```

#### `skilldev.file.read` — 讀取檔案內容

**請求引數**：`{ "task_id": "sd_xxx", "path": "SKILL.md" }`

**響應**：
```json
{
  "ok": true,
  "path": "SKILL.md",
  "content": "---\nname: arxiv_searcher\n..."
}
```

---

## 7. 核心資料模型

### 7.1 SkillDevState — 執行時狀態（唯一可信源）

```python
@dataclass
class SkillDevState:
    task_id: str
    stage: SkillDevStage        # 當前階段
    mode: SkillDevTaskMode      # create / create_with_resources / modify
    iteration: int              # 改進輪次（從 0 開始）

    # 輸入
    input: dict                 # query, tools, resources, existing_skill

    # 中間產物（按階段逐漸填入）
    reference_texts: list[str]  # resources 解析後的文字
    existing_skill_md: str      # modify 模式時的原始 SKILL.md
    plan: dict                  # PLAN 階段輸出
    plan_confirmed_at: str      # 使用者確認計劃的時間
    evals: dict                 # TEST_DESIGN 輸出的測試用例集
    eval_results: dict          # EVALUATE 輸出的評測結果
    feedback_history: list      # 每輪 REVIEW 的使用者反饋
    desc_optimize_result: dict  # DESC_OPTIMIZE 輸出

    # 輸出
    zip_path: str               # 打包產物路徑
    zip_size: int               # 產物大小（bytes）

    # 後設資料
    created_at: str
    updated_at: str
    error: str
```

**State 的生命週期**：
- `SkillDevService._handle_start()` 建立初始 State
- Pipeline 各階段的 StageHandler 透過 `ctx.state` 讀寫
- `pipeline._checkpoint()` 在每個階段邊界將 State 序列化到 `state.json`
- `SkillDevService._handle_respond()` 從 `state.json` 載入並恢復

### 7.2 評測相關資料結構

評測階段（TEST_DESIGN → TEST_RUN → EVALUATE）使用以下結構，設計參考 [official skill-creator](https://github.com/anthropics/anthropic-quickstarts/tree/main/skill-creator)：

```
EvalSet
  └── EvalCase[]         # 每個測試用例（id, prompt, expectations[]）

GradingResult            # 單次執行的評分結果
  └── GradingExpectation[] # 每條 assertion 的 pass/fail + 證據

RunTiming                # 單次執行的耗時/token 資料

Benchmark                # 完整基準測試結果
  └── BenchmarkRun[]     # with_skill vs baseline 的對比 run 記錄

DescOptimizeIteration    # 描述最佳化的單輪迭代結果
```

---

## 8. 基礎設施

### 8.1 StateStore — 狀態持久化

**職責**：在階段邊界將 `SkillDevState` 序列化為 JSON 檔案（checkpoint），支援斷點續傳。

**儲存路徑**：
```
~/.jiuwenclaw/agent/workspace/skilldev/{task_id}/state.json
```

**核心介面**：
```python
await store.save_state(task_id, state)      # checkpoint（階段結束時呼叫）
await store.load_state(task_id)             # 恢復（resume 時呼叫）
store.load_state_sync(task_id)              # 同步版（status 查詢時呼叫）
store.list_tasks()                          # 列出所有有效 task_id
```

**擴充套件點**：當前為本地檔案實現，多例項部署時可替換為 Redis 實現，介面不變。

### 8.2 WorkspaceProvider — 任務工作區

**職責**：為每個 task_id 維護獨立、標準化的工作區目錄。

**目錄結構**：
```
~/.jiuwenclaw/agent/workspace/skilldev/{task_id}/
├── state.json          ← StateStore 的 checkpoint 檔案
├── resources/          ← 上傳的資原始檔（解壓後的原始內容）
├── skill/              ← 生成的 Skill 目錄（Agent 的寫入區）
│   ├── SKILL.md
│   └── ...（工具實現檔案等）
├── evals/
│   ├── evals.json          ← 測試用例定義（EvalSet）
│   └── iteration-{N}/      ← 第 N 輪測試的結果檔案
│       ├── grading.json
│       └── timing.json
└── output/
    └── {skill_name}.skill  ← 最終打包產物
```

**核心介面**：
```python
workspace = await provider.ensure_local(task_id)  # 確保目錄存在，返回路徑
path = provider.get_local_path(task_id)           # 僅返回路徑（不建立）
await provider.sync_to_remote(task_id)            # 擴充套件點：同步到遠端儲存
```

### 8.3 SkillDevDeps — 依賴注入

`SkillDevService` 不依賴 `JiuWenClaw` 例項，只接收最小外部依賴：

```python
@dataclass
class SkillDevDeps:
    model_name: str                     # 預設模型名
    model_client_config: dict           # 模型呼叫配置
    mcp_tools_factory: Callable[[], list] # MCP 工具工廠函式
    sysop_config: object | None         # 檔案系統訪問配置
    state_store: StateStore             # 狀態持久化
    workspace_provider: WorkspaceProvider # 工作區管理
```

由 `JiuWenClaw._get_skilldev_service()` 懶初始化並注入（首次 `skilldev.*` 請求觸發）。

---

## 9. 階段處理器開發指南

### 9.1 StageHandler 合同

每個階段實現一個 `StageHandler` 子類：

```python
class MyStageHandler(StageHandler):
    async def execute(self, ctx: SkillDevContext) -> StageResult:
        # 1. 從 ctx.state 讀取上游資料
        plan = ctx.state.plan

        # 2. 透過 ctx.emit() 向前端推送進度事件
        await ctx.emit(SkillDevEventType.PROGRESS, {"message": "開始處理..."})

        # 3. 透過 ctx.create_stage_agent() 建立隔離 Agent 執行 AI 邏輯
        agent = ctx.create_stage_agent(
            stage_name="my_stage",
            system_prompt=MY_SYSTEM_PROMPT,
            tools=["file_read", "file_write"],
        )
        result = await agent.run(prompt)

        # 4. 將結果寫入 ctx.state
        ctx.state.some_field = result

        # 5. 返回下一階段
        return StageResult(next_stage=SkillDevStage.NEXT_STAGE)
```

**關鍵約束**：
- StageHandler 不得持有跨請求的狀態（不能有例項變數儲存業務資料）
- 所有業務狀態透過 `ctx.state` 讀寫
- Agent 透過 `ctx.create_stage_agent()` 建立，每階段獨立，不共享上下文
- 透過 `ctx.workspace` 訪問任務目錄（Path 物件）

### 9.2 每階段 Agent 隔離原則

| 階段 | 推薦工具 | System Prompt 焦點 |
|---|---|---|
| PLAN | `web_search` | 分析需求，輸出結構化 plan JSON |
| GENERATE | `file_write`, `file_read` | 按 plan 生成 SKILL.md 及支撐檔案 |
| TEST_DESIGN | （無檔案工具） | 根據 SKILL.md 設計測試用例 |
| TEST_RUN | `file_read`, skill 呼叫工具 | 執行測試，記錄結果 |
| EVALUATE | （無檔案工具） | Grader 評分 + Analyst 分析 |
| IMPROVE | `file_read`, `file_write` | 根據 feedback 修改 SKILL.md |
| DESC_OPTIMIZE | （無檔案工具） | 迭代最佳化描述文字 |

### 9.3 註冊新階段的步驟

1. 在 `stages/` 下建立 `{name}_stage.py`，實現 `StageHandler`
2. 在 `stages/__init__.py` 匯出新 Handler
3. 在 `schema.py` 的 `SkillDevStage` 列舉中新增新階段值
4. 在 `pipeline.py` 的 `STAGE_HANDLERS` 字典中註冊
5. 如需在 Todo 列表中顯示，在 `schema.py` 的 `_STAGE_GROUPS` 中配置歸屬分組

---

## 10. 端到端呼叫示例

以下是一次完整 Skill 開發流程的介面呼叫時序（前端視角）：

```
① 使用者在對話方塊輸入需求
  → 前端傳送: skilldev.start { query, tools }
  ← 後端推送: skilldev.started { task_id }
  ← 後端推送: (多個事件流...)
  ← 後端推送: skilldev.confirm_request { confirm_type: "plan_confirm", data: { plan } }
  ← 後端推送: skilldev.suspended { stage: "plan_confirm" }

② 前端彈出計劃確認框，使用者檢視並點選"確認"
  → 前端傳送: skilldev.respond { task_id, action: "confirm", plan: {...} }
  ← 後端推送: (GENERATE / VALIDATE / TEST_DESIGN / TEST_RUN / EVALUATE 各階段事件流)
  ← 後端推送: skilldev.confirm_request { confirm_type: "review", data: { benchmark, report } }
  ← 後端推送: skilldev.suspended { stage: "review" }

③ 前端彈出評測結果審閱框，使用者點選"繼續改進"
  → 前端傳送: skilldev.respond { task_id, action: "improve", feedback: "..." }
  ← 後端推送: (IMPROVE / TEST_RUN / EVALUATE 迭代事件流)
  ← 後端推送: skilldev.confirm_request { confirm_type: "review", data: { benchmark, report } }
  ← 後端推送: skilldev.suspended

④ 使用者對結果滿意，點選"透過，進入打包"
  → 前端傳送: skilldev.respond { task_id, action: "accept" }
  ← 後端推送: (PACKAGE 事件流)
  ← 後端推送: skilldev.artifact_ready { type: "skill_package", downloadable: true }
  ← 後端推送: skilldev.confirm_request { confirm_type: "desc_optimize_confirm" }
  ← 後端推送: skilldev.suspended

⑤ 使用者選擇"最佳化"描述
  → 前端傳送: skilldev.respond { task_id, action: "optimize" }
  ← 後端推送: (DESC_OPTIMIZE 事件流)
  ← 後端推送: skilldev.desc_opt_ready { before: "...", after: "..." }
  ← 後端推送: skilldev.completed

⑥ 使用者下載產物
  → 前端傳送: skilldev.download { task_id }
  ← 後端返回: { filename, content_base64, size_bytes }

⑦（可選）使用者瀏覽工作區檔案
  → 前端傳送: skilldev.file.list { task_id }
  ← 後端返回: { tree: [...] }
  → 前端傳送: skilldev.file.read { task_id, path: "SKILL.md" }
  ← 後端返回: { content: "..." }
```

---

## 11. 關鍵設計決策與約束

### 決策一：Pipeline 不長駐記憶體
**Why**：避免大量併發任務的記憶體積壓；強制所有狀態經過 StateStore 持久化，使服務重啟透明。
**Trade-off**：每次請求都有 `load_state` / `save_state` 的檔案 I/O 開銷，但對於分鐘級的 AI 任務可以忽略不計。

### 決策二：單一 `skilldev.respond` 確認入口
**Why**：前端不需要知道當前處於哪個掛起點，只需將使用者的決策資料（`action` + 附加欄位）發給後端，後端自動根據 `task_id` 當前階段路由。
**擴充套件影響**：新增掛起點時，前端程式碼無需修改，只需在 `SUSPENSION_POINTS` 中註冊新的 `SuspensionConfig`。

### 決策三：後端驅動 UI 狀態
**Why**：Todo 列表、彈框內容、產物列表等 UI 狀態全部由後端事件攜帶，前端純渲染，避免前後端狀態同步問題。
**實現**：`compute_todos()` 是 Todo 狀態的唯一計算來源；`CONFIRM_REQUEST` 事件攜帶彈框的完整描述（標題、描述、按鈕列表、展示資料）。

### 決策四：每階段獨立 Agent
**Why**：工具隔離（PLAN 階段不應有檔案寫入工具）、Prompt 隔離（每階段有焦點明確的系統提示）、記憶體隔離（避免長 context 干擾）。
**當前狀態**：`create_stage_agent()` 介面已定義，實際接入 `openjiuwen ReActAgent` 的程式碼待實現（已有佔位標註）。

### 決策五：工作區路徑統一
**Why**：SkillDev 的任務目錄必須在 JiuWenClaw 的統一工作區下，避免資料散落在系統各處。
**約定**：`~/.jiuwenclaw/agent/workspace/skilldev/{task_id}/`，由 `get_workspace_dir() / "skilldev"` 構造。

---

## 12. 當前待辦和擴充套件點

| 專案 | 位置 | 說明 |
|---|---|---|
| 接入 ReActAgent | `context.py:create_stage_agent()` | 待接入 `openjiuwen` 實際 Agent 構建邏輯 |
| 工具註冊邏輯 | `context.py:_register_tools()` | 按工具名白名單註冊到 Agent |
| sysop_config 構造 | `interface.py:_get_skilldev_service()` | 從 `_sysop_card_id` 構造檔案系統許可權配置 |
| 取消邏輯 | `service.py:_handle_cancel()` | 中斷正在執行的 Pipeline（協程取消） |
| 遠端儲存同步 | `workspace.py:sync_to_remote()` | 多例項部署時同步到 S3/OBS/NFS |
| StateStore Redis 實現 | `store.py` | 多例項部署的分散式狀態儲存替換 |
| 各 StageHandler 的 Agent 實現 | `stages/*.py` | 當前各階段均有待實現註釋，邏輯框架已完整 |
