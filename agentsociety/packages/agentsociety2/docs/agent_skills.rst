Agent Skills（智能体技能）
=================================

概述
------

Agent Skills 是 PersonAgent 的能力插件系统。PersonAgent 本身是轻量编排器，
真正的认知与行为能力由独立 skill 提供（如 observation、needs、cognition、plan、memory）。

当前实现采用两条核心原则：

1. **Metadata-first**：选择阶段只读取技能元数据，不加载完整内容。
2. **Selected-only**：每步只执行 LLM 选中的技能，不存在固定 always/dynamic/finalize 层。

这意味着：技能是否执行由当前上下文决定，而不是由“预设层级”决定。


设计目标
---------

* **按需加载**：降低每步不必要的加载与执行开销。
* **可解释选择**：选择依据来自 SKILL.md 元数据，便于调试与治理。
* **热更新友好**：支持运行时扫描、导入、启用/禁用与重载。
* **依赖可控**：用 requires 声明依赖，避免硬编码耦合。


Skill 目录结构
----------------

内置技能位于包内目录，自定义技能位于工作区目录：

.. code-block:: text

   agentsociety2/agent/skills/
   ├── observation/
   │   ├── SKILL.md
   │   └── scripts/
   │       └── observation.py
   ├── cognition/
   │   ├── SKILL.md
   │   └── scripts/
   │       └── cognition.py
   └── ...

   {workspace}/custom/skills/
   └── my_skill/
       ├── SKILL.md
       └── scripts/
           └── my_skill.py

Skill 的两种模式（与当前 PersonAgent skills-first 设计一致）：

1. **Prompt-only（推荐）**：不声明 ``script``。当模型选择并 activate skill 后，SKILL.md 作为行为指南注入上下文，模型使用内置原子工具（bash/codegen/workspace_* 等）完成任务。
2. **Subprocess script（确定性计算/解析用）**：在 frontmatter 中声明 ``script: scripts/my_skill.py``。执行时以子进程运行脚本，参数通过 ``--args-json`` 传入，产物写入 agent workspace（``AGENT_WORK_DIR``）。


SKILL.md 格式
--------------

每个 skill 目录应包含 ``SKILL.md``。文件头部使用 YAML frontmatter 描述元数据：

.. code-block:: markdown

   ---
   name: cognition
   description: Update emotions and form intentions from current context
   requires:
     - observation
   ---

   # Cognition Skill
   ...

字段说明：

.. list-table::
   :widths: 24 76
   :header-rows: 1

   * - 字段
     - 说明
   * - ``name``
     - Skill 名称（唯一标识）。
   * - ``description``
     - 给选择器看的功能描述，尽量具体、可判别。
   * - ``inputs``
     - 可选，依赖的输入文件列表（如 ``["state/emotion.json"]``）。
   * - ``outputs``
     - 可选，输出的文件列表（如 ``["memory/episodic.json"]``）。
   * - ``script``
     - 可选，脚本路径（如 ``scripts/main.py``）。
   * - ``executor``
     - 可选，执行器类型（如 ``codegen``）。
   * - ``disable_model_invocation``
     - 可选，是否禁用模型调用。
   * - ``requires``
     - 依赖的其他 skill 名称列表。


每步执行流程
--------------

PersonAgent.step() 的流程如下：

1. 注入 L0 技能目录（metadata）+ 工作区状态 + 最近工具历史。
2. 进入 tool-loop：模型每轮选择一个工具调用（activate/read/execute/workspace_* 等）。
3. 当调用某个 skill 时：
   - 运行时会按需加载 SKILL.md（L1）与 skill 目录文件（L2）。
   - 若 skill 声明 ``requires``，运行时会自动激活其依赖；缺依赖则拒绝调用并返回 missing 列表。
4. 达到 done 或轮次上限后结束本 step，并持久化最小会话状态与工具历史。

关键点：

* **技能是能力目录 + 行为规范 +（可选）子进程脚本**，而不是框架内 pipeline。
* **L0/L1/L2 渐进披露** 用于减少上下文负担。
* **requires 是运行时行为** （自动补齐依赖/缺依赖阻止），而不是仅展示字段。


依赖管理
----------

使用 ``requires`` 声明依赖的其他 skill 名称：

.. code-block:: yaml

   ---
   name: cognition
   requires:
     - observation
   ---

推荐实践：

* 用 ``requires`` 明确最小前置条件。
* 保持 ``description`` 可操作，避免”泛描述”。


Memory 语义
------------

认知相关技能通常先把内容写入 ``_cognition_memory`` 缓冲：

* 当 ``memory`` 技能在本步被选中执行时，缓冲会被 flush 到长期记忆。
* 当 ``memory`` 未被选中时，缓冲不会丢失，会保留到后续 step。
* 在 Agent ``close()`` 时，会执行兜底 flush，避免遗留缓冲丢失。

因此，memory 行为不再是固定“Finalize 层”，而是由选择结果驱动。


运行时管理 API
----------------

后端提供 Agent Skills 管理接口（前缀 ``/api/v1/agent-skills``）：

* ``GET /list``：列出技能（builtin + custom）
* ``POST /enable``：启用技能
* ``POST /disable``：禁用技能
* ``POST /scan``：扫描 ``{workspace}/custom/skills``
* ``POST /import``：从外部目录导入技能
* ``POST /reload``：热重载单个技能
* ``POST /remove``：删除自定义技能
* ``GET /{name}/info``：查看技能详细信息（含 SKILL.md 内容）

这些接口同时被 VS Code 扩展与手动调试流程使用。


自定义 Skill 最小示例
----------------------

目录：

.. code-block:: text

   {workspace}/custom/skills/hello_skill/
   ├── SKILL.md
   └── scripts/
       └── hello_skill.py

``SKILL.md``：

.. code-block:: markdown

   ---
   name: hello_skill
   description: Add a short greeting into step log
   inputs: []
   outputs: []
   requires: []
   ---

``scripts/hello_skill.py``：

.. code-block:: python

   import argparse
   import json
   from pathlib import Path

   def main() -> int:
       parser = argparse.ArgumentParser()
       parser.add_argument("--args-json", default="{}")
       ns = parser.parse_args()
       args = json.loads(ns.args_json or "{}")
       result = {"ok": True, "summary": "hello_skill: greeted", "tick": args.get("tick")}
       Path("hello_skill.txt").write_text("hello_skill: greeted", encoding="utf-8")
       print(json.dumps(result, ensure_ascii=False))
       return 0

   if __name__ == "__main__":
       raise SystemExit(main())

导入并启用后，主 LLM 会在合适上下文中选择它执行。


最佳实践
---------

1. ``description`` 写成”触发条件 + 输出结果”，便于选择器判断。
2. ``requires`` 只声明必要依赖，避免过度耦合。
3. Skill 代码尽量幂等，避免重复执行造成状态污染。
4. 对关键技能保留清晰日志，便于复盘每步选择与执行。


参考
------

* :doc:`agents` - PersonAgent 使用说明
* :doc:`api/skills` - SkillRegistry API
* :doc:`development` - 开发指南
