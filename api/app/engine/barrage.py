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

from app.engine.combat import compute_damage, disorder_penalty
from app.engine.events import BarrageHit, BarrageResolved, Event
from app.engine.hex_coords import Axial, distance
from app.engine.models import (
    BarrageOrder,
    GameState,
    HoldOrder,
    Order,
    Posture,
    Unit,
    UnitId,
)
from app.engine.movement import EngineError
from app.engine.stats import (
    COLUMN_RULES,
    TERRAIN_STATS,
    UNIT_STATS,
    Terrain,
    UnitType,
)


@dataclass(frozen=True)
class _Strike:
    """One unit's pre-applied damage from one barrage, with its modifiers."""

    unit_id: UnitId
    damage: int
    terrain_bonus: int
    hold_bonus: int
    defense_penalty: int


@dataclass(frozen=True)
class _Shot:
    """A single barrage's strikes against the start-of-phase occupants of a hex.

    ``strikes`` lists the deployed occupant first (if any), then stacked columns
    by id; empty when the hex held nobody.
    """

    artillery_id: UnitId
    target: Axial
    strikes: tuple[_Strike, ...]


def resolve_barrage(
    state: GameState, orders: list[Order]
) -> tuple[GameState, list[Event]]:
    """Resolve all Barrage orders simultaneously, returning a new state + events.

    ``state`` is not mutated. Non-Barrage orders are ignored here (movement and
    later phases handle them). Damage from every barrage is computed against the
    occupants present at the *start* of the phase and summed before removal, so
    two barrages on one hex are genuinely simultaneous. A barrage strikes every
    unit in the hex: the deployed occupant with its terrain/Hold bonus (less any
    disorder), and each stacked column at its column penalty with no terrain or
    Hold bonus (WORLD_SPEC §4.6). Raises `EngineError` if a Barrage order
    references an unknown unit, a non-artillery or packed unit, or a target
    outside the unit's range.
    """
    units = {u.id: u.model_copy() for u in state.units}
    hexes = state.map.index
    occupants_at: dict[Axial, list[Unit]] = defaultdict(list)
    for unit in sorted(units.values(), key=_strike_order):
        occupants_at[unit.coord].append(unit)
    hold_ids: set[UnitId] = {o.unit_id for o in orders if isinstance(o, HoldOrder)}

    barrages = sorted(
        (o for o in orders if isinstance(o, BarrageOrder)), key=lambda o: o.unit_id
    )

    # Pass 1: validate and compute each barrage's damage vs. its start occupants.
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
        if artillery.posture is Posture.COLUMN:
            raise EngineError(f"unit {artillery.id} is packed and cannot barrage")
        if (
            distance(artillery.coord, order.target)
            > UNIT_STATS[artillery.type].attack_range
        ):
            raise EngineError(
                f"barrage target {order.target} is out of range for unit {artillery.id}"
            )

        strikes = tuple(
            _strike(occupant, hexes[order.target].terrain, occupant.id in hold_ids)
            for occupant in occupants_at.get(order.target, [])
        )
        for strike in strikes:
            total_damage[strike.unit_id] += strike.damage
        shots.append(_Shot(artillery.id, order.target, strikes))

    # Pass 2: apply summed damage simultaneously, then build events + new state.
    destroyed: set[UnitId] = set()
    for occupant_id, dealt in total_damage.items():
        survivor_hp = max(0, units[occupant_id].hp - dealt)
        units[occupant_id].hp = survivor_hp
        if survivor_hp == 0:
            destroyed.add(occupant_id)

    events: list[Event] = []
    for shot in shots:
        hits = [_hit_record(units[s.unit_id], s, destroyed) for s in shot.strikes]
        events.append(
            BarrageResolved(
                artillery_id=shot.artillery_id,
                target=shot.target,
                hit=hits[0] if hits else None,
                extra_hits=hits[1:],
            )
        )

    new_state = state.fork()
    new_state.units = [units[u.id] for u in state.units if u.id not in destroyed]
    return new_state, events


def _strike_order(unit: Unit) -> tuple[int, UnitId]:
    """Deployed occupant first, then columns, each by id."""
    return (0 if unit.posture is Posture.DEPLOYED else 1, unit.id)


def _strike(occupant: Unit, terrain: Terrain, holding: bool) -> _Strike:
    """Damage one barrage deals to one occupant of the target hex."""
    if occupant.posture is Posture.COLUMN:
        penalty = COLUMN_RULES.column_defense_penalty
        damage = compute_damage(UnitType.ARTILLERY, occupant.type, -penalty)
        return _Strike(occupant.id, damage, 0, 0, penalty)
    terrain_bonus = TERRAIN_STATS[terrain].defense_bonus
    hold_bonus = 1 if holding else 0
    penalty = disorder_penalty(occupant.disordered)
    damage = compute_damage(
        UnitType.ARTILLERY, occupant.type, terrain_bonus + hold_bonus - penalty
    )
    return _Strike(occupant.id, damage, terrain_bonus, hold_bonus, penalty)


def _hit_record(unit: Unit, strike: _Strike, destroyed: set[UnitId]) -> BarrageHit:
    """Build the event record for one struck unit (post-damage HP)."""
    return BarrageHit(
        unit_id=unit.id,
        unit_type=unit.type,
        owner=unit.owner,
        terrain_bonus=strike.terrain_bonus,
        hold_bonus=strike.hold_bonus,
        damage=strike.damage,
        hp_after=unit.hp,
        destroyed=unit.id in destroyed,
        defense_penalty=strike.defense_penalty,
    )
