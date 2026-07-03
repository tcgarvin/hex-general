"""Baseline policies: the measuring sticks for "is the smart one smart?" (§2.2).

* `PassivePolicy` — every owned unit Holds, no recruitment. The floor for "does
  acting beat doing nothing."
* `RandomPolicy` — a seeded-random *legal* order for every owned unit. The floor
  for "better than noise." It is stateless across turns: the RNG is reseeded from
  ``(seed, slot, view.turn)`` each `plan()` call, so it is fully deterministic
  given ``(seed, view, slot)`` (AI_SPEC §4.1) regardless of instance reuse.

Both return only statically-legal plans (they pass `validate_submission`).
`RandomPolicy` deliberately does not aim at anything — it may barrage empty hexes
and wander — because its job is to be a weak, reproducible opponent.
"""

from __future__ import annotations

import hashlib
import random

from app.engine.hex_coords import Axial, distance, neighbors
from app.engine.models import (
    BarrageOrder,
    GameState,
    HoldOrder,
    MoveOrder,
    Order,
    PlayerId,
    RestOrder,
    Unit,
)
from app.engine.stats import UNIT_STATS, UnitType
from app.ai.board import BoardIndex
from app.ai.policy import BotPlan


def _stable_seed(seed: int, slot: PlayerId, turn: int) -> int:
    """A process-stable RNG seed from ``(seed, slot, turn)``.

    Uses SHA-256 rather than ``hash()`` so the value does not depend on Python's
    per-process string hash randomization — determinism must hold across runs.
    """
    digest = hashlib.sha256(f"{seed}:{slot}:{turn}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


class PassivePolicy:
    """Every owned unit Holds; never recruits (AI_SPEC §2.2)."""

    name = "passive"

    def plan(self, view: GameState, slot: PlayerId) -> BotPlan:
        orders: list[Order] = [
            HoldOrder(unit_id=u.id) for u in view.units if u.owner == slot
        ]
        return BotPlan(orders=orders, recruitments=[])


class RandomPolicy:
    """A seeded-random legal order for every owned unit (AI_SPEC §2.2)."""

    name = "random"

    def __init__(self, seed: int = 0) -> None:
        self._seed = seed

    def plan(self, view: GameState, slot: PlayerId) -> BotPlan:
        rng = random.Random(_stable_seed(self._seed, slot, view.turn))
        board = BoardIndex(view, slot)
        orders: list[Order] = []
        for unit in board.own_units:
            orders.append(self._order_for(rng, board, unit))
        return BotPlan(orders=orders, recruitments=[])

    def _order_for(self, rng: random.Random, board: BoardIndex, unit: Unit) -> Order:
        choices = ["hold", "rest", "move"]
        if unit.type is UnitType.ARTILLERY:
            choices.append("barrage")
        pick = rng.choice(choices)

        if pick == "hold":
            return HoldOrder(unit_id=unit.id)
        if pick == "rest":
            return RestOrder(unit_id=unit.id)
        if pick == "barrage":
            target = self._random_barrage_target(rng, board, unit.coord)
            if target is not None:
                return BarrageOrder(unit_id=unit.id, target=target)
            return HoldOrder(unit_id=unit.id)

        path = self._random_walk(rng, board, unit.coord, UNIT_STATS[unit.type].move)
        if path:
            return MoveOrder(unit_id=unit.id, path=path)
        return HoldOrder(unit_id=unit.id)

    def _random_walk(
        self,
        rng: random.Random,
        board: BoardIndex,
        start: Axial,
        allowance: int,
    ) -> list[Axial]:
        """A random contiguous passable path of length 1..allowance (statically
        legal). Backtracking is avoided so the walk actually goes somewhere."""
        steps = rng.randint(1, max(1, allowance))
        path: list[Axial] = []
        cur = start
        prev = start
        for _ in range(steps):
            options = [n for n in neighbors(cur) if board.passable(n) and n != prev]
            if not options:
                break
            nxt = rng.choice(options)
            path.append(nxt)
            prev, cur = cur, nxt
        return path

    def _random_barrage_target(
        self, rng: random.Random, board: BoardIndex, coord: Axial
    ) -> Axial | None:
        reach = UNIT_STATS[UnitType.ARTILLERY].attack_range
        in_range = [
            h.coord
            for h in board.view.map.hexes
            if 0 < distance(coord, h.coord) <= reach
        ]
        if not in_range:
            return None
        return rng.choice(in_range)
