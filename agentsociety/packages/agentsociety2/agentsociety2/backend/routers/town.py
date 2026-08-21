"""即时小镇 API。

世界常驻运行：AI 小人各自跑自己的决策循环、各自指向自己的模型端点，
人类玩家通过 WebSocket 用 WASD 直接走动。没有 experiment / replay 概念。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from agentsociety2.logger import get_logger
from agentsociety2.town.agents import SPRITES, TownAgentConfig
from agentsociety2.town.llm_client import EndpointTestResult, LLMEndpoint, test_endpoint
from agentsociety2.town.map_layout import all_rooms
from agentsociety2.town.store import load_agents, new_agent_id, save_agents
from agentsociety2.town.world import WorldEngine, get_world

logger = get_logger()

router = APIRouter(prefix="/api/v1/town", tags=["town"])

MASKED_API_KEY = "***"


class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    sprite: str = SPRITES[0]
    persona: str = Field("小镇的普通居民。", max_length=2000)
    room_id: str = "plaza"
    llm: LLMEndpoint
    decision_interval_s: float = Field(8.0, ge=2.0, le=600.0)
    enabled: bool = True
    skip_connection_test: bool = False


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=40)
    sprite: Optional[str] = None
    persona: Optional[str] = Field(None, max_length=2000)
    llm: Optional[LLMEndpoint] = None
    decision_interval_s: Optional[float] = Field(None, ge=2.0, le=600.0)
    enabled: Optional[bool] = None


class TownDefaults(BaseModel):
    """新增小人表单的预填值，来自 .env 的 GOD_LLM_*。"""

    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = ""
    has_api_key: bool = False


class SayRequest(BaseModel):
    actor_id: str
    text: str = Field(..., min_length=1, max_length=200)


class GotoRequest(BaseModel):
    room_id: str


def _persist(world: WorldEngine) -> None:
    save_agents(list(world.configs.values()))


async def bootstrap_world() -> WorldEngine:
    """启动世界循环，并把 agents.json 里的小人放回小镇。"""
    world = get_world()
    await world.start()
    if not world.configs:
        for config in load_agents():
            try:
                await world.add_agent(config)
            except Exception:  # noqa: BLE001 - 单个小人恢复失败不该挡住启动
                logger.exception("Failed to restore town agent %s", config.id)
    return world


async def shutdown_world() -> None:
    await get_world().stop()


@router.get("/map")
async def get_map() -> dict[str, Any]:
    return get_world().map


@router.get("/rooms")
async def get_rooms() -> list[dict[str, Any]]:
    return [
        {"id": room["id"], "name": room["name"], "name_en": room["name_en"]}
        for room in all_rooms()
    ]


@router.get("/defaults", response_model=TownDefaults)
async def get_defaults() -> TownDefaults:
    provider = (os.getenv("GOD_LLM_PROVIDER") or "ollama").strip().lower()
    if provider not in {"ollama", "openai"}:
        provider = "ollama"
    default_base = "http://localhost:11434" if provider == "ollama" else "https://api.openai.com/v1"
    return TownDefaults(
        provider=provider,
        base_url=(os.getenv("GOD_LLM_API_BASE") or default_base).strip(),
        model=(os.getenv("GOD_LLM_MODEL") or "").strip(),
        has_api_key=bool((os.getenv("GOD_LLM_API_KEY") or "").strip()),
    )


@router.get("/sprites")
async def get_sprites() -> list[str]:
    return SPRITES


@router.get("/state")
async def get_state() -> dict[str, Any]:
    world = get_world()
    return {**world.snapshot(), "events": list(world.events)}


@router.get("/agents")
async def list_town_agents() -> list[dict[str, Any]]:
    return get_world().agent_list()


@router.post("/agents/test-connection", response_model=EndpointTestResult)
async def test_agent_connection(endpoint: LLMEndpoint) -> EndpointTestResult:
    return await test_endpoint(endpoint)


@router.post("/agents", status_code=201)
async def create_town_agent(request: AgentCreateRequest) -> dict[str, Any]:
    if not request.skip_connection_test:
        result = await test_endpoint(request.llm)
        if not result.ok:
            raise HTTPException(
                status_code=400,
                detail=f"模型端点连接失败：{result.error}",
            )

    world = get_world()
    config = TownAgentConfig(
        id=new_agent_id(),
        name=request.name,
        sprite=request.sprite if request.sprite in SPRITES else SPRITES[0],
        persona=request.persona,
        room_id=request.room_id,
        llm=request.llm,
        decision_interval_s=request.decision_interval_s,
        enabled=request.enabled,
    )
    await world.add_agent(config)
    _persist(world)
    return {"id": config.id, "agents": world.agent_list()}


@router.patch("/agents/{agent_id}")
async def update_town_agent(agent_id: str, request: AgentUpdateRequest) -> dict[str, Any]:
    world = get_world()
    current = world.configs.get(agent_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"Unknown town agent: {agent_id}")

    updates = request.model_dump(exclude_unset=True, exclude_none=True)
    llm_update = updates.pop("llm", None)
    if llm_update is not None:
        # 前端拿到的是掩码后的 key，未真正改动时保留原值。
        if not llm_update.get("api_key") or llm_update.get("api_key") == MASKED_API_KEY:
            llm_update["api_key"] = current.llm.api_key
        updates["llm"] = LLMEndpoint.model_validate(llm_update)

    config = current.model_copy(update=updates)
    await world.update_agent(config)
    _persist(world)
    return {"id": config.id, "agents": world.agent_list()}


@router.delete("/agents/{agent_id}")
async def delete_town_agent(agent_id: str) -> dict[str, Any]:
    world = get_world()
    removed = await world.remove_agent(agent_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Unknown town agent: {agent_id}")
    _persist(world)
    return {"removed": agent_id, "agents": world.agent_list()}


@router.post("/agents/{agent_id}/goto")
async def send_agent_to_room(agent_id: str, request: GotoRequest) -> dict[str, Any]:
    world = get_world()
    if agent_id not in world.actors:
        raise HTTPException(status_code=404, detail=f"Unknown actor: {agent_id}")
    if not world.goto(agent_id, request.room_id):
        raise HTTPException(status_code=400, detail=f"找不到通往 {request.room_id} 的路")
    return {"ok": True}


@router.post("/say")
async def say(request: SayRequest) -> dict[str, Any]:
    world = get_world()
    if request.actor_id not in world.actors:
        raise HTTPException(status_code=404, detail=f"Unknown actor: {request.actor_id}")
    world.say(request.actor_id, request.text)
    return {"ok": True}


_DIRECTIONS = {"up", "down", "left", "right"}


@router.websocket("/ws")
async def town_ws(websocket: WebSocket) -> None:
    """推送地图/快照/事件；接收人类玩家的加入、方向输入与发言。"""
    await websocket.accept()
    world = await bootstrap_world()
    await world.add_subscriber(websocket)
    human_id: str | None = None
    try:
        while True:
            try:
                message = await websocket.receive_json()
            except (ValueError, TypeError):
                # 客户端发了非 JSON，忽略这一条而不是掉线。
                continue
            if not isinstance(message, dict):
                continue
            kind = str(message.get("type") or "")

            if kind == "join":
                if human_id is not None:
                    world.remove_human(human_id)
                actor = world.add_human(str(message.get("name") or "访客"))
                human_id = actor.id
                await websocket.send_json({"type": "joined", "actor_id": actor.id})

            elif kind == "input" and human_id:
                direction = message.get("dir")
                world.set_input(
                    human_id,
                    direction if direction in _DIRECTIONS else None,  # type: ignore[arg-type]
                )

            elif kind == "say" and human_id:
                world.say(human_id, str(message.get("text") or ""))

            elif kind == "leave" and human_id:
                world.remove_human(human_id)
                human_id = None
    except WebSocketDisconnect:
        pass
    except (asyncio.CancelledError, RuntimeError):
        pass
    finally:
        world.remove_subscriber(websocket)
        if human_id:
            world.remove_human(human_id)
