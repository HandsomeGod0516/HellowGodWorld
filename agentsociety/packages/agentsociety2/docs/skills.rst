研究技能
========================================

AgentSociety 2 包含一组 LLM 原生的研究技能，用于自动化科学研究工作流。

概述
------------

研究技能模块提供以下功能：

* **文献检索**: 搜索和管理学术论文
* **假设生成**: 从研究问题生成可测试的假设
* **实验设计**: 设计完整的实验配置
* **网络研究**: 使用 Miro 进行网络搜索和总结
* **论文撰写**: 使用 EasyPaper 生成学术论文
* **数据分析**: 分析实验数据并生成报告
* **智能体处理**: 智能体选择、生成和过滤

Claude Code Skills
--------------------

研究工作流主要通过 Claude Code 的“skills-first”方式提供：
- AgentSociety 内置研究 skills：随 VSCode 插件打包，可在插件树视图中浏览（只读）。
- Agent(Person) 扩展 skills：由后端 `/api/v1/agent-skills/*` 管理，支持扫描/导入/热重载。

* **agentsociety-literature-search** - 文献检索
* **agentsociety-hypothesis** - 假设管理（add, get, list, delete）
* **agentsociety-experiment-config** - 实验配置生成与验证
* **agentsociety-run-experiment** - 实验执行与监控
* **agentsociety-analysis** - 数据分析
* **agentsociety-synthesize** - 结果综合
* **agentsociety-generate-paper** - 论文生成
* **agentsociety-quick-web-search** - 快速网络搜索
* **agentsociety-web-research** - 深度网络研究

Python API
--------------------

研究技能也可以通过 Python API 直接调用。

文献技能 (literature)
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from agentsociety2.skills.literature import search_literature_and_save, load_literature_index

   # 搜索并保存文献（默认搜索所有数据源）
   await search_literature_and_save(
       workspace_path=Path("./workspace"),
       query="agent-based modeling social networks",
       limit=10,
       year_from=2020,      # 可选：年份筛选
       year_to=2024,
       enable_multi_query=True,  # 可选：启用多查询模式
   )

   # 指定数据源搜索
   await search_literature_and_save(
       workspace_path=Path("./workspace"),
       query="machine learning",
       limit=5,
       sources=["local", "arxiv"],  # 可选：指定数据源
   )

   # 加载文献索引
   index = load_literature_index(workspace_path=Path("./workspace"))

**数据源**:
- ``local``: RAGFlow 本地知识库
- ``arxiv``: arXiv 预印本平台
- ``crossref``: CrossRef DOI 元数据库
- ``openalex``: OpenAlex 学术图谱 (2.5亿+ 论文)

**配置**:
需要在 ``.env`` 文件中配置 API:

.. code-block:: bash

   LITERATURE_SEARCH_API_URL=http://localhost:8008/api/search
   LITERATURE_SEARCH_API_KEY=lit-your-api-key-here

假设技能 (hypothesis)
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from agentsociety2.skills.hypothesis import add_hypothesis, get_hypothesis, list_hypotheses

   # 添加假设
   add_hypothesis(
       workspace_path=Path("./workspace"),
       hypothesis="网络密度越高，信息传播速度越快"
   )

   # 列出假设
   hypotheses = list_hypotheses(workspace_path=Path("./workspace"))

实验技能 (experiment)
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from agentsociety2.skills.experiment import (
       start_experiment, get_experiment_status,
       get_available_env_modules, get_available_agent_modules
   )

   # 获取可用模块
   env_modules = get_available_env_modules()
   agent_modules = get_available_agent_modules()

   # 启动实验
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

   # 使用便捷函数
   result = await run_analysis(
       workspace_path=Path("./workspace"),
       hypothesis_id="1",
       experiment_id="1"
   )

   # 同一 hypothesis 下批量分析（experiment_ids 不传则自动发现）
   batch = await run_analysis_many(
       workspace_path=str(Path("./workspace")),
       hypothesis_id="1",
       experiment_ids=["1", "2", "3"],  # 可选
   )

   # 统一入口：single | batch | synthesize
   out = await run_analysis_workflow(
       workspace_path=str(Path("./workspace")),
       mode="synthesize",
       hypothesis_ids=["1"],          # 可选，不传则自动发现
       experiment_ids=["1", "2", "3"] # 可选，不传则分析全部
   )

   # 使用 Analyzer 类
   analyzer = Analyzer(workspace_path=Path("./workspace"))
   await analyzer.analyze(hypothesis_id="1", experiment_id="1")

论文技能 (paper)
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

下面是一个使用 Claude Code Skills 的典型研究工作流：

1. **定义研究话题** - 编辑 ``TOPIC.md``
2. **文献检索** - 使用 ``/agentsociety-literature-search``
3. **创建假设** - 使用 ``/agentsociety-hypothesis add``
4. **配置实验** - 使用 ``/agentsociety-experiment-config validate/prepare/run``
5. **执行实验** - 使用 ``/agentsociety-run-experiment start``
6. **分析结果** - 使用 ``/agentsociety-analysis``
7. **生成论文** - 使用 ``/agentsociety-generate-paper``

配置
------------------------

研究技能使用相同的 LLM 配置。可以通过环境变量为特定技能配置不同的模型：

.. code-block:: bash

   # 默认 LLM
   export AGENTSOCIETY_LLM_MODEL="gpt-5.4"

   # 代码生成（实验设计、分析）
   export AGENTSOCIETY_CODER_LLM_MODEL="gpt-5.4"

   # 高频操作（智能体生成）
   export AGENTSOCIETY_NANO_LLM_MODEL="gpt-5.4-nano"

Agent Skills
--------------------

AgentSociety 2 还支持 Agent Skills，这些是 PersonAgent 的认知能力模块：

* **observation** - 环境感知
* **needs** - 需求系统
* **cognition** - 认知与意图
* **plan** - 规划与执行
* **memory** - 记忆管理

详见 :doc:`agent_skills`。

参考
------------------------

* :doc:`cli` - 使用 CLI 运行实验
* :doc:`agent_skills` - Agent Skills 详解
* :doc:`custom_modules` - 创建自定义模块
* :doc:`development` - 开发指南
