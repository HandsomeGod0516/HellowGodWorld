核心概念
=============

本部分解釋 AgentSociety 2 的核心概念。

架構概述
---------------------

AgentSociety 2 圍繞三個主要元件構建：

* **智慧體 (Agents)**: 使用 LLM 與環境互動的自主實體
* **環境模組 (Environment Modules)**: 定義模擬規則的可組合元件
* **AgentSociety**: 管理智慧體和環境的協調器

.. graphviz::

   digraph agentsociety2 {
       rankdir=TB;
       node [shape=box, style=rounded];

       Agent [label="Agent"];
       CodeGenRouter [label="CodeGenRouter"];
       EnvModule [label="Env Module"];
       Tool [label="@tool()"];

       Agent -> CodeGenRouter [label="ask/intervene"];
       CodeGenRouter -> EnvModule [label="calls tools"];
       EnvModule -> Tool [label="decorated with"];
   }

完整系統架構
~~~~~~~~~~~~~~~~~

.. graphviz::

   digraph full_architecture {
       rankdir=TB;
       node [shape=box, style=rounded];
       edge [fontsize=10];

       subgraph cluster_ui {
           label = "使用者介面層";
           style=filled;
           color=lightgrey;
           CLI [label="CLI 命令列"];
           WebUI [label="Web 前端"];
           API [label="REST API"];
       }

       subgraph cluster_core {
           label = "核心模擬層";
           style=filled;
           color=lightblue;
           Society [label="AgentSociety\n協調器"];
           Router [label="Router\n路由器"];
           Storage [label="ReplayWriter / Workspace\n儲存"];
       }

       subgraph cluster_agents {
           label = "智慧體層";
           style=filled;
           color=lightgreen;
           Agent1 [label="PersonAgent 1"];
           Agent2 [label="PersonAgent 2"];
           AgentN [label="... PersonAgent N"];
       }

       subgraph cluster_env {
           label = "環境層";
           style=filled;
           color=lightyellow;
           Env1 [label="SocialSpace"];
           Env2 [label="EconomySpace"];
           EnvN [label="... 自定義模組"];
       }

       subgraph cluster_external {
           label = "外部服務";
           style=filled;
           color=lavender;
           LLM [label="LLM Provider"];
           Memory [label="mem0 (遙測已禁用)"];
       }

       CLI -> Society;
       WebUI -> API;
       API -> Society;

       Society -> Router;
       Society -> Storage;
       Society -> Agent1;
       Society -> Agent2;
       Society -> AgentN;

       Router -> Env1;
       Router -> Env2;
       Router -> EnvN;

       Agent1 -> Router;
       Agent2 -> Router;
       AgentN -> Router;

       Society -> LLM;
       Agent1 -> Memory;
       Agent2 -> Memory;
   }

智慧體-環境介面
----------------------------

智慧體透過兩個主要方法與環境互動：

* **ask()**: 查詢或觀察環境狀態
* **intervene()**: 修改環境狀態

這個統一介面允許智慧體與任何環境模組自然通訊。

@tool 裝飾器
-------------------

環境模組透過 ``@tool`` 裝飾器公開其功能：

.. code-block:: python

   from agentsociety2.env import EnvBase, tool

   class MyEnvironment(EnvBase):
       @tool(readonly=True, kind="observe")
       def get_weather(self, agent_id: int) -> str:
           """Get current weather for agent."""
           return f"Weather for agent {agent_id}"

       @tool(readonly=False)
       def set_temperature(self, temp: int) -> str:
           """Set temperature."""
           self._temperature = temp
           return f"Temperature set to {temp}"

**引數：**

* ``readonly`` (bool): 函式是否修改狀態
  * ``True`` = 只讀觀察
  * ``False`` = 修改環境

* ``kind`` (str): 用於最佳化的函式類別
  * ``"observe"``: 單引數觀察
  * ``"statistics"``: 聚合查詢（無引數）
  * ``None``: 常規工具

CodeGenRouter
-------------

CodeGenRouter 透過以下方式將智慧體連線到環境模組：

1. 從環境模組中提取工具簽名
2. 根據智慧體輸入生成呼叫適當工具的程式碼
3. 在沙盒環境中安全執行程式碼
4. 將結果返回給智慧體

這種方法允許智慧體與任何環境模組組合互動，而無需更改程式碼。

工具類別
---------------

**觀察工具** (``readonly=True``, ``kind="observe"``)

具有單個 ``agent_id`` 引數的智慧體特定觀察：

.. code-block:: python

   @tool(readonly=True, kind="observe")
   def get_agent_location(self, agent_id: int) -> str:
       """Get current location of agent."""
       return f"Agent {agent_id} is at location X"

**統計工具** (``readonly=True``, ``kind="statistics"``)

沒有引數的聚合查詢（除了 ``self``）：

.. code-block:: python

   @tool(readonly=True, kind="statistics")
   def get_average_happiness(self) -> str:
       """Get average happiness of all agents."""
       avg = sum(self.happiness.values()) / len(self.happiness)
       return f"Average happiness: {avg}"

**常規工具**

具有任何簽名的通用工具：

.. code-block:: python

   @tool(readonly=False)
   def set_happiness(self, agent_id: int, value: float) -> str:
       """Set happiness level for agent."""
       self.happiness[agent_id] = value
       return f"Set agent {agent_id}'s happiness to {value}"

工具類別層次結構
~~~~~~~~~~~~~~~~~

.. graphviz::

   digraph tool_hierarchy {
       rankdir=TB;
       node [shape=box, style=rounded];

       Root [label="@tool 裝飾器", shape=ellipse];

       Readonly [label="readonly=True", shape=diamond];
       Readwrite [label="readonly=False", shape=diamond];

       Observe [label="kind='observe'", shape=box];
       Statistics [label="kind='statistics'", shape=box];
       Regular [label="kind=None", shape=box];

       Root -> Readonly;
       Root -> Readwrite;

       Readonly -> Observe;
       Readonly -> Statistics;
       Readwrite -> Regular;

       Observe [label="觀察工具\n單引數(agent_id)"];
       Statistics [label="統計工具\n無引數"];
       Regular [label="常規工具\n任意簽名"];
   }

智慧體-環境互動流程
~~~~~~~~~~~~~~~~~~~~

.. graphviz::

   digraph interaction_flow {
       rankdir=TB;
       node [shape=box, style=rounded];

       Agent [label="智慧體"];
       Ask [label="ask/intervene()"];
       Router [label="Router"];
       Tools [label="@tool 方法"];
       Env [label="環境狀態"];
       Response [label="響應"];

       Agent -> Ask;
       Ask -> Router;
       Router -> Tools [label="提取工具簽名"];
       Router -> Tools [label="生成呼叫程式碼"];
       Tools -> Env [label="執行工具"];
       Env -> Tools [label="返回結果"];
       Tools -> Router;
       Router -> Response;
       Response -> Agent;
   }
