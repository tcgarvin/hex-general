"""Server-side order validation — the HTTP boundary's gatekeeper (roadmap Phase 5).

The execution engine deliberately *trusts* its orders: it assumes each order
references a real unit, paths are contiguous, barrages are in range, and recruits
are legal, and it crashes loudly if not (see `barrage.py`, which names "full order
legality/visibility" as this boundary's job). This module is that boundary. It
takes the typed orders parsed from an untrusted request body plus the submitting
player's slot, and rejects anything statically illegal with `OrderValidationError`
(which the route turns into HTTP 422) before the orders are ever stored.

What is validated here is **static** legality — facts that do not depend on the
opponent's simultaneous orders:

  * ownership — every order targets a unit the caller owns; recruits are for the
    caller's own slot (this is the security boundary: a token cannot move enemy
    units);
  * one order per unit;
  * move paths are contiguous, on-map, and avoid impassable terrain (SPEC §3.4);
  * barrage is by an artillery unit at an on-map hex within range (SPEC §7.2);
  * recruits target a city the caller controls, are affordable from the current
    bank, and stay under the unit cap (SPEC §6.5, §6.1).

**Dynamic** interactions — ZOC stops, stacking conflicts, edge swaps, and the
exact end-of-turn bank after economy — are the engine's authority and are not
re-checked here. Recruitment legality is validated against the *current* state
(banks/control before this turn resolves); the rare capture-then-recruit-in-one-
turn combo is therefore not accepted by the API in v0.1 even though the engine
supports it. This conservatism keeps a locked turn guaranteed to resolve.
"""

from __future__ import annotations

from app.engine.hex_coords import Axial, distance, is_adjacent
from app.engine.models import (
    BarrageOrder,
    GameState,
    Hex,
    MoveOrder,
    Order,
    PlayerId,
    Recruitment,
    Unit,
)
from app.engine.stats import TERRAIN_STATS, UNIT_STATS, Terrain, UnitType


class OrderValidationError(Exception):
    """A submitted order is statically illegal for the requesting player."""


def validate_submission(
    state: GameState,
    slot: PlayerId,
    orders: list[Order],
    recruitments: list[Recruitment],
) -> None:
    """Validate one player's full submission against ``state``.

    Raises `OrderValidationError` on the first problem found; returns ``None`` when
    the whole submission is statically legal for ``slot``.
    """
    units_by_id = {u.id: u for u in state.units}
    hexes_by_coord = {h.coord: h for h in state.map.hexes}

    seen: set[int] = set()
    for order in orders:
        unit = units_by_id.get(order.unit_id)
        if unit is None:
            raise OrderValidationError(f"order references unknown unit {order.unit_id}")
        if unit.owner != slot:
            raise OrderValidationError(f"unit {order.unit_id} is not owned by {slot!r}")
        if order.unit_id in seen:
            raise OrderValidationError(f"unit {order.unit_id} has more than one order")
        seen.add(order.unit_id)

        if isinstance(order, MoveOrder):
            _validate_move(order, unit, hexes_by_coord)
        elif isinstance(order, BarrageOrder):
            _validate_barrage(order, unit, hexes_by_coord)

    _validate_recruitments(state, slot, recruitments, hexes_by_coord)


def _validate_move(
    order: MoveOrder, unit: Unit, hexes_by_coord: dict[Axial, Hex]
) -> None:
    """Path must be a contiguous chain of on-map, passable hexes from the unit."""
    prev = unit.coord
    for step in order.path:
        if not is_adjacent(prev, step):
            raise OrderValidationError(
                f"unit {order.unit_id} path step {step} is not adjacent to {prev}"
            )
        hex_ = hexes_by_coord.get(step)
        if hex_ is None:
            raise OrderValidationError(
                f"unit {order.unit_id} path step {step} is off-map"
            )
        if not TERRAIN_STATS[hex_.terrain].passable:
            raise OrderValidationError(
                f"unit {order.unit_id} path step {step} is impassable terrain"
            )
        prev = step


def _validate_barrage(
    order: BarrageOrder, unit: Unit, hexes_by_coord: dict[Axial, Hex]
) -> None:
    """Only artillery may barrage, at an on-map hex within its range (SPEC §7.2)."""
    if unit.type is not UnitType.ARTILLERY:
        raise OrderValidationError(
            f"unit {order.unit_id} is {unit.type} and cannot barrage"
        )
    if order.target not in hexes_by_coord:
        raise OrderValidationError(
            f"barrage target {order.target} for unit {order.unit_id} is off-map"
        )
    reach = UNIT_STATS[UnitType.ARTILLERY].attack_range
    if distance(unit.coord, order.target) > reach:
        raise OrderValidationError(
            f"barrage target {order.target} is out of range "
            f"(>{reach}) for unit {order.unit_id}"
        )


def _validate_recruitments(
    state: GameState,
    slot: PlayerId,
    recruitments: list[Recruitment],
    hexes_by_coord: dict[Axial, Hex],
) -> None:
    """Recruits must be the caller's own, at a controlled city, and affordable.

    Affordability and the unit cap are checked against the *current* bank and unit
    count, accumulating across the submission so two recruits can't each spend the
    same coin. The engine re-validates at the recruitment phase against end-of-turn
    state and remains the final authority (SPEC §6.5).
    """
    if not recruitments:
        return

    control_at = {s.coord: s.controlled_by for s in state.settlements}
    bank_by_player = {p.id: p.bank for p in state.players}
    if slot not in bank_by_player:
        raise OrderValidationError(f"unknown player slot {slot!r}")

    unit_count = sum(1 for u in state.units if u.owner == slot)
    spent = 0
    claimed: set[Axial] = set()
    for recruit in recruitments:
        if recruit.owner != slot:
            raise OrderValidationError(
                f"recruit for {recruit.owner!r} is not owned by caller {slot!r}"
            )
        hex_ = hexes_by_coord.get(recruit.coord)
        if hex_ is None or hex_.terrain is not Terrain.CITY:
            raise OrderValidationError(f"recruit at {recruit.coord} is not a city")
        if control_at.get(recruit.coord) != slot:
            raise OrderValidationError(
                f"recruit at {recruit.coord} is not a city controlled by {slot!r}"
            )
        if recruit.coord in claimed:
            raise OrderValidationError(
                f"two recruits target the same city {recruit.coord}"
            )
        claimed.add(recruit.coord)

        unit_count += 1
        if unit_count > state.config.unit_cap:
            raise OrderValidationError(
                f"recruiting exceeds the unit cap of {state.config.unit_cap}"
            )
        spent += UNIT_STATS[recruit.type].recruit_cost
        if spent > bank_by_player[slot]:
            raise OrderValidationError(
                f"recruits cost {spent} but {slot!r} has only "
                f"{bank_by_player[slot]} banked"
            )
