"""網格 A* 尋路。四方向鄰居，曼哈頓距離啟發。"""

from __future__ import annotations

import heapq

Tile = tuple[int, int]


def neighbors(tile: Tile, walkable: set[Tile]) -> list[Tile]:
    x, y = tile
    return [
        candidate
        for candidate in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
        if candidate in walkable
    ]


def nearest_walkable(tile: Tile, walkable: set[Tile]) -> Tile:
    """把任意格子吸附到最近的可走格；``walkable`` 為空時原樣返回。"""
    if tile in walkable or not walkable:
        return tile
    return min(
        walkable,
        key=lambda item: abs(item[0] - tile[0]) + abs(item[1] - tile[1]),
    )


def astar(start: Tile, goal: Tile, walkable: set[Tile]) -> list[Tile] | None:
    """返回含起點與終點的完整路徑；不可達返回 ``None``。"""
    start = nearest_walkable(start, walkable)
    goal = nearest_walkable(goal, walkable)
    if start == goal:
        return [start]
    if start not in walkable or goal not in walkable:
        return None

    frontier: list[tuple[int, Tile]] = [(0, start)]
    came_from: dict[Tile, Tile | None] = {start: None}
    cost_so_far: dict[Tile, int] = {start: 0}

    while frontier:
        _, current = heapq.heappop(frontier)
        if current == goal:
            break
        for neighbor in neighbors(current, walkable):
            new_cost = cost_so_far[current] + 1
            if neighbor not in cost_so_far or new_cost < cost_so_far[neighbor]:
                cost_so_far[neighbor] = new_cost
                priority = new_cost + abs(goal[0] - neighbor[0]) + abs(goal[1] - neighbor[1])
                heapq.heappush(frontier, (priority, neighbor))
                came_from[neighbor] = current

    if goal not in came_from:
        return None

    path = [goal]
    current: Tile | None = goal
    while came_from[current] is not None:
        current = came_from[current]
        path.append(current)  # type: ignore[arg-type]
    path.reverse()
    return path
