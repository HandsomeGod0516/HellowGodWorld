# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillDevPipeline — 確定性狀態機編排器.

Pipeline 是整個 SkillDev 流程的骨架：
- 維護階段跳轉順序（STAGE_HANDLERS 登錄檔）
- 在掛起點（PLAN_CONFIRM / REVIEW）checkpoint 並暫停
- 提供 run() 和 resume() 兩個執行入口
- 每次請求建立、執行到掛起點/完成後釋放（不長駐記憶體）

Pipeline 不關心"怎麼做"，只關心"做什麼順序"。
具體邏輯全部委託給各階段的 StageHandler.execute()。
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from jiuwenclaw.server.runtime.skill.skilldev.context import SkillDevContext
from jiuwenclaw.server.runtime.skill.skilldev.deps import SkillDevDeps
from jiuwenclaw.server.runtime.skill.skilldev.schema import (
    SUSPENSION_POINTS,
    SkillDevEvent,
    SkillDevEventType,
    SkillDevStage,
    SkillDevState,
    compute_todos,
)
from jiuwenclaw.server.runtime.skill.skilldev.stages import (
    DescOptimizeStageHandler,
    EvaluateStageHandler,
    GenerateStageHandler,
    ImproveStageHandler,
    InitStageHandler,
    PackageStageHandler,
    PlanStageHandler,
    TestDesignStageHandler,
    TestRunStageHandler,
    ValidateStageHandler,
)

logger = logging.getLogger(__name__)


class SkillDevPipeline:
    """SkillDev 確定性狀態機.

    生命週期：每次請求建立 → run()/resume() 執行 → checkpoint → 物件釋放。
    不長駐記憶體，不持有 JiuWenClaw 例項。
    """

    # PLAN_CONFIRM / REVIEW / DESC_OPTIMIZE_CONFIRM 是掛起點，由 SUSPENSION_POINTS 處理
    STAGE_HANDLERS = {
        SkillDevStage.INIT: InitStageHandler,
        SkillDevStage.PLAN: PlanStageHandler,
        SkillDevStage.GENERATE: GenerateStageHandler,
        SkillDevStage.VALIDATE: ValidateStageHandler,
        SkillDevStage.TEST_DESIGN: TestDesignStageHandler,
        SkillDevStage.TEST_RUN: TestRunStageHandler,
        SkillDevStage.EVALUATE: EvaluateStageHandler,
        SkillDevStage.IMPROVE: ImproveStageHandler,
        SkillDevStage.PACKAGE: PackageStageHandler,
        SkillDevStage.DESC_OPTIMIZE: DescOptimizeStageHandler,
    }

    def __init__(self, task_id: str, state: SkillDevState, deps: SkillDevDeps) -> None:
        self.task_id = task_id
        self.state = state
        self._deps = deps
        self._event_queue: asyncio.Queue = asyncio.Queue()

    async def run(self) -> AsyncIterator[SkillDevEvent]:
        """從當前階段開始執行，直到遇到掛起點或終態.

        Yields:
            SkillDevEvent：各階段產生的事件，由 Service 轉換為 AgentResponseChunk
        """
        while self.state.stage not in (SkillDevStage.COMPLETED, SkillDevStage.ERROR):
            # 命中掛起點：推送確認請求 → checkpoint → 暫停
            if self.state.stage in SUSPENSION_POINTS:
                suspension = SUSPENSION_POINTS[self.state.stage]
                await self._emit(
                    SkillDevEventType.TODOS_UPDATE,
                    {
                        "todos": compute_todos(self.state.stage, self.state.mode),
                    },
                )
                await self._emit(
                    SkillDevEventType.CONFIRM_REQUEST,
                    {
                        "confirm_type": suspension.confirm_type,
                        "title": suspension.title,
                        "message": suspension.message,
                        "data": suspension.extract_data(self.state),
                        "actions": suspension.actions,
                    },
                )
                await self._checkpoint()
                break

            # 執行當前階段
            handler_cls = self.STAGE_HANDLERS.get(self.state.stage)
            if handler_cls is None:
                raise RuntimeError(f"階段 {self.state.stage} 沒有對應的處理器")

            workspace = await self._deps.workspace_provider.ensure_local(self.task_id)
            ctx = SkillDevContext(
                task_id=self.task_id,
                deps=self._deps,
                state=self.state,
                workspace=workspace,
                event_queue=self._event_queue,
            )

            await self._emit(
                SkillDevEventType.STAGE_CHANGED,
                {
                    "stage": self.state.stage.value,
                    "iteration": self.state.iteration,
                },
            )
            await self._emit(
                SkillDevEventType.TODOS_UPDATE,
                {
                    "todos": compute_todos(self.state.stage, self.state.mode),
                },
            )

            try:
                handler = handler_cls()
                result = await handler.execute(ctx)
                self.state.stage = result.next_stage
                await self._checkpoint()
            except Exception as exc:
                logger.exception(
                    "[Pipeline] 階段 %s 執行失敗: %s", self.state.stage.value, exc
                )
                self.state.stage = SkillDevStage.ERROR
                self.state.error = str(exc)
                await self._emit(SkillDevEventType.ERROR, {"message": str(exc)})
                await self._checkpoint()
                break

        # 排空事件佇列，yield 給呼叫方
        while not self._event_queue.empty():
            yield self._event_queue.get_nowait()

    async def resume(self, data: dict) -> AsyncIterator[SkillDevEvent]:
        """從掛起點恢復執行.

        Args:
            data: 外部傳入的恢復資料（plan 確認內容 / 評測反饋）

        Yields:
            SkillDevEvent：恢復後各階段產生的事件
        """
        current_stage = self.state.stage
        if current_stage not in SUSPENSION_POINTS:
            raise ValueError(f"階段 {current_stage} 不是掛起點，無法呼叫 resume()")

        suspension = SUSPENSION_POINTS[current_stage]

        # 呼叫 on_resume 更新狀態（寫入使用者確認的 plan / 反饋）
        suspension.on_resume(self.state, data)

        # 計算下一階段（REVIEW 階段的 next_stage 是函式，根據 action 動態決定）
        next_stage = suspension.next_stage
        if callable(next_stage):
            next_stage = next_stage(data)
        self.state.stage = next_stage

        async for event in self.run():
            yield event

    async def _emit(self, event_type: SkillDevEventType, payload: dict) -> None:
        """向事件佇列寫入一個事件."""
        event = SkillDevEvent(
            event_type=event_type,
            payload={"task_id": self.task_id, **payload},
            task_id=self.task_id,
        )
        await self._event_queue.put(event)

    async def _checkpoint(self) -> None:
        """階段邊界：持久化狀態 + 同步工作區檔案."""
        await self._deps.state_store.save_state(self.task_id, self.state)
        await self._deps.workspace_provider.sync_to_remote(self.task_id)
        logger.debug(
            "[Pipeline] checkpoint: task_id=%s stage=%s",
            self.task_id,
            self.state.stage.value,
        )
