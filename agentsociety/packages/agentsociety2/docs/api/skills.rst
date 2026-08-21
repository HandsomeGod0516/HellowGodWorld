Agent Skills 模組
==================

本模組提供智慧體技能的註冊與管理，支援漸進式載入。

SkillRegistry
-------------

.. autoclass:: agentsociety2.agent.skills.SkillRegistry
   :members:
   :undoc-members:
   :show-inheritance:

SkillInfo
---------

.. autoclass:: agentsociety2.agent.skills.SkillInfo
   :members:
   :undoc-members:

工具函式
--------

.. autofunction:: agentsociety2.agent.skills.get_skill_registry

SKILL.md Frontmatter
--------------------

SKILL.md 檔案使用 YAML frontmatter 宣告 skill 元資訊：

.. code-block:: yaml

   ---
   name: my_skill
   description: 這是一個示例 skill
   script: scripts/main.py
   executor: codegen
   disable_model_invocation: false
   requires:
     - other_skill
   ---

**支援的欄位**：

- ``name``: Skill 名稱（預設為目錄名）
- ``description``: 描述資訊
- ``script``: 指令碼路徑（可選）
- ``executor``: 執行器型別（如 "codegen"）
- ``disable_model_invocation``: 是否禁用模型呼叫
- ``requires``: 依賴的其他 skill 名稱列表
