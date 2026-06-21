"""Simultaneous combat resolution (SPEC §12, §13; roadmap Phase 3).

This is the pure math at the heart of the engine: given the units contesting a
single hex during one movement pulse, it computes simultaneous damage, removes
nobody (the caller applies the outcome), and decides who — if anyone — may occupy
the hex. It performs no I/O and reads only the static stat tables, so it is fully
unit-testable headless.

Damage model (SPEC §13.1, §12.2, §12.4): every participant deals its full attack
value to *every* opposing unit, simultaneously. The only asymmetry is terrain and
Hold: those modifiers protect a **stationary defender** occupying the contested
hex (SPEC §12.10) and apply against every attacker striking it; moving attackers
never receive them. A lone defender dogpiled by several attackers therefore
retaliates against all of them (SPEC §12.4).

Occupation (SPEC §12.5/§12.6/§12.8): a unit enters the contested hex only if all
opposition to its side is destroyed and the side's deterministically designated
*lead entrant* survived. Otherwise nobody new enters.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine.events import CombatRole
from app.engine.hex_coords import Axial
from app.engine.models import Hex, Unit, UnitId
from app.engine.stats import TERRAIN_STATS, UNIT_STATS, UnitType

# Lead-entrant type precedence: armor before infantry before artillery (SPEC §12.6).
_TYPE_PRIORITY: dict[UnitType, int] = {
    UnitType.ARMOR: 0,
    UnitType.INFANTRY: 1,
    UnitType.ARTILLERY: 2,
}

_MIN_DAMAGE = 1
_MAX_DAMAGE = 5


def compute_damage(
    attacker_type: UnitType, defender_type: UnitType, defender_mods: int
) -> int:
    """Return one unit's damage to another (SPEC §13.1).

    ``raw = attack - defense - defender_mods``, clamped to ``1..5``.
    ``defender_mods`` is terrain + Hold for a stationary defender, else ``0``.
    This is the single home for the formula; the optional §13.2 HP-scaling
    variant (deferred to Phase 10) would change only this function.
    """
    raw = UNIT_STATS[attacker_type].attack - UNIT_STATS[defender_type].defense
    raw -= defender_mods
    return max(_MIN_DAMAGE, min(_MAX_DAMAGE, raw))


def defender_modifiers(hex_: Hex, has_hold_order: bool) -> int:
    """Terrain + Hold defensive bonus for a stationary defender (SPEC §12.10, §13.1).

    Applied only by the contest resolver to the hex's stationary occupant — never
    to a moving attacker.
    """
    bonus = TERRAIN_STATS[hex_.terrain].defense_bonus
    if has_hold_order:
        bonus += 1
    return bonus


def lead_entrant(units: list[Unit]) -> UnitId:
    """Pick the deterministic lead entrant of a side (SPEC §12.6).

    Tie-break: armor > infantry > artillery, then higher current HP, then lower
    unit id. ``units`` must be non-empty.
    """
    chosen = min(
        units,
        key=lambda u: (_TYPE_PRIORITY[u.type], -u.hp, u.id),
    )
    return chosen.id


@dataclass(frozen=True)
class UnitCombatResult:
    """One unit's role, modifiers, damage, and fate in a resolved contest."""

    role: CombatRole
    terrain_bonus: int
    hold_bonus: int
    damage_dealt: int
    damage_taken: int
    hp_after: int
    destroyed: bool


@dataclass(frozen=True)
class ContestOutcome:
    """The result of one contest: per-unit results and the occupation outcome.

    ``entrant_id`` is the surviving lead entrant that may occupy the hex, or
    ``None`` if nobody new enters (opposing survivors remain, or every entrant
    died — SPEC §12.5). ``results`` is keyed by unit id and covers every
    participant (the stationary defender, if any, plus all attackers).
    """

    results: dict[UnitId, UnitCombatResult]
    defender_id: UnitId | None
    entrant_id: UnitId | None


def resolve_contest(
    coord: Axial,
    defender: Unit | None,
    attackers: list[Unit],
    hold_orders: set[UnitId],
    hexes: dict[Axial, Hex],
) -> ContestOutcome:
    """Resolve one contested hex into simultaneous damage + an occupation outcome.

    ``defender`` is the stationary occupant of ``coord`` (``None`` for a contest
    over an empty hex). ``attackers`` are the units moving into ``coord`` — they
    must include at least one unit opposing every other side (the caller filters
    friendly-only collisions out as traffic jams, SPEC §8.2). ``hold_orders``
    holds the ids of units with a Hold order, used only for the defender's bonus.
    """
    participants: list[Unit] = ([defender] if defender is not None else []) + attackers

    defender_mods = 0
    if defender is not None:
        defender_mods = defender_modifiers(hexes[coord], defender.id in hold_orders)

    def mods_for(target: Unit) -> int:
        return defender_mods if target is defender else 0

    # Simultaneous damage: every unit hits every opposing unit at full value.
    dealt: dict[UnitId, int] = {u.id: 0 for u in participants}
    taken: dict[UnitId, int] = {u.id: 0 for u in participants}
    for source in participants:
        for target in participants:
            if target.owner == source.owner:
                continue
            damage = compute_damage(source.type, target.type, mods_for(target))
            dealt[source.id] += damage
            taken[target.id] += damage

    hp_after: dict[UnitId, int] = {
        u.id: max(0, u.hp - taken[u.id]) for u in participants
    }
    destroyed: set[UnitId] = {uid for uid, hp in hp_after.items() if hp == 0}

    entrant_id = _decide_entrant(defender, attackers, destroyed)

    results: dict[UnitId, UnitCombatResult] = {}
    for unit in participants:
        is_defender = unit is defender
        results[unit.id] = UnitCombatResult(
            role=CombatRole.DEFENDER if is_defender else CombatRole.ATTACKER,
            terrain_bonus=(
                TERRAIN_STATS[hexes[coord].terrain].defense_bonus if is_defender else 0
            ),
            hold_bonus=(1 if is_defender and unit.id in hold_orders else 0),
            damage_dealt=dealt[unit.id],
            damage_taken=taken[unit.id],
            hp_after=hp_after[unit.id],
            destroyed=unit.id in destroyed,
        )

    return ContestOutcome(
        results=results,
        defender_id=defender.id if defender is not None else None,
        entrant_id=entrant_id,
    )


def _decide_entrant(
    defender: Unit | None, attackers: list[Unit], destroyed: set[UnitId]
) -> UnitId | None:
    """Who, if anyone, occupies the contested hex after combat (SPEC §12.5/§12.8).

    Entry requires that exactly one side has survivors, that side is an attacking
    side (the stationary defender never "enters"), and that side's designated
    lead entrant survived.
    """
    participants: list[Unit] = ([defender] if defender is not None else []) + attackers
    surviving_owners = {u.owner for u in participants if u.id not in destroyed}
    if len(surviving_owners) != 1:
        return None
    (winner,) = tuple(surviving_owners)

    winner_attackers = [a for a in attackers if a.owner == winner]
    if not winner_attackers:
        # Only the stationary defender survived; nobody new enters.
        return None

    lead = lead_entrant(winner_attackers)
    return lead if lead not in destroyed else None
