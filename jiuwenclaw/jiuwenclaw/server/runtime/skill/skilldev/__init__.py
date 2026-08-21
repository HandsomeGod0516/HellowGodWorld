# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillDev — Skill 開發模式模組.

提供 Skill 建立/最佳化/升級的全流程能力，面向平臺前端開發者。

主要入口：
    SkillDevService.handle(request) → AsyncIterator[AgentResponseChunk]

核心元件：
    schema.py       — 資料模型（階段、狀態、事件、掛起點）
    deps.py         — 最小外部依賴定義（由 JiuWenClaw 注入）
    store.py        — 狀態持久化（StateStore）
    workspace.py    — 工作區管理（WorkspaceProvider）
    context.py      — 階段執行上下文（SkillDevContext）
    pipeline.py     — 確定性狀態機編排器（SkillDevPipeline）
    service.py      — 無狀態請求處理器（SkillDevService）
    stages/         — 各階段處理器（StageHandler 子類）
"""

from jiuwenclaw.server.runtime.skill.skilldev.deps import SkillDevDeps
from jiuwenclaw.server.runtime.skill.skilldev.service import SkillDevService
from jiuwenclaw.server.runtime.skill.skilldev.store import StateStore
from jiuwenclaw.server.runtime.skill.skilldev.workspace import WorkspaceProvider

__all__ = [
    "SkillDevDeps",
    "SkillDevService",
    "StateStore",
    "WorkspaceProvider",
]
