"""Column route planning over the static map (WORLD_SPEC §5).

Travel orders name a destination, not a path; this module turns that into the
exact hex chain a column marches. Routes are planned over terrain and roads only
and ignore units entirely, so every observer computes the same route (terrain is
public) and a route never needs recomputing as the board changes.

Cost model: entering a hex costs the column step price for that hex (road vs
off-road, `GameMap.column_costs`); impassable and off-map hexes are excluded.
Search is A* with the hex distance times the cheapest step as its heuristic,
which never overestimates, so routes are least-cost. Ties are broken by a fixed
key, so the same map, origin and destination always produce the same route.

Everything here is pure. The hot loop works on ``(q, r)`` tuples rather than
`Axial` models because a cross-world route expands tens of thousands of hexes.
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from itertools import pairwise

from app.engine.hex_coords import AXIAL_DIRECTIONS, Axial
from app.engine.models import GameMap
from app.engine.stats import COLUMN_RULES

_Coord = tuple[int, int]
_STEPS: tuple[_Coord, ...] = tuple((d.q, d.r) for d in AXIAL_DIRECTIONS)


def find_route(
    game_map: GameMap,
    origin: Axial,
    destination: Axial,
    via: Sequence[Axial] = (),
) -> list[Axial] | None:
    """The least-cost column route from ``origin`` through ``via`` to ``destination``.

    Returns the hexes to enter, in order (``origin`` excluded), or ``None`` when
    some leg has no route (an unreachable or impassable waypoint). Each leg is
    planned independently, so a waypoint can be revisited. Returns ``[]`` when
    every waypoint is ``origin`` itself.
    """
    costs = game_map.column_costs
    stops = [(c.q, c.r) for c in (origin, *via, destination)]
    route: list[_Coord] = []
    for start, goal in pairwise(stops):
        leg = _astar(costs, start, goal)
        if leg is None:
            return None
        route.extend(leg)
    return [Axial(q=q, r=r) for q, r in route]


def route_pulses(game_map: GameMap, route: Sequence[Axial]) -> int:
    """Total column pulses to march ``route`` on an open road (no waiting)."""
    costs = game_map.column_costs
    return sum(costs[(c.q, c.r)] for c in route)


def travel_eta(game_map: GameMap, route: Sequence[Axial], *, packed: bool) -> int:
    """Turns a column needs to march ``route``, counting the turn it arrives.

    Mirrors the engine's march budget (`app.engine.movement`): a column spends one
    pulse of credit per pulse, a step fires once its credit covers the hex's
    cost, and unspent credit is lost at the end of a turn. A deployed unit
    (``packed=False``) spends the opening pulses of its first turn packing. Assumes
    a clear road: no contact, no full stacks. ``[]`` takes zero turns.
    """
    costs = game_map.column_costs
    turns = 0
    index = 0
    budget = COLUMN_RULES.pulses_per_turn
    if not packed:
        budget -= COLUMN_RULES.pack_pulses
    while index < len(route):
        turns += 1
        spent = 0
        while index < len(route):
            cost = costs[(route[index].q, route[index].r)]
            if spent + cost > budget:
                break
            spent += cost
            index += 1
        budget = COLUMN_RULES.pulses_per_turn
    return turns


def _astar(
    costs: dict[_Coord, int], start: _Coord, goal: _Coord
) -> list[_Coord] | None:
    """A* from ``start`` to ``goal``; the hexes entered (``start`` excluded)."""
    if start == goal:
        return []
    if goal not in costs:
        return None
    cheapest = min(COLUMN_RULES.road_step_pulses, COLUMN_RULES.offroad_step_pulses)
    gq, gr = goal

    def heuristic(q: int, r: int) -> int:
        dq = q - gq
        dr = r - gr
        return cheapest * ((abs(dq) + abs(dq + dr) + abs(dr)) // 2)

    best: dict[_Coord, int] = {start: 0}
    came_from: dict[_Coord, _Coord] = {}
    # Heap key: estimated total, then deeper first (fewer re-expansions on ties),
    # then the coordinate itself, so the order is fully deterministic.
    frontier: list[tuple[int, int, int, int]] = [
        (heuristic(*start), 0, start[0], start[1])
    ]
    closed: set[_Coord] = set()
    while frontier:
        _, neg_g, q, r = heapq.heappop(frontier)
        current = (q, r)
        if current == goal:
            return _walk_back(came_from, start, goal)
        if current in closed:
            continue
        closed.add(current)
        g = -neg_g
        for dq, dr in _STEPS:
            nxt = (q + dq, r + dr)
            step = costs.get(nxt)
            if step is None or nxt in closed:
                continue
            tentative = g + step
            if tentative < best.get(nxt, tentative + 1):
                best[nxt] = tentative
                came_from[nxt] = current
                heapq.heappush(
                    frontier,
                    (tentative + heuristic(*nxt), -tentative, nxt[0], nxt[1]),
                )
    return None


def _walk_back(
    came_from: dict[_Coord, _Coord], start: _Coord, goal: _Coord
) -> list[_Coord]:
    """Rebuild the path ``start`` → ``goal`` from A*'s parent links."""
    path = [goal]
    while path[-1] != start:
        path.append(came_from[path[-1]])
    path.pop()
    path.reverse()
    return path
