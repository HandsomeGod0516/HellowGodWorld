"""固定小鎮地圖：六個房間透過環形走廊連線到中央廣場。

地圖完全由本檔案的常量表生成，不讀取任何 Tiled 圖包或圖集資源。
前端只需拿到 :func:`build_world_map` 的結果就能用色塊繪製整張地圖。
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
    """房間的可走內部：外框往內縮一圈（縮掉的那圈是牆）。"""
    return _rect(rect["x"] + 1, rect["y"] + 1, rect["w"] - 2, rect["h"] - 2)


PLAZA: Rect = _rect(17, 12, 11, 9)
PLAZA_CENTER: Tile = (22, 16)
# 廣場右上角擺一份食物，血量低的時候可以走過去吃東西回血。
FOOD_SPOT: Tile = (PLAZA["x"] + PLAZA["w"] - 2, PLAZA["y"] + 1)

# 從廣場四邊伸出的主幹走廊。
CORRIDORS: list[Rect] = [
    _rect(21, 8, 3, 4),    # 上：接正上方房間
    _rect(21, 21, 3, 4),   # 下：接正下方房間
    _rect(2, 15, 15, 3),   # 左：接左側兩個房間
    _rect(28, 15, 15, 3),  # 右：接右側兩個房間
    _rect(8, 12, 1, 3),    # 左上房間下門 -> 左走廊
    _rect(8, 18, 1, 3),    # 左下房間上門 -> 左走廊
    _rect(35, 12, 1, 3),   # 右上房間下門 -> 右走廊
    _rect(35, 18, 1, 3),   # 右下房間上門 -> 右走廊
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
        "name": "咖啡館",
        "name_en": "Cafe",
        "rect": _rect(17, 1, 11, 7),
        "door": (22, 7),
        "anchor": (22, 4),
    },
    {
        "id": "library",
        "name": "圖書館",
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
        "name": "廚房",
        "name_en": "Kitchen",
        "rect": _rect(4, 21, 10, 8),
        "door": (8, 21),
        "anchor": (8, 24),
    },
    {
        "id": "gameroom",
        "name": "遊戲室",
        "name_en": "Game Room",
        "rect": _rect(31, 21, 10, 8),
        "door": (35, 21),
        "anchor": (35, 24),
    },
    {
        "id": "meeting",
        "name": "會議室",
        "name_en": "Meeting Room",
        "rect": _rect(17, 25, 11, 7),
        "door": (22, 25),
        "anchor": (22, 28),
    },
]

PLAZA_ROOM: RoomDef = {
    "id": "plaza",
    "name": "中央廣場",
    "name_en": "Central Plaza",
    "rect": PLAZA,
    "door": PLAZA_CENTER,
    "anchor": PLAZA_CENTER,
}

ROOM_IDS: list[str] = [room["id"] for room in ROOMS] + [PLAZA_ROOM["id"]]


def all_rooms() -> list[RoomDef]:
    """六個房間加上中央廣場，廣場排在最後。"""
    return [*ROOMS, PLAZA_ROOM]


def walkable_tiles() -> set[Tile]:
    """廣場 + 走廊 + 房間內部 + 門。房間外框其餘部分是牆。"""
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
    """房間外框上除門以外的格子，供前端畫牆。"""
    walkable = walkable_tiles()
    walls: set[Tile] = set()
    for room in ROOMS:
        walls.update(tile for tile in _tiles(room["rect"]) if tile not in walkable)
    return walls


def room_of(tile: Tile) -> str | None:
    """判斷一個格子屬於哪個房間；走廊返回 ``None``。"""
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
    """房間中心；未知房間落到廣場中心。"""
    room = room_by_id(room_id)
    return room["anchor"] if room else PLAZA_CENTER


def build_world_map() -> dict:
    """給前端的地圖描述：色塊矩形 + 房間語義。"""
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
        "food": {"x": FOOD_SPOT[0], "y": FOOD_SPOT[1]},
    }
