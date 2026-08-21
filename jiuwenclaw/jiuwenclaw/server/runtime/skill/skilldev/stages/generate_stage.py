# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""GENERATE 階段處理器.

職責：
- 建立 GENERATE 專屬 ReActAgent（配備檔案讀寫工具 + 生成 Prompt）
- 按確認後的 plan 的 directory_structure 建立目錄
- Agent 按依賴順序逐檔案生成（SKILL.md 優先，其次 scripts/，最後 assets/）
- 推送 ARTIFACT_READY 事件通知前端產物就緒（驅動右側附件列表）

Agent 工具白名單：["file_read", "file_write"]
"""

from __future__ import annotations

import logging
from pathlib import Path

from jiuwenclaw.server.runtime.skill.skilldev.context import SkillDevContext
from jiuwenclaw.server.runtime.skill.skilldev.schema import SkillDevEventType, SkillDevStage
from jiuwenclaw.server.runtime.skill.skilldev.stages.base import StageHandler, StageResult

logger = logging.getLogger(__name__)

GENERATE_SYSTEM_PROMPT = """你是一個 Skill 開發專家。根據已確認的開發計劃，生成完整的 Skill 檔案集。

## SKILL.md 格式要求（必須嚴格遵守）

**YAML Frontmatter（必填）：**
```
---
name: skill-name-here
description: 用祈使句描述何時觸發、做什麼。描述應聚焦使用者意圖而非實現細節。≤1024 字元。
---
```

規則：
- name 必須是 kebab-case（小寫字母、數字、連字元），≤64 字元
- description 不能包含 < 或 >
- 僅允許的 frontmatter key: name, description, license, allowed-tools, metadata, compatibility

## Skill 目錄結構

```
skill-name/
├── SKILL.md (必需)
├── scripts/    - 確定性/重複性任務的可執行指令碼
├── references/ - 按需載入的領域文件
└── assets/     - 輸出中使用的模板、圖示、字型等
```

## 寫作原則（對齊官方 Skill Writing Guide）

### 漸進式資訊展示 (Progressive Disclosure)
1. **後設資料**（name + description）— 始終在上下文中（~100 詞）
2. **SKILL.md 正文** — 觸發時載入（<500 行為佳）
3. **捆綁資源** — 按需載入（無大小限制，指令碼可不載入直接執行）

### 寫作風格
- 使用祈使句式（"執行 X" 而非 "這個 skill 會執行 X"）
- 解釋 **為什麼** 而非堆砌規則；避免過度使用 MUST/NEVER/ALWAYS
- 使用心理模型讓模型理解意圖，比死板指令更有效
- 保持 SKILL.md ≤500 行；超過時拆分到 references/ 並標明何時查閱

### 輸出格式定義
明確定義預期輸出結構，使用模板或示例：
```markdown
## 報告結構
ALWAYS use this exact template:
# [Title]
## Executive summary
## Key findings
## Recommendations
```

### 發現重複工作 → 捆綁指令碼
如果測試中發現模型反覆獨立編寫類似的輔助指令碼，應將其捆綁到 scripts/ 中。

### description 的觸發性
當前模型傾向於"不夠主動觸發"skill。description 應略微"推進式"——
除了說明 skill 做什麼，還要列舉具體觸發場景，即使使用者沒有明確提到 skill 名稱。
"""


class GenerateStageHandler(StageHandler):
    """GENERATE 階段：Agent 按 plan 生成完整 skill 檔案集."""

    async def execute(self, ctx: SkillDevContext) -> StageResult:
        plan = ctx.state.plan
        if not plan:
            raise ValueError("GENERATE 階段缺少 plan，請先完成 PLAN 階段")

        skill_dir = ctx.workspace / "skill"
        generation_order = self._resolve_generation_order(plan)

        await ctx.emit(
            SkillDevEventType.PROGRESS,
            {
                "message": f"正在生成 {len(generation_order)} 個檔案...",
                "files_total": len(generation_order),
                "files_done": 0,
            },
        )

        generated_files = await self._generate_all_files(
            ctx, skill_dir, generation_order
        )

        await ctx.emit(
            SkillDevEventType.ARTIFACT_READY,
            {
                "artifact": {
                    "id": "skill_files",
                    "name": (plan or {}).get("skill_name", "skill"),
                    "type": "skill_md",
                    "files": generated_files,
                    "browsable": True,
                    "downloadable": False,
                },
            },
        )
        return StageResult(next_stage=SkillDevStage.VALIDATE)

    def _resolve_generation_order(self, plan: dict) -> list[tuple[str, str]]:
        """確定檔案生成順序：SKILL.md 優先，scripts/ 其次，其餘最後.

        Returns:
            [(filepath, role_description), ...]，按生成順序排列
        """
        directory_structure: dict = plan.get("directory_structure", {})
        order: list[tuple[str, str]] = []

        # SKILL.md 必須最先生成（其他檔案生成時需參考它）
        if "SKILL.md" in directory_structure:
            order.append(("SKILL.md", directory_structure["SKILL.md"]))

        # scripts/ 次之
        for path, role in directory_structure.items():
            if path != "SKILL.md" and path.startswith("scripts/"):
                order.append((path, role))

        # 其餘檔案
        for path, role in directory_structure.items():
            if path != "SKILL.md" and not path.startswith("scripts/"):
                order.append((path, role))

        return order

    async def _generate_all_files(
        self,
        ctx: SkillDevContext,
        skill_dir: Path,
        generation_order: list[tuple[str, str]],
    ) -> list[str]:
        """逐檔案呼叫 Agent 生成內容.

        待實現: 接入 create_stage_agent + 逐檔案生成邏輯
        """
        # 待實現:
        # agent = ctx.create_stage_agent(
        #     stage_name="generate",
        #     system_prompt=GENERATE_SYSTEM_PROMPT,
        #     tools=["file_read", "file_write"],
        #     max_iterations=30,
        # )
        # for idx, (filepath, role) in enumerate(generation_order):
        #     (skill_dir / filepath).parent.mkdir(parents=True, exist_ok=True)
        #     content = await self._generate_single_file(agent, ctx, filepath, role)
        #     (skill_dir / filepath).write_text(content, encoding="utf-8")
        #     await ctx.emit(SkillDevEventType.PROGRESS, {
        #         "message": f"已生成: {filepath}",
        #         "files_done": idx + 1,
        #         "files_total": len(generation_order),
        #     })

        logger.warning("[GenerateStage] _generate_all_files 尚未實現，建立佔位檔案")
        generated = []
        for filepath, role in generation_order:
            full_path = skill_dir / filepath
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(
                f"# {filepath}\n\n<!-- 待實現: 由 Agent 生成，職責：{role} -->\n",
                encoding="utf-8",
            )
            generated.append(filepath)
        return generated

    async def _generate_single_file(
        self, agent, ctx: SkillDevContext, filepath: str, role: str
    ) -> str:
        """為單個檔案生成內容.

        待實現: 構造 per-file prompt，呼叫 Agent，返回檔案內容
        """
        raise NotImplementedError

    async def _validate_scripts(self, skill_dir: Path) -> None:
        """驗證生成的 Python 指令碼語法正確性.

        待實現: 使用 py_compile 或 ast.parse 檢查語法
        """
        # for py_file in skill_dir.rglob("*.py"):
        #     import ast
        #     try:
        #         ast.parse(py_file.read_text(encoding="utf-8"))
        #     except SyntaxError as e:
        #         raise ValueError(f"指令碼語法錯誤 {py_file}: {e}") from e
        pass
