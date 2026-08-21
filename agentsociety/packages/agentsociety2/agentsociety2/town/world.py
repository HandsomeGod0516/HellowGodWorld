"""常驻的即时世界：固定 tick 推进移动，WebSocket 广播快照。

与 AgentSociety 的 experiment / tick / replay 生命周期无关。
世界一直在跑，AI 小人各自决策，人类玩家用 WASD 直接走。
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
    SPEECH_VISIBLE_SECONDS,
    SPRITES,
    Facing,
    TownActor,
    TownAgentConfig,
    run_agent_loop,
)
from .map_layout import (
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

logger = get_logger()

TICK_HZ = 20.0
BROADCAST_HZ = 10.0
TILES_PER_SECOND = 3.6
NEARBY_RADIUS_TILES = 8.0
EVENT_HISTORY = 200

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
    """世界的唯一实例。持有所有角色、跑移动循环、管 WS 订阅者。"""

    def __init__(self) -> None:
        self.map = build_world_map()
        self.walkable = walkable_tiles()
        self.actors: dict[str, TownActor] = {}
        self.configs: dict[str, TownAgentConfig] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=EVENT_HISTORY)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subscribers: set[Any] = set()
        self._event_tasks: set[asyncio.Task[None]] = set()
        self._loop_task: asyncio.Task[None] | None = None
        self._tick = 0
        self._lock = asyncio.Lock()

    # ---------- 生命周期 ----------

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
        """世界循环。单次异常不会让世界停下，睡一秒继续跑。"""
        tick_dt = 1.0 / TICK_HZ
        broadcast_every = max(1, round(TICK_HZ / BROADCAST_HZ))
        while True:
            try:
                self._tick += 1
                for actor in list(self.actors.values()):
                    self._advance(actor, tick_dt)
                if self._tick % broadcast_every == 0:
                    await self.broadcast({"type": "snapshot", **self.snapshot()})
                await asyncio.sleep(tick_dt)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 世界循环必须活着
                logger.exception("Town world tick failed; continuing in 1s")
                await asyncio.sleep(1.0)

    # ---------- 移动 ----------

    def _advance(self, actor: TownActor, dt: float) -> None:
        distance = TILES_PER_SECOND * dt
        if actor.kind == "human" and actor.input_dir:
            self._advance_free(actor, actor.input_dir, distance)
            return
        self._advance_path(actor, distance)

    def _advance_free(self, actor: TownActor, direction: Facing, distance: float) -> None:
        dx, dy = _FACING_VECTORS[direction]
        next_x = min(max(actor.x + dx * distance, 0.0), GRID_WIDTH - 1.0)
        next_y = min(max(actor.y + dy * distance, 0.0), GRID_HEIGHT - 1.0)
        if (round(next_x), round(next_y)) in self.walkable:
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
                    room = room_by_id(actor.target_room)
                    actor.status = f"到达{room['name']}" if room else "到达目的地"
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
            actor.x, actor.y = float(target_x), float(target_y)
            actor.path_index += 1
            return
        actor.x += dx / remaining * distance
        actor.y += dy / remaining * distance

    # ---------- 世界查询 ----------

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

    # ---------- 动作 ----------

    def goto(self, actor_id: str, room_id: str) -> bool:
        actor = self.actors.get(actor_id)
        if actor is None:
            return False
        path = astar(actor.tile(), room_anchor(room_id), self.walkable)
        if not path:
            return False
        actor.path = path
        actor.path_index = 0
        actor.target_room = room_id
        actor.moving = True
        return True

    def wander(self, actor_id: str) -> None:
        """决策失败时的兜底：随机挑一个房间走过去。"""
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

    def set_input(self, actor_id: str, direction: Facing | None) -> None:
        actor = self.actors.get(actor_id)
        if actor is None:
            return
        actor.input_dir = direction
        if direction is None:
            actor.moving = False
            return
        # 手动走动优先：丢掉之前 goto 留下的路径，松键后才不会被拉回去。
        actor.path = []
        actor.path_index = 0
        actor.target_room = None

    # ---------- 角色增删 ----------

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
                status="刚到达",
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
        """改配置后重启该小人的决策循环，位置与朝向保留。"""
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
        except Exception:  # noqa: BLE001 - 收尾时的失败不该阻止移除
            logger.debug("Town agent %s raised while shutting down", agent_id, exc_info=True)

    def add_human(self, name: str) -> TownActor:
        actor_id = f"human-{uuid.uuid4().hex[:8]}"
        spawn = nearest_walkable(PLAZA_CENTER, self.walkable)
        actor = TownActor(
            id=actor_id,
            name=name.strip()[:40] or "访客",
            sprite=random.choice(SPRITES),
            kind="human",
            x=float(spawn[0]),
            y=float(spawn[1]),
            status="刚进入小镇",
        )
        self.actors[actor_id] = actor
        self._push_event("join", actor)
        return actor

    def remove_human(self, actor_id: str) -> None:
        actor = self.actors.pop(actor_id, None)
        if actor is not None:
            self._push_event("leave", actor)

    # ---------- 快照与广播 ----------

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
        event = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind,
            "actor_id": actor.id,
            "actor_name": actor.name,
            "ts": time.time(),
            **extra,
        }
        self.events.append(event)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 同步上下文（例如测试里直接操作世界）只记录事件，不广播。
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
            except Exception:  # noqa: BLE001 - 断开的订阅者直接丢掉
                self._subscribers.discard(websocket)

    async def broadcast_agent_list(self) -> None:
        await self.broadcast({"type": "agent_list", "agents": self.agent_list()})


_world: WorldEngine | None = None


def get_world() -> WorldEngine:
    global _world
    if _world is None:
        _world = WorldEngine()
    return _world
