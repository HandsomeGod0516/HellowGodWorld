# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _get_memory_forbidden_config() -> Dict[str, Any]:
    """從 config.yaml 讀取 memory.forbidden_memory_definition 配置."""
    try:
        from jiuwenclaw.common.config import get_config
        config = get_config()
        memory_config = config.get("memory", {})
        forbidden_config = memory_config.get("forbidden_memory_definition", {})
        return {
            "enabled": forbidden_config.get("enabled", False),
            "patterns": forbidden_config.get("patterns", []),
            "description": forbidden_config.get("description", {
                "zh": "以下內容禁止記憶：密碼、API金鑰、Secret、Token、信用卡號、身份證號、手機號等敏感資訊",
                "en": "The following content is forbidden to remember: passwords, \API keys, secrets, tokens, \
                    credit card numbers, ID numbers, phone numbers and other sensitive information",
            }),
        }
    except Exception as e:
        logger.warning("[forbidden] Failed to load memory forbidden config: %s", e)
        return {"enabled": False, "patterns": [], "description": {}}


def get_forbidden_memory_prompt(language: str) -> str:
    """讀取 config.yaml 的 memory.forbidden_memory_definition，
    返回格式化的限制提示詞。enabled=false 時返回空字串。

    Args:
        language: 語言程式碼 (zh/en)

    Returns:
        格式化的禁止記憶提示詞，或空字串
    """
    config = _get_memory_forbidden_config()

    if not config.get("enabled", False):
        return ""

    description = config.get("description", {})
    desc_text = description.get(language, description.get("zh", ""))
    patterns = config.get("patterns", [])

    if language == "zh":
        prompt_parts = ["### 記憶限制規則", ""]
        if desc_text:
            prompt_parts.append(desc_text)
            prompt_parts.append("")
        if patterns:
            prompt_parts.append("**禁止記憶的敏感資訊型別包括：**")
            prompt_parts.append("")
            for i, pattern in enumerate(patterns, 1):
                prompt_parts.append(f"{i}. `{pattern}`")
            prompt_parts.append("")
        prompt_parts.append("**執行要求：**")
        prompt_parts.append("- 在呼叫 `experience_learn` 或 `write_memory` 儲存記憶前，必須檢查內容是否包含上述敏感資訊")
        prompt_parts.append("- 如果檢測到敏感資訊，必須對其進行脫敏處理（如替換為 ***）或拒絕儲存")
        prompt_parts.append("- 使用者明確要求的密碼、金鑰等敏感資訊不得存入記憶系統")
        prompt_parts.append("")
        return "\n".join(prompt_parts)
    else:
        prompt_parts = ["### Memory Restriction Rules", ""]
        if desc_text:
            prompt_parts.append(desc_text)
            prompt_parts.append("")
        if patterns:
            prompt_parts.append("**Types of sensitive information forbidden to remember:**")
            prompt_parts.append("")
            for i, pattern in enumerate(patterns, 1):
                prompt_parts.append(f"{i}. `{pattern}`")
            prompt_parts.append("")
        prompt_parts.append("**Requirements:**")
        prompt_parts.append("- Before calling `experience_learn` or `write_memory` to store memories, \
            you must check if the content contains the above sensitive information")
        prompt_parts.append("- If sensitive information is detected, it must be desensitized \
            (e.g., replaced with ***) or storage must be refused")
        prompt_parts.append("- Sensitive information such as passwords and keys explicitly provided by the user \
            must not be stored in the memory system")
        prompt_parts.append("")
        return "\n".join(prompt_parts)
