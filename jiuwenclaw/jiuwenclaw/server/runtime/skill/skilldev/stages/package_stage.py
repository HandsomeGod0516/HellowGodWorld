# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""PACKAGE 階段處理器.

- 將 skill/ 目錄打包為 {skill_name}.skill（zip 格式，與官方 .skill 格式一致）
- 排除 evals/（根目錄級）、__pycache__、node_modules、.DS_Store、*.pyc 等
- 推送 ARTIFACT_READY 事件 → 跳轉到 DESC_OPTIMIZE_CONFIRM
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from jiuwenclaw.server.runtime.skill.skilldev.context import SkillDevContext
from jiuwenclaw.server.runtime.skill.skilldev.schema import SkillDevEventType, SkillDevStage
from jiuwenclaw.server.runtime.skill.skilldev.stages.base import StageHandler, StageResult

logger = logging.getLogger(__name__)

# 打包排除規則
_EXCLUDE_DIRS = {"__pycache__", "node_modules", ".git"}
_EXCLUDE_FILES = {".DS_Store"}
_EXCLUDE_GLOBS = {"*.pyc"}
_ROOT_EXCLUDE_DIRS = {"evals"}


class PackageStageHandler(StageHandler):
    """PACKAGE 階段：打包 skill/ 為 .skill (zip) 檔案."""

    async def execute(self, ctx: SkillDevContext) -> StageResult:
        skill_dir = ctx.workspace / "skill"
        output_dir = ctx.workspace / "output"
        output_dir.mkdir(exist_ok=True)

        skill_name = (ctx.state.plan or {}).get("skill_name", "skill")
        # 官方格式為 .skill（本質是 zip）
        skill_filename = f"{skill_name}.skill"
        skill_path = output_dir / skill_filename

        await ctx.emit(
            SkillDevEventType.PROGRESS, {"message": f"正在打包 {skill_filename}..."}
        )

        self._zip_skill_dir(skill_dir, skill_path)

        ctx.state.zip_path = str(skill_path)
        ctx.state.zip_size = skill_path.stat().st_size

        await ctx.emit(
            SkillDevEventType.ARTIFACT_READY,
            {
                "artifact": {
                    "id": "skill_package",
                    "name": skill_filename,
                    "type": "skill_package",
                    "size_bytes": ctx.state.zip_size,
                    "browsable": True,
                    "downloadable": True,
                },
            },
        )
        return StageResult(next_stage=SkillDevStage.DESC_OPTIMIZE_CONFIRM)

    def _zip_skill_dir(self, skill_dir: Path, zip_path: Path) -> None:
        """將 skill_dir 打包為 zip，排除無關檔案."""
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in skill_dir.rglob("*"):
                if not file_path.is_file():
                    continue
                if self._should_exclude(file_path, skill_dir):
                    continue
                arcname = file_path.relative_to(skill_dir)
                zf.write(file_path, arcname)
        logger.info(
            "[PackageStage] 打包完成: %s (%d bytes)", zip_path, zip_path.stat().st_size
        )

    def _should_exclude(self, file_path: Path, skill_dir: Path) -> bool:
        """判斷檔案是否應被排除出 zip 包.

        排除規則：目錄級排除 + 檔案級排除 + glob 匹配。
        """
        import fnmatch

        rel_path = file_path.relative_to(skill_dir)
        parts = rel_path.parts

        if any(part in _EXCLUDE_DIRS for part in parts):
            return True

        # 根目錄級別的排除（如 evals/）
        if len(parts) > 0 and parts[0] in _ROOT_EXCLUDE_DIRS:
            return True

        if rel_path.name in _EXCLUDE_FILES:
            return True

        return any(fnmatch.fnmatch(rel_path.name, pat) for pat in _EXCLUDE_GLOBS)
