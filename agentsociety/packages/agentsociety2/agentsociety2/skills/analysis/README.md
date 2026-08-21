# Analysis（实验分析子模块）

从仿真工作区读取 SQLite 与实验文档，经 **数据优先** 的多阶段流程生成洞察、图表与中英双语报告。

**说明**：本文件是**给人看的模块文档**（普通 README）。与 Agent/扩展里可 invocable 的 `SKILL.md` 不是同一类文件。代码入口见 `__init__.py` 导出。

## 架构（分层）

```
service.py     Analyzer · Synthesizer · run_analysis_workflow
     │
agents.py      AnalysisAgent（洞察 → 策略/ReAct 工具环 → 可视化裁判）
     │
data.py        DataReader · ContextLoader · DataSummary
output.py      Reporter · ReportWriter · AssetManager · EDAGenerator
executor.py    AnalysisRunner · CodeExecutor · ToolRegistry
llm_contracts.py   LLM 输出 XML 契约（函数 + 常量）
instruction_md/    可拼接 Markdown 能力说明（utils.get_analysis_skills）
utils.py       路径 · Schema · XML 解析
models.py      AnalysisConfig、裁判类型（AnalysisJudgment 等）、路径常量
```

**单实验主路径**：`Analyzer.analyze` → `AnalysisAgent.analyze`（`DataReader.read_full_summary` + `AnalysisRunner`）→ `AssetProcessor`/`AssetManager` + `Reporter.generate`。

**LLM 两类约定**：`llm_contracts.py` 规定 **XML 输出形状**（与代码解析器一致）；`instruction_md/` 提供 **可编辑的行为说明**（由下面「instruction 技能」机制注入）。

## instruction 技能（`analysis_skill_names`）是干什么的？

这里说的 **skill** 不是 Agent 目录里的 `SKILL.md` 技能，而是 **分析子模块专用的「指令片段」**：

1. **内容**：`instruction_md/*.md` 里的 Markdown（frontmatter 仅用于元数据，**不会**发给 LLM）。
2. **注入位置**：拼进 **system**（或带 system 的消息）里，让模型在写洞察、选工具、写报告时遵守同一套流程与质量要求。
3. **筛选规则**（`get_analysis_skills`）：
   - `required: true` 的条目（如 `xml_contract`）**总是**注入；
   - `analysis_skill_strict_selection=True` 时，再额外注入 `analysis_skill_names` 里列出的 `name`；
   - `False` 且未指定名单时，注入目录下全部片段。

这样可以在 **不改 Python** 的情况下，通过增删 Markdown 或改配置调整分析风格；与 `llm_contracts` 分工为：**契约管格式，instruction 管语义与流程**。

## 快速开始

```python
from agentsociety2.skills.analysis import run_analysis, run_synthesis, AnalysisConfig

result = await run_analysis(
    workspace_path="./workspace",
    hypothesis_id="1",
    experiment_id="1",
)

await run_synthesis(workspace_path="./workspace", hypothesis_ids=["1", "2"])
```

## 工作区与产物

**输入**：`hypothesis_{id}/experiment_{id}/run/sqlite.db`、`EXPERIMENT.md`；假设侧 `HYPOTHESIS.md`。

**单实验输出**：`presentation/hypothesis_{id}/experiment_{id}/`

| 路径 | 说明 |
|------|------|
| `report.md` / `report.html` | 默认报告（优先中文） |
| `report_zh.*` / `report_en.*` | 中英分文件 |
| `data/analysis_summary.json` | 结构化分析结果 |
| `data/eda_profile.html` / `eda_sweetviz.html` | 可选 EDA |
| `charts/` | 代码执行生成的图表 |
| `assets/` | 报告引用图片（从 charts / run/artifacts 汇总） |
| `README.md` | 该次分析**输出目录**内自动生成的文件索引（与本包 `README.md` 不同） |

**综合**：`synthesis/synthesis_report_*.md|html`（见 `Synthesizer`）。

## 公共 API（节选）

| 符号 | 用途 |
|------|------|
| `run_analysis` / `run_analysis_many` / `run_analysis_workflow` | 便捷入口 |
| `run_synthesis` | 跨实验综合 |
| `Analyzer` / `Synthesizer` | 编排类 |
| `AnalysisAgent` | 核心多阶段智能体 |
| `AnalysisConfig` | 温度、重试、`analysis_skill_names` 等 |

完整列表见 `__init__.py` 中 `__all__`。

## 配置要点

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

## instruction_md 文件索引

| 文件 | `name`（frontmatter） |
|------|------------------------|
| `00_xml_contract.md` | `xml_contract`（`required: true`） |
| `10_subagent_workflow.md` | `subagent_workflow` |
| `15_visualization_reliability.md` | `visualization_reliability` |
| `20_core_skills.md` | `core_skills` |
| `30_advanced_analysis.md` | `advanced_analysis` |

## 行为约定

- XML 解析失败会触发阶段内重试；报告阶段见 `Reporter` 与 `ReportGenerationResult`。
- 大数据集在代码执行 prompt 中要求采样，避免 OOM。
- 空表须在洞察与报告中显式说明数据限制。

## 扩展与 IDE

- VS Code：使用扩展内 `extension/skills/agentsociety-analysis`（该目录下的 `SKILL.md` 才是工作流技能说明），脚本调用 `run_analysis_workflow` 等同 API。
