"""Agent 配置持久化：``.god/town/agents.json``。

沿用 ``scripts/god.sh`` 的 ``.god/`` 狀態目錄約定，可用 ``GOD_STATE_DIR`` 覆蓋。
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from ..logger import get_logger
from .agents import TownAgentConfig

logger = get_logger()


def state_dir() -> Path:
    override = os.getenv("GOD_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[5] / ".god"


def agents_file() -> Path:
    return state_dir() / "town" / "agents.json"


def new_agent_id() -> str:
    return uuid.uuid4().hex[:12]


def load_agents() -> list[TownAgentConfig]:
    path = agents_file()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read %s; starting with an empty town", path)
        return []
    configs: list[TownAgentConfig] = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            configs.append(TownAgentConfig.model_validate(entry))
        except Exception:  # noqa: BLE001 - 單條壞配置不該擋住整個小鎮
            logger.warning("Skipping invalid town agent entry: %r", entry)
    return configs


def save_agents(configs: list[TownAgentConfig]) -> None:
    path = agents_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [config.model_dump() for config in configs]
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
