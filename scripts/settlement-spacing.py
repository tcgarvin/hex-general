#!/usr/bin/env python3
"""Measure how crowded a generated world's settlements are (WORLD_SPEC §6).

For each seed it generates a world and reports the straight-line hex distance from
each settlement (town or city) to its nearest other settlement (mean, median), the
column pulses along the cheapest route to the nearest of its three closest
neighbours (mean; roads priced as the engine does), the counts of settlements,
towns, cities and non-base sites, and the generation time. Optional overrides try a
parameter value without touching the default.

Run from ``api/`` (it imports the generator)::

    uv run python ../scripts/settlement-spacing.py --slots 200 --seeds 1,2,3
    uv run python ../scripts/settlement-spacing.py --slots 12 --seeds 1,2,3 \\
        --hexes-per-settlement 50
"""

from __future__ import annotations

import argparse
import dataclasses
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from app.engine.economy_rules import SiteKind  # noqa: E402
from app.engine.hex_coords import distance  # noqa: E402
from app.engine.stats import COLUMN_RULES, TERRAIN_STATS, Terrain  # noqa: E402
from app.world.generate import WorldParams, generate  # noqa: E402
from app.world.grid import HexGrid  # noqa: E402
from app.world.road_metrics import _dijkstra, _index  # noqa: E402

_NEIGHBOURS_TRIED = 3


def measure(slots: int, seed: int, overrides: dict[str, int]) -> dict[str, float]:
    """Spacing, count and timing figures for one generated world."""
    params = dataclasses.replace(WorldParams.for_slots(slots, seed), **overrides)
    started = time.perf_counter()
    world = generate(params)
    seconds = time.perf_counter() - started

    points = world.settlements
    nearest = []
    near_sets = []
    for a in points:
        by_distance = sorted(
            (distance(a, b), k) for k, b in enumerate(points) if b != a
        )
        nearest.append(by_distance[0][0])
        near_sets.append([k for _, k in by_distance[:_NEIGHBOURS_TRIED]])

    grid = HexGrid(world.params.width, world.params.height)
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
        for h in world.game_map.hexes
    ]
    pulses = []
    for a, near in zip(points, near_sets):
        reach = _dijkstra(
            grid, cost, _index(grid, a), {_index(grid, points[k]) for k in near}
        )
        pulses.append(min(reach.values()))

    cities = sum(
        world.game_map.hexes[_index(grid, c)].terrain is Terrain.CITY for c in points
    )
    sites = [s for s in world.game_map.sites if s.kind is not SiteKind.BASE]
    return {
        "settlements": len(points),
        "towns": len(points) - cities,
        "cities": cities,
        "sites": len(sites),
        "nn_mean": statistics.mean(nearest),
        "nn_median": statistics.median(nearest),
        "pulses_mean": statistics.mean(pulses),
        "seconds": seconds,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slots", type=int, default=200)
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--hexes-per-settlement", type=int)
    parser.add_argument("--city-share", type=float)
    args = parser.parse_args()
    overrides: dict[str, int | float] = {}
    if args.hexes_per_settlement is not None:
        overrides["hexes_per_settlement"] = args.hexes_per_settlement
    if args.city_share is not None:
        overrides["city_share"] = args.city_share
    header = (
        "seed  slots  settl  towns  cities  sites  nn_mean  nn_median  "
        "pulses_mean  seconds"
    )
    print(header)
    for seed in (int(s) for s in args.seeds.split(",")):
        m = measure(args.slots, seed, overrides)
        print(
            f"{seed:>4}  {args.slots:>5}  {m['settlements']:>5.0f}  {m['towns']:>5.0f}"
            f"  {m['cities']:>6.0f}  {m['sites']:>5.0f}  {m['nn_mean']:>7.2f}"
            f"  {m['nn_median']:>9.1f}  {m['pulses_mean']:>11.2f}  {m['seconds']:>7.1f}"
        )


if __name__ == "__main__":
    main()
