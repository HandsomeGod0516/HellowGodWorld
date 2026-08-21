# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillDevDeps — SkillDevService 的最小外部依賴定義.

設計原則：SkillDevService 不依賴 JiuWenClaw 例項，
只接收以下最小依賴集，由 JiuWenClaw 在初始化時注入。

JiuWenClaw 內部的 SkillManager、EvolutionService、對話歷史等
對 SkillDev 完全不可見，確保模組邊界清晰。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from jiuwenclaw.server.runtime.skill.skilldev.store import StateStore
from jiuwenclaw.server.runtime.skill.skilldev.workspace import WorkspaceProvider


@dataclass
class SkillDevDeps:
    """SkillDevService 的全部外部依賴（由 JiuWenClaw 構造並注入）."""

    # 模型配置：為每個階段建立獨立 ReActAgent 的基礎
    model_name: str
    model_client_config: dict

    # 工具能力：按需給 Agent 配工具
    # mcp_tools_factory: 返回當前可用 MCP 工具列表的工廠函式
    mcp_tools_factory: Callable[[], list]
    # sysop_config: 檔案系統訪問配置（SysOperationCard）；None 表示禁止檔案操作
    sysop_config: object | None

    # 基礎設施
    state_store: StateStore
    workspace_provider: WorkspaceProvider
