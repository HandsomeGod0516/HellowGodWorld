Agent 模組
==========

本模組提供智慧體的核心類和資料模型。

核心類
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

資料模型
--------

當前 Agent 已切換到 skills-first 的執行方式：資料結構更多透過 skill frontmatter + SKILL.md + tool-loop 的 JSON 結果來約定。
如需擴充套件技能與檢視技能元資訊，請參見 :doc:`/agent_skills` 與 :doc:`/skills`。
