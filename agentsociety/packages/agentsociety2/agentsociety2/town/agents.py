"""小鎮裡的角色：可序列化的 Agent 配置，以及每個 AI 自己的決策迴圈。

每個 AI 小人都有獨立的 :class:`asyncio.Task`，按自己的節奏呼叫自己的模型端點，
自行決定去哪、說什麼。世界迴圈只負責把決策結果推進成平滑移動，
不需要外部觸發 step。
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

if TYPE_CHECKING:  # pragma: no cover - 僅為型別標註，避免與 world 迴圈匯入
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
MAX_HP = 100.0
DEFAULT_BEHAVIOR_HINT = "按人物設定自然地生活：去有意思的地方、和附近的人搭話；要不要吃東西、什麼時候去，自己判斷，血量沒了會死"


class TownAgentConfig(BaseModel):
    """一個 AI 小人的持久化配置。"""

    id: str
    name: str = Field(..., min_length=1, max_length=40)
    sprite: str = SPRITES[0]
    persona: str = Field("小鎮的普通居民。", max_length=2000)
    room_id: str = "plaza"
    llm: LLMEndpoint
    decision_interval_s: float = Field(8.0, ge=2.0, le=600.0)
    enabled: bool = True
    behavior_hint: str = Field(DEFAULT_BEHAVIOR_HINT, max_length=500)


@dataclass
class TownActor:
    """世界裡的一個實體，AI 與人類玩家共用。座標用浮點 tile，支援格內插值。"""

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
    status: str = "剛到達"
    last_error: str | None = None
    hp: float = MAX_HP
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
        f"你是畫素小鎮裡的居民「{config.name}」。\n"
        f"你的人物設定：{config.persona}\n\n"
        "小鎮由一箇中央廣場和六個房間組成，可去的地點：\n"
        f"{_room_catalog()}\n\n"
        "小鎮裡藏著食物（確切位置沒有標在地圖上），血量（hp）會隨時間慢慢下降，"
        "站到食物旁邊會回血；血量歸零會直接從小鎮消失，無法恢復。\n"
        "食物在哪裡要自己走近了才會發現：夠近時，下方觀察裡的「食物」欄位會告訴你距離、"
        "夠近了甚至可以直接 goto 填「food」走過去；還沒發現食物的時候，"
        "可以用 action=move 搭配方向自由走動，到處探索找找看。\n\n"
        "你透過輸出 JSON 來行動，每次只輸出一個 JSON 物件，不要輸出任何其它文字：\n"
        '{"action": "goto|move|say|idle", "room": "地點id", "direction": "up|down|left|right", '
        '"text": "要說的話", "reason": "一句話動機"}\n\n'
        "規則：\n"
        "- action=goto 時必須給 room，可以是上面列出的地點 id；「food」只有在你已經發現食物時才有效。\n"
        "- action=move 時必須給 direction（up/down/left/right），會朝那個方向自由走動，"
        "直到你下一次決策為止；用來探索還沒去過的地方。\n"
        "- action=say 時必須給 text，只有在你附近的人才聽得到，請控制在 40 字內。\n"
        "- action=idle 表示留在原地觀察。\n"
        f"- {config.behavior_hint}"
    )


def build_observation(world: "WorldEngine", actor: TownActor) -> dict[str, Any]:
    room_id = world.room_of_actor(actor)
    room = room_by_id(room_id)
    food = world.food_status(actor)
    food_observation: dict[str, Any] = (
        {"距離你": food["distance"], "你在食物旁邊": food["at_food"]}
        if food["discovered"]
        else {"還沒發現食物在哪": True}
    )
    return {
        "你的位置": {
            "地點": room["name"] if room else "走廊",
            "地點id": room_id or "corridor",
            "座標": [round(actor.x, 1), round(actor.y, 1)],
        },
        "正在做": actor.status,
        "血量": round(actor.hp, 1),
        "食物": food_observation,
        "附近的人": [
            {
                "名字": other.name,
                "身份": "玩家" if other.kind == "human" else "居民",
                "正在做": other.status,
            }
            for other in world.nearby(actor.id)
        ],
        "最近聽到": list(actor.heard),
        "可去的地點": [room["id"] for room in all_rooms()],
    }


_DIRECTIONS = ("up", "down", "left", "right")


def apply_decision(world: "WorldEngine", actor: TownActor, decision: dict[str, Any]) -> None:
    action = str(decision.get("action") or "idle").strip().lower()
    reason = str(decision.get("reason") or "").strip()

    if action != "move":
        # 上一輪如果是 move，這一輪不是就先停下來，goto/say/idle 不該帶著自由走動的慣性。
        world.set_input(actor.id, None)

    if action == "goto":
        room_id = str(decision.get("room") or "").strip()
        if room_id == "food" and not world.food_status(actor)["discovered"]:
            actor.last_error = "還沒發現食物在哪，先用 move 到處走走看看"
            return
        destination_name = "食物" if room_id == "food" else (room_by_id(room_id) or {}).get("name")
        if destination_name is None:
            actor.last_error = f"模型給了未知地點：{room_id!r}"
            world.wander(actor.id)
            return
        if world.goto(actor.id, room_id):
            actor.status = f"前往{destination_name}" + (f"（{reason}）" if reason else "")
            actor.last_error = None
        else:
            actor.last_error = f"找不到通往 {room_id} 的路"
        return

    if action == "move":
        direction = str(decision.get("direction") or "").strip().lower()
        if direction not in _DIRECTIONS:
            actor.last_error = f"模型給了不合法的方向：{direction!r}"
            world.wander(actor.id)
            return
        world.set_input(actor.id, direction)  # type: ignore[arg-type]
        actor.status = "四處走走探索" + (f"（{reason}）" if reason else "")
        actor.last_error = None
        return

    if action == "say":
        text = str(decision.get("text") or "").strip()
        if not text:
            actor.status = "沉默"
            return
        world.say(actor.id, text)
        actor.status = "說話中"
        actor.last_error = None
        return

    actor.status = reason or "在原地觀察"
    actor.last_error = None


async def decide_once(world: "WorldEngine", agent_id: str) -> None:
    """跑一輪決策：觀察 -> 調自己的模型 -> 套用動作。異常由呼叫方處理。"""
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
                "這是你現在看到的情況（JSON）：\n"
                f"{observation}\n\n"
                "請輸出你的下一步行動 JSON。"
            ),
        },
    ]
    reply = await asyncio.wait_for(
        chat(config.llm, messages, timeout=DECISION_TIMEOUT_SECONDS),
        timeout=DECISION_TIMEOUT_SECONDS + 5,
    )
    decision = extract_json_object(reply)
    if decision is None:
        raise ValueError(f"模型回覆裡沒有可解析的 JSON：{reply[:200]!r}")
    apply_decision(world, actor, decision)


async def run_agent_loop(world: "WorldEngine", agent_id: str) -> None:
    """一個 AI 小人的自主迴圈。任何單次失敗都不阻塞世界，退化成隨機走動。"""
    await asyncio.sleep(random.uniform(*STARTUP_JITTER_SECONDS))
    while True:
        config = world.configs.get(agent_id)
        actor = world.actors.get(agent_id)
        if config is None or actor is None:
            return
        if not config.enabled:
            actor.status = "已暫停"
            await asyncio.sleep(1.0)
            continue
        try:
            await decide_once(world, agent_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - 單個小人的失敗不該拖垮世界
            actor.last_error = f"{type(error).__name__}: {error}"
            actor.status = "決策失敗，隨便走走"
            world.wander(agent_id)
        await asyncio.sleep(config.decision_interval_s)
