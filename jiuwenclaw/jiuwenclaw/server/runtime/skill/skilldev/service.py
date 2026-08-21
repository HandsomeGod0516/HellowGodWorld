# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillDevService — 無狀態請求處理器.

設計要點：
- 無狀態：不持有 Pipeline 物件，不做 Pipeline 生命週期管理
- 每次請求：StateStore 載入狀態 → 建立 Pipeline → 執行 → checkpoint → 釋放
- 路由層（Gateway）保證同一 task_id 的請求路由到同一例項，Service 無需關心

對外只暴露一個入口：handle(request) → AsyncIterator[AgentResponseChunk]

前端只需 5 個 method：
- skilldev.start     → 發起新任務
- skilldev.respond   → 統一確認（後端根據 task_id 當前階段自動路由）
- skilldev.status    → 查狀態 / 列任務
- skilldev.download  → 下載產物
- skilldev.cancel    → 取消任務
- skilldev.file.list → 獲取檔案樹
- skilldev.file.read → 讀取檔案內容
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import AsyncIterator

from jiuwenclaw.common.schema.agent import AgentRequest, AgentResponseChunk
from jiuwenclaw.common.schema.message import ReqMethod
from jiuwenclaw.server.runtime.skill.skilldev.deps import SkillDevDeps
from jiuwenclaw.server.runtime.skill.skilldev.pipeline import SkillDevPipeline
from jiuwenclaw.server.runtime.skill.skilldev.schema import (
    SUSPENSION_POINTS,
    SkillDevEvent,
    SkillDevState,
    SkillDevStage,
    generate_task_id,
)

logger = logging.getLogger(__name__)

# method → handler 對映，避免 if/elif 鏈
_METHOD_DISPATCH = {
    ReqMethod.SKILLDEV_START: "_handle_start",
    ReqMethod.SKILLDEV_RESPOND: "_handle_respond",
    ReqMethod.SKILLDEV_STATUS: "_handle_status",
    ReqMethod.SKILLDEV_DOWNLOAD: "_handle_download",
    ReqMethod.SKILLDEV_CANCEL: "_handle_cancel",
    ReqMethod.SKILLDEV_FILE_LIST: "_handle_file_list",
    ReqMethod.SKILLDEV_FILE_READ: "_handle_file_read",
}


class SkillDevService:
    """SkillDev 模式的服務入口（無狀態）."""

    def __init__(self, deps: SkillDevDeps) -> None:
        self._deps = deps

    # ------------------------------------------------------------------
    # 統一入口
    # ------------------------------------------------------------------

    async def handle(self, request: AgentRequest) -> AsyncIterator[AgentResponseChunk]:
        """根據 ReqMethod 分發到具體處理函式."""
        handler_name = _METHOD_DISPATCH.get(request.req_method)
        if handler_name is None:
            yield self._error_chunk(
                request.request_id,
                request.channel_id,
                f"未知 method: {request.req_method}",
            )
            return

        handler = getattr(self, handler_name)
        result = handler(request.params, request.request_id, request.channel_id)

        if hasattr(result, "__aiter__"):
            async for chunk in result:
                yield chunk
        else:
            yield result

    # ------------------------------------------------------------------
    # skilldev.start — 發起新任務
    # ------------------------------------------------------------------

    async def _handle_start(
        self, params: dict, request_id: str, channel_id: str
    ) -> AsyncIterator[AgentResponseChunk]:
        task_id = generate_task_id()
        state = SkillDevState(
            task_id=task_id,
            input={
                "query": params.get("query", ""),
                "tools": params.get("tools", []),
                "resources": params.get("resources", []),
                "existing_skill": params.get("existing_skill"),
            },
        )
        pipeline = SkillDevPipeline(task_id=task_id, state=state, deps=self._deps)

        yield AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"event_type": "skilldev.started", "task_id": task_id},
            is_complete=False,
        )

        async for event in pipeline.run():
            yield self._event_to_chunk(event, request_id, channel_id)

        yield AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={
                "event_type": "skilldev.suspended",
                "task_id": task_id,
                "stage": state.stage.value,
            },
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # skilldev.respond — 統一確認入口
    # 前端只管發 {task_id, action, ...}，後端根據當前階段自動路由
    # ------------------------------------------------------------------

    async def _handle_respond(
        self, params: dict, request_id: str, channel_id: str
    ) -> AsyncIterator[AgentResponseChunk]:
        task_id = params.get("task_id")
        if not task_id:
            yield self._error_chunk(request_id, channel_id, "缺少 task_id 引數")
            return

        state = await self._deps.state_store.load_state(task_id)
        if state is None:
            yield self._error_chunk(request_id, channel_id, f"任務 {task_id} 不存在")
            return

        if state.stage not in SUSPENSION_POINTS:
            yield self._error_chunk(
                request_id,
                channel_id,
                f"任務 {task_id} 當前階段 {state.stage.value} 不是掛起點，無法 respond",
            )
            return

        pipeline = SkillDevPipeline(task_id=task_id, state=state, deps=self._deps)

        async for event in pipeline.resume(data=params):
            yield self._event_to_chunk(event, request_id, channel_id)

        is_done = state.stage == SkillDevStage.COMPLETED
        yield AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={
                "event_type": "skilldev.completed" if is_done else "skilldev.suspended",
                "task_id": task_id,
                "stage": state.stage.value,
            },
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # skilldev.status — 查狀態 / 列任務
    # 傳 task_id → 返回單個任務狀態；不傳 → 返回任務列表
    # ------------------------------------------------------------------

    def _handle_status(
        self, params: dict, request_id: str, channel_id: str
    ) -> AgentResponseChunk:
        task_id = params.get("task_id")
        if not task_id:
            task_ids = self._deps.state_store.list_tasks()
            return AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload={"ok": True, "tasks": task_ids},
                is_complete=True,
            )

        state = self._deps.state_store.load_state_sync(task_id)
        payload = (
            state.to_status_dict() if state else {"error": f"任務 {task_id} 不存在"}
        )
        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"ok": state is not None, **payload},
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # skilldev.download — 下載產物
    # ------------------------------------------------------------------

    def _handle_download(
        self, params: dict, request_id: str, channel_id: str
    ) -> AgentResponseChunk:
        task_id = params.get("task_id")
        if not task_id:
            return self._error_chunk(request_id, channel_id, "缺少 task_id 引數")

        state = self._deps.state_store.load_state_sync(task_id)
        if state is None or not state.zip_path:
            return self._error_chunk(
                request_id, channel_id, f"任務 {task_id} 尚未完成打包"
            )

        zip_path = Path(state.zip_path)
        if not zip_path.exists():
            return self._error_chunk(request_id, channel_id, "產物檔案不存在")

        content_b64 = base64.b64encode(zip_path.read_bytes()).decode()
        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={
                "ok": True,
                "filename": zip_path.name,
                "content_base64": content_b64,
                "size_bytes": state.zip_size,
            },
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # skilldev.cancel — 取消任務
    # ------------------------------------------------------------------
    @staticmethod
    async def _handle_cancel(params: dict, request_id: str, channel_id: str) -> AgentResponseChunk:
        task_id = params.get("task_id", "")
        # 待實現: 實現取消邏輯（中斷正在執行的 Pipeline）
        logger.warning("[SkillDevService] cancel 尚未實現: task_id=%s", task_id)
        return await AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"ok": True, "message": "取消請求已接收（實現待完善）"},
            is_complete=True,)

    # ------------------------------------------------------------------
    # skilldev.file.list — 獲取工作區檔案樹（供產物彈窗瀏覽）
    # ------------------------------------------------------------------

    def _handle_file_list(
        self, params: dict, request_id: str, channel_id: str
    ) -> AgentResponseChunk:
        task_id = params.get("task_id")
        if not task_id:
            return self._error_chunk(request_id, channel_id, "缺少 task_id 引數")

        workspace = self._deps.workspace_provider.get_local_path(task_id)
        skill_dir = workspace / "skill"
        if not skill_dir.exists():
            return AgentResponseChunk(
                request_id=request_id,
                channel_id=channel_id,
                payload={"ok": True, "tree": []},
                is_complete=True,
            )

        tree = self._build_file_tree(skill_dir, skill_dir)
        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"ok": True, "tree": tree},
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # skilldev.file.read — 讀取工作區檔案內容
    # ------------------------------------------------------------------

    def _handle_file_read(
        self, params: dict, request_id: str, channel_id: str
    ) -> AgentResponseChunk:
        task_id = params.get("task_id")
        file_path = params.get("path", "")
        if not task_id or not file_path:
            return self._error_chunk(
                request_id, channel_id, "缺少 task_id 或 path 引數"
            )

        workspace = self._deps.workspace_provider.get_local_path(task_id)
        skill_dir = workspace / "skill"
        full_path = (skill_dir / file_path).resolve()

        if not str(full_path).startswith(str(skill_dir.resolve())):
            return self._error_chunk(
                request_id, channel_id, "路徑非法：不能訪問工作區外的檔案"
            )

        if not full_path.exists() or not full_path.is_file():
            return self._error_chunk(request_id, channel_id, f"檔案不存在: {file_path}")

        try:
            content = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = f"[二進位制檔案，大小 {full_path.stat().st_size} bytes]"

        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"ok": True, "path": file_path, "content": content},
            is_complete=True,
        )

    # ------------------------------------------------------------------
    # 工具函式
    # ------------------------------------------------------------------

    @staticmethod
    def _event_to_chunk(
        event: SkillDevEvent, request_id: str, channel_id: str
    ) -> AgentResponseChunk:
        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"event_type": event.event_type.value, **event.payload},
            is_complete=False,
        )

    @staticmethod
    def _error_chunk(
        request_id: str, channel_id: str, message: str
    ) -> AgentResponseChunk:
        return AgentResponseChunk(
            request_id=request_id,
            channel_id=channel_id,
            payload={"event_type": "skilldev.error", "error": message},
            is_complete=True,
        )

    @staticmethod
    def _build_file_tree(directory: Path, root: Path) -> list[dict]:
        """遞迴構建檔案樹."""
        result: list[dict] = []
        try:
            entries = sorted(
                directory.iterdir(), key=lambda p: (not p.is_dir(), p.name)
            )
        except PermissionError:
            return result

        for entry in entries:
            if entry.name.startswith("."):
                continue
            rel = str(entry.relative_to(root)).replace("\\", "/")
            if entry.is_dir():
                children = SkillDevService._build_file_tree(entry, root)
                result.append({"path": rel + "/", "type": "dir", "children": children})
            else:
                result.append(
                    {"path": rel, "type": "file", "size": entry.stat().st_size}
                )
        return result
