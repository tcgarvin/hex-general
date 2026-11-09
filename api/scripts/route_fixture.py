"""Generate the route-parity fixture for the TypeScript column router.

Run from ``api/``::

    uv run python scripts/route_fixture.py

Writes ``frontend/src/world/__fixtures__/routes.json``: a generated world's
CompactMap plus deterministic route cases with the Python router's answers.
``frontend/src/world/routing.test.ts`` asserts the TS router matches exactly.
Regenerate whenever routing, column costs, or the world generator change.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from app.engine.hex_coords import Axial, distance
from app.engine.map_codec import encode_map
from app.engine.routing import find_route, route_pulses, travel_eta
from app.engine.stats import TERRAIN_STATS
from app.world.generate import WorldParams, generate

SLOTS = 40
WORLD_SEED = 5
PAIR_SEED = 20261109
RANDOM_PAIRS = 24
OUTPUT = (
    Path(__file__).resolve().parents[2] / "frontend/src/world/__fixtures__/routes.json"
)

Case = tuple[Axial, Axial, list[Axial]]


def _pt(a: Axial) -> list[int]:
    return [a.q, a.r]


def _build_cases(
    passable: list[Axial], roads: list[Axial], impassable: list[Axial]
) -> list[Case]:
    rng = random.Random(PAIR_SEED)
    cases: list[Case] = []
    # Longest: the farthest passable pair among a sample of extremes.
    ends = (
        sorted(passable, key=lambda a: (a.q, a.r))[:60]
        + sorted(passable, key=lambda a: (-a.q, -a.r))[:60]
    )
    far = max(
        ((a, b) for a in ends for b in ends),
        key=lambda p: (distance(*p), _pt(p[0]), _pt(p[1])),
    )
    cases.append((far[0], far[1], []))
    cases.append((far[1], far[0], []))
    origin = rng.choice(passable)
    cases.append((origin, origin, []))
    cases.append((roads[0], roads[0], []))
    near = [a for a in passable if 1 <= distance(origin, a) <= 4]
    for dest in rng.sample(near, 3):
        cases.append((origin, dest, []))
    for dest in rng.sample(impassable, 2):
        cases.append((origin, dest, []))
    cases.append((origin, Axial(q=-5, r=-5), []))  # off the map
    for _ in range(RANDOM_PAIRS):
        cases.append((rng.choice(passable), rng.choice(passable), []))
    for _ in range(6):  # road-to-road
        cases.append((rng.choice(roads), rng.choice(roads), []))
    for count in (1, 2, 1, 2, 1):
        a, b = rng.choice(passable), rng.choice(passable)
        cases.append((a, b, rng.sample(passable, count)))
    a, b = rng.choice(passable), rng.choice(passable)
    cases.append((a, b, [a]))  # waypoint at the origin
    cases.append((a, b, [rng.choice(impassable)]))  # unreachable waypoint
    return cases


def main() -> None:
    world = generate(WorldParams.for_slots(SLOTS, WORLD_SEED))
    game_map = world.game_map
    passable = [h.coord for h in game_map.hexes if TERRAIN_STATS[h.terrain].passable]
    roads = [h.coord for h in game_map.hexes if h.road]
    impassable = [
        h.coord for h in game_map.hexes if not TERRAIN_STATS[h.terrain].passable
    ]
    results = []
    for origin, destination, via in _build_cases(passable, roads, impassable):
        route = find_route(game_map, origin, destination, via)
        results.append(
            {
                "origin": _pt(origin),
                "destination": _pt(destination),
                "via": [_pt(v) for v in via],
                "route": None if route is None else [_pt(a) for a in route],
                "pulses": None if route is None else route_pulses(game_map, route),
                "etaPacked": (
                    None if route is None else travel_eta(game_map, route, packed=True)
                ),
                "etaDeployed": (
                    None if route is None else travel_eta(game_map, route, packed=False)
                ),
            }
        )
    compact = encode_map(game_map)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps({"map": compact.model_dump(mode="json"), "cases": results}) + "\n"
    )
    print(f"wrote {len(results)} cases to {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
