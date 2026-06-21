"""Tests for the repair phase (roadmap Phase 4; SPEC §7.3).

A resting, undamaged unit heals +1 up to 7 HP anywhere, or +2 up to 10 HP on a
town/city. "Up to" is a ceiling, never a reduction; a unit that took damage this
turn, or has no Rest order, does not heal.
"""

from __future__ import annotations

from app.engine.events import UnitRepaired
from app.engine.repair import resolve_repair
from engine.helpers import build, hp_of, move, rest


def test_rest_on_plains_heals_one_up_to_seven() -> None:
    state = build(
        ["..."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0, "hp": 5}],
    )
    new_state, events = resolve_repair(state, [rest(1)], damaged=set())

    assert hp_of(new_state, 1) == 6
    assert events == [
        UnitRepaired(unit_id=1, hp_before=5, hp_after=6, in_settlement=False)
    ]


def test_rest_on_plains_does_not_exceed_seven() -> None:
    state = build(
        ["..."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0, "hp": 7}],
    )
    new_state, events = resolve_repair(state, [rest(1)], damaged=set())

    assert hp_of(new_state, 1) == 7
    assert events == []


def test_rest_in_town_heals_two_up_to_ten() -> None:
    state = build(
        ["t.."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0, "hp": 5}],
    )
    new_state, events = resolve_repair(state, [rest(1)], damaged=set())

    assert hp_of(new_state, 1) == 7
    assert isinstance(events[0], UnitRepaired)
    assert events[0].in_settlement is True


def test_rest_in_city_clamps_to_max_hp() -> None:
    state = build(
        ["c.."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0, "hp": 9}],
    )
    new_state, _ = resolve_repair(state, [rest(1)], damaged=set())

    assert hp_of(new_state, 1) == 10


def test_damaged_resting_unit_does_not_repair() -> None:
    state = build(
        ["..."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0, "hp": 5}],
    )
    new_state, events = resolve_repair(state, [rest(1)], damaged={1})

    assert hp_of(new_state, 1) == 5
    assert events == []


def test_non_resting_unit_does_not_repair() -> None:
    state = build(
        ["..."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0, "hp": 5}],
    )
    # A Move order is not a Rest, so no repair even though the unit is below max.
    new_state, events = resolve_repair(state, [move(1, (1, 0))], damaged=set())

    assert hp_of(new_state, 1) == 5
    assert events == []
