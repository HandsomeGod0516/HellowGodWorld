研究技能
========================================

AgentSociety 2 包含一組 LLM 原生的研究技能，用於自動化科學研究工作流。

概述
------------

研究技能模組提供以下功能：

* **文獻檢索**: 搜尋和管理學術論文
* **假設生成**: 從研究問題生成可測試的假設
* **實驗設計**: 設計完整的實驗配置
* **網路研究**: 使用 Miro 進行網路搜尋和總結
* **論文撰寫**: 使用 EasyPaper 生成學術論文
* **資料分析**: 分析實驗資料並生成報告
* **智慧體處理**: 智慧體選擇、生成和過濾

Claude Code Skills
--------------------

研究工作流主要透過 Claude Code 的“skills-first”方式提供：
- AgentSociety 內建研究 skills：隨 VSCode 外掛打包，可在外掛樹檢視中瀏覽（只讀）。
- Agent(Person) 擴充套件 skills：由後端 `/api/v1/agent-skills/*` 管理，支援掃描/匯入/熱過載。

* **agentsociety-literature-search** - 文獻檢索
* **agentsociety-hypothesis** - 假設管理（add, get, list, delete）
* **agentsociety-experiment-config** - 實驗配置生成與驗證
* **agentsociety-run-experiment** - 實驗執行與監控
* **agentsociety-analysis** - 資料分析
* **agentsociety-synthesize** - 結果綜合
* **agentsociety-generate-paper** - 論文生成
* **agentsociety-quick-web-search** - 快速網路搜尋
* **agentsociety-web-research** - 深度網路研究

Python API
--------------------

研究技能也可以透過 Python API 直接呼叫。

文獻技能 (literature)
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from agentsociety2.skills.literature import search_literature_and_save, load_literature_index

   # 搜尋並儲存文獻（預設搜尋所有資料來源）
   await search_literature_and_save(
       workspace_path=Path("./workspace"),
       query="agent-based modeling social networks",
       limit=10,
       year_from=2020,      # 可選：年份篩選
       year_to=2024,
       enable_multi_query=True,  # 可選：啟用多查詢模式
   )

   # 指定資料來源搜尋
   await search_literature_and_save(
       workspace_path=Path("./workspace"),
       query="machine learning",
       limit=5,
       sources=["local", "arxiv"],  # 可選：指定資料來源
   )

   # 載入文獻索引
   index = load_literature_index(workspace_path=Path("./workspace"))

**資料來源**:
- ``local``: RAGFlow 本地知識庫
- ``arxiv``: arXiv 預印本平臺
- ``crossref``: CrossRef DOI 後設資料庫
- ``openalex``: OpenAlex 學術圖譜 (2.5億+ 論文)

**配置**:
需要在 ``.env`` 檔案中配置 API:

.. code-block:: bash

   LITERATURE_SEARCH_API_URL=http://localhost:8008/api/search
   LITERATURE_SEARCH_API_KEY=lit-your-api-key-here

假設技能 (hypothesis)
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from agentsociety2.skills.hypothesis import add_hypothesis, get_hypothesis, list_hypotheses

   # 新增假設
   add_hypothesis(
       workspace_path=Path("./workspace"),
       hypothesis="網路密度越高，資訊傳播速度越快"
   )

   # 列出假設
   hypotheses = list_hypotheses(workspace_path=Path("./workspace"))

實驗技能 (experiment)
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from agentsociety2.skills.experiment import (
       start_experiment, get_experiment_status,
       get_available_env_modules, get_available_agent_modules
   )

   # 獲取可用模組
   env_modules = get_available_env_modules()
   agent_modules = get_available_agent_modules()

   # 啟動實驗
   await start_experiment(
       workspace_path=Path("./workspace"),
       hypothesis_id="1",
       experiment_id="1"
   )

分析技能 (analysis)
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from agentsociety2.skills.analysis import (
       run_analysis,
       run_analysis_many,
       run_analysis_workflow,
       Analyzer,
       run_synthesis,
   )

   # 使用便捷函式
   result = await run_analysis(
       workspace_path=Path("./workspace"),
       hypothesis_id="1",
       experiment_id="1"
   )

   # 同一 hypothesis 下批次分析（experiment_ids 不傳則自動發現）
   batch = await run_analysis_many(
       workspace_path=str(Path("./workspace")),
       hypothesis_id="1",
       experiment_ids=["1", "2", "3"],  # 可選
   )

   # 統一入口：single | batch | synthesize
   out = await run_analysis_workflow(
       workspace_path=str(Path("./workspace")),
       mode="synthesize",
       hypothesis_ids=["1"],          # 可選，不傳則自動發現
       experiment_ids=["1", "2", "3"] # 可選，不傳則分析全部
   )

   # 使用 Analyzer 類
   analyzer = Analyzer(workspace_path=Path("./workspace"))
   await analyzer.analyze(hypothesis_id="1", experiment_id="1")

論文技能 (paper)
~~~~~~~~~~~~~~~~~

.. code-block:: python

   from agentsociety2.skills.paper.generator import generate_paper_from_metadata

   result = await generate_paper_from_metadata(
       metadata=paper_metadata,
       output_dir=Path("./output"),
       figures_source_dir=Path("./figures")
   )

完整工作流示例
------------------------

下面是一個使用 Claude Code Skills 的典型研究工作流：

1. **定義研究話題** - 編輯 ``TOPIC.md``
2. **文獻檢索** - 使用 ``/agentsociety-literature-search``
3. **建立假設** - 使用 ``/agentsociety-hypothesis add``
4. **配置實驗** - 使用 ``/agentsociety-experiment-config validate/prepare/run``
5. **執行實驗** - 使用 ``/agentsociety-run-experiment start``
6. **分析結果** - 使用 ``/agentsociety-analysis``
7. **生成論文** - 使用 ``/agentsociety-generate-paper``

配置
------------------------

研究技能使用相同的 LLM 配置。可以透過環境變數為特定技能配置不同的模型：

.. code-block:: bash

   # 預設 LLM
   export AGENTSOCIETY_LLM_MODEL="gpt-5.4"

   # 程式碼生成（實驗設計、分析）
   export AGENTSOCIETY_CODER_LLM_MODEL="gpt-5.4"

   # 高頻操作（智慧體生成）
   export AGENTSOCIETY_NANO_LLM_MODEL="gpt-5.4-nano"

Agent Skills
--------------------

AgentSociety 2 還支援 Agent Skills，這些是 PersonAgent 的認知能力模組：

* **observation** - 環境感知
* **needs** - 需求系統
* **cognition** - 認知與意圖
* **plan** - 規劃與執行
* **memory** - 記憶管理

詳見 :doc:`agent_skills`。

參考
------------------------

* :doc:`cli` - 使用 CLI 執行實驗
* :doc:`agent_skills` - Agent Skills 詳解
* :doc:`custom_modules` - 建立自定義模組
* :doc:`development` - 開發指南
