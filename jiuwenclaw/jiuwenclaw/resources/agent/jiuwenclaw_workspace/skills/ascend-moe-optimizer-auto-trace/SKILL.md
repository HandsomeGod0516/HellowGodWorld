---
name: ascend-moe-optimizer-auto-trace
description: >
  為昇騰運算元在原始碼中接入 TRACE_POINT 與 MoeTracing，串通 trace_preprocessor、profiling tensor、point_map.json、
  save_profiling_data 與 trace_collector 生成 Chrome trace。強調門禁 G1–G5：全鏈路預處理與 OPP、profiling 為資料輸出最後一位、
  整條編譯與示例指令碼聯調、落盤路徑在 spawn 前 resolve。遵循函式級粒度與就地擴充套件，禁止另註冊 xxx_profiling 類第二入口，
  保持原 Op 與 torch.ops 名稱及簽名不變。在使用者提到運算元打點、Profiling、Chrome trace、MoeTracing，或將結論寫入本 skill 時讀取。
---

# 昇騰運算元自動打點

## Agent 速查（執行本 skill 時先讀）

**紅線**：使用者未明確說「只要改原始碼裡的 TRACE / 不要 GM / 不要改 Op 輸出與 pybind」時，**禁止只改 `op_kernel` 或只插樁不交聯調指令碼**。須滿足下表 **G1–G5**；任一缺失須在回覆中寫明「未完成項 + 後續風險」，不得宣稱已閉環。

| 門禁 | 必須滿足 |
|------|----------|
| **G1 預處理** | 團隊 **`compile_ascend_proj.sh`（或等價）** 已接入 **`trace_preprocessor.py` hook**；**當次編譯**在構建樹生成 **`point_map.json`**，且與**當前執行的 OPP/核**同源 |
| **G2 輸出位次** | **`profiling_data` 為全部 Tensor「資料輸出」中的最後一個**（主輸出 `1…N`，再第 `N+1` 路 profiling）。**`op_host` / infer / tiling（若描述輸出）/ 類 `Init` / `__global__` / `aclnnInner_*` / 手寫 `pregen/.../aclnn_*` / `EXEC_NPU_CMD` 實參** 順序一致；禁止只改其中一層 |
| **G3 編譯** | 用專案**常用整條命令**跑通 **OPP**（及若有的 **pybind whl**）。**不等於**僅透過 `validate_trace_points.py` / `check_compile_safety.py` |
| **G4 聯調與後處理** | 在既有 **`examples/*_sample.py` 和/或 `test_*.py`** 中：**裝置同步**（如 `torch_npu.npu.synchronize`）→ **`trace_utils.save_profiling_data`**；若生成 Chrome：呼叫 **`trace_collector.py`**，且 **`point_map.json` 滿足 G1**。**不得**「運算元已多一路輸出，但指令碼仍按舊 arity 解包且從不落盤」 |
| **G5 落盤路徑** | 傳給 `save_profiling_data` / `trace_collector` 的 **`profiling_dir`、`chrome_trace`、`point_map`**：在 **`multiprocessing.spawn` 或等價並行之前** 一律 **`Path(...).expanduser().resolve()` 為絕對路徑**。相對路徑在 `save_profiling_data` 內會拼到 **`trace_utils.py` 所在目錄**，與 shell cwd 不一致 → 易出現 **No rank\*.pt** |

**模式 A / B（與步驟 7 一致）**：**A** = `profiling_data` **OPTIONAL**，Python 側可不增返回值個數；**B** = 同一 `torch.ops` 名，**返回值最後一項**為 profiling。**使用者要落盤 / Chrome** 時優先 **B** 或在 sample 中顯式接 optional 核心引數；OpDef **REQUIRED** 時禁止用 nullptr 規避。

**閱讀順序**：本段門禁 → 下文「目標」與「全鏈路操作性定義」→ **必須執行的流程 1–7** → **[reference.md](reference.md)**。

---

## 目標

根據自然語言需求，為目標運算元生成可落地的運算元側打點程式碼。

邊界約束：
- 本 skill **負責** 運算元程式碼插樁 + profiling 資料採集/解析工具鏈的完整閉環。
- 本 skill **不修改** 運算元的業務邏輯（matmul、通訊等功能程式碼），僅新增 profiling 相關程式碼。
- 本 skill **需要支援** 在僅有運算元程式碼時，自動補齊打點所需工程指令碼、編譯接入、以及從 profiling tensor 到 Chrome Trace JSON 的完整處理鏈路。
- **就地改造、少增檔案**：優先改現有編譯指令碼、示例與 UT；避免平行維護新 `sh`、新 `run_*`、新整檔案測試副本（細則見步驟 6–7 與下表）。
- **同一運算元、同一介面名**：profiling 視為對**原運算元**的增強，**禁止**再註冊名為 **`xxx_profiling`**、**`*_with_profiling`** 或任何「看起來像另一個運算元」的 **Op / `torch.ops` 入口**；**運算元在圖與 Python 側的註冊名保持不變**（若工程允許 arity +1，僅在**同一**名下多返回 profiling 張量；輸入形參名與順序也儘量不變，新增輸出走既有擴充套件約定而非改名分叉）。

**預設交付標準（本 skill 執行時按此閉環，除非使用者明確只要「僅插樁、不要 GM」）**：
- **運算元側**：在 `*_base.h` 中 **`ENABLE_MOE_PROFILING` 預設為 `1`**（關閉裝置側寫入改為 `0` 並**重編核**；禁止依賴「不向裝置傳 profiling 張量」規避，與 REQUIRED 契約一致時尤其如此）；**`profiling_data`（或工程約定的同名輸出）與主輸出同級**（OpDef / infer / pybind / 核形參與 `Init` 順序一致），核入口棧 buffer、`SetMoeProfilePtr`、**GM 寫回**齊全。
- **`profiling_data` 在「資料輸出」中的位置（易執行錯、須寫死）**：凡本 skill 走 **模式 B / REQUIRED**、或使用者要求 **可採集 GM profiling** 時，**在所有與 GE/裝置繫結的輸出列表裡，`profiling_data` 必須是最後一個 Output**（主輸出 `1…N` 在前，**第 N+1 個且僅最後一個**為 profiling）。**Infer / tiling 中該輸出的索引、`aclnnInner_*` 與手寫 `pregen/.../aclnn_*.cpp` 形參順序、`EXEC_NPU_CMD` 實參、`__global__`/`Init` 的 GM 槽位**須與同序；**workspace / tiling 緩衝等非 Tensor 輸出**若與 Tensor 輸出混排，以**該運算元工程既有約定**為準，但 **profiling 張量不得插在主輸出中間**。禁止只改 `op_host` 而漏改 infer/pregen/pybind/核入口任一處導致「看似編過、執行時錯槽」。
- **編譯**：在團隊實際使用的 **`compile_ascend_proj.sh`（或等價）** 中已部署 **`trace_preprocessor.py`** hook（`# TRACE_PREPROCESSOR_HOOK_START/END`）；本倉庫 UMDK 路徑為 **`umdk/build/cam/comm_operator/compile_ascend_proj.sh`**，工具鏈指令碼與 skill **`scripts/`** 對齊（可用 `bootstrap_trace_toolchain.py` 同步）。
- **測試**：在既有 **`*_sample.py` / `test_*.py`** 上擴充套件——**返回值 arity** 與 **`torch.ops` 解包**相容多一路 profiling；**`torch_npu.npu.synchronize`（或等價）後**再落盤；可選 **`--point_map` + `trace_collector.py`** 生成 Chrome trace（具體 CLI 以目標倉庫已存在的示例指令碼為準）。

**使用者用語與預設範圍（避免只做「半套」）**  
- 使用者僅說 **「打點 / 插樁 / trace / profiling / 效能點位」** 且**未**寫明 **「只要改原始碼裡的 TRACE_POINT 字串、不要改 Op 輸出 / 不要 GM / 不要動 pybind」** 等縮範圍指令時，**一律按上文「預設交付標準」執行全鏈路**（運算元 + profiling 張量繫結 + 編譯預處理 + 示例或 UT 解包）。  
- 僅當使用者**明確**縮小範圍（例如「只加點位、本迭代不接 profiling 輸出」）時，才可省略 GM / Op 變更，並應在回覆中說明後續補齊項與風險。

**「全鏈路」操作性定義（避免只改少數檔案就交差）**  
以下視為**同一交付物**，缺任一項即屬半套（須在回覆中列出未完成項）：**①** 編譯管線中的 **`trace_preprocessor.py` hook**（生成與當次 OPP 一致的 `point_map.json`）；**②** `op_host` / **infer** / **tiling（若有輸出描述）** 與 **核 `Init`/`__global__`** 的輸出順序一致，且 **profiling 為最後一路資料輸出**（見上條）；**③** **`aclnnInner_*` 與手寫 `pregen/.../aclnn_*` 對齊**；**④** **pybind** 多路返回或 `EXEC_NPU_CMD` 與之一致；**⑤** 既有 **`examples/*_sample.py` 或 `test_*.py`**：在 **`torch_npu.npu.synchronize`（或等價）之後** 呼叫 **`save_profiling_data`**，且父程序或文件可 **`trace_collector.py` → `chrome_trace.json`**（與 **`point_map.json` 同源**）。**僅 kernel 內 `TRACE_POINT` + 工具鏈指令碼存在，但 sample/UT 仍不解包、不落盤、不接 collector —— 不算完成本 skill 預設交付。**

**推薦執行順序（與下方步驟編號對應）**：掃描與規劃（1→2→3）→ 插樁（4）→ 靜態校驗（5）→ 部署工具鏈與編譯接入（6）→ Profile 測試指令碼分叉（7，可與 6 並行準備，但須在 pybind/運算元已暴露 profiling 輸出之後才有意義）。

## Skill 自維護（元規則）

與本 skill 範圍相關的討論（排障、形狀、ABI、profiling 與主路徑關係等）若得出 **可複用、非一次性** 的結論，**應在同一會話或使用者確認後寫回本倉庫 skill**，避免經驗只留在聊天記錄裡。

- **寫哪裡**：預設編輯本目錄下的 `SKILL.md`（與 `reference.md` 同級；本倉庫示例路徑見 `reference.md` 文首）；過長細節寫入 `reference.md` 並保持連結。
- **寫什麼**：短條目、可執行檢查項、易錯的「不要 / 必須」、與程式碼路徑/常量名的對應；**不要**整段貼上 plog 或冗長堆疊。
- **本倉庫 UMDK 與 Skill 同步**：若修改本 skill **`scripts/`** 下的 `trace_preprocessor.py`、`trace_utils.py`、`trace_save.py`、`trace_collector.py`、`validate_trace_points.py`、`check_compile_safety.py`、`inspect_rank_pt.py`、`bootstrap_trace_toolchain.py`，應**同步更新** **`umdk/build/cam/comm_operator/`** 下同名檔案（若倉庫內另有**對照/金標樹**（本倉常見為並行目錄下的 `build/cam/comm_operator/`），應與之對齊或文件說明有意差異）。批次同步：`python3 <skill_root>/scripts/bootstrap_trace_toolchain.py --build-dir umdk/build/cam/comm_operator`（`<skill_root>` 為含本 `SKILL.md` 的目錄；從倉庫根代入 `jiuwenclaw/resources/agent/jiuwenclaw_workspace/skills/ascend-moe-optimizer-auto-trace/`）。
- **何時寫**：使用者明確要求「記成規則 / 寫進 skill」時必做；若新結論 **修正** skill 裡舊錶述（例如 optional vs REQUIRED），應直接改原文並保持一致性。
- **觸發詞**：使用者說「記錄規則」「經驗更新到 skill」「探討的結論落盤」等，按本條執行。

**近期已併入本 skill 的探討結論（示例索引，便於檢索）**

| 主題 | 要點 |
|------|------|
| **Agent 門禁 G1–G5** | 文首 **「Agent 速查」**；預設交付先逐條滿足，回覆對照 **「輸出約定」** 宣告；**G5** 與 `save_profiling_data` 相對路徑陷阱見 [reference.md](reference.md)「常見陷阱」。 |
| **`point_map.json` 與 Chrome 解析** | 必須與**當前已安裝 OPP/核**為**同一次** `trace_preprocessor` 產物；路徑填**真實檔案**（勿用 `/path/to/...` 佔位）。Host 落盤 profiling 須在 **NPU `synchronize`（或等價）之後**。`skipped_no_mapping` 高而 `rank*.pt` 非空 ⇒ **對映與二進位制不一致**，非「沒打點」。詳見 [reference.md](reference.md) 末尾相關小結。 |
| profiling 輸出地位（示例：多輸出運算元） | 若採用獨立 `profiling_data`：**與主輸出同級**繫結（OpDef/pybind/核 `__global__`/`Init` 順序一致）；REQUIRED 時禁止向裝置傳空 profiling；關裝置側寫入用宏 + 重編核。若工程選擇「複用既有 GM / optional」須與圖語義一致，**勿混用**兩種繫結。 |
| 核寫回與 host 可見性 | 裝置寫 profiling GM 後，若 host 讀數異常或陳舊，可按平臺補充 cache 一致性操作（如 **`DataCacheCleanAndInvalid`** 等），以目標 CANN/AscendC 文件為準。 |
| 混合核入口同步 | 1C2V 等場景下，若在 `SetMoeProfilePtr` 前後或首條 `MoeTracing` 前出現邊界異常，可按運算元語義在 AIC/AIV 間補 **CrossCore 屏障**，避免 trace 與執行順序錯位。 |
| **大塊實現 / `#include` 子樹（易漏檢）** | 入口 **`op_kernel/<入口>.h`** 往往只排程；**真正耗時的 matmul / epilogue / 通訊 / 分核 `operator()`** 常在 **`gemm/`、`kernel/`、`epilogue/`、`raw_distributed/` 等子目錄標頭檔案**中。必須從入口 **遞迴掃全 `op_kernel/`**，對這些翻譯單元打點；**禁止**只改入口殼子。自檢：對目標運算元目錄 **`grep -E 'MoeTracing|TRACE_POINT' .../op_kernel`**，長耗時路徑上應有與「打點密度」匹配的命中。若倉庫另有**參考樹**（如 `*_trace/`、legacy 目錄），可對照查漏，**交工以當前構建所用原始碼樹為準**。 |
| 編譯接入形態 | **改造已有編譯指令碼**，用標記塊插入 `trace_preprocessor.py`；**不**新增平行「專用編譯 sh」作為唯一入口。工具鏈優先放在與 `compile_*.sh` 同目錄的可提交路徑；`bootstrap` / `apply_trace_scaffold` 僅在其他倉無副本或一次性接入時使用。 |
| 就地改造與檔案數量 | **儘量少新建檔案**：在既有 `*_sample.py`、`compile_*.sh`、`test_<op>.py` 上擴充套件；工具鏈與預處理指令碼優先與現有 build 目錄同倉提交。 |
| 運算元命名與介面 | **禁止**單獨運算元名 **`xxx_profiling`** / **`*_with_profiling`**（及同類變體）；**保持原運算元註冊名與 `torch.ops` 名不變**，profiling 為同運算元改造（多一路輸出時用 **同一 Op 名** + 文件化的返回值擴充套件，而非第二個運算元）。 |
| `MIX_AIC_1_2_SLOTS_PER_GROUP` | `1 + GetSubBlockNum()`，本任務 1C2V 下常數為 `1 + 2`；Infer 中拆成 `MIX_AIC_1_2_SUBBLOCK_NUM` 與 `1 + …` 避免魔法數 `3`。 |
| `MAX_INFER_GETBLOCKNUM_UB = 128` | Infer 無 `GetBlockNum()`；為防低估 profiling GM；執行時常見 24 與上界無關；寧可略大佔 GM，不可估小。 |
| **預設全鏈路 / `ENABLE_MOE_PROFILING`** | 交工預設含 **profiling 輸出（或與工程一致的繫結方式）+ 預處理 hook + 示例或 UT 解包**；裝置側宏預設 **`1`**。**Infer 與動態輸出**：若主輸出行數/形狀依賴執行時計數、infer 難以與 tiling 一致，可**僅對 `profiling_data` 在 infer 中強制 shape/dtype**，其餘輸出仍由圖或 tiling 推導（須在工程內驗證 GE/執行時無衝突）；此為**工程權衡**，非所有運算元必需。 |

## 輸入

- 目標運算元路徑，例如 `src/.../op_kernel/<op>.h`（或倉庫約定的 `ascend_kernels/<op>/` 根目錄）。
- **自然語言需求**：若未顯式縮小範圍，預設按 **「預設交付標準」** 與 **「使用者用語與預設範圍」** 執行（見文首）。
- 打點風格：`MoeTracing(TRACE_POINT("label", "B/E"))` 或帶上下文 `MoeTracing(TRACE_POINT("label", "B/E"), extraId, index)`。
- 約束條件：
  - 函式級粒度（見 [reference.md](reference.md)「打點密度與均勻性要求」）
  - 根節點名稱固定為 `processing`
  - 最大深度為 7（實際按語義需要決定，不要人為卡在淺層）
  - 對深層或低價值呼叫鏈執行智慧合併

## 插樁覆蓋必達清單（交工前自檢）

以下與具體運算元目錄結構無關；**不得**只改「最外層排程標頭檔案 / 單檔案入口」即視為完成插樁。

1. **Kernel 入口**：`op_kernel` 下實際參與編譯的 device 入口（通常為 `*.cpp` 中的 `__global__` / `__aicore__` 函式）——含 profiling 棧 buffer、與 GM 寫回等與本 skill 約定一致的邏輯時，必須接入且與 `op_host` 引數個數一致。
2. **入口標頭檔案 + 遞迴 `#include` 可達的全部實現**：在**該運算元** `op_kernel/`（含任意子目錄）內，凡實現 **AIC / AIV 分核主流程階段**的翻譯單元（含模板 `operator()<AscendC::AIC>` / `operator()<AscendC::AIV>`、分核 `Process`、通訊、epilogue、與入口鏈路上的大塊計算/融合邏輯等），**均須具備與語義匹配的 B/E 點位**；僅最外層已打點、**深層實現標頭檔案未打點**視為未完成。易漏檢形態：**入口頭只做轉發**，大塊邏輯在子目錄標頭檔案中——須 **逐層 `#include` 跟到底**，不得以「檔名像數學庫」為由跳過（見上表 **大塊實現 / `#include` 子樹**）。
3. **`op_host` / `infer` / pybind**：profiling 輸出、形狀推導、Python 解包 arity 等按本 skill 其他章節執行；凡在 OpDef 中將 **`profiling_data`（或等價名）標為 `REQUIRED`** 的運算元，均須滿足下文 **「profiling_data 與主輸出同等工程地位」** 全條（禁止 nullptr optional、核 `__global__` 與類 `Init` / `aclnn` 形參順序一致等）。
4. **密度門檻**：見 [reference.md](reference.md)「打點密度與均勻性要求」——**按每種核型別（AIC、AIV）分別**核對可見語義標籤數；未達標時優先在「大塊實現」內補**階段邊界**（見步驟 4 與 [reference.md](reference.md)「常見陷阱」），而不是在入口重複堆疊同義點位。

## 必須執行的流程

1. **掃描目的碼**
   - 從入口檔案出發，**遞迴跟隨 `#include` 進入同運算元目錄下的所有標頭檔案**，直到遍歷完整個運算元內部程式碼樹。不能只看入口 `.h`，必須讀取其直接或間接包含的所有實現檔案。
   - 識別主流程階段與函式邊界；特別關注 **模板例項化呼叫鏈**：如果入口函式呼叫了模板類並最終執行 `operator()()`，該 `operator()` 同樣屬於主流程階段邊界，必須跟進到對應標頭檔案。
   - 將 **`#include` 拉起的、參與編譯的** 所有子目錄標頭檔案列入待打點清單；對 **子目錄中檔名含 `workspace` / `kernel` / `gemm` / `epilogue` 等大塊實現** 尤須逐檔案開啟核對（與上條「易漏檢」一致），**不得**因模板深或行數多而跳過。
   - 識別 **AIC / AIV 分核執行路徑**：如果運算元使用混合核（1C2V 等），AIC 分支和 AIV 分支各自是獨立的主流程，需要分別打點。
   - 對於 1C2V 等模式，**必須檢查 `operator()<AIV>()` 內部是否存在角色分工**（如 send core / recv core / compute core / share quant core）。不同 AIV 核可能透過 `aivIdx` 或 `GetSubBlockIdx()` 走完全不同的分支，每種角色的主要工作階段都需要獨立打點。
   - 儘量保留已存在且合法的點位。

2. **構建打點樹**
   - L1 必須是 `processing`。
   - L2 至 L7 必須來源於當前運算元真實語義（不要把 `dispatch/combine` 當作全域性預設詞）；合併規則見步驟 3，語義需要時用到 L6/L7 是正常的。
   - 對 AIC/AIV 分核執行路徑，分別用 `<phase> aic` / `<phase> aiv` 作為 L2/L3 區分。
   - 對 expert group 迴圈、stage 迴圈等帶索引的重複結構，打點時必須傳遞索引引數（見 [reference.md](reference.md)「MoeTracing 執行時規格」）。

3. **應用智慧合併規則**
   - 超過 7 層的呼叫，摺疊到最近的 L7 祖先節點。
   - 對無同步/無通訊邊界的薄封裝函式與 helper 進行合併。
   - 對熱點語義（`wait`、`sync`、`send`、`recv`、`copy`、`quant`、`dequant`）保留獨立點位。

4. **插入程式碼**
   - 使用穩定命名的 `B/E` 成對點位。
   - 保證 begin/end 詞法巢狀正確。
   - **"最內層迴圈"指 tile 級別的矩陣計算迴圈（如 matmul 塊內沿 K 的迭代、細粒度 epilogue tile 迴圈），不要在其中打點**。但 expert group 迴圈、stage 迴圈屬於階段邊界，必須在迴圈體入口/出口打點。
   - 區分「階段邊界」與「tile 內層」——**同一標頭檔案裡可能同時存在二者，不得以目錄名或檔名猜測並整檔案跳過**：
     - ✅ 需要打點：分核主流程的 **`operator()<AIC>` / `operator()<AIV>`（或等價的分核入口）** 的整體階段邊界；expert / stage 等**粗粒度**迴圈體上的入口與出口；AIC↔AIV 同步與等待；獨立語義的 epilogue、通訊、dispatch/combine 子階段等。
     - ❌ 不要打點：塊內 matmul/epilogue **單次 tile** 的內層搬運與沿 K 的緊迴圈、孤立單次 `DataCopy` 等無獨立階段語義的位置。
     - **判斷標準**：若某函式/入口是 **本分核上某一整段業務的排程或階段邊界**（典型為分核 `operator()`、或等價的大階段入口），則打點；若僅為 **單次 tile 或單次微核心呼叫的內層實現**，則不打點。檔名、子目錄名**不作為**是否跳過的依據。

5. **校驗**
   - 對改動檔案執行 `scripts/validate_trace_points.py`，檢查點位命名與 B/E 配對。
   - 若倉庫內**同一運算元存在多套原始碼樹**（例如金標目錄與產品目錄），建議**對每一套各自的 `op_kernel`（或等價目錄）各跑一遍**上述指令碼，避免分叉漂移。
   - 執行 `scripts/check_compile_safety.py <operator_dir>`，靜態檢查插樁是否會引入編譯錯誤。此指令碼檢查：花括號平衡、預處理指令配對（`#if`/`#endif`）、MoeTracing 標頭檔案可達性、TRACE_POINT 引數語法、變數作用域、profiling guard 閉合、kernel 引數與 op_host 註冊的一致性。
   - **步驟 5 的定位**：主要覆蓋**運算元原始碼樹內**的常見靜態錯誤；**不能**替代完整 OPP / `cust_opapi` / pybind 工程編譯。例如 **`aclnnInner_*`（自動生成）與倉庫內手寫 `pregen/.../aclnn_*.cpp` 簽名不一致**、`EXEC_NPU_CMD` 宏對引數左值的要求、CPack 安裝路徑缺失等，指令碼未必能檢出。
   - 如果校驗失敗，修正問題後重新執行。兩個指令碼都透過後，**仍須**用目標倉庫的 **`build.sh` / `compile_ascend_proj.sh`（或 CI 等價命令）跑通一次完整編譯**作為最終門禁（見 [reference.md](reference.md)「編譯與打包門禁」）。

6. **部署工具鏈並接入編譯（必須執行，不可跳過）**
   - 此步驟不是可選的"預設場景"，而是打點流程的必要組成部分。即使插樁程式碼已正確插入，如果工具鏈指令碼未部署、預處理未接入編譯，打點資料無法採集和解析。
   - **少新檔案、改已有入口（優先原則）**：**不要**為打點單獨再維護一條「新的編譯 `sh`」或平行入口，替代團隊已在用的命令。正確做法是：在**現有** `compile_ascend_proj.sh`（或 CI 呼叫的等價指令碼）裡，於 `copy_ops`/原始碼拷入構建樹之後、`./build.sh` 之前，插入**一段**預處理呼叫，並用 `# TRACE_PREPROCESSOR_HOOK_START` / `# TRACE_PREPROCESSOR_HOOK_END` 包裹，便於冪等與審查。日常編譯仍只跑**原**命令；`apply_trace_scaffold.sh` 僅是**一次性接入助手**（跑完 bootstrap + patch + verify），**不是**長期編譯入口。
   - **工具鏈放哪**：若倉庫已把 `trace_preprocessor.py` / `trace_utils.py` / `trace_collector.py` 等與編譯指令碼放在**同一可提交目錄**（例如本倉庫 `umdk/build/cam/comm_operator/`），hook 內用 `dirname "${BASH_SOURCE[0]}"` 解析到的目錄呼叫即可，**無需**再 `bootstrap` 複製一份到別處，避免重複檔案與路徑漂移。僅當目標倉**沒有**可提交的副本、且不希望把 `.py` 納入版本庫時，才用 `bootstrap_trace_toolchain.py` 拷到指定 `build_dir`。
   - **發現 build 目錄**：在專案中搜尋編譯指令碼（如 `compile*.sh`、`build*.sh`、`Makefile`、`CMakeLists.txt`），定位運算元的 build 目錄。常見位置如 `build/`、`scripts/` 等，不要假設目錄名稱。
   - **部署指令碼（按需）**：無倉內副本時，執行 `bootstrap_trace_toolchain.py` 將下列指令碼複製到目標 build 目錄：`trace_preprocessor.py`、`trace_utils.py`、`trace_save.py`、`trace_collector.py`、`validate_trace_points.py`、`check_compile_safety.py`、`inspect_rank_pt.py`（以指令碼內 `TOOLCHAIN_FILES` 為準）。
   - **接入編譯**：執行 `patch_build_pipeline.py` 在**現有**編譯指令碼中注入預處理 hook；anchor 不匹配時，**手工**在同一指令碼、同一相對順序插入命令並加 `# TRACE_PREPROCESSOR_HOOK_START` / `END` 標記。
   - **校驗部署**：執行 `verify_trace_scaffold.py` 確認指令碼檔案存在且編譯 hook 已就位。
   - 不覆蓋使用者已有指令碼；已存在時只做缺失補齊或可控更新。
   - **完整編譯門禁**：工具鏈部署完成後，必須在實際使用的環境（容器 / CI / 本機）中執行**與團隊一致的一條完整編譯**（含運算元包與 pybind，若專案如此組織）。僅「預處理成功」或僅步驟 5 透過，**不等於**產物可安裝、可 import。常見工程問題見 [reference.md](reference.md)「編譯與打包門禁」。

7. **Profile 測試指令碼分叉（預設交付的組成部分；非「有空再做」）**
   - 與本段相關的交付門禁：**G4**（同步後落盤、collector 與 point_map 同源）、**G5**（`profiling_dir` 等 **`resolve()`**）。不滿足則預設交付不完整。
   - **Python 面兩種模式（勿混為一談）**：
     - **模式 A（保持原返回值個數）**：圖 / `op_host` 註冊 **OPTIONAL** `profiling_data`（或等價名）時，公開 pybind 可仍只返回原先主輸出；在 C++ 裡透過 `aclnn*GetWorkspaceSize` 向 Inner 傳入**空 optional / nullptr** 表示本次不採 profiling。原 UT、原 `torch.ops` arity **不變**。**注意**：一旦某運算元在 OpDef 中將 `profiling_data` 標為 **REQUIRED**，則**禁止**再使用該 nullptr 路徑，否則圖語義、GE 繫結與裝置引數不一致。
     - **模式 B（同一運算元名、返回值 arity +1）**：在 **Op 註冊名 / `torch.ops` 名與輸入簽名均不變** 的前提下，僅在**同一**運算元名上擴充套件返回值（多一路 `profiling_data`）。**禁止**新增 **`xxx_profiling`**、**`*_with_profiling`** 等第二套運算元或第二套 `torch.ops` 名（那是「另一個運算元」，與本原則衝突）。呼叫方用 `..., _ = op(...)` 忽略最後一項即可保持業務邏輯不變；落盤與 Chrome 在**團隊已有或本 skill 擴充套件的** `*_sample.py` 中用 **`--profiling_dir`**（寫 `rank*.pt`）、可選 **`--point_map`** + **`--chrome_trace`**（spawn 結束後 **`subprocess`** 調 `trace_collector.py`）完成，避免再增 `run_*` / `*_profile.py` 整檔案。
   - **多主輸出運算元：`profiling_data` 與主輸出同等工程地位（REQUIRED 時強制契約）**  
     打點 / profiling 的 **GM 輸出** 必須與**該運算元全部主輸出**在圖與繫結上**同級**，不得單獨做成「可選旁路」導致向裝置傳 `nullptr` 或與主輸出引數生命週期不一致。設主輸出共 **N** 路，profiling 為第 **N+1** 路 GM 輸出（具體列舉名以 `op_host` 為準）。實現檢查清單：
     1. **`op_host` OpDef**（`op_host/<op>.cpp` 或團隊等價路徑）：`Output("profiling_data")` 使用 **`ParamType(REQUIRED)`**，與主輸出同級。
     2. **InferShape / InferDataType**（`op_host/<op>_infer.cpp` 等）：對 profiling 輸出索引做與主輸出相同的 **nullptr 門禁**；**始終**設定其維度與 dtype，不得依賴「可選輸出可能不存在」分支。
     3. **pybind**（`pybind/<op>.cpp` 等）：**始終**分配並向 `aclnn<OpName>` / `EXEC_NPU_CMD` 傳入 profiling 的 `at::Tensor`（與主輸出同為實張量）。**禁止**用 `c10::nullopt`、環境變數等方式向裝置側傳入「空 profiling GM」以規避繫結。
     4. **裝置類 `Init`**（`op_kernel/<入口>.h`）：GM 形參順序為 **主輸出 1…N，再 `profiling_data`，再 `workspace`/tiling 等**——須與 OpDef / `aclnn` 一致（具體是否緊挨 workspace 以該運算元既有約定為準，但**不得**與核入口亂序）。
     5. **`__global__` 核函式入口**（`op_kernel/<op>.cpp` 等）：與 OpDef / `Init` **同序**；改序後必須 **全量重編運算元包 / OPP** 並做一次執行驗證（plog 引數槽與 DFX），避免與舊二進位制混用導致錯參。
     6. **關閉裝置側 trace 寫入**：透過 **`ENABLE_MOE_PROFILING`**（在 `<op>_base.h` 或團隊等價 base 頭）與**重編核**控制核內是否寫入；**不要**依賴「不傳 profiling 張量」——在 REQUIRED 契約下該做法非法且易與引數槽位/除錯結論混淆。
   - **目的**：歷史指令碼若只解包前 N 個主輸出，需在升級後改為多解包一位（可用 `_` 丟棄）；專門採集指令碼顯式接收 profiling 張量並 `save_profiling_data`。
   - **禁止**：為適配 profiling 在 **profile 用途之外** 把 `trace_utils` 硬塞進核心數值 UT 的主路徑。原 UT 仍以數值斷言為主；若必須相容舊 arity，可在呼叫處用 `*head, _ = op(...)` 或固定長度解包。
   - **推薦（少新檔案）**：在**原有** `examples/<op>_sample.py` 或團隊 driver（非 pytest）中擴充套件：對 **`torch.ops...<原運算元名>(...)`** 使用 `len(outs)` 分支，向 `forward` 返回元組**末尾**附帶 `profiling`（或 `None`）；`__main__` 增加 profiling / trace 相關 CLI；子程序內 `save_profiling_data`，父程序在 `mp.spawn(..., join=True)` 之後可用 `subprocess` 呼叫 `trace_collector.py`。**運算元名與介面名不變**；**不要**註冊 **`xxx_profiling`** / **`xxx_with_profiling`**。若僅有 pytest UT、無 sample，再在**同一份** `test_<op>.py` 裡增加輔助函式（仍優於新建整檔案副本）。
   - **命名與位置**：優先改現有 `*_sample.py` / 團隊已有 driver；確需 pytest 專用斷言時再在同一目錄的 `test_<op>.py` 內加函式，避免另建 `test_<op>_profile.py` 除非團隊明確要求分拆檔案。
   - **必改內容**：
     - 對主入口 `torch.ops.<lib>.<op>(...)` 在 **`len(outs)`** 上相容「舊 arity / 新 arity（多一路 profiling）」；最後一項為 profiling 時參與落盤。
     - 封裝運算元的 `nn.Module` 的 `_apply_ops` 若把 profiling 傳到 `forward`，下游解包須與元組長度一致；數值對拍仍只比較主輸出，可用 `_` 忽略 profiling。
     - **SmallOps / 對照路徑**：baseline 不返回 profiling 時保持原元組長度不變；帶 profiling 的路徑在對比時只對主輸出子集 `assert_close`。
   - **與工具鏈對接**：`build/.../trace_utils.py` 的 `save_profiling_data`；**模式**為：若設 **`--profiling_dir`**，在 **`torch_npu.npu.synchronize`（或等價）之後** 再 `save_profiling_data`；`__main__` 在 **`--profiling_dir` 且 `--point_map`** 時用 **`subprocess`** 呼叫 **`trace_collector.py`** 寫 **`chrome_trace.json`**（輸出路徑可用 **`--chrome_trace`**）。**本倉庫**可在 `umdk/src/cam/examples/` 下查詢已接入上述 CLI 的 sample 作參照（檔名隨運算元而變）。
   - **無 NPU 靜態校驗**：可在 sample 或 UT 中增加 **`--trace_checks`**（或等價入口），內部呼叫 `validate_trace_points.py` 與 `check_compile_safety.py`，指令碼路徑優先解析到倉內已提交的 `comm_operator` 工具鏈目錄。
   - **`trace_utils` 匯入**：將含 `trace_utils.py` 的目錄加入 `sys.path` 後再 `import`；目錄不存在時列印提示並跳過（見 sample 實現）。
   - **環境說明**：`save_profiling_data` 的 `base_h_path` 指向 `<op>_base.h`（`ENABLE_MOE_PROFILING` / `PROF_SIZE_PER_CORE`）；sample 預設嘗試倉庫內相對路徑。
   - **pytest**：無單獨 `test_*_profile.py` 時，在 **`test_<op>.py`** 內增加無 NPU 的校驗函式即可。


## 命名規則

- 通用根標籤固定為 `processing`。
- 階段標籤必須從當前運算元語義中提取。
- 標籤採用 **空格分隔的層級路徑**，字首表示所屬階段，字尾表示具體子階段。例如 `"dispatch-phase1 aic"` 表示「dispatch-phase1」主階段下 AIC 分支。
- 名稱描述"做什麼"，不要過度繫結實現細節。
- 在語義不變時，儘量保持命名穩定。

示例（名稱僅示意，須與當前運算元真實階段一致）：
- `processing`
- `dispatch-phase1`
- `dispatch-phase1 aic`、`dispatch-phase1 aiv`
- `dispatch-phase1 moe-process`（帶 groupIdx）
- `dispatch-phase1 wait-token`（帶 groupIdx）
- `combine-phase block-epilogue waiting`（帶 stageId）
- `combine-phase block-epilogue calc`（帶 stageId）
- `combine-phase combine-send`、`combine-phase combine-recv`

## 詳細參考

以下已移至 [reference.md](reference.md)：MoeTracing 模板與緩衝區、Profiling 搬運規格、infer 與 pybind 對齊、編譯與打包門禁、打點密度、`trace.json` 四步流程、`point_map` 契約、固定指令碼一覽與示例命令、常見陷阱。

執行本 skill 時以門禁與上文「必須執行的流程」為準；需要完整樣板程式碼或大表時展開 `reference.md`。

## 輸出約定

完成後回覆中**必須**包含：

**門禁對照（預設範圍）**  
- 用 **G1–G5** 逐條宣告 **已滿足 / 未滿足**；未滿足須寫原因與使用者需補動作。

**技術與結果**  
- 插樁修改的檔案列表（含 **`op_kernel/` 子樹**，不僅是入口殼子）。
- 最終點位層級（L1 為 `processing`；合併關係可簡述）。
- `validate_trace_points.py` 與 `check_compile_safety.py` 結果（或說明為何目標倉未跑）。
- **全鏈路改動摘要**：至少列出 **`op_host` / infer / tiling / 核入口 / pregen `aclnn_*` / pybind** 中是否已對齊 **G2**（profiling 最後一路、順序一致）。
- 工具鏈：hook 所在指令碼、`point_map.json` 典型路徑形態；若 bootstrap 了哪些檔案到 build 目錄。
- **步驟 7**：改動的 **`examples/*_sample.py` / `test_*.py` 路徑**；是否 **`synchronize` → `save_profiling_data`**；Chrome 是否 **`trace_collector` + 同源 point_map**；**路徑是否已 `resolve()`**（G5）。
- **UMDK**：wheel 路徑 **`umdk/output/cam/comm_operator/dist/`**、安裝命令；**`libcam.so`** / **返回值個數** 見 [reference.md](reference.md)「編譯與打包門禁」。
- 生成 **`chrome_trace.json`** 的命令列示例（引數用真實形態，避免 `/path/to` 佔位誤導）。
