# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""INIT 階段處理器.

職責：
1. 建立工作區目錄（resources/ skill/ evals/ output/）
2. 解析資源包（base64 → 檔案 → 提取文字）
3. 解析已有 skill zip（修改/升級場景）
4. 判斷任務模式（CREATE / CREATE_WITH_RESOURCES / MODIFY）
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from jiuwenclaw.server.runtime.skill.skilldev.context import SkillDevContext
from jiuwenclaw.server.runtime.skill.skilldev.schema import (
    SkillDevEventType,
    SkillDevStage,
    SkillDevTaskMode,
    determine_task_mode,
)
from jiuwenclaw.server.runtime.skill.skilldev.stages.base import StageHandler, StageResult

logger = logging.getLogger(__name__)


class InitStageHandler(StageHandler):
    """INIT 階段：解析請求引數，準備工作區."""

    async def execute(self, ctx: SkillDevContext) -> StageResult:
        await ctx.emit(SkillDevEventType.PROGRESS, {"message": "正在初始化工作區..."})

        # 判斷任務模式
        ctx.state.mode = determine_task_mode(ctx.state.input)
        logger.info("[InitStage] task_id=%s mode=%s", ctx.task_id, ctx.state.mode.value)

        # 工作區已由 Pipeline 的 ensure_local 建立，此處直接使用
        resources_dir = ctx.workspace / "resources"
        skill_dir = ctx.workspace / "skill"

        # 解析上傳的資原始檔
        resources = ctx.state.input.get("resources", [])
        if resources:
            await ctx.emit(
                SkillDevEventType.PROGRESS,
                {"message": f"正在解析 {len(resources)} 個資原始檔..."},
            )
            ctx.state.reference_texts = await self._extract_resources(
                resources, resources_dir
            )

        # 解析已有 skill 包（修改/升級場景）
        existing_skill = ctx.state.input.get("existing_skill")
        if existing_skill:
            await ctx.emit(
                SkillDevEventType.PROGRESS, {"message": "正在解析已有 Skill 包..."}
            )
            ctx.state.existing_skill_md = await self._extract_existing_skill(
                existing_skill, skill_dir
            )

        await ctx.emit(
            SkillDevEventType.PROGRESS, {"message": "初始化完成，準備生成開發計劃"}
        )
        return StageResult(next_stage=SkillDevStage.PLAN)

    async def _extract_resources(
        self, resources: list[dict], dest_dir: Path
    ) -> list[str]:
        """解析資原始檔列表，提取純文字內容.

        支援格式：.zip（解壓）/ .docx（python-docx）/ .pdf（pdfplumber）/ .txt / .md

        待實現: 實現各格式的文字提取邏輯
        """
        texts: list[str] = []
        for res in resources:
            name = res.get("name", "unknown")
            content_b64 = res.get("content_base64", "")
            try:
                raw = base64.b64decode(content_b64)
                file_path = dest_dir / name
                file_path.write_bytes(raw)
                # 待實現: 根據字尾分發到對應解析器（docx/pdf/txt/md/zip）
                text = self._parse_file_to_text(file_path)
                if text:
                    texts.append(text)
            except Exception as exc:
                logger.warning(
                    "[InitStage] 資原始檔解析失敗: name=%s error=%s", name, exc
                )
        return texts

    def _parse_file_to_text(self, file_path: Path) -> str:
        """將檔案解析為純文字.

        待實現: 實現各格式的解析邏輯：
            - .docx → python-docx
            - .pdf  → pdfplumber
            - .txt / .md → 直接讀取
            - .zip  → 解壓後遞迴處理
        """
        suffix = file_path.suffix.lower()
        if suffix in (".txt", ".md"):
            return file_path.read_text(encoding="utf-8", errors="ignore")
        # 待實現: 其他格式
        logger.warning("[InitStage] 暫不支援的檔案格式: %s", suffix)
        return ""

    async def _extract_existing_skill(
        self, existing_skill: dict, dest_dir: Path
    ) -> str | None:
        """解壓已有 skill.zip，提取 SKILL.md 內容.

        待實現: 實現 zip 解壓邏輯
        """
        # 待實現:
        # import zipfile, io
        # raw = base64.b64decode(existing_skill["content_base64"])
        # with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        #     zf.extractall(dest_dir)
        # skill_md = dest_dir / "SKILL.md"
        # return skill_md.read_text(encoding="utf-8") if skill_md.exists() else None
        logger.warning("[InitStage] _extract_existing_skill 尚未實現")
        return None
