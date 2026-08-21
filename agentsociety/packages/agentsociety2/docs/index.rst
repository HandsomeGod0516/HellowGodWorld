AgentSociety 2
==============

**AgentSociety 2** 是一個現代化的、LLM 原生的智慧體模擬平臺，專為社會科學研究和實驗設計。它提供了一個靈活的框架，用於在模擬環境中建立和管理智慧體。

.. image:: https://img.shields.io/pypi/v/agentsociety2.svg
   :target: https://pypi.org/project/agentsociety2/
   :alt: PyPI Version

.. image:: https://img.shields.io/pypi/pyversions/agentsociety2.svg
   :target: https://pypi.org/project/agentsociety2/
   :alt: Python Versions

.. image:: https://img.shields.io/badge/License-Apache%202.0-blue.svg
   :target: LICENSE
   :alt: License

核心特性
------------

* **LLM 驅動的智慧體**: 建立具有個性、記憶和推理能力的智慧體，由大語言模型驅動。

* **靈活的環境模組**: 使用可組合的工具和狀態管理構建自定義模擬環境。

* **非同步優先設計**: 高效能非同步架構，實現高效的多智慧體模擬。

* **回放與分析**: 基於 SQLite 的內建儲存，用於實驗跟蹤和分析。

* **研究技能**: 內建文獻檢索、假設生成、實驗設計、論文撰寫等 LLM 原生工作流。

* **REST API**: 基於 FastAPI 的獨立後端服務，支援外部整合。

* **CLI 工具**: 強大的命令列介面，支援實驗執行和進度跟蹤。

* **可擴充套件**: 輕鬆擴充套件自定義智慧體、環境和工具。

安裝
------------

.. code-block:: bash

   pip install agentsociety2

詳細安裝說明請參見 :doc:`installation`。

快速開始
-----------

.. code-block:: python

   from agentsociety2 import PersonAgent
   from agentsociety2.society import AgentSociety

   # Create an agent
   agent = PersonAgent(
       id=1,
       profile={
           "name": "Alice",
           "age": 28,
           "personality": "friendly and curious",
           "bio": "A software engineer who loves hiking."
       }
   )

   # Ask the agent a question
   response = await agent.ask("What's your favorite hobby?")
   print(response)

更多示例請參見 :doc:`quickstart`。

文件
-------------

.. toctree::
   :maxdepth: 2
   :caption: 入門指南:

   installation
   quickstart
   cli
   concepts
   interaction

.. toctree::
   :maxdepth: 2
   :caption: 使用者指南:

   agents
   agent_skills
   env_modules
   storage
   custom_modules
   skills

.. toctree::
   :maxdepth: 2
   :caption: 開發者指南:

   development
   module_and_parameter_management
   contributing

.. toctree::
   :maxdepth: 2
   :caption: 參考:

   api/index
   examples

連結
-----

* **GitHub**: https://github.com/tsinghua-fib-lab/AgentSociety
* **PyPI**: https://pypi.org/project/agentsociety2/
* **Issues**: https://github.com/tsinghua-fib-lab/AgentSociety/issues

搜尋
------

* :ref:`search`
