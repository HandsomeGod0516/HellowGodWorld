"""AgentSociety2 技能模块。

本模块包含各种研究工作流的核心业务逻辑，支持完整的科研流程：

技能列表
========

- **literature**: 学术文献搜索与管理，支持检索、索引和格式化
- **experiment**: 实验配置与执行，支持参数生成和配置验证
- **hypothesis**: 假设生成与管理，支持创建、读取、列表和删除
- **web_research**: 使用 Miro MCP 服务进行网络研究
- **paper**: 学术论文生成，支持 EasyPaper 工作流
- **analysis**: 数据分析与报告生成，包含洞察智能体和数据探索智能体

使用示例
========

.. code-block:: python

    from agentsociety2.skills import literature, hypothesis, analysis

    # 文献检索
    results = await literature.search_literature("machine learning")

    # 创建假设
    hypothesis.add_hypothesis(
        workspace_path=Path("./workspace"),
        hypothesis="社会网络密度影响信息传播速度"
    )

    # 分析实验结果
    await analysis.run_analysis(
        workspace_path=Path("./workspace"),
        hypothesis_id="1",
        experiment_id="1"
    )
"""

from agentsociety2.skills import (
    literature,
    experiment,
    hypothesis,
    web_research,
    paper,
    analysis,
)

__all__ = [
    "literature",
    "experiment",
    "hypothesis",
    "web_research",
    "paper",
    "analysis",
]
