# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""WorkspaceProvider — SkillDev 任務工作區管理.

職責：提供每個 task_id 的隔離工作區目錄，並維護標準目錄結構。

目錄結構（單機本地模式）：
    ~/.jiuwenclaw/agent/workspace/skilldev/{task_id}/
    ├── state.json          ← StateStore checkpoint
    ├── resources/          ← 上傳的資原始檔（解壓後）
    ├── skill/              ← 生成的 skill 目錄
    │   ├── SKILL.md
    │   └── ...
    ├── evals/
    │   ├── evals.json      ← 測試用例定義
    │   └── iteration-{N}/  ← 每輪測試結果
    └── output/
        └── {skill_name}.skill  ← 最終打包產物

base_dir 由呼叫方傳入，約定為 get_workspace_dir() / "skilldev"，
與整個 jiuwenclaw 的目錄體系保持一致，不另起頂級目錄。

擴充套件點：替換為支援遠端物件儲存的實現（介面不變），
        sync_to_remote 屆時將檔案同步到 S3/OBS。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkspaceProvider:
    """SkillDev 工作區管理（本地檔案系統實現）."""

    def __init__(self, base_dir: Path) -> None:
        """
        Args:
            base_dir: SkillDev 工作區根目錄，約定為 get_workspace_dir() / "skilldev"
                      即 ~/.jiuwenclaw/agent/workspace/skilldev/
        """
        self._base_dir = base_dir

    def get_local_path(self, task_id: str) -> Path:
        """返回指定任務的本地工作區路徑（不保證已建立）."""
        return self._base_dir / task_id

    async def ensure_local(self, task_id: str) -> Path:
        """確保工作區目錄及其標準子目錄存在，返回工作區根路徑."""
        workspace = self._base_dir / task_id
        for sub in ("resources", "skill", "evals", "output"):
            (workspace / sub).mkdir(parents=True, exist_ok=True)
        logger.debug("[WorkspaceProvider] workspace ready: %s", workspace)
        return workspace

    async def sync_to_remote(self, task_id: str) -> None:
        """將本地工作區同步到遠端儲存（本地實現為空操作）.

        擴充套件點：多例項部署時，此處將檔案同步到共享儲存（S3/OBS/NFS），
        以支援不同例項間的工作區共享。當前單機部署無需實現。
        """
        # 待實現: 生產環境實現遠端同步（S3 / OBS / NFS）
        pass
