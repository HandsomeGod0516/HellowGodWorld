使用智能体
===================

本部分介绍如何在 AgentSociety 2 中使用智能体。

创建智能体
---------------

PersonAgent
~~~~~~~~~~~

``PersonAgent`` 是一个 **skills-first / tool-using** 智能体实现。它本身是一个轻量编排器，核心行为是“对标 Claude Code 的工具循环”：在每个 step 内注入身份与技能目录，然后由主模型逐轮选择并执行工具（包括技能激活与技能执行）。

.. code-block:: python

   from agentsociety2 import PersonAgent

   agent = PersonAgent(
       id=1,
       profile={
           "name": "Alice",
           "age": 28,
           "personality": "friendly and curious",
           "bio": "A software engineer who loves hiking."
       }
   )

内置 Skills
^^^^^^^^^^^

每个 simulation tick，PersonAgent 都会执行同一套“工具循环”的流程：

.. list-table::
     :widths: 28 72
     :header-rows: 1

     * - 阶段
       - 说明
     * - 注入上下文
       - system prompt 注入身份信息、技能目录、工具表。
     * - 激活技能
       - 需要某个技能时，先用 ``activate_skill`` 加载该技能完整说明（通常来自 ``SKILL.md``）。
     * - 执行技能/工具
       - 用 ``execute_skill`` 执行技能，或直接调用 ``bash`` / ``grep`` / ``glob`` / ``codegen`` 等工具。
     * - 结束条件
       - 当主模型输出 ``done=true`` 时结束本 step。

常见内置技能包括 ``observation``、``needs``、``cognition``、``plan``、``memory``。
它们都不再属于固定“必须执行层”，而是由 LLM 按上下文按需选择。

详细说明请参见 :doc:`agent_skills`。

配置文件可以包含你希望的任何字段；PersonAgent 会把这些信息用于塑造其行为与决策。

自定义智能体
~~~~~~~~~~~~~

.. note::

   对于扩展 PersonAgent 的认知能力，推荐使用 **Agent Skills** 系统。
   参见 :doc:`agent_skills` 了解如何创建自定义 skill。

   只有在需要完全不同的智能体架构时，才需要创建自定义智能体类。

要创建自定义智能体，请继承 ``AgentBase`` 并实现必需的抽象方法：

需要实现的方法
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

创建自定义智能体时，必须实现 ``AgentBase`` 的这些抽象方法：

1. **async def ask(self, message: str, readonly: bool = True) -> str**

   处理来自环境或用户的问题并返回响应。

   参数:
       message: 要处理的问题或指令
       readonly: 智能体是否可以修改环境（False = 可以修改）

   返回:
       智能体的响应字符串

2. **async def step(self, tick: int, t: datetime) -> str**

   执行一个模拟步骤。在 AgentSociety 模拟运行期间调用。

   参数:
       tick: 此步骤的持续时间（秒）
       t: 此步骤后的当前模拟日期时间

   返回:
       智能体在此步骤中的操作描述

3. **async def dump(self) -> dict**

   将智能体状态序列化为字典以便保存/加载。

4. **async def load(self, dump_data: dict)**

   从先前转储的字典中恢复智能体状态。

参考实现
^^^^^^^^^^^^^^^^^^^^^^^^

有关完整参考，请参阅源代码中的 ``PersonAgent``。

示例:
^^^^^^

.. code-block:: python

   from agentsociety2.agent import AgentBase
   from datetime import datetime

   class MyAgent(AgentBase):
       def __init__(self, id: int, profile: dict, **kwargs):
           super().__init__(id=id, profile=profile, **kwargs)
           # Add custom initialization
           self._custom_state = profile.get("custom_field", {})

       async def ask(self, question: str, readonly: bool = True) -> str:
           # Process the question and return a response
           # Use self._env to interact with the environment
           return await super().ask(question, readonly=readonly)

       async def step(self, tick: int, t: datetime) -> str:
           # Execute one simulation step
           return await super().step(tick, t)

       async def dump(self) -> dict:
           # Save state
           return {
               "custom_state": self._custom_state,
               "profile": self._profile,
           }

       async def load(self, dump_data: dict):
           # Restore state
           self._custom_state = dump_data.get("custom_state", {})

智能体配置文件
--------------

配置文件设计
~~~~~~~~~~~~~

一个好的智能体配置文件应包括：

* **身份**: 姓名、年龄、角色
* **个性**: 特征、偏好、怪癖
* **背景**: 历史、专业知识、关系
* **目标**: 动机、欲望、恐惧

.. code-block:: python

   profile = {
       # Identity
       "name": "Dr. Sarah Chen",
       "age": 35,
       "occupation": "climate scientist",

       # Personality
       "personality": "analytical, passionate, slightly anxious",
       "traits": ["detail-oriented", "empathetic", "curious"],

       # Background
       "education": "PhD in Atmospheric Science",
       "experience": "10 years in climate research",
       "achievements": ["Published 30+ papers", "Nobel nominee"],

       # Goals
       "goal": "raise awareness about climate change",
       "fears": ["sea level rise", "ecosystem collapse"]
   }

与智能体交互
-----------------------

ask() 方法
~~~~~~~~~~~~~~~~~

.. code-block:: python

   response = await agent.ask(
       "What's your opinion on renewable energy?",
       readonly=True  # No side effects
   )

``readonly`` 参数控制智能体是否可以修改环境：

* ``readonly=True``: 仅查询，无副作用
* ``readonly=False``: 可能调用修改状态的环境工具

step() 方法
~~~~~~~~~~~~~~~~~

``step()`` 方法在 AgentSociety 模拟期间自动调用：

.. code-block:: python

   # Called by AgentSociety.run()
   # tick = duration in seconds, t = current simulation time
   action_description = await agent.step(tick=3600, t=datetime.now())

持久化
~~~~~~~~~~~~~~~

``PersonAgent`` 当前的持久化分成两层：

1. **Agent workspace 文件**：由 ``PersonAgent`` 自身维护，位于 ``run/agents/agent_xxxx/``。
2. **环境 replay dataset**：由环境模块通过 ``ReplayWriter`` 写入 SQLite。

也就是说，``PersonAgent`` 不会把自己的 step 状态直接写入 ``agent_status`` 之类的
SQLite 表；如果你需要检查 agent 过程数据，应优先查看：

* ``agent_config.json``: Agent 配置
* ``session_state.json``: 会话状态
* ``tool_calls.jsonl``: 工具调用日志
* ``thread_messages.jsonl``: Thread 消息
* ``AGENT_CONTEXT.md``: 动态上下文文件
* ``AGENT_FILES.md``: 工作区文件清单
* ``state/*.json``: 状态文件（情绪、需求、意图、规划等）
* ``wal/``: Write-Ahead Log 目录

智能体记忆
------------

在当前版本中，记忆能力推荐通过 **Agent Skills** 来实现（例如 `memory` 技能）。

也就是说：

1. `PersonAgent` 提供独立工作目录与工具能力（读写文件、执行技能等）。
2. 是否写入记忆、写入什么、以及持久化方式，由 `memory` 技能的 `SKILL.md` 与其脚本实现决定。

如果你想替换/扩展记忆策略，优先做法是新增/替换 skill，而不是修改 `PersonAgent` 本体。
