# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""TEST_RUN 階段處理器.

職責：
- 為每個測試用例並行建立兩個子 Agent：
    · with_skill：注入當前生成的 Skill 後執行用例
    · baseline：不注入 Skill，作為對照組
- 收集兩組結果，寫入 iteration-{N}/ 目錄
- 推送 TEST_PROGRESS 事件反饋進度

這是整個 Pipeline 中技術複雜度最高的階段，涉及：
- 子 Agent 建立與 Skill 注入
- 並行任務排程與結果收集
- 檔案系統隔離（每個測試用例獨立目錄）

擴充套件點：
- 子 Agent 並行度可配置（預設與測試用例數相同）
- with_skill / baseline 的執行邏輯封裝在 SkillDevTestRunner 中（待獨立模組實現）
"""

from __future__ import annotations

import asyncio
import json
import logging

from jiuwenclaw.server.runtime.skill.skilldev.context import SkillDevContext
from jiuwenclaw.server.runtime.skill.skilldev.schema import SkillDevEventType, SkillDevStage
from jiuwenclaw.server.runtime.skill.skilldev.stages.base import StageHandler, StageResult

logger = logging.getLogger(__name__)


class TestRunStageHandler(StageHandler):
    """TEST_RUN 階段：子 Agent 並行執行測試用例（with_skill vs baseline）."""

    async def execute(self, ctx: SkillDevContext) -> StageResult:
        evals = ctx.state.evals
        if not evals or not evals.get("evals"):
            raise ValueError("TEST_RUN 階段缺少測試用例，請先完成 TEST_DESIGN 階段")

        eval_cases = evals["evals"]
        iteration = ctx.state.iteration
        iter_dir = ctx.workspace / "evals" / f"iteration-{iteration}"
        iter_dir.mkdir(parents=True, exist_ok=True)

        total_tasks = len(eval_cases) * 2  # with_skill + baseline
        await ctx.emit(
            SkillDevEventType.TEST_PROGRESS,
            {
                "total": total_tasks,
                "completed": 0,
                "message": f"開始執行 {len(eval_cases)} 個測試用例...",
            },
        )

        results = await self._run_all_evals(ctx, eval_cases, iter_dir)

        await ctx.emit(
            SkillDevEventType.TEST_PROGRESS,
            {
                "total": total_tasks,
                "completed": total_tasks,
                "message": "測試執行完成",
            },
        )

        return StageResult(next_stage=SkillDevStage.EVALUATE)

    async def _run_all_evals(
        self, ctx: SkillDevContext, eval_cases: list[dict], iter_dir
    ) -> list[dict]:
        """並行執行所有測試用例.

        待實現: 接入 SkillDevTestRunner，為每個用例建立 with_skill + baseline 子 Agent
        """
        # 待實現:
        # tasks = []
        # for case in eval_cases:
        #     case_dir = iter_dir / case["name"]
        #     case_dir.mkdir(parents=True, exist_ok=True)
        #     tasks.append(self._run_single_eval(ctx, case, case_dir))
        # results = await asyncio.gather(*tasks, return_exceptions=True)
        # return results

        logger.warning("[TestRunStage] _run_all_evals 尚未實現，寫入佔位結果")
        results = []
        for case in eval_cases:
            eval_name = case.get("name", f"eval-{case.get('id', 0)}")
            case_dir = iter_dir / eval_name
            (case_dir / "with_skill").mkdir(parents=True, exist_ok=True)
            (case_dir / "baseline").mkdir(parents=True, exist_ok=True)

            # 寫入 eval_metadata.json（對齊官方格式）
            eval_metadata = {
                "eval_id": case.get("id", 0),
                "eval_name": eval_name,
                "prompt": case.get("prompt", ""),
                "assertions": case.get("assertions", []),
            }
            (case_dir / "eval_metadata.json").write_text(
                json.dumps(eval_metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # 佔位 timing.json（實際應從 subagent task notification 中捕獲）
            timing_placeholder = {
                "total_tokens": 0,
                "duration_ms": 0,
                "total_duration_seconds": 0.0,
            }
            for config in ("with_skill", "baseline"):
                config_dir = case_dir / config
                (config_dir / "result.json").write_text(
                    '{"status": "待實現", "output": "待實現"}', encoding="utf-8"
                )
                (config_dir / "timing.json").write_text(
                    json.dumps(timing_placeholder, indent=2), encoding="utf-8"
                )

            results.append({"eval_id": case.get("id", 0), "status": "placeholder"})
            await ctx.emit(
                SkillDevEventType.TEST_PROGRESS,
                {
                    "message": f"已完成（佔位）：{eval_name}",
                },
            )
        return results

    async def _run_single_eval(
        self, ctx: SkillDevContext, case: dict, case_dir
    ) -> dict:
        """為單個測試用例建立 with_skill + baseline 兩組子 Agent 並行執行.

        待實現: 實現 SkillDevTestRunner.run(case, skill_dir, case_dir)
        """
        # with_skill_result, baseline_result = await asyncio.gather(
        #     self._run_with_skill(ctx, case, case_dir / "with_skill"),
        #     self._run_baseline(ctx, case, case_dir / "baseline"),
        # )
        # return {"eval_id": case["id"], "with_skill": with_skill_result, "baseline": baseline_result}
        raise NotImplementedError
