# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""StageHandler 基類和 StageResult.

每個階段處理器繼承 StageHandler，實現 execute() 方法。
execute() 執行完成後返回 StageResult，告知 Pipeline 下一個跳轉階段。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from jiuwenclaw.server.runtime.skill.skilldev.schema import SkillDevStage


@dataclass
class StageResult:
    """階段執行結果，由 Pipeline 讀取以驅動狀態跳轉."""

    next_stage: SkillDevStage


class StageHandler(ABC):
    """SkillDev Pipeline 階段處理器基類.

    每個階段獨立實現，透過 execute() 與 Pipeline 互動。
    處理器不應持有跨請求的狀態——所有狀態均透過 SkillDevContext 傳入。
    """

    @abstractmethod
    async def execute(self, ctx) -> StageResult:
        """執行階段邏輯.

        Args:
            ctx: SkillDevContext，包含 state、workspace、emit、create_stage_agent 等

        Returns:
            StageResult，Pipeline 據此跳轉到下一階段
        """
