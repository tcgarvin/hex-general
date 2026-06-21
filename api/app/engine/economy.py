"""Economy phase — muster income and upkeep (SPEC §6.2–6.4, §11.3 Phase 5).

Each player's bank is updated by their net muster for the turn:

    net = gross settlement income - unit upkeep

Gross income sums the income of every settlement the player controls (town +2,
city +3, SPEC §6.2); upkeep sums the upkeep of every unit they own (SPEC §6.3).
A positive net is banked; a negative net (upkeep exceeding income) is paid from
the bank, which floors at 0 — there is no further penalty for a shortfall in v0.1.
The bank also has a ceiling (SPEC §6.4); muster that would exceed it is lost.

``resolve_economy`` is pure: it copies the input state, mutates nothing else, and
performs no I/O. It runs after capture, so a settlement taken this turn counts.
"""

from __future__ import annotations

from collections import defaultdict

from app.engine.events import EconomyResolved, Event
from app.engine.models import GameState, PlayerId
from app.engine.stats import SETTLEMENT_STATS, UNIT_STATS


def resolve_economy(state: GameState) -> tuple[GameState, list[Event]]:
    """Apply income and upkeep to every player's bank (SPEC §6.2–6.4).

    ``state`` is not mutated. One `EconomyResolved` event is emitted per player in
    roster order, so the replay can show each player's muster accounting.
    """
    new_state = state.model_copy(deep=True)
    bank_cap = new_state.config.muster_bank_cap
    terrain_at = {h.coord: h.terrain for h in new_state.map.hexes}

    income: dict[PlayerId, int] = defaultdict(int)
    for settlement in new_state.settlements:
        if settlement.controlled_by is not None:
            terrain = terrain_at[settlement.coord]
            income[settlement.controlled_by] += SETTLEMENT_STATS[terrain].income

    upkeep: dict[PlayerId, int] = defaultdict(int)
    for unit in new_state.units:
        upkeep[unit.owner] += UNIT_STATS[unit.type].upkeep

    events: list[Event] = []
    for player in new_state.players:
        gross = income[player.id]
        spent = upkeep[player.id]
        net = gross - spent
        bank_before = player.bank
        uncapped = max(0, bank_before + net)
        bank_after = min(uncapped, bank_cap)
        events.append(
            EconomyResolved(
                player=player.id,
                income=gross,
                upkeep=spent,
                net=net,
                bank_before=bank_before,
                bank_after=bank_after,
                lost_to_cap=uncapped - bank_after,
            )
        )
        player.bank = bank_after

    return new_state, events
