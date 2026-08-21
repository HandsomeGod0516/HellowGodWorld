# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""StateStore — SkillDev 任務狀態的持久化層.

職責：在 Pipeline 的階段邊界 checkpoint 狀態，支援斷線/重啟後從上次進度恢復。

當前實現：本地檔案（state.json），適合單機部署。
擴充套件點：替換為 Redis 實現以支援多例項水平擴充套件（介面不變）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from jiuwenclaw.server.runtime.skill.skilldev.schema import SkillDevState

logger = logging.getLogger(__name__)


class StateStore:
    """SkillDev 任務狀態儲存（本地檔案實現）.

    執行緒/協程安全注意：當前本地檔案實現不加鎖，
    因為路由層保證同一 task_id 的請求始終路由到同一例項，不存在併發寫入。
    """

    def __init__(self, base_dir: Path) -> None:
        """
        Args:
            base_dir: SkillDev 工作區根目錄，約定為 get_workspace_dir() / "skilldev"
                      即 ~/.jiuwenclaw/agent/workspace/skilldev/
        """
        self._base_dir = base_dir

    def _state_file(self, task_id: str) -> Path:
        return self._base_dir / task_id / "state.json"

    async def save_state(self, task_id: str, state: SkillDevState) -> None:
        """將狀態序列化並寫入 state.json（checkpoint）."""
        state.touch()
        state_file = self._state_file(task_id)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        data = state.to_checkpoint_dict()
        state_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.debug(
            "[StateStore] checkpoint saved: task_id=%s stage=%s",
            task_id,
            state.stage.value,
        )

    async def load_state(self, task_id: str) -> SkillDevState | None:
        """從 state.json 恢復狀態，不存在則返回 None."""
        state_file = self._state_file(task_id)
        if not state_file.exists():
            logger.warning("[StateStore] state not found: task_id=%s", task_id)
            return None
        data = json.loads(state_file.read_text(encoding="utf-8"))
        state = SkillDevState.from_checkpoint_dict(data)
        logger.debug(
            "[StateStore] state loaded: task_id=%s stage=%s", task_id, state.stage.value
        )
        return state

    def load_state_sync(self, task_id: str) -> SkillDevState | None:
        """同步版 load_state，供非 async 上下文使用（如 status 查詢）."""
        state_file = self._state_file(task_id)
        if not state_file.exists():
            return None
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return SkillDevState.from_checkpoint_dict(data)

    def list_tasks(self) -> list[str]:
        """列出所有存在 checkpoint 的 task_id."""
        if not self._base_dir.exists():
            return []
        return [
            d.name
            for d in self._base_dir.iterdir()
            if d.is_dir() and (d / "state.json").exists()
        ]
