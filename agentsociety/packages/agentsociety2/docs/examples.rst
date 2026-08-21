示例
========

本部分包含演示 AgentSociety 2 功能的示例程式碼。

執行示例
----------------

所有示例都位於 ``packages/agentsociety2/examples/`` 目錄中。

**前提條件：**

1. 安裝 AgentSociety 2: ``pip install agentsociety2``
2. 配置 LLM API 憑證（參見 :doc:`installation`）
3. 導航到示例目錄

.. code-block:: bash

   cd packages/agentsociety2/examples
   python basics/01_hello_agent.py

基本示例
--------------

這些示例演示 AgentSociety 2 的基本概念：

**Hello Agent** (``basics/01_hello_agent.py``)

一個最小示例，展示：

* 建立具有個性配置檔案的單個智慧體
* 設定 SimpleSocialSpace 環境
* 使用 AgentSociety 協調智慧體-環境互動

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

**自定義環境模組** (``basics/02_custom_env_module.py``)

演示建立自定義環境模組：

* 使用 @tool 裝飾器定義自定義環境
* 實現 step() 和工具方法，並按需提供 ``kind="observe"`` 的只讀工具
* 向 CodeGenRouter 註冊模組

**回放系統** (``basics/03_replay_system.py``)

展示全面的資料跟蹤：

* 為環境模組啟用 ReplayWriter
* 生成 replay catalog 和環境 replay dataset
* 結合 agent workspace 檔案檢查本地 thread / 工具日誌

博弈論示例
---------------------

**囚徒困境** (``games/01_prisoners_dilemma.py``)

一個經典的博弈論場景：

* 兩個具有不同個性的智慧體
* 具有收益的順序決策
* 對結果的反思

**公共物品博弈** (``games/02_public_goods.py``)

多輪集體行動實驗：

* 四個具有不同個性特徵的智慧體
* 多輪貢獻決策
* 小組結果計算

高階示例
-----------------

**自定義智慧體** (``advanced/01_custom_agent.py``)

使用自定義智慧體型別擴充套件 AgentSociety 2：

* 實現必需的抽象方法（ask、step、dump、load）
* 為研究需求建立專門的智慧體

**多路由器比較** (``advanced/02_multi_router.py``)

比較不同的推理策略：

* ReActRouter: 迭代推理和行動
* PlanExecuteRouter: 計劃優先執行
* CodeGenRouter: 生成程式碼執行（推薦）
