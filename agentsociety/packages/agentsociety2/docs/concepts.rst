核心概念
=============

本部分解释 AgentSociety 2 的核心概念。

架构概述
---------------------

AgentSociety 2 围绕三个主要组件构建：

* **智能体 (Agents)**: 使用 LLM 与环境交互的自主实体
* **环境模块 (Environment Modules)**: 定义模拟规则的可组合组件
* **AgentSociety**: 管理智能体和环境的协调器

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

完整系统架构
~~~~~~~~~~~~~~~~~

.. graphviz::

   digraph full_architecture {
       rankdir=TB;
       node [shape=box, style=rounded];
       edge [fontsize=10];

       subgraph cluster_ui {
           label = "用户界面层";
           style=filled;
           color=lightgrey;
           CLI [label="CLI 命令行"];
           WebUI [label="Web 前端"];
           API [label="REST API"];
       }

       subgraph cluster_core {
           label = "核心模拟层";
           style=filled;
           color=lightblue;
           Society [label="AgentSociety\n协调器"];
           Router [label="Router\n路由器"];
           Storage [label="ReplayWriter / Workspace\n存储"];
       }

       subgraph cluster_agents {
           label = "智能体层";
           style=filled;
           color=lightgreen;
           Agent1 [label="PersonAgent 1"];
           Agent2 [label="PersonAgent 2"];
           AgentN [label="... PersonAgent N"];
       }

       subgraph cluster_env {
           label = "环境层";
           style=filled;
           color=lightyellow;
           Env1 [label="SocialSpace"];
           Env2 [label="EconomySpace"];
           EnvN [label="... 自定义模块"];
       }

       subgraph cluster_external {
           label = "外部服务";
           style=filled;
           color=lavender;
           LLM [label="LLM Provider"];
           Memory [label="mem0 (遥测已禁用)"];
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

智能体-环境接口
----------------------------

智能体通过两个主要方法与环境交互：

* **ask()**: 查询或观察环境状态
* **intervene()**: 修改环境状态

这个统一接口允许智能体与任何环境模块自然通信。

@tool 装饰器
-------------------

环境模块通过 ``@tool`` 装饰器公开其功能：

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

**参数：**

* ``readonly`` (bool): 函数是否修改状态
  * ``True`` = 只读观察
  * ``False`` = 修改环境

* ``kind`` (str): 用于优化的函数类别
  * ``"observe"``: 单参数观察
  * ``"statistics"``: 聚合查询（无参数）
  * ``None``: 常规工具

CodeGenRouter
-------------

CodeGenRouter 通过以下方式将智能体连接到环境模块：

1. 从环境模块中提取工具签名
2. 根据智能体输入生成调用适当工具的代码
3. 在沙盒环境中安全执行代码
4. 将结果返回给智能体

这种方法允许智能体与任何环境模块组合交互，而无需更改代码。

工具类别
---------------

**观察工具** (``readonly=True``, ``kind="observe"``)

具有单个 ``agent_id`` 参数的智能体特定观察：

.. code-block:: python

   @tool(readonly=True, kind="observe")
   def get_agent_location(self, agent_id: int) -> str:
       """Get current location of agent."""
       return f"Agent {agent_id} is at location X"

**统计工具** (``readonly=True``, ``kind="statistics"``)

没有参数的聚合查询（除了 ``self``）：

.. code-block:: python

   @tool(readonly=True, kind="statistics")
   def get_average_happiness(self) -> str:
       """Get average happiness of all agents."""
       avg = sum(self.happiness.values()) / len(self.happiness)
       return f"Average happiness: {avg}"

**常规工具**

具有任何签名的通用工具：

.. code-block:: python

   @tool(readonly=False)
   def set_happiness(self, agent_id: int, value: float) -> str:
       """Set happiness level for agent."""
       self.happiness[agent_id] = value
       return f"Set agent {agent_id}'s happiness to {value}"

工具类别层次结构
~~~~~~~~~~~~~~~~~

.. graphviz::

   digraph tool_hierarchy {
       rankdir=TB;
       node [shape=box, style=rounded];

       Root [label="@tool 装饰器", shape=ellipse];

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

       Observe [label="观察工具\n单参数(agent_id)"];
       Statistics [label="统计工具\n无参数"];
       Regular [label="常规工具\n任意签名"];
   }

智能体-环境交互流程
~~~~~~~~~~~~~~~~~~~~

.. graphviz::

   digraph interaction_flow {
       rankdir=TB;
       node [shape=box, style=rounded];

       Agent [label="智能体"];
       Ask [label="ask/intervene()"];
       Router [label="Router"];
       Tools [label="@tool 方法"];
       Env [label="环境状态"];
       Response [label="响应"];

       Agent -> Ask;
       Ask -> Router;
       Router -> Tools [label="提取工具签名"];
       Router -> Tools [label="生成调用代码"];
       Tools -> Env [label="执行工具"];
       Env -> Tools [label="返回结果"];
       Tools -> Router;
       Router -> Response;
       Response -> Agent;
   }
