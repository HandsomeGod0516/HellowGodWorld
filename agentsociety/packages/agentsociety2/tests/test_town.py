from __future__ import annotations

import asyncio
from collections import deque

import httpx
import pytest

from agentsociety2.town import llm_client
from agentsociety2.town.agents import TownAgentConfig, apply_decision
from agentsociety2.town.llm_client import LLMEndpoint, extract_json_object
from agentsociety2.town.map_layout import (
    GRID_HEIGHT,
    GRID_WIDTH,
    PLAZA_CENTER,
    ROOMS,
    all_rooms,
    build_world_map,
    room_of,
    walkable_tiles,
)
from agentsociety2.town.pathfind import astar, nearest_walkable
from agentsociety2.town.world import WorldEngine


# ---------- 地图 ----------


def test_every_room_reaches_the_plaza():
    walkable = walkable_tiles()
    for room in all_rooms():
        path = astar(room["anchor"], PLAZA_CENTER, walkable)
        assert path is not None, f"{room['id']} 走不到广场"
        assert path[0] == room["anchor"]
        assert path[-1] == PLAZA_CENTER


def test_room_anchors_are_inside_their_own_room():
    for room in all_rooms():
        assert room_of(room["anchor"]) == room["id"]


def test_map_has_six_rooms_plus_plaza():
    world_map = build_world_map()
    assert len(ROOMS) == 6
    assert len(world_map["rooms"]) == 6
    assert world_map["plaza_room"]["id"] == "plaza"
    assert world_map["grid_w"] == GRID_WIDTH
    assert world_map["grid_h"] == GRID_HEIGHT


def test_walls_are_not_walkable():
    walkable = walkable_tiles()
    for wall in build_world_map()["walls"]:
        assert (wall["x"], wall["y"]) not in walkable


# ---------- 寻路 ----------


def test_astar_walks_around_a_wall():
    walkable = {(x, 0) for x in range(5)} | {(x, 2) for x in range(5)} | {(4, 1)}
    path = astar((0, 0), (0, 2), walkable)
    assert path is not None
    assert (4, 1) in path


def test_astar_returns_none_when_disconnected():
    walkable = {(0, 0), (5, 5)}
    assert astar((0, 0), (5, 5), walkable) is None


def test_nearest_walkable_snaps_outside_tiles():
    walkable = {(3, 3), (9, 9)}
    assert nearest_walkable((2, 3), walkable) == (3, 3)


# ---------- 模型端点 ----------


def _endpoint(provider: str) -> LLMEndpoint:
    base = "http://localhost:11434" if provider == "ollama" else "http://localhost:8000/v1"
    return LLMEndpoint(provider=provider, base_url=base, model="test-model")


def _patch_transport(monkeypatch, handler):
    original = httpx.AsyncClient

    class StubClient(original):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", StubClient)


def test_endpoint_ok_for_ollama(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "test-model"}]})
        assert request.url.path == "/api/chat"
        return httpx.Response(200, json={"message": {"content": "pong"}})

    _patch_transport(monkeypatch, handler)
    result = asyncio.run(llm_client.test_endpoint(_endpoint("ollama")))
    assert result.ok
    assert result.sample_reply == "pong"
    assert result.models == ["test-model"]


def test_endpoint_ok_for_openai_compatible(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "test-model"}]})
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    _patch_transport(monkeypatch, handler)
    result = asyncio.run(llm_client.test_endpoint(_endpoint("openai")))
    assert result.ok
    assert result.sample_reply == "pong"


def test_endpoint_reports_missing_model(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "other-model"}]})

    _patch_transport(monkeypatch, handler)
    result = asyncio.run(llm_client.test_endpoint(_endpoint("ollama")))
    assert not result.ok
    assert "test-model" in (result.error or "")


def test_endpoint_reports_connection_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_transport(monkeypatch, handler)
    result = asyncio.run(llm_client.test_endpoint(_endpoint("ollama")))
    assert not result.ok
    assert "无法连接" in (result.error or "")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('{"action":"idle"}', {"action": "idle"}),
        ('```json\n{"action":"say","text":"hi"}\n```', {"action": "say", "text": "hi"}),
        ('好的，我决定：{"action":"goto","room":"cafe"} 就这样', {"action": "goto", "room": "cafe"}),
        ('{"text":"含 } 的字符串","action":"say"}', {"text": "含 } 的字符串", "action": "say"}),
        ("没有 JSON", None),
    ],
)
def test_extract_json_object(raw, expected):
    assert extract_json_object(raw) == expected


# ---------- 世界 ----------


def _config(agent_id: str = "a1", room_id: str = "plaza") -> TownAgentConfig:
    return TownAgentConfig(
        id=agent_id,
        name="测试居民",
        persona="喜欢到处走。",
        room_id=room_id,
        llm=_endpoint("ollama"),
    )


def test_goto_builds_a_path_and_arrival_clears_it():
    world = WorldEngine()
    actor = world.add_human("玩家")
    assert world.goto(actor.id, "cafe")
    assert len(actor.path) > 1
    assert actor.target_room == "cafe"

    for _ in range(20_000):
        world._advance(actor, 1 / 20.0)
        if not actor.moving and not actor.path:
            break

    assert actor.tile() == next(r["anchor"] for r in ROOMS if r["id"] == "cafe")
    assert actor.target_room is None


def test_human_input_respects_walls():
    world = WorldEngine()
    actor = world.add_human("玩家")
    actor.x, actor.y = 18.0, 12.0  # 广场左上角内侧
    world.set_input(actor.id, "up")
    for _ in range(200):
        world._advance(actor, 1 / 20.0)
    assert actor.tile() in world.walkable, "不该走进墙格"
    assert actor.tile()[1] == 12, "应该被广场上方的边界挡住"


def test_say_is_only_heard_nearby():
    world = WorldEngine()
    speaker = world.add_human("说话的人")
    listener = world.add_human("旁边的人")
    far = world.add_human("远处的人")
    listener.x, listener.y = speaker.x + 2, speaker.y
    far.x, far.y = speaker.x + 30, speaker.y

    world.say(speaker.id, "你好")
    assert any("你好" in line for line in listener.heard)
    assert not far.heard
    assert speaker.speech() == "你好"


def test_add_and_remove_agent_hot_swaps():
    async def scenario():
        world = WorldEngine()
        config = _config()
        actor = await world.add_agent(config)
        assert actor.id in world.actors
        assert world.agent_list()[0]["id"] == config.id
        assert await world.remove_agent(config.id)
        assert config.id not in world.actors
        assert not await world.remove_agent(config.id)
        await world.stop()

    asyncio.run(scenario())


def test_agent_list_masks_api_key():
    async def scenario():
        world = WorldEngine()
        config = _config()
        config.llm.api_key = "secret-value"
        await world.add_agent(config)
        listed = world.agent_list()[0]
        assert listed["llm"]["api_key"] == "***"
        await world.stop()

    asyncio.run(scenario())


def test_unknown_room_in_decision_falls_back_to_wandering():
    world = WorldEngine()
    actor = world.add_human("玩家")
    apply_decision(world, actor, {"action": "goto", "room": "nonexistent"})
    assert actor.last_error is not None
    assert actor.target_room is not None, "兜底应该给它挑一个真实房间"


def test_say_decision_without_text_is_ignored():
    world = WorldEngine()
    actor = world.add_human("玩家")
    actor.heard = deque(maxlen=4)
    apply_decision(world, actor, {"action": "say", "text": "   "})
    assert actor.speech() is None
