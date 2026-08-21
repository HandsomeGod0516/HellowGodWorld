# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""DESC_OPTIMIZE 階段處理器.

核心流程（對齊官方 Description Optimization，但用我們自己的模型 API 實現）：

1. Agent 生成 ~20 個 trigger eval queries（should_trigger / should_not_trigger）
2. Train/test split (60% / 40%)
3. 迭代最佳化迴圈（最多 max_iterations 輪）：
   a. 對每個 query，呼叫模型判斷當前 description 是否會觸發
   b. 統計 pass rate
   c. 基於失敗案例，呼叫模型生成改進的 description
   d. 如果 train 全部透過則提前退出
4. 選 test score 最高的 description（防過擬合）
5. 將 best_description 寫回 SKILL.md frontmatter

官方實現用 `claude -p` CLI subprocess 做觸發測試和描述改進。
我們的實現透過 ctx.create_stage_agent 直接呼叫模型 API，不依賴 CLI。
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path

from jiuwenclaw.server.runtime.skill.skilldev.context import SkillDevContext
from jiuwenclaw.server.runtime.skill.skilldev.schema import (
    DescOptimizeIteration,
    SKILL_DESC_MAX_LEN,
    SkillDevEventType,
    SkillDevStage,
    TriggerEvalQuery,
)
from jiuwenclaw.server.runtime.skill.skilldev.stages.base import StageHandler, StageResult
from jiuwenclaw.server.runtime.skill.skilldev.stages.validate_stage import (
    parse_skill_frontmatter,
)

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5
HOLDOUT_RATIO = 0.4

# ---------------------------------------------------------------------------
# Prompts（內化自官方 improve_description.py 的 prompt 結構）
# ---------------------------------------------------------------------------

TRIGGER_QUERY_GEN_PROMPT = """\
你是一個 Skill 觸發最佳化專家。根據以下 Skill 的名稱和描述，生成 20 個測試查詢。

Skill 名稱: {skill_name}
當前 Description: {description}

## 要求

### should_trigger=true 的查詢（約 10 個）
- 使用者確實需要這個 Skill 時會說的話
- 不同表達風格（正式/隨意/簡短/詳細）
- 有些不直接提及 Skill 名稱但確實需要其功能
- 包含具體細節（檔案路徑、個人背景、資料名稱等）

### should_trigger=false 的查詢（約 10 個）
- 關鍵詞相近但實際不需要這個 Skill 的 **近似場景**
- 相鄰領域、歧義措辭、看似相關但應由其他工具處理
- 不要用明顯無關的查詢（"寫斐波那契函式"對 PDF 技能來說太容易區分了）

輸出 JSON 陣列：
[{{"query": "具體的使用者查詢", "should_trigger": true}}, ...]
"""

IMPROVE_DESC_PROMPT = """\
你正在最佳化一個名為 "{skill_name}" 的 Skill 的 description 欄位。
description 出現在模型的 available_skills 列表中，模型僅憑 description 決定是否使用該 Skill。

當前 description：
"{current_description}"

當前得分：{scores_summary}

{failure_details}

{history_section}

## 要求

根據失敗案例，寫一個更好的 description：
- 從失敗中 **泛化**，不要過擬合到具體查詢
- 用祈使句（"Use when..." 而非 "This skill does..."）
- 聚焦使用者意圖而非實現細節
- 讓觸發場景具體且可區分
- 嚴格不超過 {max_len} 字元

請在 <new_description> 標籤中只輸出新的 description 文字：
<new_description>新描述內容</new_description>
"""


@dataclass
class _OptimizationLoopInput:
    """描述最佳化迴圈的輸入引數封裝."""

    skill_name: str
    skill_body: str
    current_desc: str
    train_set: list[TriggerEvalQuery]
    test_set: list[TriggerEvalQuery]


@dataclass
class _ImproveDescriptionInput:
    """描述改進步驟的輸入引數封裝."""

    skill_name: str
    skill_body: str
    current_desc: str
    train_results: list[dict]
    history: list[DescOptimizeIteration]


class DescOptimizeStageHandler(StageHandler):
    """DESC_OPTIMIZE 階段：最佳化 SKILL.md 的 description 以提高觸發準確率."""

    async def execute(self, ctx: SkillDevContext) -> StageResult:
        skill_dir = ctx.workspace / "skill"
        skill_md = skill_dir / "SKILL.md"

        if not skill_md.exists():
            await ctx.emit(
                SkillDevEventType.PROGRESS, {"message": "未找到 SKILL.md，跳過描述最佳化"}
            )
            return StageResult(next_stage=SkillDevStage.COMPLETED)

        skill_name, current_desc, body = parse_skill_frontmatter(skill_md)

        # Step 1: 生成觸發測試查詢
        await ctx.emit(
            SkillDevEventType.PROGRESS, {"message": "正在生成觸發測試查詢集..."}
        )
        queries = await self._generate_trigger_queries(ctx, skill_name, current_desc)

        # Step 2: Train/test split
        train_set, test_set = self._split_eval_set(queries, HOLDOUT_RATIO)

        await ctx.emit(
            SkillDevEventType.PROGRESS,
            {
                "message": f"開始描述最佳化迴圈（train={len(train_set)}, test={len(test_set)}）",
            },
        )

        # Step 3: 最佳化迴圈
        loop_input = _OptimizationLoopInput(
            skill_name=skill_name,
            skill_body=body,
            current_desc=current_desc,
            train_set=train_set,
            test_set=test_set,
        )
        best_desc, history = await self._optimization_loop(ctx, loop_input)

        # Step 4: 寫回 SKILL.md
        if best_desc and best_desc != current_desc:
            self._apply_description(skill_md, current_desc, best_desc)

        # Step 5: 結果
        best_iter = (
            max(history, key=lambda h: h.test_passed or 0)
            if test_set and history
            else (max(history, key=lambda h: h.train_passed) if history else None)
        )
        result = {
            "original_description": current_desc,
            "best_description": best_desc,
            "best_score": f"{best_iter.test_passed}/{best_iter.test_total}"
            if best_iter and best_iter.test_passed is not None
            else (
                f"{best_iter.train_passed}/{best_iter.train_total}"
                if best_iter
                else "N/A"
            ),
            "iterations_run": len(history),
            "history": [h.to_dict() for h in history],
        }
        ctx.state.desc_optimize_result = result

        await ctx.emit(SkillDevEventType.DESC_OPT_READY, result)
        return StageResult(next_stage=SkillDevStage.COMPLETED)

    # ------------------------------------------------------------------
    # 生成觸發測試查詢
    # ------------------------------------------------------------------

    async def _generate_trigger_queries(
        self,
        ctx: SkillDevContext,
        skill_name: str,
        description: str,
    ) -> list[TriggerEvalQuery]:
        """呼叫 Agent 生成 ~20 個觸發測試查詢.

        待實現: 接入 create_stage_agent
        """
        # 待實現:
        # agent = ctx.create_stage_agent("desc_opt_gen", prompt, ...)
        # output = await agent.run(...)
        # parsed = json.loads(output)
        # return [TriggerEvalQuery(**q) for q in parsed]

        logger.warning("[DescOptimize] _generate_trigger_queries 待接入 Agent")
        return [
            TriggerEvalQuery(
                query=f"幫我用 {skill_name} 完成一個任務", should_trigger=True
            ),
            TriggerEvalQuery(query="幫我寫一個排序演算法", should_trigger=False),
        ]

    # ------------------------------------------------------------------
    # Train/test split（內化自官方 run_loop.py 的 split_eval_set）
    # ------------------------------------------------------------------

    @staticmethod
    def _split_eval_set(
        queries: list[TriggerEvalQuery],
        holdout: float,
        seed: int = 42,
    ) -> tuple[list[TriggerEvalQuery], list[TriggerEvalQuery]]:
        """按 should_trigger 分層切分 train/test."""
        rng = random.Random(seed)

        trigger = [q for q in queries if q.should_trigger]
        no_trigger = [q for q in queries if not q.should_trigger]
        rng.shuffle(trigger)
        rng.shuffle(no_trigger)

        n_t = max(1, int(len(trigger) * holdout))
        n_nt = max(1, int(len(no_trigger) * holdout))

        test = trigger[:n_t] + no_trigger[:n_nt]
        train = trigger[n_t:] + no_trigger[n_nt:]
        return train, test

    # ------------------------------------------------------------------
    # 最佳化迴圈（內化自官方 run_loop.py 的核心邏輯）
    # ------------------------------------------------------------------

    async def _optimization_loop(
        self,
        ctx: SkillDevContext,
        loop_input: _OptimizationLoopInput,
    ) -> tuple[str, list[DescOptimizeIteration]]:
        """執行 eval → improve 迴圈，返回 (best_description, history)."""
        skill_name = loop_input.skill_name
        skill_body = loop_input.skill_body
        current_desc = loop_input.current_desc
        train_set = loop_input.train_set
        test_set = loop_input.test_set
        history: list[DescOptimizeIteration] = []

        for i in range(1, MAX_ITERATIONS + 1):
            await ctx.emit(
                SkillDevEventType.PROGRESS,
                {
                    "message": f"描述最佳化第 {i}/{MAX_ITERATIONS} 輪...",
                },
            )

            # 評估 train + test
            train_results = await self._eval_description(ctx, current_desc, train_set)
            test_results = (
                await self._eval_description(ctx, current_desc, test_set)
                if test_set
                else None
            )

            train_passed = sum(1 for r in train_results if r["pass"])
            iteration = DescOptimizeIteration(
                iteration=i,
                description=current_desc,
                train_passed=train_passed,
                train_total=len(train_set),
                test_passed=sum(1 for r in test_results if r["pass"])
                if test_results
                else None,
                test_total=len(test_set) if test_results else None,
            )
            history.append(iteration)

            # 全部透過則提前退出
            if train_passed == len(train_set):
                break

            # 最後一輪不再改進
            if i == MAX_ITERATIONS:
                break

            # 改進 description
            improve_input = _ImproveDescriptionInput(
                skill_name=skill_name,
                skill_body=skill_body,
                current_desc=current_desc,
                train_results=train_results,
                history=history,
            )
            current_desc = await self._improve_description(ctx, improve_input)

        # 選 test score 最高的（防過擬合）
        if test_set:
            best = max(history, key=lambda h: h.test_passed or 0)
        else:
            best = max(history, key=lambda h: h.train_passed)
        return best.description, history

    # ------------------------------------------------------------------
    # 單次評估：判斷 description 對一組 queries 是否觸發
    # ------------------------------------------------------------------

    async def _eval_description(
        self,
        ctx: SkillDevContext,
        description: str,
        queries: list[TriggerEvalQuery],
    ) -> list[dict]:
        """對每個 query，呼叫模型判斷當前 description 是否會觸發.

        待實現: 接入 create_stage_agent 實際評估
              核心問題是模擬"模型看到 skill description 後是否會讀取該 skill"
        """
        # 待實現:
        # for query in queries:
        #     triggered = await self._test_single_trigger(ctx, description, query.query)
        #     ...

        logger.warning("[DescOptimize] _eval_description 待接入 Agent")
        return [
            {
                "query": q.query,
                "should_trigger": q.should_trigger,
                "triggered": q.should_trigger,  # 佔位：假設全部正確
                "pass": True,
            }
            for q in queries
        ]

    # ------------------------------------------------------------------
    # 改進 description（內化自官方 improve_description.py 的 prompt 結構）
    # ------------------------------------------------------------------

    async def _improve_description(
        self,
        ctx: SkillDevContext,
        improve_input: _ImproveDescriptionInput,
    ) -> str:
        """呼叫模型基於失敗案例改進 description.

        待實現: 接入 create_stage_agent
        """
        # 待實現:
        # failed_triggers = [r for r in train_results if r["should_trigger"] and not r["pass"]]
        # false_triggers = [r for r in train_results if not r["should_trigger"] and not r["pass"]]
        # prompt = IMPROVE_DESC_PROMPT.format(...)
        # agent = ctx.create_stage_agent("desc_improver", prompt, ...)
        # output = await agent.run(...)
        # return _extract_new_description(output)

        logger.warning("[DescOptimize] _improve_description 待接入 Agent")
        return improve_input.current_desc

    # ------------------------------------------------------------------
    # 將最佳化後的 description 寫回 SKILL.md
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_description(skill_md: Path, old_desc: str, new_desc: str) -> None:
        """替換 SKILL.md frontmatter 中的 description 欄位."""
        content = skill_md.read_text(encoding="utf-8")

        match = re.match(r"^(---\n)(.*?)(\n---)", content, re.DOTALL)
        if not match:
            return

        frontmatter = match.group(2)
        # 替換 description 行（簡單場景：單行 description: xxx）
        new_fm = re.sub(
            r"(description:\s*).*",
            rf"\g<1>{new_desc}",
            frontmatter,
            count=1,
        )
        new_content = match.group(1) + new_fm + match.group(3) + content[match.end():]
        skill_md.write_text(new_content, encoding="utf-8")
