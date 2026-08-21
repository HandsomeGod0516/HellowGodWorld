"""PersonAgent 初始化輔助工具（用於測試/腳手架）。

該模組為 `agentsociety2.agent.tests` 提供最小的初始化能力：構造 init_state（workspace seed），
並建立可被 :class:`~agentsociety2.agent.person.PersonAgent` 消費的配置物件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentsociety2.agent.person import PersonAgent


@dataclass
class PersonInitConfig:
    """PersonAgent 初始化配置（workspace seed）。"""

    agent_id: int
    name: str = ""
    profile: dict[str, Any] = field(default_factory=dict)
    force_overwrite: bool = False
    _seed: dict[str, Any] = field(default_factory=dict, repr=False)

    def set_state(self, _: str, rel_path: str, value: Any) -> "PersonInitConfig":
        """寫入一個將被 seed 到 workspace 的檔案。"""
        self._seed[str(rel_path).strip()] = value
        return self

    def to_init_state(self) -> dict[str, Any]:
        return {
            "init_state_force": bool(self.force_overwrite),
            "workspace_seed": dict(self._seed),
        }


def init_needs_state(*, satiety: float = 0.5, energy: float = 0.5) -> dict[str, Any]:
    """生成 needs.json 的最小結構（用於測試）。

    :param satiety: 飽腹度（0~1）。
    :param energy: 精力（0~1）。
    :returns: needs.json 物件。
    """
    current_need = "satiety" if satiety < energy else "energy"
    return {
        "satiety": float(satiety),
        "energy": float(energy),
        "current_need": current_need,
        "thresholds": {"satiety": 0.3, "energy": 0.3},
        "can_interrupt": True,
    }


def init_personality_state(
    *, extraversion: float = 0.5, neuroticism: float = 0.5
) -> dict[str, Any]:
    """生成 personality.json 的最小結構（用於測試）。

    :param extraversion: 外向性（0~1）。
    :param neuroticism: 神經質（0~1）。
    :returns: personality.json 物件。
    """
    return {
        "traits": {
            "extraversion": float(extraversion),
            "neuroticism": float(neuroticism),
        },
        "personality_description": "test personality",
    }


def init_emotion_state(
    *, primary: str = "Hope", valence: float = 0.0, arousal: float = 0.5
) -> dict[str, Any]:
    """生成 emotion.json 的最小結構（用於測試）。

    :param primary: 主導情緒標籤。
    :param valence: 效價（-1~1）。
    :param arousal: 喚醒度（0~1）。
    :returns: emotion.json 物件。
    """
    return {
        "primary": str(primary),
        "valence": float(valence),
        "arousal": float(arousal),
        "mood": {
            "valence": float(valence),
            "arousal": float(arousal),
            "stability": 0.7,
        },
        "intensities": {
            "joy": 3,
            "sadness": 3,
            "fear": 3,
            "disgust": 3,
            "anger": 3,
            "surprise": 3,
        },
    }


def discover_skill_schemas() -> dict[str, list[str]]:
    """返回測試用的“技能輸出檔案約定”。

    注：真實系統的技能輸出由 SKILL.md 定義並由 skill 指令碼生成。測試僅需要一個穩定集合
    來驗證 workspace seed/目錄建立是否正常。
    """
    return {
        "needs": ["needs.json"],
        "personality": ["personality.json"],
        "cognition": ["emotion.json"],
    }


def create_person_agent(config: PersonInitConfig) -> PersonAgent:
    """基於配置建立 PersonAgent（不初始化 env）。"""
    agent = PersonAgent(
        id=int(config.agent_id),
        profile=config.profile,
        name=config.name or None,
        init_state=config.to_init_state(),
    )
    return agent
