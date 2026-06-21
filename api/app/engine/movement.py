"""Movement-pulse execution (SPEC §7.1, §8, §11.1–11.3; roadmap Phase 2).

This is the first slice of the deterministic execution engine: the movement
phase of a turn, with no combat yet. ``resolve_movement`` is a pure function —
it copies the input state, never mutates it, performs no I/O, and uses no
randomness — so it is fully unit-testable headless and its event log doubles as
the replay (see `app.engine.events`).

Resolution model (SPEC §11.1):

  * Each Move order names an exact path of adjacent hexes (SPEC §7.1). A unit
    attempts one step per movement *pulse* and never reroutes.
  * A unit's step budget is its type's ``move`` (`app.engine.stats`), plus 1 if
    its *entire* planned path follows road hexes (SPEC §3.3). The turn runs for
    as many pulses as the largest budget.
  * Within a pulse all moves are simultaneous. Conflicts resolve as:
      - terrain illegal (off-map / river) → the step fails, the unit stops;
      - two-or-more friendly units onto one hex → traffic jam, none enter, all
        stop (SPEC §8.2);
      - one mover onto a hex a *moving* unit is vacating → it follows (chains of
        followers resolve via a fixpoint);
      - one mover onto a hex held by a unit that stays → blocked, it stops.
  * After a successful step, a unit adjacent to any enemy has entered an enemy
    zone of control and halts further movement (SPEC §8.3).

Combat is Phase 3. Until then, running into an enemy is a documented seam: the
unit stops with no damage and a `MoveBlocked` (reason ``ENEMY``/``CONTESTED``)
records where combat will later occur. Likewise, two units swapping hexes or a
pure rotation cycle cannot resolve simultaneously without stacking, so those
units stay put and stop — a v0.1 limitation; enemy swaps are §12.9 combat.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.engine.events import (
    Event,
    MoveBlocked,
    MoveBlockedReason,
    TrafficJam,
    UnitMoved,
    ZoneOfControlStop,
)
from app.engine.hex_coords import Axial, is_adjacent, neighbors
from app.engine.models import GameState, Hex, MoveOrder, Order, PlayerId, Unit, UnitId
from app.engine.stats import TERRAIN_STATS, UNIT_STATS


class EngineError(Exception):
    """An order or path violates an engine invariant (a caller/programming bug).

    Order *legality* against game rules (range, costs, ownership) is validated
    upstream in Phase 5; this guards the structural contract the pure engine
    relies on: one order per unit, referencing a real unit, with a contiguous
    path. Distinct from `app.engine.scenario.ScenarioError` (load-time content).
    """


@dataclass
class _Mover:
    """Mutable per-unit movement bookkeeping for the duration of resolution."""

    unit_id: UnitId
    owner: PlayerId
    path: list[Axial]
    max_steps: int
    steps_taken: int = 0
    stopped: bool = False

    @property
    def active(self) -> bool:
        """Whether the unit still has a step to attempt this turn."""
        return not self.stopped and self.steps_taken < self.max_steps

    def next_target(self) -> Axial:
        """The hex this unit will attempt to enter on its next pulse."""
        return self.path[self.steps_taken]


def resolve_movement(
    state: GameState, orders: list[Order]
) -> tuple[GameState, list[Event]]:
    """Resolve the movement phase, returning a new state and the event log.

    ``state`` is not mutated. Non-Move orders (Barrage/Rest/Hold) and units
    without an order do not move — they stay put and occupy their hex. Raises
    `EngineError` if two orders target the same unit, an order references an
    unknown unit, or a move path is not a contiguous chain of adjacent hexes.
    """
    units: dict[UnitId, Unit] = {u.id: u.model_copy(deep=True) for u in state.units}
    hexes: dict[Axial, Hex] = {h.coord: h for h in state.map.hexes}

    movers = _build_movers(state, units, orders, hexes)

    positions: dict[Axial, UnitId] = {u.coord: u.id for u in units.values()}
    events: list[Event] = []
    pulse_count = max((m.max_steps for m in movers.values()), default=0)
    for pulse in range(1, pulse_count + 1):
        _resolve_pulse(pulse, hexes, units, positions, movers, events)

    new_state = state.model_copy(deep=True)
    new_state.units = [units[u.id] for u in state.units]
    return new_state, events


def _build_movers(
    state: GameState,
    units: dict[UnitId, Unit],
    orders: list[Order],
    hexes: dict[Axial, Hex],
) -> dict[UnitId, _Mover]:
    """Validate orders and build a `_Mover` for each unit that will move."""
    seen: set[UnitId] = set()
    movers: dict[UnitId, _Mover] = {}
    for order in orders:
        if order.unit_id in seen:
            raise EngineError(f"unit {order.unit_id} has more than one order")
        seen.add(order.unit_id)
        if order.unit_id not in units:
            raise EngineError(f"order references unknown unit {order.unit_id}")
        if not isinstance(order, MoveOrder):
            continue
        unit = units[order.unit_id]
        _validate_path(unit, order.path)
        budget = _move_budget(unit, order.path, hexes)
        max_steps = min(budget, len(order.path))
        if max_steps > 0:
            movers[unit.id] = _Mover(
                unit_id=unit.id,
                owner=unit.owner,
                path=list(order.path),
                max_steps=max_steps,
            )
    return movers


def _validate_path(unit: Unit, path: list[Axial]) -> None:
    """Ensure every step of a move path is adjacent to the previous hex."""
    prev = unit.coord
    for step in path:
        if not is_adjacent(prev, step):
            raise EngineError(
                f"unit {unit.id} path step {step} is not adjacent to {prev}"
            )
        prev = step


def _move_budget(unit: Unit, path: list[Axial], hexes: dict[Axial, Hex]) -> int:
    """Return the unit's step budget, applying the road bonus (SPEC §3.3).

    The +1 road bonus applies only when the unit starts on a road *and* every
    hex of its planned path is a road hex (bridges count as roads).
    """
    base = UNIT_STATS[unit.type].move
    if _path_is_all_road(unit.coord, path, hexes):
        return base + 1
    return base


def _path_is_all_road(
    origin: Axial, path: list[Axial], hexes: dict[Axial, Hex]
) -> bool:
    """Whether the origin and every path hex carry a road (off-map → False)."""
    for coord in (origin, *path):
        hex_ = hexes.get(coord)
        if hex_ is None or not hex_.road:
            return False
    return True


def _resolve_pulse(
    pulse: int,
    hexes: dict[Axial, Hex],
    units: dict[UnitId, Unit],
    positions: dict[Axial, UnitId],
    movers: dict[UnitId, _Mover],
    events: list[Event],
) -> None:
    """Resolve one movement pulse, mutating ``units``/``positions``/``events``.

    Units are processed in id order wherever ordering is observable, so the
    event log is deterministic regardless of order dictionary insertion.
    """

    def block(mover: _Mover, target: Axial, reason: MoveBlockedReason) -> None:
        events.append(
            MoveBlocked(
                pulse=pulse,
                unit_id=mover.unit_id,
                origin=units[mover.unit_id].coord,
                target=target,
                reason=reason,
            )
        )
        mover.stopped = True

    active = sorted((m for m in movers.values() if m.active), key=lambda m: m.unit_id)

    # Step 1: terrain legality (off-map / impassable end movement, SPEC §11.3).
    terrain_ok: list[tuple[_Mover, Axial]] = []
    for mover in active:
        target = mover.next_target()
        hex_ = hexes.get(target)
        if hex_ is None:
            block(mover, target, MoveBlockedReason.OFF_MAP)
        elif not TERRAIN_STATS[hex_.terrain].passable:
            block(mover, target, MoveBlockedReason.IMPASSABLE)
        else:
            terrain_ok.append((mover, target))

    # Step 2: a hex held by an enemy at pulse start is combat (Phase 3 seam).
    friendly: list[tuple[_Mover, Axial]] = []
    for mover, target in terrain_ok:
        occupant = positions.get(target)
        if occupant is not None and units[occupant].owner != mover.owner:
            block(mover, target, MoveBlockedReason.ENEMY)
        else:
            friendly.append((mover, target))

    # Step 3: group by destination — friendly pile-ups jam, enemy pile-ups are
    # a contested-hex combat seam, lone movers proceed to the follow fixpoint.
    by_target: dict[Axial, list[_Mover]] = defaultdict(list)
    for mover, target in friendly:
        by_target[target].append(mover)
    singles: dict[UnitId, Axial] = {}
    for target, contenders in by_target.items():
        if len(contenders) == 1:
            singles[contenders[0].unit_id] = target
            continue
        if len({m.owner for m in contenders}) == 1:
            events.append(
                TrafficJam(
                    pulse=pulse,
                    coord=target,
                    unit_ids=sorted(m.unit_id for m in contenders),
                )
            )
            for mover in contenders:
                mover.stopped = True
        else:
            for mover in contenders:
                block(mover, target, MoveBlockedReason.CONTESTED)

    # Step 4: apply lone moves, settling follow chains by fixpoint. A move lands
    # only when its target is free; vacating an origin can free a follower's
    # target, so iterate until no further progress. True cycles never free up
    # and fall through to the blocked sweep below.
    moved: list[UnitId] = []
    pending = set(singles)
    progressed = True
    while progressed:
        progressed = False
        for unit_id in sorted(pending):
            target = singles[unit_id]
            if target in positions:
                continue
            mover = movers[unit_id]
            origin = units[unit_id].coord
            del positions[origin]
            positions[target] = unit_id
            units[unit_id].coord = target
            mover.steps_taken += 1
            events.append(
                UnitMoved(pulse=pulse, unit_id=unit_id, origin=origin, dest=target)
            )
            moved.append(unit_id)
            pending.discard(unit_id)
            progressed = True

    for unit_id in sorted(pending):
        # Target stayed occupied — by a stationary friendly unit, a jammed unit,
        # or a cycle peer. Enemies were filtered in step 2, so this is OCCUPIED.
        block(movers[unit_id], singles[unit_id], MoveBlockedReason.OCCUPIED)

    # Step 5: entering an enemy ZOC halts further movement (SPEC §8.3). Use the
    # post-pulse positions; the triggering step already succeeded above.
    for unit_id in moved:
        unit = units[unit_id]
        if _has_adjacent_enemy(unit.coord, unit.owner, units, positions):
            mover = movers[unit_id]
            if not mover.stopped:
                mover.stopped = True
                events.append(
                    ZoneOfControlStop(pulse=pulse, unit_id=unit_id, coord=unit.coord)
                )


def _has_adjacent_enemy(
    coord: Axial,
    owner: PlayerId,
    units: dict[UnitId, Unit],
    positions: dict[Axial, UnitId],
) -> bool:
    """Whether any of ``coord``'s six neighbours holds an enemy unit."""
    for neighbor_coord in neighbors(coord):
        occupant = positions.get(neighbor_coord)
        if occupant is not None and units[occupant].owner != owner:
            return True
    return False
