"""Column route planning (WORLD_SPEC §5): least-cost, deterministic, unit-blind.

Costs come from `COLUMN_RULES`: a road hex costs 1 pulse to enter and any other
passable hex 2. Fixture grids are authored directly in axial coordinates.
"""

from __future__ import annotations

import heapq
import random

from app.engine.hex_coords import Axial, neighbors
from app.engine.models import GameMap
from app.engine.routing import find_route, route_pulses, travel_eta

from engine.helpers import ax, build


def _map(terrain: list[str], roads: list[str] | None = None) -> GameMap:
    return build(terrain, [], roads=roads).map


def _is_chain(origin: Axial, route: list[Axial]) -> bool:
    prev = origin
    for step in route:
        if step not in neighbors(prev):
            return False
        prev = step
    return True


def test_straight_road_route() -> None:
    game_map = _map(["." * 12], roads=["#" * 12])
    route = find_route(game_map, ax(0, 0), ax(10, 0))
    assert route == [ax(q, 0) for q in range(1, 11)]
    assert route_pulses(game_map, route) == 10


def test_route_prefers_a_road_detour() -> None:
    # Row 0 is road, row 1 plain. Going 8 hexes east along row 1 costs 16 pulses;
    # hopping up to the road and back down costs 10.
    game_map = _map(["." * 10, "." * 10], roads=["#" * 10, "." * 10])
    route = find_route(game_map, ax(0, 1), ax(8, 1))
    assert route is not None
    assert route_pulses(game_map, route) == 10
    assert _is_chain(ax(0, 1), route)
    assert all(step.r == 0 for step in route[:-1])


def test_route_crosses_a_river_at_the_bridge() -> None:
    game_map = _map(
        ["..~..", "..~..", "..=..", "..~.."],
        roads=[".....", ".....", "..#..", "....."],
    )
    route = find_route(game_map, ax(0, 0), ax(4, 0))
    assert route is not None
    assert ax(2, 2) in route
    assert _is_chain(ax(0, 0), route)


def test_unreachable_destination_has_no_route() -> None:
    game_map = _map(["..~..", "..~..", "..~.."])
    assert find_route(game_map, ax(0, 1), ax(4, 1)) is None


def test_impassable_or_off_map_destination_has_no_route() -> None:
    game_map = _map(["..~.."])
    assert find_route(game_map, ax(0, 0), ax(2, 0)) is None
    assert find_route(game_map, ax(0, 0), ax(40, 0)) is None


def test_route_passes_through_waypoints_in_order() -> None:
    game_map = _map(["." * 8] * 6)
    via = [ax(3, 4), ax(6, 0)]
    route = find_route(game_map, ax(0, 0), ax(7, 2), via)
    assert route is not None
    assert _is_chain(ax(0, 0), route)
    assert route.index(via[0]) < route.index(via[1])
    assert route[-1] == ax(7, 2)


def test_route_to_own_hex_is_empty() -> None:
    game_map = _map(["..."])
    assert find_route(game_map, ax(1, 0), ax(1, 0)) == []


def _dijkstra_cost(game_map: GameMap, origin: Axial, goal: Axial) -> int | None:
    costs = game_map.column_costs
    best = {(origin.q, origin.r): 0}
    frontier = [(0, origin.q, origin.r)]
    while frontier:
        g, q, r = heapq.heappop(frontier)
        if (q, r) == (goal.q, goal.r):
            return g
        if g > best[(q, r)]:
            continue
        for n in neighbors(ax(q, r)):
            step = costs.get((n.q, n.r))
            if step is not None and g + step < best.get((n.q, n.r), 10**9):
                best[(n.q, n.r)] = g + step
                heapq.heappush(frontier, (g + step, n.q, n.r))
    return None


def test_routes_are_least_cost_and_deterministic_on_random_maps() -> None:
    rng = random.Random(7)
    for _ in range(25):
        terrain = ["".join(rng.choice("....w~") for _ in range(14)) for _ in range(12)]
        roads = [
            "".join(rng.choice("##..") if cell != "~" else "." for cell in terrain_row)
            for terrain_row in terrain
        ]
        game_map = _map(terrain, roads)
        open_hexes = sorted(game_map.column_costs)
        (oq, orr), (gq, gr) = rng.sample(open_hexes, 2)
        origin, goal = ax(oq, orr), ax(gq, gr)
        route = find_route(game_map, origin, goal)
        expected = _dijkstra_cost(game_map, origin, goal)
        if expected is None:
            assert route is None
            continue
        assert route is not None
        assert _is_chain(origin, route)
        assert route_pulses(game_map, route) == expected
        assert find_route(game_map, origin, goal) == route


def test_eta_counts_packing_and_full_turns() -> None:
    game_map = _map(["." * 40], roads=["#" * 40])
    road = [ax(q, 0) for q in range(1, 39)]
    # Deployed: 12 road hexes on the packing turn, then 16 a turn.
    assert travel_eta(game_map, road[:12], packed=False) == 1
    assert travel_eta(game_map, road[:13], packed=False) == 2
    assert travel_eta(game_map, road[:28], packed=False) == 2
    assert travel_eta(game_map, road[:29], packed=False) == 3
    # Already packed: 16 a turn.
    assert travel_eta(game_map, road[:16], packed=True) == 1
    assert travel_eta(game_map, road[:17], packed=True) == 2
    assert travel_eta(game_map, [], packed=True) == 0


MIXED_ROADS = "##" + "." * 8 + "#" + "." * 29
"""Road at q 0, 1 and 10 only: 1 road, 8 off-road, 1 road, then off-road east."""


def test_eta_loses_leftover_credit_at_turn_end() -> None:
    # 1 road + 8 off-road + 1 road + 7 off-road = 32 pulses, but a step can't
    # straddle turns: 15 pulses fit in turn 1, 15 in turn 2, the last step in 3.
    game_map = _map(["." * 40], roads=[MIXED_ROADS])
    route = [ax(q, 0) for q in range(1, 18)]
    assert route_pulses(game_map, route) == 32
    assert travel_eta(game_map, route, packed=True) == 3
