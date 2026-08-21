Agent Skills 模块
==================

本模块提供智能体技能的注册与管理，支持渐进式加载。

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

工具函数
--------

.. autofunction:: agentsociety2.agent.skills.get_skill_registry

SKILL.md Frontmatter
--------------------

SKILL.md 文件使用 YAML frontmatter 声明 skill 元信息：

.. code-block:: yaml

   ---
   name: my_skill
   description: 这是一个示例 skill
   script: scripts/main.py
   executor: codegen
   disable_model_invocation: false
   requires:
     - other_skill
   ---

**支持的字段**：

- ``name``: Skill 名称（默认为目录名）
- ``description``: 描述信息
- ``script``: 脚本路径（可选）
- ``executor``: 执行器类型（如 "codegen"）
- ``disable_model_invocation``: 是否禁用模型调用
- ``requires``: 依赖的其他 skill 名称列表
