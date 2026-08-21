AgentSociety 2
==============

**AgentSociety 2** 是一个现代化的、LLM 原生的智能体模拟平台，专为社会科学研究和实验设计。它提供了一个灵活的框架，用于在模拟环境中创建和管理智能体。

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

* **LLM 驱动的智能体**: 创建具有个性、记忆和推理能力的智能体，由大语言模型驱动。

* **灵活的环境模块**: 使用可组合的工具和状态管理构建自定义模拟环境。

* **异步优先设计**: 高性能异步架构，实现高效的多智能体模拟。

* **回放与分析**: 基于 SQLite 的内置存储，用于实验跟踪和分析。

* **研究技能**: 内置文献检索、假设生成、实验设计、论文撰写等 LLM 原生工作流。

* **REST API**: 基于 FastAPI 的独立后端服务，支持外部集成。

* **CLI 工具**: 强大的命令行界面，支持实验运行和进度跟踪。

* **可扩展**: 轻松扩展自定义智能体、环境和工具。

安装
------------

.. code-block:: bash

   pip install agentsociety2

详细安装说明请参见 :doc:`installation`。

快速开始
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

更多示例请参见 :doc:`quickstart`。

文档
-------------

.. toctree::
   :maxdepth: 2
   :caption: 入门指南:

   installation
   quickstart
   cli
   concepts
   interaction

.. toctree::
   :maxdepth: 2
   :caption: 用户指南:

   agents
   agent_skills
   env_modules
   storage
   custom_modules
   skills

.. toctree::
   :maxdepth: 2
   :caption: 开发者指南:

   development
   module_and_parameter_management
   contributing

.. toctree::
   :maxdepth: 2
   :caption: 参考:

   api/index
   examples

链接
-----

* **GitHub**: https://github.com/tsinghua-fib-lab/AgentSociety
* **PyPI**: https://pypi.org/project/agentsociety2/
* **Issues**: https://github.com/tsinghua-fib-lab/AgentSociety/issues

搜索
------

* :ref:`search`
