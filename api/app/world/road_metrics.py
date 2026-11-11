"""Shape and travel measurements of a generated world's road network (WORLD_SPEC §6).

A review aid for the road generator: how ruled the roads look (straight runs
along the three hex axes, turn mix), how far they wander (detour against the
straight-line distance), what ground they take, and what that means for travel
(column pulses between neighbouring bases and from each base to its sites, priced
like the engine's `GameMap.column_costs`).

Run: ``uv run python -m app.world.road_metrics --slots 12 --seed 1
[--road-style natural]``.
"""

from __future__ import annotations

import argparse
import heapq
import statistics
from collections import Counter, deque
from collections.abc import Sequence
from dataclasses import dataclass

from app.engine.economy_rules import SiteKind
from app.engine.hex_coords import Axial
from app.engine.stats import COLUMN_RULES, TERRAIN_STATS, Terrain
from app.world.generate import ROAD_STYLES, GeneratedWorld, WorldParams, generate
from app.world.grid import HexGrid

LONG_RUN = 8
"""A straight run longer than this many hexes reads as a ruled line."""

_DETOUR_NEIGHBORS = 3  # nearest settlements per settlement for the detour ratio
_DETOUR_REACH = 16  # only settlement pairs this close (hexes) are compared
_SETTLEMENT = (Terrain.TOWN, Terrain.CITY)


@dataclass(frozen=True)
class RoadMetrics:
    """One world's road measurements (see `measure`)."""

    road_hexes: int
    bridges: int
    run_median: float
    run_p90: float
    run_max: int
    long_run_share: float
    turns_straight: float
    turns_gentle: float
    turns_sharp: float
    detour_median: float
    detour_p90: float
    road_on_woods: float
    road_on_hills: float
    land_woods: float
    land_hills: float
    base_to_base_median: float
    base_to_base_max: int
    base_to_site_median: float
    base_to_site_max: int

    def as_dict(self) -> dict[str, float]:
        """The measurements as a flat name → number mapping."""
        return {name: float(getattr(self, name)) for name in self.__dataclass_fields__}


def measure(world: GeneratedWorld) -> RoadMetrics:
    """Measure ``world``'s road network.

    *Runs* are maximal lines of road hexes along one hex axis, each hex adjacent
    to the next (lengths of 2 or more). *Turns* classify road hexes with exactly
    two road neighbours, settlements aside: straight through, a gentle (60°)
    turn, or a sharp (120°) corner. The *detour* of a settlement pair is its
    road-only distance over its hex distance, for each settlement's nearest
    neighbours. Travel is in column pulses (`COLUMN_RULES`): between the bases of
    territories sharing a real border, and from each base to every site in its
    territory.
    """
    game_map = world.game_map
    grid = HexGrid(game_map.width, game_map.height)
    terrain = [h.terrain for h in game_map.hexes]
    road = [h.road for h in game_map.hexes]
    runs = straight_runs(grid, road)
    long_hexes = _long_run_hexes(grid, road)
    turns = turn_mix(grid, road, terrain)
    detours = detour_ratios(grid, road, [_index(grid, c) for c in world.settlements])
    on_road = Counter(t for t, r in zip(terrain, road, strict=True) if r)
    land = Counter(t for t in terrain if TERRAIN_STATS[t].passable)
    paved = sum(on_road.values()) - on_road[Terrain.BRIDGE]
    paved -= sum(on_road[t] for t in _SETTLEMENT)
    dry = sum(land.values()) - land[Terrain.BRIDGE] - sum(land[t] for t in _SETTLEMENT)
    between, to_sites = travel_pulses(world, grid)
    total_turns = sum(turns.values())
    return RoadMetrics(
        road_hexes=sum(road),
        bridges=on_road[Terrain.BRIDGE],
        run_median=statistics.median(runs),
        run_p90=_quantile(runs, 0.9),
        run_max=max(runs),
        long_run_share=round(len(long_hexes) / sum(road), 4),
        turns_straight=round(turns["straight"] / total_turns, 4),
        turns_gentle=round(turns["gentle"] / total_turns, 4),
        turns_sharp=round(turns["sharp"] / total_turns, 4),
        detour_median=round(statistics.median(detours), 3),
        detour_p90=round(_quantile(detours, 0.9), 3),
        road_on_woods=round(on_road[Terrain.WOODS] / paved, 4),
        road_on_hills=round(on_road[Terrain.HILLS] / paved, 4),
        land_woods=round(land[Terrain.WOODS] / dry, 4),
        land_hills=round(land[Terrain.HILLS] / dry, 4),
        base_to_base_median=statistics.median(between),
        base_to_base_max=max(between),
        base_to_site_median=statistics.median(to_sites),
        base_to_site_max=max(to_sites),
    )


def straight_runs(grid: HexGrid, road: Sequence[bool]) -> list[int]:
    """Lengths of every maximal straight line of road hexes (2 or more) along the
    three hex axes."""
    runs: list[int] = []
    for axis in range(3):
        for i in range(grid.size):
            if not road[i]:
                continue
            back = grid.neighbor_in[i][axis + 3]
            if back >= 0 and road[back]:
                continue  # not the start of a run
            length = 1
            j = grid.neighbor_in[i][axis]
            while j >= 0 and road[j]:
                length += 1
                j = grid.neighbor_in[j][axis]
            if length >= 2:
                runs.append(length)
    return runs


def _long_run_hexes(grid: HexGrid, road: Sequence[bool]) -> set[int]:
    """Road hexes on a straight run longer than `LONG_RUN`."""
    hexes: set[int] = set()
    for axis in range(3):
        for i in range(grid.size):
            back = grid.neighbor_in[i][axis + 3]
            if not road[i] or (back >= 0 and road[back]):
                continue
            line = [i]
            j = grid.neighbor_in[i][axis]
            while j >= 0 and road[j]:
                line.append(j)
                j = grid.neighbor_in[j][axis]
            if len(line) > LONG_RUN:
                hexes.update(line)
    return hexes


def turn_mix(
    grid: HexGrid, road: Sequence[bool], terrain: Sequence[Terrain]
) -> Counter[str]:
    """Counts of ``straight``, ``gentle`` and ``sharp`` road hexes: those with
    exactly two road neighbours, settlements aside, by the angle between them."""
    mix: Counter[str] = Counter()
    for i in range(grid.size):
        if not road[i] or terrain[i] in _SETTLEMENT:
            continue
        dirs = [d for d, n in enumerate(grid.neighbor_in[i]) if n >= 0 and road[n]]
        if len(dirs) != 2:
            continue
        gap = (dirs[1] - dirs[0]) % 6
        gap = min(gap, 6 - gap)
        mix[{3: "straight", 2: "gentle", 1: "sharp"}[gap]] += 1
    return mix


def detour_ratios(
    grid: HexGrid, road: Sequence[bool], settlements: Sequence[int]
) -> list[float]:
    """Road-only distance over hex distance for each settlement and its nearest
    neighbours within `_DETOUR_REACH` (each pair once)."""
    pairs: set[tuple[int, int]] = set()
    for s in settlements:
        near = sorted(
            (grid.distance(s, t), t)
            for t in settlements
            if t != s and grid.distance(s, t) <= _DETOUR_REACH
        )
        for _, t in near[:_DETOUR_NEIGHBORS]:
            pairs.add((min(s, t), max(s, t)))
    ratios: list[float] = []
    by_start: dict[int, list[int]] = {}
    for a, b in sorted(pairs):
        by_start.setdefault(a, []).append(b)
    for a, targets in sorted(by_start.items()):
        steps = _road_steps(grid, road, a, set(targets))
        for b in targets:
            ratios.append(steps[b] / grid.distance(a, b))
    return ratios


def _road_steps(
    grid: HexGrid, road: Sequence[bool], start: int, targets: set[int]
) -> dict[int, int]:
    """Steps along road from ``start`` to each of ``targets`` (BFS). Raises
    `KeyError` if a target is off ``start``'s road network: every settlement is
    on one network."""
    dist = {start: 0}
    queue = deque([start])
    while queue and not targets <= dist.keys():
        u = queue.popleft()
        for v in grid.neighbors[u]:
            if road[v] and v not in dist:
                dist[v] = dist[u] + 1
                queue.append(v)
    missing = targets - dist.keys()
    if missing:
        raise KeyError(f"settlements {sorted(missing)} are off the road network")
    return dist


def travel_pulses(world: GeneratedWorld, grid: HexGrid) -> tuple[list[int], list[int]]:
    """Column pulses between the bases of bordering territories, and from each
    base to each site in its territory (least-cost, as `find_route` prices it)."""
    hexes = world.game_map.hexes
    cost = [
        (
            0
            if not TERRAIN_STATS[h.terrain].passable
            else (
                COLUMN_RULES.road_step_pulses
                if h.road
                else COLUMN_RULES.offroad_step_pulses
            )
        )
        for h in hexes
    ]
    bases = [_index(grid, c) for c in world.capitals]
    territory = world.territory
    contacts: Counter[tuple[int, int]] = Counter()
    for i in range(grid.size):
        p = territory[i]
        for v in grid.neighbors[i]:
            if p >= 0 and territory[v] > p:
                contacts[(p, territory[v])] += 1
    neighbours: dict[int, set[int]] = {}
    for (p, q), count in contacts.items():
        if count >= 4:
            neighbours.setdefault(p, set()).add(q)
    sites: dict[int, list[int]] = {}
    for site in world.game_map.sites:
        if site.kind is SiteKind.BASE:
            continue
        i = _index(grid, site.coord)
        sites.setdefault(territory[i], []).append(i)
    between: list[int] = []
    to_sites: list[int] = []
    for p, base in enumerate(bases):
        targets = {bases[q] for q in neighbours.get(p, ())} | set(sites.get(p, ()))
        if not targets:
            continue
        pulses = _dijkstra(grid, cost, base, targets)
        between += [pulses[bases[q]] for q in sorted(neighbours.get(p, ()))]
        to_sites += [pulses[s] for s in sites.get(p, ())]
    return between, to_sites


def _dijkstra(
    grid: HexGrid, cost: Sequence[int], start: int, targets: set[int]
) -> dict[int, int]:
    """Least summed entry cost from ``start`` to each of ``targets``."""
    best = {start: 0}
    done: set[int] = set()
    heap = [(0, start)]
    while heap and not targets <= done:
        g, u = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        for v in grid.neighbors[u]:
            step = cost[v]
            if step <= 0:
                continue
            if g + step < best.get(v, g + step + 1):
                best[v] = g + step
                heapq.heappush(heap, (g + step, v))
    return {t: best[t] for t in targets}


def _index(grid: HexGrid, coord: Axial) -> int:
    return grid.index_of(coord.q, coord.r)


def _quantile(values: Sequence[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def main() -> None:
    """CLI: generate a world and print its road measurements."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slots", type=int, default=12)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--road-style", choices=ROAD_STYLES, default="direct")
    args = parser.parse_args()
    world = generate(
        WorldParams.for_slots(args.slots, args.seed, road_style=args.road_style)
    )
    for key, value in measure(world).as_dict().items():
        print(f"{key:24s} {value:g}")


if __name__ == "__main__":
    main()
