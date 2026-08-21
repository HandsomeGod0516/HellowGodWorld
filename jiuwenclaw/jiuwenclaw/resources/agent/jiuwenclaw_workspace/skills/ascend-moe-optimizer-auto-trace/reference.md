# 昇騰運算元自動打點 · 詳細參考

本檔案是 [SKILL.md](SKILL.md) 的延伸：MoeTracing 與 Profiling 規格、編譯門禁、打點密度、`trace.json` 與 `point_map` 契約、常見陷阱及固定指令碼清單。門禁 G1–G5 與「必須執行的流程」仍以 SKILL 正文為準。

**步驟編號**：下文出現的「步驟 1–7」「步驟 5」「步驟 6」「步驟 7」等，若無特別宣告，一律指 **[SKILL.md](SKILL.md)** 中「必須執行的流程」的同名步驟。

## Skill 根目錄與本倉庫路徑

下文中的 `<skill_root>` 表示與本 `SKILL.md` 同級目錄。**本倉庫**（workspace 根下常見相對路徑）示例：`jiuwenclaw/resources/agent/jiuwenclaw_workspace/skills/ascend-moe-optimizer-auto-trace/`。

## MoeTracing 執行時規格

MoeTracing 不是簡單的空宏。當專案的 base 標頭檔案中缺少 MoeTracing 定義時，必須按以下規格在運算元已有的 `_base.h` 檔案中補齊（不要新建單獨的標頭檔案）。

### 宏定義

```cpp
#define ENABLE_MOE_PROFILING 1
#define PROF_SIZE_PER_CORE 2048
#define ENABLE_MOE_PROFILING_BARRIER true
```

### per-core profiling buffer 指標

每個核擁有獨立的 profiling buffer，透過 block-local 指標訪問：

```cpp
__BLOCK_LOCAL__ __inline__ int64_t* g_moeProfilePtr;

__aicore__ inline int64_t* GetMoeProfilePtr(uint32_t idx = 0)
{
    return &g_moeProfilePtr[idx];
}
```

如果運算元存在 AIC/AIV 分核編譯（`SPLIT_CORE_CUBE` / `SPLIT_CORE_VEC`），需要為每種核型別宣告獨立的指標變數（`g_moeProfilePtrCube` / `g_moeProfilePtrVec`），並在 `GetMoeProfilePtr()` 中根據編譯宏選擇。

### MoeTracing 函式實現

MoeTracing 是 **模板函式**，不是宏。模板引數 `sync` 控制是否在記錄前插入 `PipeBarrier<PIPE_ALL>()`：

```cpp
template <bool sync = ENABLE_MOE_PROFILING_BARRIER>
__aicore__ inline void MoeTracingWithCycle(int64_t data, int64_t cycle)
{
#if ENABLE_MOE_PROFILING
    if constexpr (sync) {
        AscendC::PipeBarrier<PIPE_ALL>();
    }
    int64_t *profileData = GetMoeProfilePtr();
    profileData[profileData[0]++] = data;
    profileData[PROF_SIZE_PER_CORE - profileData[0]] = cycle;
#endif
}
```

Buffer 佈局：`profileData[0]` 是寫入索引，**正向寫 point_id 資料，反向寫 cycle 時間戳**。

### 三種呼叫過載

```cpp
// 基礎呼叫：記錄 point_id + 當前 cycle
template <bool sync = ENABLE_MOE_PROFILING_BARRIER>
__aicore__ inline void MoeTracing(int64_t data)
{
    MoeTracingWithCycle<sync>(data, AscendC::GetSystemCycle());
}

// 帶索引：將 index 編碼到 data 高 32 位，用於區分不同 expert group / stage
template <bool sync = ENABLE_MOE_PROFILING_BARRIER>
__aicore__ inline void MoeTracing(int64_t data, uint32_t index)
{
    MoeTracing<sync>(data | (int64_t)(((uint64_t)index) << 32));
}

// 帶 extraId + index：用於同時傳遞 stageId 和迴圈索引
template <bool sync = ENABLE_MOE_PROFILING_BARRIER>
__aicore__ inline void MoeTracing(int64_t data, uint32_t extraId, uint32_t index)
{
    MoeTracing<sync>(data, (extraId | (index << 8)));
}
```

### 呼叫示例

```cpp
// 基礎打點（字首/字尾隨運算元語義命名，下為示意）
MoeTracing(TRACE_POINT("dispatch-phase1 aic", "B"));

// 帶 groupIdx（區分不同 expert / tile 組）
MoeTracing(TRACE_POINT("dispatch-phase1 moe-process", "B"), 0, groupIdx);

// 強制 barrier 後再記錄（覆蓋預設 sync 引數）
MoeTracing<true>(TRACE_POINT("combine-phase combine-barrier-all", "E"));
```

命名規則與標籤示例見 [SKILL.md](SKILL.md)「命名規則」，此處不重複。

## Profiling 資料搬運規格

打點資料寫入 per-core 棧上 buffer 後，需要一條完整鏈路將其搬到 Host 側。本 skill 要求在運算元框架上**顯式新增一個 profiling 輸出 tensor**，而不是複用已有輸入 tensor 的 GM 地址。**預設交付（G2）**：該輸出在 **Op 註冊的所有 Tensor 輸出中排在最後**（第 `N+1` 路）。**ParamType** 可為 OPTIONAL（模式 A）或 REQUIRED（模式 B / 強制採數）；下文程式碼片段用 OPTIONAL 僅為示意語法，**位次規則不因 OPTIONAL/REQUIRED 改變**。Python 側 **「圖多一路 optional」 vs 「返回值多一項」** 見 [SKILL.md](SKILL.md) 步驟 7。

### 1. 運算元框架層：新增 profiling 輸出（在既有 output 之後多註冊一個）

在 `op_host` 運算元定義中在**全部主輸出之後**再註冊 profiling（示意可為 OPTIONAL，實際以工程與模式為準）：

```cpp
// op_host/<op>.cpp — 運算元註冊
this->Output("profiling_data")
    .ParamType(OPTIONAL)
    .DataType({ge::DT_INT64, ge::DT_INT64, ge::DT_INT64, ge::DT_INT64})
    .Format({ge::FORMAT_ND, ge::FORMAT_ND, ge::FORMAT_ND, ge::FORMAT_ND})
    .UnknownShapeFormat({ge::FORMAT_ND, ge::FORMAT_ND, ge::FORMAT_ND, ge::FORMAT_ND});
```

在 `op_host/<op>_infer.cpp` 中推導 shape：

**`totalCores` 必須等於 AIC 核數 + AIV 核數，不能只取其中一種。** kernel 側 buffer 佈局為：AIC 核寫 `[0, aicNum)` 區間，AIV 核寫 `[aicNum, aicNum + aivNum)` 區間。如果 `totalCores` 只取了 AIC 數量，AIV 核的寫入會越界。

**禁止硬編碼核數。** 不同硬體型號的核數不同（如 24C+48V=72、20C+40V=60），硬編碼任何具體數字都會在其他型號上出錯。正確做法按優先順序：

1. **tiling 函式**（推薦）：透過 `platform_ascendc::PlatformAscendC(context.GetPlatformInfo())` 獲取 `GetCoreNumAic()` 和 `GetCoreNumAiv()`，已有的 tiling 流程通常已包含此邏輯。
2. **kernel 入口**：透過 `AscendC::GetBlockNum()` 獲取實際 AIC 核數，AIV 核數 = `GetBlockNum() * GetSubBlockNum()`（1C2V 下 SubBlockNum=2）。
3. **infer / pybind 側**：`InferShapeContext` 沒有平臺查詢 API，**不能在 infer 裡讀到實機的 `GetBlockNum()`**，只能寫一個**對執行時 `GetBlockNum()` 的上界**來定 profiling 一維長度。對 **`KERNEL_TYPE_MIX_AIC_1_2`**，kernel 裡邏輯槽數約為 **`GetBlockNum() * (1 + GetSubBlockNum())`**（常見 1C2V：`SubBlockNum=2` ⇒ **每路 AIC 組對應 3 個槽**）。這與「物理上有多少顆 Cube」不是同一個數：例如單卡 **24 Cube + 48 Vector** 時，若執行時 `GetBlockNum()` 為 24，則只需 **72** 個槽；歷史上若誤把「槽數上界」當成「只有 Cube 數」、且該數 **小於** `3 * GetBlockNum()`，AIV 側 `(GetBlockNum() + GetBlockIdx())` 才可能越界。工程內常量如 **`MAX_INFER_GETBLOCKNUM_UB`** 是對 **`GetBlockNum()` 的 infer 上界約定**，須與 **pybind 分配的元素個數**一致，**不是**從矽片規格直接讀出的核數；常見寫法 `MAX_PROFILING_CORE_SLOTS = MAX_INFER_GETBLOCKNUM_UB * MIX_AIC_1_2_SLOTS_PER_GROUP`（係數隨核型別而變）。

```cpp
// op_host/<op>_infer.cpp — infer 上界（命名與工程內已有運算元對齊即可）
constexpr uint32_t MAX_INFER_GETBLOCKNUM_UB = 128;
constexpr uint32_t MIX_AIC_1_2_SLOTS_PER_GROUP = 3;
constexpr uint32_t MAX_PROFILING_CORE_SLOTS = MAX_INFER_GETBLOCKNUM_UB * MIX_AIC_1_2_SLOTS_PER_GROUP;
gert::Shape *profilingShape = context->GetOutputShape(OUTPUT_PROFILING_DATA);
profilingShape->SetDimNum(1);
profilingShape->SetDim(0, MAX_PROFILING_CORE_SLOTS * PROF_SIZE_PER_CORE);
context->SetOutputDataType(OUTPUT_PROFILING_DATA, ge::DT_INT64);
```

```cpp
// pybind — 模式 B：元素個數與 infer 完全一致；每槽 PROF_SIZE_PER_CORE 如 2048
constexpr int64_t kProfilingElems = static_cast<int64_t>(MAX_PROFILING_CORE_SLOTS) * PROF_SIZE_PER_CORE;
at::Tensor profilingData = at::zeros({kProfilingElems}, opts.dtype(at::kLong));
return {mainOut0, mainOut1, profilingData};  // 主輸出個數因運算元而異；須與 infer 元素個數一致
```

### 1.1 `aclnn` 外層包裝與 `aclnnInner_*`（自動生成）的簽名對齊

在 `op_host` 中**增加、刪除或調整任一 Output（含 OPTIONAL）** 後，工具鏈生成的 **`build_out/autogen/aclnnInner_<Op>*.h/.cpp`** 中 `aclnnInner<Op>GetWorkspaceSize` / `aclnnInner<Op>` 的引數列表會隨之變化。

若倉庫中另有**手寫維護**的對外封裝（常見於 `pregen/build_out/autogen/aclnn_<op>.h`、`aclnn_<op>.cpp`，或等價路徑），其形參順序與型別必須與 **當前** `aclnnInner_*` **逐參一致**（含 optional profiling 的 `const aclTensor*` 等），否則 **`cust_opapi` 等目標會在完整編譯階段才報錯**，`check_compile_safety.py` 未必覆蓋。

**交工前自檢**：改完 `op_host` / `infer` 後，開啟最新一次 msopgen 或編譯產物中的 `aclnnInner_*` 宣告，與 `pregen/.../aclnn_*.h` 中 `aclnn<Op>GetWorkspaceSize` 對比；外層實現應只做薄轉發（含將 optional 原樣傳入 Inner）。

```cpp
// 示意：Inner 已含 profilingDataOutOptional 時，外層必須多傳一格再接到 workspaceSize
return aclnnInnerMyOpGetWorkspaceSize(/* ... */, lastMainOutputOut,
    profilingDataOutOptional, workspaceSize, executor);
```

### 2. Kernel 入口（`.cpp`）：buffer 初始化 + 搬出

在 kernel 入口函式中，運算元執行**前後**分別處理 profiling buffer：

**執行前**——在棧上分配 buffer、初始化寫索引和起始時間戳、設定指標：

```cpp
#if ENABLE_MOE_PROFILING
    int64_t profData[PROF_SIZE_PER_CORE];
    profData[0] = 1;
    profData[PROF_SIZE_PER_CORE - 1] = AscendC::GetSystemCycle();
    SetMoeProfilePtr(&profData[0]);
#endif
```

**執行後**——將棧上 buffer 逐條寫到 profiling output tensor 的 GM 地址：

```cpp
#if ENABLE_MOE_PROFILING
    AscendC::GlobalTensor<int64_t> profGlobal;
    profGlobal.SetGlobalBuffer((__gm__ int64_t *)(profiling_data));
    // AIC 核寫前半段，AIV 核寫後半段
    AscendC::GlobalTensor<int64_t> coreGlobal;
    if (g_coreType == AscendC::AIC) {
        coreGlobal = profGlobal[AscendC::GetBlockIdx() * PROF_SIZE_PER_CORE];
    } else {
        coreGlobal = profGlobal[(AscendC::GetBlockNum() + AscendC::GetBlockIdx()) * PROF_SIZE_PER_CORE];
    }
    for (unsigned i = 0; i < profData[0]; ++i) {
        coreGlobal(i) = profData[i];
        coreGlobal(PROF_SIZE_PER_CORE - i - 1) = profData[PROF_SIZE_PER_CORE - i - 1];
    }
    // DataCacheCleanAndInvalid 確保 host 可讀
#endif
```

輔助函式 `SetMoeProfilePtr` 的定義放在 `.cpp` 入口檔案中，根據分核編譯宏選擇正確的 block-local 指標：

```cpp
__aicore__ inline void SetMoeProfilePtr(int64_t *profilePtr)
{
#if __CCE_AICORE__ == 220 || defined(__DAV_C310__) || defined(__DAV_310R6__)
#ifdef SPLIT_CORE_CUBE
    g_moeProfilePtrCube = profilePtr;
#elif defined(SPLIT_CORE_VEC)
    g_moeProfilePtrVec = profilePtr;
#else
    g_moeProfilePtr = profilePtr;
#endif
#else
    g_moeProfilePtr = profilePtr;
#endif
}
```

### 3. Host 側（Python）：讀取 + 儲存

`trace_utils.py` 中 `save_profiling_data` 需要：
- 從 `_base.h` 讀取 `PROF_SIZE_PER_CORE` 與 `ENABLE_MOE_PROFILING`（若實現裡提供 `base_h_path` 引數，**新運算元應傳入當前運算元的 `<op>_base.h` 絕對路徑**，避免工具鏈內寫死的相對路徑仍指向示例運算元）。
- 將 profiling tensor reshape 為 `(total_cores, PROF_SIZE_PER_CORE)`；`get_core_num_list()` 等若仍為示例中的硬編碼或環境變數，需與目標硬體/tiling 一致，否則分組索引會越界或切分錯誤。
- 按 AIC/AIV 核型別分組（考慮 1C2V 對映）
- 儲存為 `rank{id}.pt` 供後續解析工具使用

```python
from pathlib import Path
import trace_utils

profiling = profiling_data.cpu()
out_dir = str(Path("./prof_out").resolve())  # 第三參為輸出目錄，須絕對路徑（見 G5）
op_base_h = Path("/repo/.../src/.../<op>_base.h").resolve()
trace_utils.save_profiling_data(profiling, rank_id, out_dir, base_h_path=str(op_base_h) if op_base_h.is_file() else None)
```

### 3.1 Pybind：`EXEC_NPU_CMD` 與 optional 引數的左值約束

若 pybind 透過 `EXEC_NPU_CMD(aclnnXxx, ...)` 呼叫 `aclnnXxxGetWorkspaceSize`，宏內部通常會對實參做 `ConvertTypes(...)` 一類展開，**要求可繫結到非 const 左值引用**（具體以專案內 `pytorch_npu_helper.hpp` 為準）。

因此向 `aclnn*GetWorkspaceSize` 多傳一個 **optional profiling tensor** 時：

- **禁止**寫成 `c10::optional<at::Tensor>()` 等**純右值**直接塞進宏引數列表（典型編譯錯誤：無法繫結到 `optional&`）。
- **應**在宏外宣告具名變數，例如 `c10::optional<at::Tensor> profilingDataOptional;`（預設不採），再傳入 `EXEC_NPU_CMD(..., profilingDataOptional)`；若本次要採 profiling，則先對該變數賦值再呼叫。

模式 A（見 [SKILL.md](SKILL.md) 步驟 7）下常用「空 optional + 原 return 個數不變」；模式 B 再與「多返回一個 `at::Tensor`」的 pybind 示意配合。

## 編譯與打包門禁（工程側）

本節與打點語義無關，但為「[SKILL.md](SKILL.md) 步驟 6 + 完整編譯」中反覆出現的工程問題；不同倉庫指令碼名可能不同，以實際 `compile*.sh` / `build.sh` 為準。

- **CANN / msopgen 須在 PATH 中**：`msopgen`、`ccec` 等通常依賴 `source ${ASCEND_HOME_PATH}/bin/setenv.bash`（或專案規定的 setenv）。在 **docker exec 非登入 shell**、CI 裸 `bash -lc` 等場景下，若編譯指令碼先呼叫 `msopgen` 再 source，會導致 **`msopgen: command not found`**；應在**首次**呼叫 `msopgen` **之前**注入環境（由專案統一改 `compile_ascend_proj.sh` 等，或由執行者在同一 shell 中先 source）。
- **原始碼屬主與構建使用者**：`msopgen` 可能對輸入 JSON 做「當前使用者須為檔案 owner」校驗。容器內若以 **root** 編譯、倉庫掛載為普通使用者屬主，會報錯；應以與掛載卷**一致的使用者**（如 `docker exec --user <uid>:<gid>`）執行編譯，或按團隊規範在映象內對齊屬主。
- **`AddCustom.json` 與 `msopgen`（UMDK 實踐）**：`msopgen gen -i .../AddCustom.json` 可能報 **`You are not the owner of path ...`**。本倉庫在 **`umdk/build/cam/comm_operator/compile_ascend_proj.sh`** 中於 **`msopgen` 之前** 對 **`./ascend_kernels/AddCustom.json`** 嘗試 **`chown $(id -u):$(id -g)`**，失敗則 **`sudo chown`**。若以 **root** 成功 `chown`，該檔案在工作區可能變為 **root 屬主**；若希望掛載卷仍歸開發者，優先 **`docker exec -u <與卷一致的 uid>`** 跑整條編譯，或事後 **`chown` 回開發使用者**。
- **`build_out` 清理與佔位目錄**：部分 msopgen 工程的 `build.sh` 會對 `build_out` 做 `rm -rf build_out/*` 後再 `cmake --build`。若 CPack / `cmake_install.cmake` 仍引用 **`op_kernel/binary/config/`** 等路徑，而工具鏈未生成該目錄，會在 **package** 階段失敗；可在 **`--target binary` 之後、`package`（或等價）之前** 由專案指令碼 `mkdir -p` 佔位（空目錄即可），具體路徑以生成工程為準。
- **門禁順序**：工具鏈部署（[SKILL.md](SKILL.md) 步驟 6）→ **完整編譯透過**（運算元包 + 若有的 pybind wheel）→ 再視情況跑 [SKILL.md](SKILL.md) 步驟 7 / 裝置側 UT。勿將「僅 validate / check_compile_safety 透過」誤認為已滿足交付。

### UMDK `comm_operator`：pybind whl 標準產物路徑（勿預設寫 `/tmp`）

與 **`umdk/build/cam/comm_operator/build_pybind.sh`** 一致，wheel 輸出目錄為 **`${MODULE_BUILD_OUT_PATH}/dist`**，即倉庫內：

- **`umdk/output/cam/comm_operator/dist/`** — 成功構建後在此生成 **`umdk_cam_op_lib-*.whl`**。

**推薦命令**（在 **`umdk/build/cam`** 下，僅編 pybind、不跑運算元 `msopgen`）：

```bash
./build.sh comm_operator -p
```

安裝：

```bash
pip install --force-reinstall umdk/output/cam/comm_operator/dist/umdk_cam_op_lib-*.whl
```

手工執行 `python3 setup.py bdist_wheel` 時，**`--dist-dir` 應指向上述 `dist`（可先 `mkdir -p`）**，**不要**隨意寫到 **`/tmp`**，以免與 CI、文件和歸檔路徑脫節。

運算元 OPP **`.run`** 由 **`compile_ascend_proj.sh`** 等完整運算元鏈路生成，通常落在 **`umdk/output/cam/comm_operator/run/`**（如 **`CAM_ascend910_93_debian_aarch64.run`**，SOC 名隨 `-c` 變化）。**whl 與 `.run` 需分別安裝**；僅升級 whl 而不升級已裝 OPP 時，注意版本是否匹配。

### `import umdk_cam_op_lib`：`libcam.so` 與 Ascend / 驅動庫

部分環境打出的 **`umdk_cam_op_lib*.so`** 在 ELF **`DT_NEEDED`** 中會依賴 **`libcam.so`**（CAM host 側產物）。若執行時 **`LD_LIBRARY_PATH`** 未包含其所在目錄，會報 **`ImportError: libcam.so: cannot open shared object file`**。

- 將含 **`libcam.so`** 的目錄加入 **`LD_LIBRARY_PATH`**（常見為各團隊 **`comm_operator` host 編譯輸出目錄**，例如部分樹佈局下的 **`umdk/src/cam/comm_operator/build`**，以實際產物為準）。
- **本倉庫部分示例**在模組載入時呼叫 **`_prepend_cam_op_native_lib_path()`** 一類輔助：支援環境變數 **`UMDK_CAM_NATIVE_LIB_DIR`**，並在 **`import torch_npu` / `import umdk_cam_op_lib` 之前** 寫入 **`LD_LIBRARY_PATH`**（同一程序內、在擴充套件被 `dlopen` 前生效）；其它倉庫按既有 driver 方式處理依賴路徑即可。
- **`torch_npu`** 另需 Ascend CANN **`.../aarch64-linux/lib64`** 及 **`/usr/local/Ascend/driver/lib64`**（及常見子路徑 **`.../driver/lib64/driver`**）等；**`docker exec` 非登入 shell** 若未繼承映象登入環境，需顯式 **`export LD_LIBRARY_PATH=...`** 或與 **`${ASCEND_HOME_PATH}/bin/setenv.bash`** 一致。

### Python / `torch.ops`：模式 B 下返回值個數升級

- **模式 B**：pybind 在**同一運算元名**上較舊版 **多返回一路 profiling tensor**（ arity = 原主輸出數 + 1）。
- **舊 whl** 仍為舊 arity 時，若寫死新長度解包會報錯。處理：**重灌**與當前 `pybind` / `op_host` 一致的 whl；或在呼叫處對 **`len(outs)`** 分支相容（見 [SKILL.md](SKILL.md) 步驟 7 與團隊 sample），並在 **rank0** 提示需升級 whl。

### 端到端 profiling + Chrome（UMDK 可參考；其它倉替換為各自的 sample/driver）

- **通用約定**（路徑、sync、`point_map`）見上文 **「point_map 與 Chrome 解析契約（通用）」**。
- **本倉庫**：在 **`umdk/src/cam/examples/`** 下選擇**已接入 profiling** 的 `*_sample.py`（非 pytest；常與數值對拍同檔案）；典型 CLI：**`--profiling_dir`**（輸出目錄，內含 `rank*.pt`）、**`--point_map`**（**真實路徑**的 `point_map.json`，與當次編譯 OPP 同源）、可選 **`--chrome_trace`**。`trace_utils` / `trace_collector` 由 sample 內嵌路徑解析到 **`umdk/build/cam/comm_operator`**；`save_profiling_data` 的 **`base_h`** 指向對應 **`<op>_base.h`**。具體預設路徑以該 sample 檔案頭註釋為準。
- **`MOE_USE_1C2V=1`** 時 **`trace_utils.get_core_num_list()`** 為 **`[24,24,24]`**，否則常見為 **`[24,48]`**；與硬體/核對映解讀需一致。

## 打點密度與均勻性要求

- **目標標籤數（按核型別分別統計）**：對 **AIC 與 AIV 各自**，在「該核實際會執行到的程式碼路徑」上，應能觀察到大約 **15～20 個不同的語義階段名**（即互不相同的 `TRACE_POINT` 標籤字串個數，**不是**全運算元 AIC+AIV 混在一起湊總數）。過少（例如某一核型別上**少於 10 個**）不利於看子階段瓶頸；過多（例如**多於 30 個**）易佔滿 buffer 且 trace 難讀。
- **均勻性**：按**當前運算元**的真實主階段劃分（名稱隨運算元語義而定，如 dispatch、多段 matmul、combine、量化等），各主階段下的子標籤數量應**大致均衡**。若某一主階段已有多個子點位，而另一主階段在對應核上仍只有入口/出口兩點，說明後者打點不足，應深入該階段所在實現（含分核 `operator()` 內部）補充子階段。
- **"函式級粒度"的正確理解**：指每個有獨立語義的階段函式（如 `SendCoreFunc`、`RecvCoreFunc`、`CompCoreFunc`、`UpdateAndCleanInfo`），不是僅限於呼叫鏈第一層入口的 `operator()`。`operator()` 內部如果有多個語義明確的子函式呼叫，每個都應該有獨立的 B/E 點位。
- **二級拆分**：即使一個子函式已經有了 B/E 點位（如 `某階段 aiv send`），如果其內部仍有語義可分離的子階段（如 count-prep vs token-DMA、spin-wait vs data-copy），也應在函式內進一步拆子標籤（如 `… aiv send-count` + `… aiv send-token`）。典型的可拆分模式包括：
  - **count/status 廣播** vs **payload 資料搬運**（dispatch、recv）
  - **spin-wait/polling** vs **實際計算或搬運**（recv-count、group-wait）
  - **shared expert** vs **routed expert** 的獨立執行路徑
  - **metadata load**（index/scale DataCopyPad）vs **per-token reduce 迴圈**（combine local-copy）
- **AIV 角色分工**：對於 1C2V 等混合核模式，`operator()<AIV>()` 內部可能透過 `aivIdx`、`GetSubBlockIdx()` 或角色標誌（`isSendCore`、`isRecvCore`、`isCompCore`）將不同 AIV 核分配到不同工作路徑。每種角色的主要工作階段都需要獨立打點，讓 trace 中能區分各類 AIV 核的時間分佈。
- **多變體對齊**：如果同一運算元有多個 kernel 變體（如 deep-fuse vs shallow-dispatch），所有變體的 AIV `operator()` 都應該有相似粒度的子階段標籤。不能一個變體有 8 個子標籤而另一個只有入口/出口。
- **自檢方法**：打點完成後，**分別**列出 AIC 與 AIV 在各自可達路徑上出現的**不同**標籤名集合並計數。若某一核型別明顯低於上述量級，或某一主業務階段在該核上仍只有一對 B/E 而無子階段，則須繼續補充（優先大塊實現標頭檔案中的階段邊界，見 [SKILL.md](SKILL.md)「插樁覆蓋必達清單」與「必須執行的流程」步驟 4）。

## 容量與擾動約束

- 每核 profiling buffer 容量有限（`PROF_SIZE_PER_CORE`），禁止預設高密度鋪點。
- 不要預設給每個小 helper 或最內層迴圈都加點。
- 優先保證可讀性與穩定定位瓶頸能力，而不是追求全覆蓋。

## 常見陷阱（快速自檢）

- **因「看起來像數學庫/大塊計算實現」而整檔案跳過**：子目錄或檔名**不能**作為免打點依據；凡含 **分核 `operator()<AIC/AIV>`（或等價階段入口）** 且屬於主流程的實現標頭檔案，必須與 tile 內層區分並打點（見 [SKILL.md](SKILL.md) 步驟 4）。
- **大塊實現頭未打點**：主耗時往往在 **`#include` 子樹**的 workspace / kernel / gemm / epilogue 模板 **`operator()`** 內；僅打外層排程頭會導致 trace 看不到真實子階段——屬**高頻遺漏**，交工前按 [SKILL.md](SKILL.md) 自維護表中 **「大塊實現 / `#include` 子樹」** 與 `grep` 自檢；若倉庫有對照樹可 diff，**交工以當前構建樹為準**。
- **`aclnnInner_*` 已變、手寫 `pregen/.../aclnn_*.cpp` 未改**：`op_host` 增刪 output 後 Inner 簽名已更新，外層仍少傳 / 錯傳 `profilingDataOptional` 等引數 → **`cust_opapi` 編譯失敗**；見上文「Profiling 資料搬運規格」小節 1.1 交工前自檢。
- **`EXEC_NPU_CMD` 傳入 `optional` 臨時量**：見上文「Profiling 資料搬運規格」小節 3.1，須使用具名 `c10::optional<at::Tensor>` 變數。
- **infer/pybind 硬編碼核數**：用安全上界或動態邏輯；與 kernel 側 per-core 寫入區間一致。
- **Python 解包 arity**：僅在使用**模式 B**（[SKILL.md](SKILL.md) 步驟 7）時 fusion / profile 指令碼比原先多接一個 profiling 張量；原 UT 不解包改時複製為 `test_<op>_profile.py`。模式 A 下原 UT arity 不變。
- **`trace_utils` 靜默不落盤**：`_base.h` 路徑不對或 `ENABLE_MOE_PROFILING` 為 0；優先檢查 `base_h_path` 與宏。
- **`save_profiling_data` 的相對路徑與 cwd 不一致（高頻誤導）**：`trace_utils.save_profiling_data(..., output_dir)` 若 **`output_dir` 為相對路徑**，實現會拼到 **`trace_utils.py` 所在目錄**（常為 `build/cam/comm_operator`），**不是** shell 的當前工作目錄。表現為：日誌裡 `Saved: .../comm_operator/.../rank*.pt`，而 `trace_collector` 或使用者在 **`examples/`** 下傳的 `./prof_out` 為空 → **No rank\*.pt**。**修復**：sample/driver 在 spawn 前將 **`profiling_dir` / `chrome_trace` / `point_map` 設為 `Path(...).resolve()` 絕對路徑**，或呼叫方始終傳絕對路徑。
- **工具鏈未部署仍以為能出 trace**：[SKILL.md](SKILL.md) 步驟 6 未完成則沒有預處理後的 `point_map.json` 與可復現的 point_id。
- **`point_map` 路徑錯誤或佔位符**：`load_mapping` 為空 → 全記錄跳過；**錯用舊工程 / 他機複製的 `point_map.json`** → `skipped_no_mapping` 極高，見上文「point_map 與 Chrome 解析契約」。
- **sync 前落盤 profiling**：見上文「Host 側何時儲存 profiling tensor」；與 map 錯配症狀不同（前者常表現為 pt 空或 counter≤1，後者 pt 正常但 decode 全跳過）。
- **未跑完整編譯即認為可交付**：[SKILL.md](SKILL.md) 步驟 5 與靜態指令碼不覆蓋 autogen / pybind / CPack 全鏈路；須滿足上文「編譯與打包門禁」。

## trace.json 生成流程

打點資料的完整處理鏈路（從裝置到視覺化）分 4 步：

### Step 1: 預處理（編譯前）

`trace_preprocessor.py` 掃描原始碼，將 `TRACE_POINT("label", "B/E")` 替換為唯一整數 point_id，生成 `point_map.json`：

```bash
python <skill_root>/scripts/trace_preprocessor.py <operator_src_dir> <output_dir> --modify
```

輸出 `point_map.json` 格式：
```json
{
  "points": {
    "1": {"label": "processing", "event_type": "B", "file": "...", "line": 415, "event_id": 1},
    "2": {"label": "dispatch-phase1 aic", "event_type": "B", ...}
  }
}
```

### Step 2: 執行運算元採集 profiling tensor

運算元執行後，Host 側獲取 profiling output tensor（通常為**最後一個** output，即比插樁前多出來的那一個），呼叫 `trace_utils.save_profiling_data` 拆分儲存：

```python
import trace_utils
from pathlib import Path

profiling = profiling_data.cpu()
out_dir = str(Path("./prof_out").resolve())  # 須絕對路徑；勿傳未 resolve 的 "./xxx"
op_base_h = Path("/abs/or/repo/path/to/<op>_base.h").resolve()
trace_utils.save_profiling_data(profiling, rank_id, out_dir, base_h_path=str(op_base_h) if op_base_h.is_file() else None)
```

也可離線儲存：
```bash
python <skill_root>/scripts/trace_save.py raw_profiling.pt --rank 0 --output profiling_data
```

### Step 3: 生成 Chrome Trace JSON

`trace_collector.py` 讀取所有 `rank*.pt` + `point_map.json`，解析 64 位組合 ID（低 32 位 point_id + 高 32 位 extra_id），配對 B/E 事件，生成 Chrome Trace 格式：

```bash
python <skill_root>/scripts/trace_collector.py profiling_data point_map.json -o chrome_trace.json
```

支援引數：
- `--clock-divisor 50.0`：時脈頻率 MHz（cycle → us 換算）
- `--extra-mode seq`：extra_id 解析模式（`seq`=高 24 位序號+低 8 位 extra，`legacy`=整體使用）
- `--depth 0`：區間深度過濾（0=全部，1=僅葉子，2=葉子+父層）

### Step 4: 視覺化

在 Chrome 瀏覽器開啟 `chrome://tracing`，載入生成的 `chrome_trace.json`。
每個 rank 對應一個 process，每個核（AIC/AIV × core_id）對應一個 thread。

### point_map 與 Chrome 解析契約（通用）

本節與**具體運算元名**無關；任意昇騰運算元只要走 `TRACE_POINT` → 前處理器 → 裝置寫整型 ID → `trace_collector` 解碼，均適用。

#### `point_map.json` 是什麼、生成在哪裡

- `trace_preprocessor.py` 掃描**參與當次編譯的那份原始碼樹**（常為 msopgen `copy_ops` 之後的生成目錄），將 `TRACE_POINT("label","B"|"E")` 替換為**唯一整數 point_id**，並在**輸出目錄**（CLI 第二參，常與該生成工程根目錄相同）寫出 **`point_map.json`**（結構一般為 `{"points": {"1": {"label", "event_type", "file", "line", "event_id"}, ...}}`）。
- **裝置側寫入的是預處理後的 point_id**；Host 側用 JSON **按字串鍵**（如 `"149"`）查 `event_type` / `label`。因此：
  - **解碼用的 `point_map.json` 必須與當前執行的核心/OPP 來自同一次預處理 + 同一次編譯**。換了一份原始碼再跑 preprocess、或複製了別臺機器的 map、或只重灌 whl 不重編運算元，都會導致 **ID 對不上**。
- **典型落點**（形態因倉庫而異，勿背死路徑）：`<…>/build_tmp/<…>/<msopgen_project_name>/point_map.json`，與編譯日誌裡預處理 hook 所操作的目錄一致。在目標環境用 `find … -name point_map.json` 或查 `compile_*` 裡 `trace_preprocessor` 的第二引數最可靠。

#### 使用時的路徑（常見誤操作）

- 傳給 `trace_collector.py` 的第二個引數、或各倉 sample/driver 裡的 `--point_map`，必須是 **`os.path` 上真實存在的檔案**。
- 文件、註釋裡的 **`<repo>/...`、`build_tmp/.../point_map.json` 僅表示目錄形態**；**禁止**把字面量 **`/path/to/...`** 當作引數——會表現為 `point_map` 載入失敗、`point_map keys: 0`、或 `load_mapping` 返回空，進而 **全部記錄被跳過**。
- 建議在呼叫前做 **`Path(path).is_file()`** 檢查並給出清晰錯誤（各倉 sample 可按需加入）。

#### Host 側何時儲存 profiling tensor（與裝置可見性）

- 裝置把 profiling 寫入 GM 後，若在 **未完成佇列同步 / 裝置到 Host 可見** 時就在 Python 裡讀張量並 `save`，可能讀到**全零或計數不更新**的緩衝，`trace_collector` 解析條數為 0。
- **通用做法**：在運算元執行返回後、落盤前呼叫 **`torch_npu.npu.synchronize(device_id)`**（或專案規定的等價同步），再 `cpu()` / `save_profiling_data`。具體插入點因框架而異（例如在 `forward` 外、`synchronize` 之後再寫盤）。

#### 如何判斷是「對映錯了」還是「沒采到數」

- 先用 **`inspect_rank_pt.py`**（見下表，與 `comm_operator` 同目錄提交）檢查 `rank*.pt`：各分組 tensor 的 **非零比例**、**`tensor[core,0]` 計數（counter）**；若大量核 **`counter > 1`**，說明 per-core 上有有效記錄，**問題不在裝置打點**。
- 再跑 **`trace_collector.py`**：看 **`otherData.skipped_no_mapping`**。若其值 **接近原始記錄總數**，而 `point_map` 鍵數正常，多為 **base_point_id 與 JSON 鍵不一致**（錯 map / 舊 map）。
- 工具在 stderr 列印的 **`diagnose:`** 行：`unique base_point_id in rank*.pt`、`point_map keys`、**`intersection`**。**`intersection` 為 0** 且兩側都非空時，可斷定 **point_map 與當前核心不是一套**；應改指向**本次編譯生成目錄**中的 `point_map.json` 並重新生成 trace。
- **勿與「sync 時機」混淆**：全零 pt → 先查同步；pt 有資料、僅 Chrome 空且 **`skipped_no_mapping` 高** → 先查 **map 路徑與版本**。

## 固定指令碼

路徑規範：
- 文件中的**編譯/校驗命令示例**優先使用**相對路徑**（便於換機復現），**不依賴** Cursor 專屬絕對路徑。
- **例外（必守）**：`save_profiling_data`、`trace_collector`、sample 的 **`--profiling_dir` / `--chrome_trace` / `--point_map`** 在程式碼裡須 **`resolve()` 成絕對路徑**（見 **G5**）。勿在示例裡暗示「相對路徑一定相對當前 shell」。

### 本倉庫 UMDK：`build/cam/comm_operator` 與 Skill 指令碼的關係

本 skill 的 **`scripts/`** 下列出了**完整**工具集；若日常只引用 `<skill_root>/scripts/...` 而**不在運算元編譯目錄提交副本**，會出現「文件裡有很多指令碼、工程裡用不上」的割裂。

**本倉庫約定**：

- **`umdk/build/cam/comm_operator/`**（與 `compile_ascend_proj.sh`、`build.sh` 同目錄）應提交與 **編譯預處理、profiling 落盤、Chrome 解析、插樁校驗**直接相關的指令碼；若本倉庫另有對照樹，**佈局與其 `build/cam/comm_operator/` 對齊**，避免工具鏈分叉。
- **同名指令碼以 Skill 為規範源**；修改行為時優先改 Skill 下檔案，再**同步複製**到 `umdk/build/cam/comm_operator/`（或合併差異後兩邊一致）。

| 檔案（`umdk/build/cam/comm_operator/`） | 作用 |
|----------------------------------------|------|
| `trace_preprocessor.py` | `TRACE_POINT` → `point_id`，生成 `point_map.json`（**`compile_ascend_proj.sh`** 中 hook 呼叫） |
| `trace_utils.py` | `save_profiling_data`、從 `*_base.h` 讀 `PROF_SIZE_PER_CORE` 等 |
| `trace_save.py` | 離線原始 `.pt` → 按核拆分輸出目錄 |
| `trace_collector.py` | `profiling_data` 目錄 + `point_map.json` → `chrome_trace.json`（stderr 含 `diagnose:` 與 `skipped_no_mapping` 提示） |
| `inspect_rank_pt.py` | 快速檢視 `rank*.pt` 形狀、非零、每核 counter，判斷 pt 是否有有效 profiling（**不依賴**具體運算元名） |
| `validate_trace_points.py` | [SKILL.md](SKILL.md) 步驟 5：標籤與 B/E 配對 |
| `check_compile_safety.py` | [SKILL.md](SKILL.md) 步驟 5：靜態安全檢查 |
| `bootstrap_trace_toolchain.py` | 將上表所列 `TOOLCHAIN_FILES` 從**本指令碼所在目錄**同步到 ``--build-dir``（冪等；``--dry-run`` / ``--list``）；**規範源**與 Skill ``scripts/`` 同名檔案一致 |
| `compile_ascend_proj.sh` / `build.sh` / `build_pybind.sh` / `set_conf.py` | 既有構建與預處理 hook |

**僅保留在 Skill 目錄、一般不提交到 UMDK `comm_operator` 的腳手架**（新倉庫一次性接入、草稿插樁）：`patch_build_pipeline.py`、`verify_trace_scaffold.py`、`apply_trace_scaffold.sh`、`generate_instrumentation_plan.py`、`instrument_operator.py`。**`bootstrap_trace_toolchain.py`** 在 **Skill 與 `umdk/build/cam/comm_operator/` 各有一份**，修改後應兩邊對齊。本倉庫已對 **`compile_ascend_proj.sh`** 做預處理接入，一般**不必**再對 UMDK 跑 `apply_trace_scaffold`；給其他倉接入時仍從 Skill 路徑執行。

**UMDK 內同步 Skill 工具鏈到本目錄（示例）**：

```bash
# 從倉庫根執行：用 Skill 目錄為源，重新整理 umdk/build/cam/comm_operator 下各指令碼
python3 <skill_root>/scripts/bootstrap_trace_toolchain.py \
  --build-dir umdk/build/cam/comm_operator
```

**本倉庫推薦呼叫方式（任選其一）**：

```bash
# 與工程同目錄的副本（推薦；與對照樹 layout 一致更佳）
cd umdk/build/cam/comm_operator
python3 validate_trace_points.py ../../../src/cam/comm_operator/ascend_kernels/<op>/op_kernel
python3 trace_collector.py <profiling_out_dir> <path/to/point_map.json> -o chrome_trace.json

# 或顯式使用 Skill 路徑（與下表「命令示例」一致）
python3 <skill_root>/scripts/validate_trace_points.py ...
```

**與 [SKILL.md](SKILL.md)「必須執行的流程」步驟對應（檢索用）**：

| 步驟 | 指令碼或產物 |
|------|----------------|
| 1–4 輔助（可選） | `generate_instrumentation_plan.py`、`instrument_operator.py` — 規劃/草稿插樁，不能替代人工審查 |
| **5 校驗** | `validate_trace_points.py`、`check_compile_safety.py`（**不替代**完整 OPP/pybind 編譯；見 [SKILL.md](SKILL.md) 步驟 5 說明與上文「編譯與打包門禁」） |
| **6 工具鏈 + 編譯接入** | **首選**：倉內已有 `trace_*.py` 時只改現有 `compile_*.sh` 注入 hook（見 [SKILL.md](SKILL.md) 步驟 6）。**按需**：`bootstrap_trace_toolchain.py` → `patch_build_pipeline.py` 或手工 hook → `verify_trace_scaffold.py`；一次性 `apply_trace_scaffold.sh`。編譯前在構建樹複製目錄跑 `trace_preprocessor.py ... --modify`，生成 `point_map.json`。**透過後須跑通目標倉庫完整 `build.sh` / `compile_ascend_proj.sh`（或 CI 等價）** |
| **6（本倉庫 UMDK 已接入）** | `umdk/build/cam/comm_operator/trace_preprocessor.py` 與 **`compile_ascend_proj.sh`** 內 **`# TRACE_PREPROCESSOR_HOOK_START/END`**：在 `copy_ops` 之後、`set_conf.py` 之前，對 **`${MODULE_BUILD_PATH}/${proj_name}`** 執行預處理（**只改當次 msopgen 生成樹**，倉內 `src` 原始碼仍保留 `TRACE_POINT` 字串）；`point_map.json` 落在該生成樹根目錄。指令碼缺失時列印 WARNING 並跳過。 |
| 執行後解析 | `trace_save.py`（離線 `.pt`）、`trace_collector.py`（→ `chrome_trace.json`）— 見上文「trace.json 生成流程」 |
| **7 Profile UT / 聯調** | 擴充套件既有 **`examples/*_sample.py`** / **`test_<op>.py`**：profiling 落盤、可選 Chrome、可選 **`--trace_checks`**；數值 UT 與 profile 入口分離（少增 `*_profile.py`）。詳解見 [SKILL.md](SKILL.md) 步驟 7；落盤後經 `trace_collector` 的流程另見上文「trace.json 生成流程」「端到端 profiling + Chrome」。 |

- 生成打點草案（函式樹 + 合併決策）：
  - `python <skill_root>/scripts/generate_instrumentation_plan.py --root <operator_dir> --entry <entry_function>`
- 根據函式邊界自動寫入打點程式碼：
  - `python <skill_root>/scripts/instrument_operator.py --target <operator_src_file_or_dir> --root-label processing`
- 校驗點位命名與 B/E 配對：
  - `python <skill_root>/scripts/validate_trace_points.py <file_or_dir>`
- 靜態編譯安全檢查（花括號平衡、預處理配對、標頭檔案可達、引數一致性等）：
  - `python <skill_root>/scripts/check_compile_safety.py <operator_dir>`
  - 加 `--strict` 將 warnings 也視為錯誤
- 預處理（編譯前替換 TRACE_POINT 為整數 ID）：
  - `python <skill_root>/scripts/trace_preprocessor.py <operator_src_dir> <output_dir> --modify`
- 儲存 profiling tensor（離線）：
  - `python <skill_root>/scripts/trace_save.py <raw_pt_file> --rank <rank_id> --output <profiling_data_dir>`
- 生成 Chrome Trace JSON：
  - `python <skill_root>/scripts/trace_collector.py <profiling_data_dir> <point_map.json> -o chrome_trace.json`
- 部署工具鏈指令碼到 build 目錄：
  - `python <skill_root>/scripts/bootstrap_trace_toolchain.py --build-dir <build_module_dir>`
- 對編譯指令碼打補丁並接入預處理（冪等）：
  - `python <skill_root>/scripts/patch_build_pipeline.py --compile-script <compile_script_path> --preprocessor-cmd "<cmd>"`
- 校驗工具鏈與編譯接入是否就緒：
  - `python <skill_root>/scripts/verify_trace_scaffold.py --build-dir <build_module_dir> --compile-script <compile_script_path>`
- 一鍵執行"部署工具鏈 + 編譯接入 + 校驗"：
  - `bash <skill_root>/scripts/apply_trace_scaffold.sh <skill_root> <build_module_dir> <compile_script_path>`
