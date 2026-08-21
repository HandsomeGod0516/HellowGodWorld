"""固定小镇地图：六个房间通过环形走廊连接到中央广场。

地图完全由本文件的常量表生成，不读取任何 Tiled 图包或图集资源。
前端只需拿到 :func:`build_world_map` 的结果就能用色块绘制整张地图。
"""

from __future__ import annotations

from typing import Iterator, TypedDict

Tile = tuple[int, int]

GRID_WIDTH = 45
GRID_HEIGHT = 33
TILE_SIZE = 32


class Rect(TypedDict):
    x: int
    y: int
    w: int
    h: int


def _rect(x: int, y: int, w: int, h: int) -> Rect:
    return {"x": x, "y": y, "w": w, "h": h}


def _tiles(rect: Rect) -> Iterator[Tile]:
    for y in range(rect["y"], rect["y"] + rect["h"]):
        for x in range(rect["x"], rect["x"] + rect["w"]):
            yield (x, y)


def _interior(rect: Rect) -> Rect:
    """房间的可走内部：外框往内缩一圈（缩掉的那圈是墙）。"""
    return _rect(rect["x"] + 1, rect["y"] + 1, rect["w"] - 2, rect["h"] - 2)


PLAZA: Rect = _rect(17, 12, 11, 9)
PLAZA_CENTER: Tile = (22, 16)

# 从广场四边伸出的主干走廊。
CORRIDORS: list[Rect] = [
    _rect(21, 8, 3, 4),    # 上：接正上方房间
    _rect(21, 21, 3, 4),   # 下：接正下方房间
    _rect(2, 15, 15, 3),   # 左：接左侧两个房间
    _rect(28, 15, 15, 3),  # 右：接右侧两个房间
    _rect(8, 12, 1, 3),    # 左上房间下门 -> 左走廊
    _rect(8, 18, 1, 3),    # 左下房间上门 -> 左走廊
    _rect(35, 12, 1, 3),   # 右上房间下门 -> 右走廊
    _rect(35, 18, 1, 3),   # 右下房间上门 -> 右走廊
]


class RoomDef(TypedDict):
    id: str
    name: str
    name_en: str
    rect: Rect
    door: Tile
    anchor: Tile


ROOMS: list[RoomDef] = [
    {
        "id": "cafe",
        "name": "咖啡馆",
        "name_en": "Cafe",
        "rect": _rect(17, 1, 11, 7),
        "door": (22, 7),
        "anchor": (22, 4),
    },
    {
        "id": "library",
        "name": "图书馆",
        "name_en": "Library",
        "rect": _rect(4, 4, 10, 8),
        "door": (8, 11),
        "anchor": (8, 7),
    },
    {
        "id": "studio",
        "name": "工作室",
        "name_en": "Studio",
        "rect": _rect(31, 4, 10, 8),
        "door": (35, 11),
        "anchor": (35, 7),
    },
    {
        "id": "kitchen",
        "name": "厨房",
        "name_en": "Kitchen",
        "rect": _rect(4, 21, 10, 8),
        "door": (8, 21),
        "anchor": (8, 24),
    },
    {
        "id": "gameroom",
        "name": "游戏室",
        "name_en": "Game Room",
        "rect": _rect(31, 21, 10, 8),
        "door": (35, 21),
        "anchor": (35, 24),
    },
    {
        "id": "meeting",
        "name": "会议室",
        "name_en": "Meeting Room",
        "rect": _rect(17, 25, 11, 7),
        "door": (22, 25),
        "anchor": (22, 28),
    },
]

PLAZA_ROOM: RoomDef = {
    "id": "plaza",
    "name": "中央广场",
    "name_en": "Central Plaza",
    "rect": PLAZA,
    "door": PLAZA_CENTER,
    "anchor": PLAZA_CENTER,
}

ROOM_IDS: list[str] = [room["id"] for room in ROOMS] + [PLAZA_ROOM["id"]]


def all_rooms() -> list[RoomDef]:
    """六个房间加上中央广场，广场排在最后。"""
    return [*ROOMS, PLAZA_ROOM]


def walkable_tiles() -> set[Tile]:
    """广场 + 走廊 + 房间内部 + 门。房间外框其余部分是墙。"""
    walkable: set[Tile] = set(_tiles(PLAZA))
    for corridor in CORRIDORS:
        walkable.update(_tiles(corridor))
    for room in ROOMS:
        walkable.update(_tiles(_interior(room["rect"])))
        walkable.add(room["door"])
    return {
        tile
        for tile in walkable
        if 0 <= tile[0] < GRID_WIDTH and 0 <= tile[1] < GRID_HEIGHT
    }


def wall_tiles() -> set[Tile]:
    """房间外框上除门以外的格子，供前端画墙。"""
    walkable = walkable_tiles()
    walls: set[Tile] = set()
    for room in ROOMS:
        walls.update(tile for tile in _tiles(room["rect"]) if tile not in walkable)
    return walls


def room_of(tile: Tile) -> str | None:
    """判断一个格子属于哪个房间；走廊返回 ``None``。"""
    x, y = int(tile[0]), int(tile[1])
    for room in ROOMS:
        rect = room["rect"]
        if rect["x"] <= x < rect["x"] + rect["w"] and rect["y"] <= y < rect["y"] + rect["h"]:
            return room["id"]
    if PLAZA["x"] <= x < PLAZA["x"] + PLAZA["w"] and PLAZA["y"] <= y < PLAZA["y"] + PLAZA["h"]:
        return PLAZA_ROOM["id"]
    return None


def room_by_id(room_id: str | None) -> RoomDef | None:
    if not room_id:
        return None
    for room in all_rooms():
        if room["id"] == room_id:
            return room
    return None


def room_anchor(room_id: str | None) -> Tile:
    """房间中心；未知房间落到广场中心。"""
    room = room_by_id(room_id)
    return room["anchor"] if room else PLAZA_CENTER


def build_world_map() -> dict:
    """给前端的地图描述：色块矩形 + 房间语义。"""
    return {
        "grid_w": GRID_WIDTH,
        "grid_h": GRID_HEIGHT,
        "tile_size": TILE_SIZE,
        "plaza": PLAZA,
        "corridors": CORRIDORS,
        "rooms": [
            {
                "id": room["id"],
                "name": room["name"],
                "name_en": room["name_en"],
                "rect": room["rect"],
                "interior": _interior(room["rect"]),
                "door": {"x": room["door"][0], "y": room["door"][1]},
                "anchor": {"x": room["anchor"][0], "y": room["anchor"][1]},
            }
            for room in ROOMS
        ],
        "plaza_room": {
            "id": PLAZA_ROOM["id"],
            "name": PLAZA_ROOM["name"],
            "name_en": PLAZA_ROOM["name_en"],
            "rect": PLAZA,
            "anchor": {"x": PLAZA_CENTER[0], "y": PLAZA_CENTER[1]},
        },
        "walls": [{"x": x, "y": y} for x, y in sorted(wall_tiles())],
    }
