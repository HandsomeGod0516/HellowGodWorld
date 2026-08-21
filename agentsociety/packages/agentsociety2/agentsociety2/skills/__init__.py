"""AgentSociety2 技能模組。

本模組包含各種研究工作流的核心業務邏輯，支援完整的科研流程：

技能列表
========

- **literature**: 學術文獻搜尋與管理，支援檢索、索引和格式化
- **experiment**: 實驗配置與執行，支援引數生成和配置驗證
- **hypothesis**: 假設生成與管理，支援建立、讀取、列表和刪除
- **web_research**: 使用 Miro MCP 服務進行網路研究
- **paper**: 學術論文生成，支援 EasyPaper 工作流
- **analysis**: 資料分析與報告生成，包含洞察智慧體和資料探索智慧體

使用示例
========

.. code-block:: python

    from agentsociety2.skills import literature, hypothesis, analysis

    # 文獻檢索
    results = await literature.search_literature("machine learning")

    # 建立假設
    hypothesis.add_hypothesis(
        workspace_path=Path("./workspace"),
        hypothesis="社會網路密度影響資訊傳播速度"
    )

    # 分析實驗結果
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
