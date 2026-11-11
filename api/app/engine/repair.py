"""Repair phase — resting units recover HP (SPEC §7.3, §11.3 Phase 4).

A unit with a Rest order that took no damage during this turn's execution recovers
HP at one of two tiers (SPEC §7.3):

  * resting anywhere: +1 HP, but only up to 7 HP (no effect at or above 7);
  * resting on settlement ground (`GameState.settlement_ground`: a town or city,
    or in a world a founded site's hex): +2 HP (``GameConfig.settlement_repair``),
    up to the 10 HP maximum.

"Up to" is a ceiling, never a reduction: a unit already above a tier's cap simply
does not heal. A resting unit that was attacked and lost HP this turn does not
repair at all (SPEC §7.3) — the caller supplies the set of units that took damage,
since "damaged" is a fact about the whole execution, not visible from final HP
alone.

``resolve_repair`` is pure: it copies the input state, mutates nothing else, and
performs no I/O.
"""

from __future__ import annotations

from app.engine.events import Event, UnitRepaired
from app.engine.models import GameState, Order, RestOrder, UnitId

# Repair tiers (SPEC §7.3): (heal amount, HP ceiling) resting in the open. The
# settlement tier heals `GameConfig.settlement_repair` (2 in v0.1, 3 in a world,
# WORLD_SPEC §7) up to full HP.
_REST_ANYWHERE = (1, 7)
_SETTLEMENT_CEILING = 10


def resolve_repair(
    state: GameState, orders: list[Order], damaged: set[UnitId]
) -> tuple[GameState, list[Event]]:
    """Heal each undamaged resting unit by its location's tier (SPEC §7.3).

    ``damaged`` is the set of unit ids that lost HP during this turn's execution;
    those units do not repair even if resting. ``state`` is not mutated. Units are
    healed in stored order for a deterministic event log.
    """
    new_state = state.fork()
    events: list[Event] = []

    resting: set[UnitId] = {
        o.unit_id
        for o in orders
        if isinstance(o, RestOrder) and o.unit_id not in damaged
    }

    ground = new_state.ground()
    for unit in new_state.units:
        if unit.id not in resting:
            continue
        in_settlement = ground.settlement_ground(unit.coord)
        amount, ceiling = (
            (new_state.config.settlement_repair, _SETTLEMENT_CEILING)
            if in_settlement
            else _REST_ANYWHERE
        )
        new_hp = min(unit.hp + amount, max(unit.hp, ceiling))
        if new_hp == unit.hp:
            continue
        events.append(
            UnitRepaired(
                unit_id=unit.id,
                hp_before=unit.hp,
                hp_after=new_hp,
                in_settlement=in_settlement,
            )
        )
        unit.hp = new_hp

    return new_state, events
