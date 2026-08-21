"""常駐的即時世界：固定 tick 推進移動，WebSocket 廣播快照。

與 AgentSociety 的 experiment / tick / replay 生命週期無關。
世界一直在跑，AI 小人各自決策，人類玩家用 WASD 直接走。
"""

from __future__ import annotations

import asyncio
import math
import random
import time
import uuid
from collections import deque
from typing import Any

from ..logger import get_logger
from .agents import (
    MAX_HP,
    SPEECH_VISIBLE_SECONDS,
    SPRITES,
    Facing,
    TownActor,
    TownAgentConfig,
    run_agent_loop,
)
from .map_layout import (
    FOOD_SPOT,
    GRID_HEIGHT,
    GRID_WIDTH,
    PLAZA_CENTER,
    ROOMS,
    build_world_map,
    room_anchor,
    room_by_id,
    room_of,
    walkable_tiles,
)
from .pathfind import astar, nearest_walkable
from .store import save_agents

logger = get_logger()

TICK_HZ = 20.0
BROADCAST_HZ = 10.0
TILES_PER_SECOND = 3.6
NEARBY_RADIUS_TILES = 8.0
EVENT_HISTORY = 200
FOOD_EAT_RADIUS_TILES = 1.5
OCCUPANCY_RADIUS_TILES = 0.6  # 两个角色中心距离小于这个就算撞上了，不能互相穿过
HP_DECAY_PER_SECOND = MAX_HP / 480.0  # 一直不吃東西，8 分鐘餓到 0
HP_REGEN_PER_SECOND = MAX_HP / 12.0  # 在食物旁邊，12 秒內吃滿

_FACING_VECTORS: dict[str, tuple[float, float]] = {
    "up": (0.0, -1.0),
    "down": (0.0, 1.0),
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
}


def _facing_for_delta(dx: float, dy: float, fallback: Facing) -> Facing:
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return fallback
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


class WorldEngine:
    """世界的唯一例項。持有所有角色、跑移動迴圈、管 WS 訂閱者。"""

    def __init__(self) -> None:
        self.map = build_world_map()
        # 食物本身是个实体，占掉一格，走不进去；靠它最近的一格空地才是真正能站的位置。
        self.walkable = walkable_tiles() - {FOOD_SPOT}
        self.food_approach = nearest_walkable(FOOD_SPOT, self.walkable)
        self.actors: dict[str, TownActor] = {}
        self.configs: dict[str, TownAgentConfig] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=EVENT_HISTORY)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subscribers: set[Any] = set()
        self._event_tasks: set[asyncio.Task[None]] = set()
        self._loop_task: asyncio.Task[None] | None = None
        self._tick = 0
        self._lock = asyncio.Lock()

    # ---------- 生命週期 ----------

    async def start(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._run(), name="town-world-loop")
            logger.info("Town world loop started")

    async def stop(self) -> None:
        for agent_id in list(self._tasks):
            await self._cancel_agent_task(agent_id)
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        logger.info("Town world loop stopped")

    async def _run(self) -> None:
        """世界迴圈。單次異常不會讓世界停下，睡一秒繼續跑。"""
        tick_dt = 1.0 / TICK_HZ
        broadcast_every = max(1, round(TICK_HZ / BROADCAST_HZ))
        while True:
            try:
                self._tick += 1
                starved: list[str] = []
                for actor in list(self.actors.values()):
                    self._advance(actor, tick_dt)
                    if self._update_hp(actor, tick_dt):
                        starved.append(actor.id)
                for agent_id in starved:
                    await self._starve(agent_id)
                if self._tick % broadcast_every == 0:
                    await self.broadcast({"type": "snapshot", **self.snapshot()})
                await asyncio.sleep(tick_dt)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 世界迴圈必須活著
                logger.exception("Town world tick failed; continuing in 1s")
                await asyncio.sleep(1.0)

    # ---------- 移動 ----------

    def food_status(self, actor: TownActor) -> dict[str, Any]:
        """食物的位置不是天生就知道的：只有走到附近（跟看到「附近的人」一个视野）才会发现。"""
        distance = math.hypot(actor.x - FOOD_SPOT[0], actor.y - FOOD_SPOT[1])
        return {
            "distance": round(distance, 1),
            "at_food": distance <= FOOD_EAT_RADIUS_TILES,
            "discovered": distance <= NEARBY_RADIUS_TILES,
        }

    def _update_hp(self, actor: TownActor, dt: float) -> bool:
        """血量會慢慢掉，站在廣場右上角的食物旁邊才會回血。
        返回 True 表示這個 AI 剛好這一 tick 餓到 0，需要被移除。
        """
        if self.food_status(actor)["at_food"]:
            actor.hp = min(MAX_HP, actor.hp + HP_REGEN_PER_SECOND * dt)
            return False
        was_alive = actor.hp > 0
        actor.hp = max(0.0, actor.hp - HP_DECAY_PER_SECOND * dt)
        return actor.kind == "ai" and was_alive and actor.hp <= 0

    async def _starve(self, agent_id: str) -> None:
        """餓到 0 血：直接移除這個 AI 居民，跟手動刪除一樣會持久化。"""
        actor = self.actors.get(agent_id)
        if actor is not None:
            self._push_event("starve", actor)
        if await self.remove_agent(agent_id):
            save_agents(list(self.configs.values()))

    def _advance(self, actor: TownActor, dt: float) -> None:
        distance = TILES_PER_SECOND * dt
        if actor.input_dir:
            self._advance_free(actor, actor.input_dir, distance)
            return
        self._advance_path(actor, distance)

    def _tile_occupied(self, mover: TownActor, x: float, y: float) -> bool:
        """粗略的人物碰撞：中心距离太近就算撞到，谁也别想穿过谁。"""
        for other in self.actors.values():
            if other.id == mover.id:
                continue
            if math.hypot(other.x - x, other.y - y) < OCCUPANCY_RADIUS_TILES:
                return True
        return False

    def _advance_free(self, actor: TownActor, direction: Facing, distance: float) -> None:
        dx, dy = _FACING_VECTORS[direction]
        next_x = min(max(actor.x + dx * distance, 0.0), GRID_WIDTH - 1.0)
        next_y = min(max(actor.y + dy * distance, 0.0), GRID_HEIGHT - 1.0)
        can_move = (round(next_x), round(next_y)) in self.walkable and not self._tile_occupied(
            actor, next_x, next_y
        )
        if can_move:
            actor.x, actor.y = next_x, next_y
            actor.moving = True
        else:
            actor.moving = False
        actor.facing = direction

    def _advance_path(self, actor: TownActor, distance: float) -> None:
        if actor.path_index >= len(actor.path) - 1:
            if actor.moving:
                actor.moving = False
                if actor.target_room:
                    if actor.target_room == "food":
                        actor.status = "到達食物旁邊"
                    else:
                        room = room_by_id(actor.target_room)
                        actor.status = f"到達{room['name']}" if room else "到達目的地"
                    self._push_event("arrive", actor, room_id=actor.target_room)
                actor.path = []
                actor.path_index = 0
                actor.target_room = None
            return

        target_x, target_y = actor.path[actor.path_index + 1]
        dx = target_x - actor.x
        dy = target_y - actor.y
        remaining = math.hypot(dx, dy)
        actor.facing = _facing_for_delta(dx, dy, actor.facing)
        actor.moving = True
        if remaining <= distance or remaining < 1e-6:
            if self._tile_occupied(actor, float(target_x), float(target_y)):
                return  # 下一格站着别人，先等一下，下一 tick 再看看让开了没
            actor.x, actor.y = float(target_x), float(target_y)
            actor.path_index += 1
            return
        step = min(distance, remaining)
        new_x = actor.x + dx / remaining * step
        new_y = actor.y + dy / remaining * step
        if self._tile_occupied(actor, new_x, new_y):
            return
        actor.x, actor.y = new_x, new_y

    # ---------- 世界查詢 ----------

    def room_of_actor(self, actor: TownActor) -> str | None:
        return room_of(actor.tile())

    def nearby(self, actor_id: str, radius: float = NEARBY_RADIUS_TILES) -> list[TownActor]:
        me = self.actors.get(actor_id)
        if me is None:
            return []
        return [
            other
            for other in self.actors.values()
            if other.id != actor_id
            and math.hypot(other.x - me.x, other.y - me.y) <= radius
        ]

    # ---------- 動作 ----------

    def goto(self, actor_id: str, room_id: str) -> bool:
        actor = self.actors.get(actor_id)
        if actor is None:
            return False
        if room_id == "food":
            # 没走近过、没被发现的食物不能直接导航过去——那等于把坐标告诉它。
            if not self.food_status(actor)["discovered"]:
                return False
            target = self.food_approach
        else:
            target = room_anchor(room_id)
        actor.input_dir = None  # goto 跟自由走动互斥，谁后决策谁生效
        path = astar(actor.tile(), target, self.walkable)
        if not path:
            return False
        actor.path = path
        actor.path_index = 0
        actor.target_room = room_id
        actor.moving = True
        return True

    def wander(self, actor_id: str) -> None:
        """決策失敗時的兜底：隨機挑一個房間走過去。"""
        self.goto(actor_id, random.choice([room["id"] for room in ROOMS]))

    def say(self, actor_id: str, text: str) -> None:
        actor = self.actors.get(actor_id)
        if actor is None or not text.strip():
            return
        clean = text.strip()[:200]
        actor.say_text = clean
        actor.say_until = time.monotonic() + SPEECH_VISIBLE_SECONDS
        for listener in self.nearby(actor_id):
            listener.heard.append(f"{actor.name}：{clean}")
        self._push_event("say", actor, text=clean)

    def announce(self, text: str) -> None:
        """向全鎮廣播一句話：不受距離限制，所有 AI 都會記到 heard 裡，下一輪決策就能看到。"""
        clean = text.strip()[:200]
        if not clean:
            return
        for actor in self.actors.values():
            if actor.kind == "ai":
                actor.heard.append(f"公告：{clean}")
        self._push_system_event("announce", text=clean)

    def dispatch_all(self, room_id: str) -> int:
        """讓所有 AI 立刻出發去某個地點，人類玩家不受影響。"""
        count = 0
        for actor in list(self.actors.values()):
            if actor.kind == "ai" and self.goto(actor.id, room_id):
                count += 1
        return count

    def set_input(self, actor_id: str, direction: Facing | None) -> None:
        actor = self.actors.get(actor_id)
        if actor is None:
            return
        actor.input_dir = direction
        if direction is None:
            actor.moving = False
            return
        # 手動走動優先：丟掉之前 goto 留下的路徑，松鍵後才不會被拉回去。
        actor.path = []
        actor.path_index = 0
        actor.target_room = None

    # ---------- 角色增刪 ----------

    async def add_agent(self, config: TownAgentConfig) -> TownActor:
        async with self._lock:
            spawn = nearest_walkable(room_anchor(config.room_id), self.walkable)
            actor = TownActor(
                id=config.id,
                name=config.name,
                sprite=config.sprite if config.sprite in SPRITES else SPRITES[0],
                kind="ai",
                x=float(spawn[0]),
                y=float(spawn[1]),
                status="剛到達",
            )
            self.actors[config.id] = actor
            self.configs[config.id] = config
            self._tasks[config.id] = asyncio.create_task(
                run_agent_loop(self, config.id), name=f"town-agent-{config.id}"
            )
        self._push_event("join", actor)
        await self.broadcast_agent_list()
        return actor

    async def remove_agent(self, agent_id: str) -> bool:
        async with self._lock:
            actor = self.actors.pop(agent_id, None)
            self.configs.pop(agent_id, None)
            await self._cancel_agent_task(agent_id)
        if actor is None:
            return False
        self._push_event("leave", actor)
        await self.broadcast_agent_list()
        return True

    async def update_agent(self, config: TownAgentConfig) -> None:
        """改配置後重啟該小人的決策迴圈，位置與朝向保留。"""
        async with self._lock:
            self.configs[config.id] = config
            actor = self.actors.get(config.id)
            if actor is not None:
                actor.name = config.name
                actor.sprite = config.sprite if config.sprite in SPRITES else actor.sprite
                actor.last_error = None
            await self._cancel_agent_task(config.id)
            self._tasks[config.id] = asyncio.create_task(
                run_agent_loop(self, config.id), name=f"town-agent-{config.id}"
            )
        await self.broadcast_agent_list()

    async def _cancel_agent_task(self, agent_id: str) -> None:
        task = self._tasks.pop(agent_id, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - 收尾時的失敗不該阻止移除
            logger.debug("Town agent %s raised while shutting down", agent_id, exc_info=True)

    def add_human(self, name: str) -> TownActor:
        actor_id = f"human-{uuid.uuid4().hex[:8]}"
        spawn = nearest_walkable(PLAZA_CENTER, self.walkable)
        actor = TownActor(
            id=actor_id,
            name=name.strip()[:40] or "訪客",
            sprite=random.choice(SPRITES),
            kind="human",
            x=float(spawn[0]),
            y=float(spawn[1]),
            status="剛進入小鎮",
        )
        self.actors[actor_id] = actor
        self._push_event("join", actor)
        return actor

    def remove_human(self, actor_id: str) -> None:
        actor = self.actors.pop(actor_id, None)
        if actor is not None:
            self._push_event("leave", actor)

    # ---------- 快照與廣播 ----------

    def _actor_payload(self, actor: TownActor) -> dict[str, Any]:
        room_id = self.room_of_actor(actor)
        room = room_by_id(room_id)
        return {
            "id": actor.id,
            "name": actor.name,
            "sprite": actor.sprite,
            "kind": actor.kind,
            "x": round(actor.x, 3),
            "y": round(actor.y, 3),
            "facing": actor.facing,
            "moving": actor.moving,
            "room_id": room_id,
            "room_name": room["name"] if room else None,
            "target_room": actor.target_room,
            "status": actor.status,
            "say": actor.speech(),
            "last_error": actor.last_error,
            "hp": round(actor.hp, 1),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "tick": self._tick,
            "actors": [self._actor_payload(actor) for actor in self.actors.values()],
        }

    def agent_list(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for agent_id, config in self.configs.items():
            actor = self.actors.get(agent_id)
            payload = config.model_dump()
            payload["llm"] = {**payload["llm"], "api_key": "***" if config.llm.api_key else None}
            payload["runtime"] = self._actor_payload(actor) if actor else None
            result.append(payload)
        return result

    def _push_event(self, kind: str, actor: TownActor, **extra: Any) -> None:
        self._push_raw_event(kind, actor_id=actor.id, actor_name=actor.name, **extra)

    def _push_system_event(self, kind: str, **extra: Any) -> None:
        self._push_raw_event(kind, actor_id="system", actor_name="GOD", **extra)

    def _push_raw_event(self, kind: str, *, actor_id: str, actor_name: str, **extra: Any) -> None:
        event = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind,
            "actor_id": actor_id,
            "actor_name": actor_name,
            "ts": time.time(),
            **extra,
        }
        self.events.append(event)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 同步上下文（例如測試裡直接操作世界）只記錄事件，不廣播。
            return
        task = asyncio.create_task(self.broadcast({"type": "event", "event": event}))
        self._event_tasks.add(task)
        task.add_done_callback(self._event_tasks.discard)

    async def add_subscriber(self, websocket: Any) -> None:
        self._subscribers.add(websocket)
        await websocket.send_json({"type": "map", "map": self.map})
        await websocket.send_json({"type": "snapshot", **self.snapshot()})
        await websocket.send_json({"type": "agent_list", "agents": self.agent_list()})
        await websocket.send_json({"type": "events", "events": list(self.events)})

    def remove_subscriber(self, websocket: Any) -> None:
        self._subscribers.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        for websocket in list(self._subscribers):
            try:
                await websocket.send_json(payload)
            except Exception:  # noqa: BLE001 - 斷開的訂閱者直接丟掉
                self._subscribers.discard(websocket)

    async def broadcast_agent_list(self) -> None:
        await self.broadcast({"type": "agent_list", "agents": self.agent_list()})


_world: WorldEngine | None = None


def get_world() -> WorldEngine:
    global _world
    if _world is None:
        _world = WorldEngine()
    return _world
