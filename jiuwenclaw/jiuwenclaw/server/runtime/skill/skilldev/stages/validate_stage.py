# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""VALIDATE 階段處理器.

校驗 GENERATE 產出的 SKILL.md 是否符合 Skill 規範：
- YAML frontmatter 存在且合法（name, description 必填）
- name 是 kebab-case，≤64 字元
- description ≤1024 字元，無 < >
- 只包含允許的 frontmatter key

校驗失敗 → 回退 GENERATE 重新生成。
校驗成功 → 進入 TEST_DESIGN。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from jiuwenclaw.server.runtime.skill.skilldev.context import SkillDevContext
from jiuwenclaw.server.runtime.skill.skilldev.schema import (
    ALLOWED_FRONTMATTER_KEYS,
    SKILL_DESC_MAX_LEN,
    SKILL_NAME_MAX_LEN,
    SkillDevEventType,
    SkillDevStage,
)
from jiuwenclaw.server.runtime.skill.skilldev.stages.base import StageHandler, StageResult

logger = logging.getLogger(__name__)


class ValidateStageHandler(StageHandler):
    """VALIDATE 階段：校驗 SKILL.md 格式合規性."""

    async def execute(self, ctx: SkillDevContext) -> StageResult:
        skill_md_path = ctx.workspace / "skill" / "SKILL.md"

        if not skill_md_path.exists():
            await ctx.emit(
                SkillDevEventType.VALIDATE_RESULT,
                {
                    "valid": False,
                    "message": "SKILL.md 未生成",
                },
            )
            return StageResult(next_stage=SkillDevStage.GENERATE)

        valid, message = validate_skill_md(skill_md_path)
        await ctx.emit(
            SkillDevEventType.VALIDATE_RESULT, {"valid": valid, "message": message}
        )

        if not valid:
            logger.warning("[ValidateStage] 校驗失敗: %s，回退到 GENERATE", message)
            return StageResult(next_stage=SkillDevStage.GENERATE)

        return StageResult(next_stage=SkillDevStage.TEST_DESIGN)


# ---------------------------------------------------------------------------
# 校驗邏輯（內化自官方 quick_validate.py）
# ---------------------------------------------------------------------------


def validate_skill_md(skill_md_path: Path) -> tuple[bool, str]:
    """校驗 SKILL.md 的 YAML frontmatter 格式.

    返回 (is_valid, message)。
    """
    content = skill_md_path.read_text(encoding="utf-8")

    if not content.startswith("---"):
        return False, "SKILL.md 缺少 YAML frontmatter（應以 --- 開頭）"

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "YAML frontmatter 格式無效"

    frontmatter = _parse_frontmatter(match.group(1))

    if "name" not in frontmatter:
        return False, "frontmatter 缺少必填欄位 'name'"
    if "description" not in frontmatter:
        return False, "frontmatter 缺少必填欄位 'description'"

    unexpected = set(frontmatter.keys()) - ALLOWED_FRONTMATTER_KEYS
    if unexpected:
        return False, f"frontmatter 包含未允許的欄位: {', '.join(sorted(unexpected))}"

    # name: kebab-case
    name = frontmatter["name"].strip()
    if name and not re.match(r"^[a-z0-9-]+$", name):
        return False, f"name '{name}' 必須是 kebab-case（小寫字母、數字、連字元）"
    if name and _has_invalid_hyphen_usage(name):
        return False, f"name '{name}' 不能以連字元開頭/結尾或包含連續連字元"
    if name and len(name) > SKILL_NAME_MAX_LEN:
        return False, f"name 過長（{len(name)} 字元，最大 {SKILL_NAME_MAX_LEN}）"

    # description
    desc = frontmatter["description"].strip()
    if "<" in desc or ">" in desc:
        return False, "description 不能包含尖括號 (< 或 >)"
    if len(desc) > SKILL_DESC_MAX_LEN:
        return False, f"description 過長（{len(desc)} 字元，最大 {SKILL_DESC_MAX_LEN}）"

    return True, "SKILL.md 校驗透過"


def parse_skill_frontmatter(skill_md_path: Path) -> tuple[str, str, str]:
    """從 SKILL.md 解析出 (name, description, body_content).

    輕量解析器，無 PyYAML 依賴。
    """
    content = skill_md_path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n?(.*)", content, re.DOTALL)
    if not match:
        return "", "", content

    fm = _parse_frontmatter(match.group(1))
    return fm.get("name", ""), fm.get("description", ""), match.group(2)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """極簡 YAML frontmatter 解析（key: value 單行 + block scalar）.

    生產環境可替換為 yaml.safe_load（需新增 PyYAML 依賴）。
    """
    result: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for line in text.split("\n"):
        key_match = re.match(r"^([a-zA-Z_-]+):\s*(.*)", line)
        if key_match:
            if current_key:
                result[current_key] = "\n".join(current_lines).strip()
            current_key = key_match.group(1)
            value = key_match.group(2)
            current_lines = [] if value in ("|", ">") else [value]
        elif current_key:
            current_lines.append(line)

    if current_key:
        result[current_key] = "\n".join(current_lines).strip()

    return result


def _has_invalid_hyphen_usage(name: str) -> bool:
    """校驗 name 中連字元使用是否非法."""
    return name.startswith("-") or name.endswith("-") or "--" in name
