"""Barrage phase — pre-movement artillery fire (SPEC §7.2, §11.3 Phase 1).

The first phase of execution: every artillery Barrage order resolves
simultaneously, before any movement pulse. A barrage targets a *hex* and damages
whatever unit occupies it at the start of execution (friend or foe — it hits a
hex, not a chosen unit). The occupant is a stationary defender, so terrain and
Hold modifiers apply (SPEC §12.10); artillery fires at range, so there is no
retaliation. Destroyed units are removed before movement begins (SPEC §11.3).

``resolve_barrage`` is pure: it copies the input state, mutates nothing, and
performs no I/O. It validates the engine's structural contract at this boundary —
the order must reference a real artillery unit and target a hex within range —
raising `EngineError`; full order legality/visibility is the Phase 5 boundary's
job (CLAUDE.md: trust invariants, validate at boundaries).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.engine.combat import compute_damage
from app.engine.events import BarrageHit, BarrageResolved, Event
from app.engine.hex_coords import Axial, distance
from app.engine.models import BarrageOrder, GameState, HoldOrder, Order, UnitId
from app.engine.movement import EngineError
from app.engine.stats import TERRAIN_STATS, UNIT_STATS, UnitType


@dataclass(frozen=True)
class _Shot:
    """A single barrage's pre-applied result against the start-of-phase occupant."""

    artillery_id: UnitId
    target: Axial
    occupant_id: UnitId | None
    damage: int
    terrain_bonus: int
    hold_bonus: int


def resolve_barrage(
    state: GameState, orders: list[Order]
) -> tuple[GameState, list[Event]]:
    """Resolve all Barrage orders simultaneously, returning a new state + events.

    ``state`` is not mutated. Non-Barrage orders are ignored here (movement and
    later phases handle them). Damage from every barrage is computed against the
    occupants present at the *start* of the phase and summed before removal, so
    two barrages on one hex are genuinely simultaneous. Raises `EngineError` if a
    Barrage order references an unknown unit, a non-artillery unit, or a target
    outside the unit's range.
    """
    units = {u.id: u.model_copy(deep=True) for u in state.units}
    hexes = {h.coord: h for h in state.map.hexes}
    occupant_at: dict[Axial, UnitId] = {u.coord: u.id for u in units.values()}
    hold_ids: set[UnitId] = {o.unit_id for o in orders if isinstance(o, HoldOrder)}

    barrages = sorted(
        (o for o in orders if isinstance(o, BarrageOrder)), key=lambda o: o.unit_id
    )

    # Pass 1: validate and compute each barrage's damage vs. its start occupant.
    shots: list[_Shot] = []
    total_damage: dict[UnitId, int] = defaultdict(int)
    for order in barrages:
        if order.unit_id not in units:
            raise EngineError(f"barrage references unknown unit {order.unit_id}")
        artillery = units[order.unit_id]
        if artillery.type is not UnitType.ARTILLERY:
            raise EngineError(
                f"unit {artillery.id} is {artillery.type}, cannot barrage"
            )
        if (
            distance(artillery.coord, order.target)
            > UNIT_STATS[artillery.type].attack_range
        ):
            raise EngineError(
                f"barrage target {order.target} is out of range for unit {artillery.id}"
            )

        occupant_id = occupant_at.get(order.target)
        if occupant_id is None:
            shots.append(_Shot(artillery.id, order.target, None, 0, 0, 0))
            continue
        occupant = units[occupant_id]
        terrain_bonus = TERRAIN_STATS[hexes[order.target].terrain].defense_bonus
        hold_bonus = 1 if occupant_id in hold_ids else 0
        damage = compute_damage(
            UnitType.ARTILLERY, occupant.type, terrain_bonus + hold_bonus
        )
        total_damage[occupant_id] += damage
        shots.append(
            _Shot(
                artillery.id,
                order.target,
                occupant_id,
                damage,
                terrain_bonus,
                hold_bonus,
            )
        )

    # Pass 2: apply summed damage simultaneously, then build events + new state.
    destroyed: set[UnitId] = set()
    for occupant_id, dealt in total_damage.items():
        survivor_hp = max(0, units[occupant_id].hp - dealt)
        units[occupant_id].hp = survivor_hp
        if survivor_hp == 0:
            destroyed.add(occupant_id)

    events: list[Event] = []
    for shot in shots:
        hit = None
        if shot.occupant_id is not None:
            occupant = units[shot.occupant_id]
            hit = BarrageHit(
                unit_id=occupant.id,
                unit_type=occupant.type,
                owner=occupant.owner,
                terrain_bonus=shot.terrain_bonus,
                hold_bonus=shot.hold_bonus,
                damage=shot.damage,
                hp_after=occupant.hp,
                destroyed=shot.occupant_id in destroyed,
            )
        events.append(
            BarrageResolved(artillery_id=shot.artillery_id, target=shot.target, hit=hit)
        )

    new_state = state.model_copy(deep=True)
    new_state.units = [units[u.id] for u in state.units if u.id not in destroyed]
    return new_state, events
