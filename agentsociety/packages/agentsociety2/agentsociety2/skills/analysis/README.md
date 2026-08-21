# Analysis（實驗分析子模組）

從模擬工作區讀取 SQLite 與實驗文件，經 **資料優先** 的多階段流程生成洞察、圖表與中英雙語報告。

**說明**：本檔案是**給人看的模組文件**（普通 README）。與 Agent/擴充套件裡可 invocable 的 `SKILL.md` 不是同一類檔案。程式碼入口見 `__init__.py` 匯出。

## 架構（分層）

```
service.py     Analyzer · Synthesizer · run_analysis_workflow
     │
agents.py      AnalysisAgent（洞察 → 策略/ReAct 工具環 → 視覺化裁判）
     │
data.py        DataReader · ContextLoader · DataSummary
output.py      Reporter · ReportWriter · AssetManager · EDAGenerator
executor.py    AnalysisRunner · CodeExecutor · ToolRegistry
llm_contracts.py   LLM 輸出 XML 契約（函式 + 常量）
instruction_md/    可拼接 Markdown 能力說明（utils.get_analysis_skills）
utils.py       路徑 · Schema · XML 解析
models.py      AnalysisConfig、裁判型別（AnalysisJudgment 等）、路徑常量
```

**單實驗主路徑**：`Analyzer.analyze` → `AnalysisAgent.analyze`（`DataReader.read_full_summary` + `AnalysisRunner`）→ `AssetProcessor`/`AssetManager` + `Reporter.generate`。

**LLM 兩類約定**：`llm_contracts.py` 規定 **XML 輸出形狀**（與程式碼解析器一致）；`instruction_md/` 提供 **可編輯的行為說明**（由下面「instruction 技能」機制注入）。

## instruction 技能（`analysis_skill_names`）是幹什麼的？

這裡說的 **skill** 不是 Agent 目錄裡的 `SKILL.md` 技能，而是 **分析子模組專用的「指令片段」**：

1. **內容**：`instruction_md/*.md` 裡的 Markdown（frontmatter 僅用於後設資料，**不會**發給 LLM）。
2. **注入位置**：拼進 **system**（或帶 system 的訊息）裡，讓模型在寫洞察、選工具、寫報告時遵守同一套流程與質量要求。
3. **篩選規則**（`get_analysis_skills`）：
   - `required: true` 的條目（如 `xml_contract`）**總是**注入；
   - `analysis_skill_strict_selection=True` 時，再額外注入 `analysis_skill_names` 裡列出的 `name`；
   - `False` 且未指定名單時，注入目錄下全部片段。

這樣可以在 **不改 Python** 的情況下，透過增刪 Markdown 或改配置調整分析風格；與 `llm_contracts` 分工為：**契約管格式，instruction 管語義與流程**。

## 快速開始

```python
from agentsociety2.skills.analysis import run_analysis, run_synthesis, AnalysisConfig

result = await run_analysis(
    workspace_path="./workspace",
    hypothesis_id="1",
    experiment_id="1",
)

await run_synthesis(workspace_path="./workspace", hypothesis_ids=["1", "2"])
```

## 工作區與產物

**輸入**：`hypothesis_{id}/experiment_{id}/run/sqlite.db`、`EXPERIMENT.md`；假設側 `HYPOTHESIS.md`。

**單實驗輸出**：`presentation/hypothesis_{id}/experiment_{id}/`

| 路徑 | 說明 |
|------|------|
| `report.md` / `report.html` | 預設報告（優先中文） |
| `report_zh.*` / `report_en.*` | 中英分檔案 |
| `data/analysis_summary.json` | 結構化分析結果 |
| `data/eda_profile.html` / `eda_sweetviz.html` | 可選 EDA |
| `charts/` | 程式碼執行生成的圖表 |
| `assets/` | 報告引用圖片（從 charts / run/artifacts 彙總） |
| `README.md` | 該次分析**輸出目錄**內自動生成的檔案索引（與本包 `README.md` 不同） |

**綜合**：`synthesis/synthesis_report_*.md|html`（見 `Synthesizer`）。

## 公共 API（節選）

| 符號 | 用途 |
|------|------|
| `run_analysis` / `run_analysis_many` / `run_analysis_workflow` | 便捷入口 |
| `run_synthesis` | 跨實驗綜合 |
| `Analyzer` / `Synthesizer` | 編排類 |
| `AnalysisAgent` | 核心多階段智慧體 |
| `AnalysisConfig` | 溫度、重試、`analysis_skill_names` 等 |

完整列表見 `__init__.py` 中 `__all__`。

## 配置要點

```python
AnalysisConfig(
    workspace_path="...",
    max_analysis_retries=5,
    max_strategy_retries=3,
    max_visualization_retries=3,
    analysis_skill_names=[
        "subagent_workflow",
        "visualization_reliability",
        "core_skills",
        "advanced_analysis",
    ],
    analysis_skill_strict_selection=True,
)
```

## instruction_md 檔案索引

| 檔案 | `name`（frontmatter） |
|------|------------------------|
| `00_xml_contract.md` | `xml_contract`（`required: true`） |
| `10_subagent_workflow.md` | `subagent_workflow` |
| `15_visualization_reliability.md` | `visualization_reliability` |
| `20_core_skills.md` | `core_skills` |
| `30_advanced_analysis.md` | `advanced_analysis` |

## 行為約定

- XML 解析失敗會觸發階段內重試；報告階段見 `Reporter` 與 `ReportGenerationResult`。
- 大資料集在程式碼執行 prompt 中要求取樣，避免 OOM。
- 空表須在洞察與報告中顯式說明資料限制。

## 擴充套件與 IDE

- VS Code：使用擴充套件內 `extension/skills/agentsociety-analysis`（該目錄下的 `SKILL.md` 才是工作流技能說明），指令碼呼叫 `run_analysis_workflow` 等同 API。
