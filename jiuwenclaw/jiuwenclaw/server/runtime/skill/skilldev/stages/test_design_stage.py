# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""TEST_DESIGN 階段處理器.

對齊官方 skill-creator 的測試設計流程：

1. 先生成 2-3 個真實使用者場景的 test prompts（不寫 assertions）
2. Assertions 應在 TEST_RUN 階段執行期間並行起草
   （當前框架簡化：在本階段一次性生成 prompts + assertions）

evals.json 格式對齊官方 references/schemas.md：
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "User's task prompt",
      "expected_output": "Description of expected result",
      "files": [],
      "expectations": ["The output includes X", "The skill used script Y"]
    }
  ]
}
"""

from __future__ import annotations

import json
import logging

from jiuwenclaw.server.runtime.skill.skilldev.context import SkillDevContext
from jiuwenclaw.server.runtime.skill.skilldev.schema import SkillDevEventType, SkillDevStage
from jiuwenclaw.server.runtime.skill.skilldev.stages.base import StageHandler, StageResult

logger = logging.getLogger(__name__)

TEST_DESIGN_SYSTEM_PROMPT = """根據以下 Skill 內容，設計 {count} 個測試用例。

## 測試用例設計原則

### prompt 要求（對齊官方標準）
- 模擬真實使用者輸入：包含檔案路徑、個人背景、具體資料名稱等細節
- 混合不同長度和表達風格（正式/隨意/簡短/詳細）
- 覆蓋不同複雜度和邊緣場景
- 有些使用者不會明確提到 skill 名稱，但確實需要這個 skill 的功能

### expectations（assertions）要求
- 每條 expectation 是一個可客觀驗證的宣告（字串）
- 使用描述性名稱，讓閱讀者一眼理解檢查的內容
- 好的 expectation 是 *區分性的*：使用 skill 時透過，不使用時大機率失敗
- 避免太容易透過的檢查（如只檢查檔名存在，不檢查內容）
- 主觀性輸出（寫作風格、設計質量）更適合人工評審，不強加 expectations

### 輸出 JSON 格式（對齊官方 evals.json schema）
{{
  "skill_name": "{skill_name}",
  "evals": [
    {{
      "id": 1,
      "prompt": "模擬使用者的真實輸入...",
      "expected_output": "預期結果的人類可讀描述",
      "files": [],
      "expectations": [
        "輸出中包含 X 的結構化資料",
        "使用了 scripts/ 中的 Y 指令碼"
      ]
    }}
  ]
}}
"""


class TestDesignStageHandler(StageHandler):
    """TEST_DESIGN 階段：Agent 設計測試用例，輸出 evals.json."""

    async def execute(self, ctx: SkillDevContext) -> StageResult:
        await ctx.emit(SkillDevEventType.PROGRESS, {"message": "正在設計測試用例..."})

        skill_content = self._read_skill_files(ctx.workspace / "skill")
        evals = await self._design_evals(ctx, skill_content)

        ctx.state.evals = evals
        evals_file = ctx.workspace / "evals" / "evals.json"
        evals_file.write_text(
            json.dumps(evals, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        count = len(evals.get("evals", []))
        await ctx.emit(
            SkillDevEventType.PROGRESS, {"message": f"已設計 {count} 個測試用例"}
        )
        return StageResult(next_stage=SkillDevStage.TEST_RUN)

    def _read_skill_files(self, skill_dir) -> str:
        """讀取 skill 目錄下所有檔案，拼接為字串供 Agent 分析."""
        parts = []
        for file_path in sorted(skill_dir.rglob("*")):
            if file_path.is_file():
                rel = file_path.relative_to(skill_dir)
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    parts.append(f"=== {rel} ===\n{content}")
                except Exception as exc:
                    logger.warning(
                        "[TestDesignStage] 讀取檔案失敗: %s (%s)", file_path, exc
                    )
        return "\n\n".join(parts)

    async def _design_evals(self, ctx: SkillDevContext, skill_content: str) -> dict:
        """呼叫 Agent 設計測試用例.

        待實現: 接入 create_stage_agent + Runner.run_agent，解析輸出 JSON
        """
        # 待實現:
        # agent = ctx.create_stage_agent(
        #     stage_name="test_design",
        #     system_prompt=TEST_DESIGN_SYSTEM_PROMPT.format(count=3),
        #     tools=[],  # 只需模型推理，無需工具
        #     max_iterations=10,
        # )
        # result = await Runner.run_agent(agent, {"skill_content": skill_content})
        # return json.loads(result["output"])

        logger.warning("[TestDesignStage] _design_evals 尚未實現，返回佔位測試用例")
        skill_name = (
            ctx.state.plan.get("skill_name", "skill") if ctx.state.plan else "skill"
        )
        return {
            "skill_name": skill_name,
            "evals": [
                {
                    "id": 1,
                    "name": "basic-usage",
                    "prompt": f"請使用 {skill_name} 完成基礎功能測試",
                    "expected_output": "待實現: 預期結果",
                    "files": [],
                    "expectations": ["待實現: 可驗證的預期宣告"],
                }
            ],
        }
