Agent 模块
==========

本模块提供智能体的核心类和数据模型。

核心类
------

AgentBase
~~~~~~~~~

.. autoclass:: agentsociety2.agent.AgentBase
   :members:
   :undoc-members:
   :show-inheritance:

PersonAgent
~~~~~~~~~~~

.. autoclass:: agentsociety2.agent.PersonAgent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

数据模型
--------

当前 Agent 已切换到 skills-first 的运行方式：数据结构更多通过 skill frontmatter + SKILL.md + tool-loop 的 JSON 结果来约定。
如需扩展技能与查看技能元信息，请参见 :doc:`/agent_skills` 与 :doc:`/skills`。
