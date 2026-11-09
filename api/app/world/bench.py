"""World-scale engine benchmark (WORLD_SPEC §10).

Builds a synthetic world-size state (default 350×350 hexes, 100 players × 15
units, ~15% woods), gives every unit a random two-step move, and times the hot
paths a scheduled world turn exercises: full turn resolution, one player's
visibility, fog redaction, and state serialization.

Run: ``uv run python -m app.world.bench [--size 350] [--players 100]``.
Recorded baseline (2026-11-08, after W0): resolve 0.07 s, visibility 0.01 s per
player, 7.4 MB state JSON. Before W0 the same probe took 5.9 s and 25.6 s.
"""

from __future__ import annotations

import argparse
import random
import time
from collections.abc import Callable
from typing import TypeVar

from app.engine.execution import resolve_execution
from app.engine.fog import redact_state
from app.engine.hex_coords import Axial, neighbors, offset_to_axial
from app.engine.models import (
    GameConfig,
    GameMap,
    GameState,
    Hex,
    MoveOrder,
    Order,
    Player,
    Unit,
    VictoryConfig,
)
from app.engine.stats import Terrain, UnitType
from app.engine.visibility import visible_hexes


def build_world(size: int, players: int, per_player: int, seed: int) -> GameState:
    """Return a synthetic world-size state with randomly scattered units."""
    rng = random.Random(seed)
    hexes = [
        Hex(
            coord=offset_to_axial(col, row),
            terrain=Terrain.WOODS if rng.random() < 0.15 else Terrain.PLAINS,
        )
        for row in range(size)
        for col in range(size)
    ]
    open_coords = [h.coord for h in hexes if h.terrain is Terrain.PLAINS]
    picks = rng.sample(open_coords, players * per_player)
    ids = [f"p{i}" for i in range(players)]
    units = [
        Unit(
            id=i + 1,
            type=rng.choice([UnitType.INFANTRY, UnitType.ARMOR]),
            owner=ids[i // per_player],
            coord=coord,
            hp=10,
        )
        for i, coord in enumerate(picks)
    ]
    return GameState(
        turn=0,
        map=GameMap(width=size, height=size, hexes=hexes),
        units=units,
        players=[Player(id=p) for p in ids],
        config=GameConfig(
            unit_cap=per_player, victory=VictoryConfig(turn_limit=10_000)
        ),
    )


def random_moves(state: GameState, seed: int) -> list[Order]:
    """Give every unit a random contiguous two-step path over on-map hexes."""
    rng = random.Random(seed)
    on_map = state.map.index
    orders: list[Order] = []
    for unit in state.units:
        path: list[Axial] = []
        cur = unit.coord
        for _ in range(2):
            cur = rng.choice([n for n in neighbors(cur) if n in on_map])
            path.append(cur)
        orders.append(MoveOrder(unit_id=unit.id, path=path))
    return orders


T = TypeVar("T")


def _timed(label: str, fn: Callable[[], T]) -> T:
    """Run ``fn`` once, print its wall time, and return its result."""
    start = time.perf_counter()
    result = fn()
    print(f"{label:<28} {time.perf_counter() - start:8.3f} s")
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size", type=int, default=350)
    parser.add_argument("--players", type=int, default=100)
    parser.add_argument("--per-player", type=int, default=15)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)

    state = build_world(args.size, args.players, args.per_player, args.seed)
    orders = random_moves(state, args.seed)
    print(f"world {args.size}×{args.size}, {len(state.units)} units")
    _timed("resolve_execution", lambda: resolve_execution(state, orders))
    _timed("visible_hexes (1 player)", lambda: visible_hexes(state, "p0"))
    _timed("redact_state (1 player)", lambda: redact_state(state, "p0"))
    blob = _timed("serialize state", state.model_dump_json)
    print(f"{'state JSON size':<28} {len(blob) / 1e6:8.1f} MB")


if __name__ == "__main__":
    main()
