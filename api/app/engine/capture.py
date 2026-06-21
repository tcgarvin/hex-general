"""Capture phase — settlement control changes (SPEC §14, §11.3 Phase 3).

The first end-of-turn phase: after movement and combat settle, every town or city
occupied by an infantry or armor unit flips to that unit's owner (SPEC §14.1).
Artillery cannot capture (SPEC §5.4), and an empty settlement is never neutralised
— control changes only by an eligible unit *taking* it, never by abandonment
(SPEC §14.2). A unit already standing on a settlement it controls produces no
change and no event.

``resolve_capture`` is pure: it copies the input state, mutates nothing, and
performs no I/O. It runs before the economy phase so a settlement captured this
turn contributes its income immediately (SPEC §11.3 ordering).
"""

from __future__ import annotations

from app.engine.events import Event, SettlementCaptured
from app.engine.models import GameState
from app.engine.stats import UNIT_STATS


def resolve_capture(state: GameState) -> tuple[GameState, list[Event]]:
    """Flip every settlement occupied by an enemy-of-the-owner capturer.

    Capture depends only on final unit positions, not on orders: any infantry or
    armor unit ending execution on a town/city captures it. ``state`` is not
    mutated. Settlements are processed in stored order for a deterministic log.
    """
    new_state = state.model_copy(deep=True)
    events: list[Event] = []

    terrain_at = {h.coord: h.terrain for h in new_state.map.hexes}
    occupant_at = {u.coord: u for u in new_state.units}

    for settlement in new_state.settlements:
        occupant = occupant_at.get(settlement.coord)
        if occupant is None or not UNIT_STATS[occupant.type].can_capture:
            continue
        if settlement.controlled_by == occupant.owner:
            continue

        events.append(
            SettlementCaptured(
                coord=settlement.coord,
                settlement=terrain_at[settlement.coord],
                captured_by=occupant.owner,
                previous_owner=settlement.controlled_by,
                unit_id=occupant.id,
            )
        )
        settlement.controlled_by = occupant.owner

    return new_state, events
