# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""IMPROVE 階段處理器.

職責：
- 讀取使用者最新反饋（feedback_history[-1]）和評測報告
- 建立 IMPROVE 專屬 ReActAgent（配備檔案讀寫工具 + 改進 Prompt）
- Agent 分析反饋，改進 skill/ 目錄下的檔案
- iteration 計數 +1，跳轉回 TEST_RUN 開啟新一輪測試

改進原則（寫入 Prompt）：
1. 從反饋中提煉通用改進，不過擬合到特定測試用例
2. 保持指令精簡，刪除無效內容
3. 解釋 why 而非堆砌 MUST/NEVER
4. 關注 benchmark 中的異常模式

Agent 工具白名單：["file_read", "file_write"]
"""

from __future__ import annotations

import logging

from jiuwenclaw.server.runtime.skill.skilldev.context import SkillDevContext
from jiuwenclaw.server.runtime.skill.skilldev.schema import SkillDevEventType, SkillDevStage
from jiuwenclaw.server.runtime.skill.skilldev.stages.base import StageHandler, StageResult

logger = logging.getLogger(__name__)

IMPROVE_SYSTEM_PROMPT = """你是一個 Skill 最佳化專家。根據使用者反饋改進 Skill。

當前是第 {iteration} 輪迭代。

使用者反饋：
{feedback}

評測報告：
{report}

當前 Skill 內容：
{skill_content}

## 改進哲學（對齊官方 skill-creator 指導）

### 1. 從反饋中泛化，不要過擬合
你在極少數示例上迭代，但 Skill 需要在海量不同場景中表現良好。
不要為特定測試用例新增瑣碎的過擬合修改或限制性的 MUST 規則。
嘗試理解使用者反饋背後的 *根本意圖*，將理解注入到指令中。

### 2. 保持精簡，刪除無效內容
閱讀測試的 transcripts（不僅是最終輸出）——如果 Skill 讓模型在不產出價值的步驟上
浪費大量時間，刪除引起這些行為的 Skill 指令並觀察效果。

### 3. 解釋 why，用心智模型替代死板規則
當今的 LLM 足夠智慧。與其寫 "ALWAYS do X" 或 "NEVER do Y"，
不如解釋 *為什麼* X 重要、為什麼 Y 會導致問題。
讓模型理解意圖後自主決策，比死板規則更有效、更優雅。

### 4. 發現重複工作 → 捆綁指令碼
閱讀測試執行的 transcripts，如果所有測試用例都獨立編寫了類似的輔助指令碼
（如 create_docx.py、build_chart.py），這是強烈訊號：
應將該指令碼寫好放入 scripts/，讓每次呼叫直接使用而非重新發明。

### 5. 關注 Benchmark 異常模式
- 某 assertion 在所有配置都 pass → 可能不具區分力，考慮加強或替換
- 某 assertion 在所有配置都 fail → 可能超出能力範圍或 assertion 本身有問題
- 高方差 eval → 可能是 flaky 測試或非確定性行為
- with_skill 反而劣於 baseline 的指標 → Skill 可能在某方面產生負面影響

### 6. 先寫草稿，再以新鮮眼光審視
寫完改進後，以全新視角審視一遍。如果某個持續性問題用當前方法解決不了，
嘗試換一種思路——不同的隱喻、不同的工作模式、不同的檔案組織方式。
嘗試成本低，或許能找到突破口。

請輸出改進後的完整檔案內容。
"""


class ImproveStageHandler(StageHandler):
    """IMPROVE 階段：Agent 根據使用者反饋改進 Skill，隨後進入下一輪測試."""

    async def execute(self, ctx: SkillDevContext) -> StageResult:
        if not ctx.state.feedback_history:
            raise ValueError("IMPROVE 階段缺少反饋歷史，請先完成 REVIEW 階段")

        latest_feedback = ctx.state.feedback_history[-1].get("feedback", {})
        report = (ctx.state.eval_results or {}).get("report", "")

        await ctx.emit(
            SkillDevEventType.PROGRESS,
            {
                "message": f"正在根據反饋進行第 {ctx.state.iteration + 1} 輪改進...",
            },
        )

        await self._run_improve_agent(ctx, latest_feedback, report)

        ctx.state.iteration += 1
        await ctx.emit(
            SkillDevEventType.PROGRESS,
            {
                "message": f"改進完成，開始第 {ctx.state.iteration} 輪測試",
            },
        )
        return StageResult(next_stage=SkillDevStage.TEST_RUN)

    async def _run_improve_agent(
        self, ctx: SkillDevContext, feedback: dict, report: str
    ) -> None:
        """呼叫 Agent 分析反饋並修改 skill 檔案.

        待實現: 接入 create_stage_agent + Runner.run_agent，實現檔案級改進
        """
        # 待實現:
        # skill_content = self._read_skill_files(ctx.workspace / "skill")
        # agent = ctx.create_stage_agent(
        #     stage_name="improve",
        #     system_prompt=IMPROVE_SYSTEM_PROMPT.format(
        #         iteration=ctx.state.iteration,
        #         feedback=json.dumps(feedback, ensure_ascii=False),
        #         report=report,
        #         skill_content=skill_content,
        #     ),
        #     tools=["file_read", "file_write"],
        #     max_iterations=25,
        # )
        # await Runner.run_agent(agent, {"task": "根據反饋改進 Skill"})
        logger.warning("[ImproveStage] _run_improve_agent 尚未實現，跳過改進")

    def _read_skill_files(self, skill_dir) -> str:
        """讀取當前 skill 目錄下所有檔案內容."""
        parts = []
        for file_path in sorted(skill_dir.rglob("*")):
            if file_path.is_file():
                rel = file_path.relative_to(skill_dir)
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                parts.append(f"=== {rel} ===\n{content}")
        return "\n\n".join(parts)
