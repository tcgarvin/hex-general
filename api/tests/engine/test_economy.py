"""Tests for the economy phase (roadmap Phase 4; SPEC §6.2–6.4).

Each player banks ``income - upkeep`` per turn: town +2 / city +3 income, per-unit
upkeep, a bank that floors at 0 on a shortfall and is capped at its ceiling.
"""

from __future__ import annotations

from app.engine.economy import resolve_economy
from app.engine.events import EconomyResolved, Event
from app.engine.models import GameState
from engine.helpers import build


def _economy(events: list[Event], player: str) -> EconomyResolved:
    match = [e for e in events if isinstance(e, EconomyResolved) and e.player == player]
    assert len(match) == 1
    return match[0]


def _bank(state: GameState, player: str) -> int:
    return next(p.bank for p in state.players if p.id == player)


def test_income_minus_upkeep_is_banked() -> None:
    # Blue controls a city (+3) and a town (+2) and fields one infantry (upkeep 1).
    state = build(
        ["c.t"],
        [{"type": "infantry", "owner": "blue", "q": 1, "r": 0}],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0, "owner": "blue"},
        ],
    )
    new_state, events = resolve_economy(state)

    econ = _economy(events, "blue")
    assert (econ.income, econ.upkeep, econ.net) == (5, 1, 4)
    assert econ.bank_after == 4
    assert _bank(new_state, "blue") == 4


def test_upkeep_shortfall_floors_bank_at_zero() -> None:
    # No controlled settlements; one infantry's upkeep cannot be paid.
    state = build(
        ["..."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        players=[{"id": "blue", "bank": 0}],
    )
    new_state, events = resolve_economy(state)

    econ = _economy(events, "blue")
    assert econ.net == -1
    assert econ.bank_after == 0
    assert _bank(new_state, "blue") == 0


def test_muster_above_cap_is_lost() -> None:
    # Bank starts near the cap; a city's income would overflow it.
    state = build(
        ["c.."],
        [],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 19}],
    )
    assert state.config.muster_bank_cap == 20
    new_state, events = resolve_economy(state)

    econ = _economy(events, "blue")
    assert econ.income == 3
    assert econ.bank_after == 20
    assert econ.lost_to_cap == 2  # 19 + 3 = 22, clamped to 20
    assert _bank(new_state, "blue") == 20


def test_neutral_settlements_yield_no_income() -> None:
    state = build(
        ["c.."],
        [],
        settlements=[{"q": 0, "r": 0}],  # neutral city
        players=[{"id": "blue", "bank": 5}],
    )
    _, events = resolve_economy(state)

    econ = _economy(events, "blue")
    assert econ.income == 0
    assert econ.bank_after == 5
