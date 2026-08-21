"""小镇里的角色：可序列化的 Agent 配置，以及每个 AI 自己的决策循环。

每个 AI 小人都有独立的 :class:`asyncio.Task`，按自己的节奏调用自己的模型端点，
自行决定去哪、说什么。世界循环只负责把决策结果推进成平滑移动，
不需要外部触发 step。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from .llm_client import LLMEndpoint, chat, extract_json_object
from .map_layout import all_rooms, room_by_id

if TYPE_CHECKING:  # pragma: no cover - 仅为类型标注，避免与 world 循环导入
    from .world import WorldEngine

Facing = Literal["up", "down", "left", "right"]
ActorKind = Literal["ai", "human"]

SPRITES = [
    "Isabella_Rodriguez",
    "Maria_Lopez",
    "Klaus_Mueller",
    "Sam_Moore",
    "Yuriko_Yamamoto",
    "Ryan_Park",
    "Abigail_Chen",
    "Eddy_Lin",
    "Mei_Lin",
    "Rajiv_Patel",
    "Ayesha_Khan",
    "Giorgio_Rossi",
    "Tamara_Taylor",
    "Wolfgang_Schulz",
    "John_Lin",
    "Jennifer_Moore",
    "Carlos_Gomez",
    "Francisco_Lopez",
    "Adam_Smith",
    "Carmen_Ortiz",
    "Jane_Moreno",
    "Tom_Moreno",
    "Latoya_Williams",
    "Arthur_Burton",
    "Hailey_Johnson",
]

SPEECH_VISIBLE_SECONDS = 6.0
HEARD_MEMORY = 8
DECISION_TIMEOUT_SECONDS = 90.0
STARTUP_JITTER_SECONDS = (0.5, 3.0)


class TownAgentConfig(BaseModel):
    """一个 AI 小人的持久化配置。"""

    id: str
    name: str = Field(..., min_length=1, max_length=40)
    sprite: str = SPRITES[0]
    persona: str = Field("小镇的普通居民。", max_length=2000)
    room_id: str = "plaza"
    llm: LLMEndpoint
    decision_interval_s: float = Field(8.0, ge=2.0, le=600.0)
    enabled: bool = True


@dataclass
class TownActor:
    """世界里的一个实体，AI 与人类玩家共用。座标用浮点 tile，支持格内插值。"""

    id: str
    name: str
    sprite: str
    kind: ActorKind
    x: float
    y: float
    facing: Facing = "down"
    moving: bool = False
    path: list[tuple[int, int]] = field(default_factory=list)
    path_index: int = 0
    target_room: str | None = None
    status: str = "刚到达"
    last_error: str | None = None
    say_text: str | None = None
    say_until: float = 0.0
    input_dir: Facing | None = None
    heard: deque[str] = field(default_factory=lambda: deque(maxlen=HEARD_MEMORY))

    def tile(self) -> tuple[int, int]:
        return (round(self.x), round(self.y))

    def speech(self) -> str | None:
        if self.say_text and time.monotonic() < self.say_until:
            return self.say_text
        return None


def _room_catalog() -> str:
    return "\n".join(
        f"- {room['id']}：{room['name']}" for room in all_rooms()
    )


def build_system_prompt(config: TownAgentConfig) -> str:
    return (
        f"你是像素小镇里的居民「{config.name}」。\n"
        f"你的人物设定：{config.persona}\n\n"
        "小镇由一个中央广场和六个房间组成，可去的地点：\n"
        f"{_room_catalog()}\n\n"
        "你通过输出 JSON 来行动，每次只输出一个 JSON 对象，不要输出任何其它文字：\n"
        '{"action": "goto|say|idle", "room": "地点id", "text": "要说的话", "reason": "一句话动机"}\n\n'
        "规则：\n"
        "- action=goto 时必须给 room，只能用上面列出的地点 id。\n"
        "- action=say 时必须给 text，只有在你附近的人才听得到，请控制在 40 字内。\n"
        "- action=idle 表示留在原地观察。\n"
        "- 按人物设定自然地生活：去有意思的地方、和附近的人搭话。"
    )


def build_observation(world: "WorldEngine", actor: TownActor) -> dict[str, Any]:
    room_id = world.room_of_actor(actor)
    room = room_by_id(room_id)
    return {
        "你的位置": {
            "地点": room["name"] if room else "走廊",
            "地点id": room_id or "corridor",
            "坐标": [round(actor.x, 1), round(actor.y, 1)],
        },
        "正在做": actor.status,
        "附近的人": [
            {
                "名字": other.name,
                "身份": "玩家" if other.kind == "human" else "居民",
                "正在做": other.status,
            }
            for other in world.nearby(actor.id)
        ],
        "最近听到": list(actor.heard),
        "可去的地点": [room["id"] for room in all_rooms()],
    }


def apply_decision(world: "WorldEngine", actor: TownActor, decision: dict[str, Any]) -> None:
    action = str(decision.get("action") or "idle").strip().lower()
    reason = str(decision.get("reason") or "").strip()

    if action == "goto":
        room_id = str(decision.get("room") or "").strip()
        room = room_by_id(room_id)
        if room is None:
            actor.last_error = f"模型给了未知地点：{room_id!r}"
            world.wander(actor.id)
            return
        if world.goto(actor.id, room_id):
            actor.status = f"前往{room['name']}" + (f"（{reason}）" if reason else "")
            actor.last_error = None
        else:
            actor.last_error = f"找不到通往 {room_id} 的路"
        return

    if action == "say":
        text = str(decision.get("text") or "").strip()
        if not text:
            actor.status = "沉默"
            return
        world.say(actor.id, text)
        actor.status = "说话中"
        actor.last_error = None
        return

    actor.status = reason or "在原地观察"
    actor.last_error = None


async def decide_once(world: "WorldEngine", agent_id: str) -> None:
    """跑一轮决策：观察 -> 调自己的模型 -> 套用动作。异常由调用方处理。"""
    actor = world.actors.get(agent_id)
    config = world.configs.get(agent_id)
    if actor is None or config is None:
        return

    observation = build_observation(world, actor)
    messages = [
        {"role": "system", "content": build_system_prompt(config)},
        {
            "role": "user",
            "content": (
                "这是你现在看到的情况（JSON）：\n"
                f"{observation}\n\n"
                "请输出你的下一步行动 JSON。"
            ),
        },
    ]
    reply = await asyncio.wait_for(
        chat(config.llm, messages, timeout=DECISION_TIMEOUT_SECONDS),
        timeout=DECISION_TIMEOUT_SECONDS + 5,
    )
    decision = extract_json_object(reply)
    if decision is None:
        raise ValueError(f"模型回复里没有可解析的 JSON：{reply[:200]!r}")
    apply_decision(world, actor, decision)


async def run_agent_loop(world: "WorldEngine", agent_id: str) -> None:
    """一个 AI 小人的自主循环。任何单次失败都不阻塞世界，退化成随机走动。"""
    await asyncio.sleep(random.uniform(*STARTUP_JITTER_SECONDS))
    while True:
        config = world.configs.get(agent_id)
        actor = world.actors.get(agent_id)
        if config is None or actor is None:
            return
        if not config.enabled:
            actor.status = "已暂停"
            await asyncio.sleep(1.0)
            continue
        try:
            await decide_once(world, agent_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - 单个小人的失败不该拖垮世界
            actor.last_error = f"{type(error).__name__}: {error}"
            actor.status = "决策失败，随便走走"
            world.wander(agent_id)
        await asyncio.sleep(config.decision_interval_s)
