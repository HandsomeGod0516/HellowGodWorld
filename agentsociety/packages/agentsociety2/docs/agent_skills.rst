Agent Skills（智慧體技能）
=================================

概述
------

Agent Skills 是 PersonAgent 的能力外掛系統。PersonAgent 本身是輕量編排器，
真正的認知與行為能力由獨立 skill 提供（如 observation、needs、cognition、plan、memory）。

當前實現採用兩條核心原則：

1. **Metadata-first**：選擇階段只讀取技能後設資料，不載入完整內容。
2. **Selected-only**：每步只執行 LLM 選中的技能，不存在固定 always/dynamic/finalize 層。

這意味著：技能是否執行由當前上下文決定，而不是由“預設層級”決定。


設計目標
---------

* **按需載入**：降低每步不必要的載入與執行開銷。
* **可解釋選擇**：選擇依據來自 SKILL.md 後設資料，便於除錯與治理。
* **熱更新友好**：支援執行時掃描、匯入、啟用/禁用與過載。
* **依賴可控**：用 requires 宣告依賴，避免硬編碼耦合。


Skill 目錄結構
----------------

內建技能位於包內目錄，自定義技能位於工作區目錄：

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

Skill 的兩種模式（與當前 PersonAgent skills-first 設計一致）：

1. **Prompt-only（推薦）**：不宣告 ``script``。當模型選擇並 activate skill 後，SKILL.md 作為行為指南注入上下文，模型使用內建原子工具（bash/codegen/workspace_* 等）完成任務。
2. **Subprocess script（確定性計算/解析用）**：在 frontmatter 中宣告 ``script: scripts/my_skill.py``。執行時以子程序執行指令碼，引數透過 ``--args-json`` 傳入，產物寫入 agent workspace（``AGENT_WORK_DIR``）。


SKILL.md 格式
--------------

每個 skill 目錄應包含 ``SKILL.md``。檔案頭部使用 YAML frontmatter 描述後設資料：

.. code-block:: markdown

   ---
   name: cognition
   description: Update emotions and form intentions from current context
   requires:
     - observation
   ---

   # Cognition Skill
   ...

欄位說明：

.. list-table::
   :widths: 24 76
   :header-rows: 1

   * - 欄位
     - 說明
   * - ``name``
     - Skill 名稱（唯一標識）。
   * - ``description``
     - 給選擇器看的功能描述，儘量具體、可判別。
   * - ``inputs``
     - 可選，依賴的輸入檔案列表（如 ``["state/emotion.json"]``）。
   * - ``outputs``
     - 可選，輸出的檔案列表（如 ``["memory/episodic.json"]``）。
   * - ``script``
     - 可選，指令碼路徑（如 ``scripts/main.py``）。
   * - ``executor``
     - 可選，執行器型別（如 ``codegen``）。
   * - ``disable_model_invocation``
     - 可選，是否禁用模型呼叫。
   * - ``requires``
     - 依賴的其他 skill 名稱列表。


每步執行流程
--------------

PersonAgent.step() 的流程如下：

1. 注入 L0 技能目錄（metadata）+ 工作區狀態 + 最近工具歷史。
2. 進入 tool-loop：模型每輪選擇一個工具呼叫（activate/read/execute/workspace_* 等）。
3. 當呼叫某個 skill 時：
   - 執行時會按需載入 SKILL.md（L1）與 skill 目錄檔案（L2）。
   - 若 skill 宣告 ``requires``，執行時會自動啟用其依賴；缺依賴則拒絕呼叫並返回 missing 列表。
4. 達到 done 或輪次上限後結束本 step，並持久化最小會話狀態與工具歷史。

關鍵點：

* **技能是能力目錄 + 行為規範 +（可選）子程序指令碼**，而不是框架內 pipeline。
* **L0/L1/L2 漸進披露** 用於減少上下文負擔。
* **requires 是執行時行為** （自動補齊依賴/缺依賴阻止），而不是僅展示欄位。


依賴管理
----------

使用 ``requires`` 宣告依賴的其他 skill 名稱：

.. code-block:: yaml

   ---
   name: cognition
   requires:
     - observation
   ---

推薦實踐：

* 用 ``requires`` 明確最小前置條件。
* 保持 ``description`` 可操作，避免”泛描述”。


Memory 語義
------------

認知相關技能通常先把內容寫入 ``_cognition_memory`` 緩衝：

* 當 ``memory`` 技能在本步被選中執行時，緩衝會被 flush 到長期記憶。
* 當 ``memory`` 未被選中時，緩衝不會丟失，會保留到後續 step。
* 在 Agent ``close()`` 時，會執行兜底 flush，避免遺留緩衝丟失。

因此，memory 行為不再是固定“Finalize 層”，而是由選擇結果驅動。


執行時管理 API
----------------

後端提供 Agent Skills 管理介面（字首 ``/api/v1/agent-skills``）：

* ``GET /list``：列出技能（builtin + custom）
* ``POST /enable``：啟用技能
* ``POST /disable``：禁用技能
* ``POST /scan``：掃描 ``{workspace}/custom/skills``
* ``POST /import``：從外部目錄匯入技能
* ``POST /reload``：熱過載單個技能
* ``POST /remove``：刪除自定義技能
* ``GET /{name}/info``：檢視技能詳細資訊（含 SKILL.md 內容）

這些介面同時被 VS Code 擴充套件與手動除錯流程使用。


自定義 Skill 最小示例
----------------------

目錄：

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

匯入並啟用後，主 LLM 會在合適上下文中選擇它執行。


最佳實踐
---------

1. ``description`` 寫成”觸發條件 + 輸出結果”，便於選擇器判斷。
2. ``requires`` 只宣告必要依賴，避免過度耦合。
3. Skill 程式碼儘量冪等，避免重複執行造成狀態汙染。
4. 對關鍵技能保留清晰日誌，便於覆盤每步選擇與執行。


參考
------

* :doc:`agents` - PersonAgent 使用說明
* :doc:`api/skills` - SkillRegistry API
* :doc:`development` - 開發指南
