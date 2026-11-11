"""Natural road routing (`app.world.roads`) on small hand-made grids."""

from __future__ import annotations

import math

from app.world.grid import HexGrid, astar
from app.world.road_metrics import straight_runs
from app.world.roads import (
    CORNER_SHARE,
    bridge_ramps,
    corner_cost,
    hex_line,
    natural_path,
    pixel_to_hex,
    smooth_path,
)

GRID = HexGrid(40, 30)


def _uniform(_: int) -> float:
    return 1.0


def _no(size: int) -> list[bool]:
    return [False] * size


def _is_chain(grid: HexGrid, path: list[int]) -> bool:
    return all(b in grid.neighbors[a] for a, b in zip(path, path[1:], strict=False))


def _turns(grid: HexGrid, path: list[int]) -> list[int]:
    """Turn per interior hex: 0 straight, 1 gentle, 2 sharp."""
    out = []
    for a, b, c in zip(path, path[1:], path[2:], strict=False):
        gap = (grid.neighbor_in[b].index(c) - grid.neighbor_in[a].index(b)) % 6
        out.append(min(gap, 6 - gap))
    return out


def _longest_run(grid: HexGrid, path: list[int]) -> int:
    marks = _no(grid.size)
    for i in path:
        marks[i] = True
    return max(straight_runs(grid, marks))


def test_pixel_to_hex_inverts_hex_centres() -> None:
    for i in range(GRID.size):
        assert pixel_to_hex(GRID, GRID.px[i], GRID.py[i]) == i


def test_hex_line_is_a_chain_of_neighbours_from_end_to_end() -> None:
    a = GRID.index_of(3, 5)
    b = GRID.index_of(30, -2)
    line = hex_line(GRID, [(GRID.px[a], GRID.py[a]), (GRID.px[b], GRID.py[b])])
    assert line[0] == a and line[-1] == b
    assert _is_chain(GRID, line)
    assert len(set(line)) == len(line)


def test_an_off_axis_road_spreads_its_turns_instead_of_two_ruled_legs() -> None:
    # A target ~15° off a hex axis. Plain hex search on even ground finds a
    # parallelogram of equally short paths and returns two straight legs;
    # corner-cut pricing makes the even zig-zag the cheapest.
    start = GRID.index_of(2, 10)
    goal = GRID.index_of(26, -3)
    direct = astar(GRID, start, goal, _uniform, 1.0)
    natural = natural_path(
        GRID, start, goal, _uniform, _no(GRID.size), _no(GRID.size), {}, 1.0
    )
    assert direct is not None and natural is not None
    assert len(natural) == len(direct)  # still a shortest path in hex steps
    assert _longest_run(GRID, direct) >= 10
    assert _longest_run(GRID, natural) <= 4
    assert 2 not in _turns(GRID, natural)


def test_corner_cost_charges_a_gentle_turn_cos_30() -> None:
    a = GRID.index_of(5, 5)
    b = GRID.neighbor_in[a][0]
    straight = [a, b, GRID.neighbor_in[b][0]]
    bent = [a, b, GRID.neighbor_in[b][1]]
    assert corner_cost(GRID, straight, _uniform) == 1.0
    assert math.isclose(corner_cost(GRID, bent, _uniform), CORNER_SHARE)


def test_a_bridge_is_crossed_only_along_its_ramps() -> None:
    # A straight river running north-south down column 10: river course edges
    # (2, 5), whose bridge tile ramps run to directions 0 and 4.
    grid = HexGrid(21, 15)
    river = [grid.col[i] == 10 for i in range(grid.size)]
    ramps = bridge_ramps(grid, river, _no(grid.size))
    middle = grid.index_of(10, 7 - 5)
    assert ramps[middle] == (0, 4)
    assert all(grid.on_edge(i) is False for i in ramps)

    def cost(v: int) -> float:
        return 6.0 if river[v] else 1.0

    start = grid.index_of(3, 7 - 1)
    goal = grid.index_of(17, 7 - 8)
    path = natural_path(grid, start, goal, cost, _no(grid.size), river, ramps, 1.0)
    assert path is not None and _is_chain(grid, path)
    crossing = [k for k, i in enumerate(path) if river[i]]
    assert len(crossing) == 1
    k = crossing[0]
    bridge = path[k]
    sides = {
        grid.neighbor_in[bridge].index(path[k - 1]),
        grid.neighbor_in[bridge].index(path[k + 1]),
    }
    assert sides == set(ramps[bridge])


def test_smoothing_keeps_ends_and_fixed_hexes_and_never_enters_water() -> None:
    start = GRID.index_of(2, 10)
    goal = GRID.index_of(26, -3)
    rough = astar(GRID, start, goal, _uniform, 1.0)
    assert rough is not None
    water = _no(GRID.size)
    water[GRID.index_of(14, 4)] = True  # a pond beside the straight line
    fixed = _no(GRID.size)
    fixed[rough[8]] = True

    def cost(v: int) -> float:
        return math.inf if water[v] else 1.0

    smooth = smooth_path(GRID, rough, fixed, cost, water)
    assert smooth[0] == start and smooth[-1] == goal
    assert rough[8] in smooth
    assert _is_chain(GRID, smooth)
    assert not any(water[i] for i in smooth)
    assert 2 not in _turns(GRID, smooth)
    assert _longest_run(GRID, smooth) < _longest_run(GRID, rough)
