示例
========

本部分包含演示 AgentSociety 2 功能的示例代码。

运行示例
----------------

所有示例都位于 ``packages/agentsociety2/examples/`` 目录中。

**前提条件：**

1. 安装 AgentSociety 2: ``pip install agentsociety2``
2. 配置 LLM API 凭证（参见 :doc:`installation`）
3. 导航到示例目录

.. code-block:: bash

   cd packages/agentsociety2/examples
   python basics/01_hello_agent.py

基本示例
--------------

这些示例演示 AgentSociety 2 的基本概念：

**Hello Agent** (``basics/01_hello_agent.py``)

一个最小示例，展示：

* 创建具有个性配置文件的单个智能体
* 设置 SimpleSocialSpace 环境
* 使用 AgentSociety 协调智能体-环境交互

.. code-block:: python

   # Create agent with profile
   agent = PersonAgent(
       id=1,
       profile={
           "name": "Alice",
           "age": 28,
           "personality": "friendly, curious, optimistic",
           "bio": "A software engineer who loves hiking and reading."
       }
   )

   # Create environment and society
   society = AgentSociety(agents=[agent], env_router=..., start_t=datetime.now())
   await society.init()

   # Interact
   response = await society.ask("What's your favorite activity?")
   print(f"Agent: {response}")

**自定义环境模块** (``basics/02_custom_env_module.py``)

演示创建自定义环境模块：

* 使用 @tool 装饰器定义自定义环境
* 实现 step() 和工具方法，并按需提供 ``kind="observe"`` 的只读工具
* 向 CodeGenRouter 注册模块

**回放系统** (``basics/03_replay_system.py``)

展示全面的数据跟踪：

* 为环境模块启用 ReplayWriter
* 生成 replay catalog 和环境 replay dataset
* 结合 agent workspace 文件检查本地 thread / 工具日志

博弈论示例
---------------------

**囚徒困境** (``games/01_prisoners_dilemma.py``)

一个经典的博弈论场景：

* 两个具有不同个性的智能体
* 具有收益的顺序决策
* 对结果的反思

**公共物品博弈** (``games/02_public_goods.py``)

多轮集体行动实验：

* 四个具有不同个性特征的智能体
* 多轮贡献决策
* 小组结果计算

高级示例
-----------------

**自定义智能体** (``advanced/01_custom_agent.py``)

使用自定义智能体类型扩展 AgentSociety 2：

* 实现必需的抽象方法（ask、step、dump、load）
* 为研究需求创建专门的智能体

**多路由器比较** (``advanced/02_multi_router.py``)

比较不同的推理策略：

* ReActRouter: 迭代推理和行动
* PlanExecuteRouter: 计划优先执行
* CodeGenRouter: 生成代码执行（推荐）
