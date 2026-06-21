"""Tests for the recruitment phase (roadmap Phase 4; SPEC §6.5, §6.1).

A recruit deploys a new unit at an empty, friendly city the player can afford,
while below the unit cap. Any violation of that contract raises `EngineError`
(the API screens requests upstream, so a bad recruit here is a bug).
"""

from __future__ import annotations

import pytest

from app.engine.events import UnitRecruited
from app.engine.movement import EngineError
from app.engine.recruitment import resolve_recruitment
from engine.helpers import at, build, recruit


def test_recruit_deploys_unit_and_spends_bank() -> None:
    state = build(
        ["c.."],
        [],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 5}],
    )
    new_state, events = resolve_recruitment(state, [recruit("blue", "infantry", 0, 0)])

    new_unit_id = at(new_state, 0, 0)
    assert new_unit_id == 1  # first unit on the board gets id 1
    recruited = next(u for u in new_state.units if u.id == new_unit_id)
    assert recruited.type.value == "infantry"
    assert recruited.hp == 10
    assert next(p.bank for p in new_state.players if p.id == "blue") == 2  # 5 - 3
    assert events == [
        UnitRecruited(
            unit_id=1,
            unit_type=recruited.type,
            owner="blue",
            coord=recruited.coord,
            cost=3,
            bank_after=2,
        )
    ]


def test_recruit_continues_id_sequence_and_chains_bank() -> None:
    state = build(
        ["c.c"],
        [{"type": "infantry", "owner": "blue", "q": 1, "r": 0}],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0, "owner": "blue"},
        ],
        players=[{"id": "blue", "bank": 9}],
    )
    new_state, events = resolve_recruitment(
        state,
        [recruit("blue", "infantry", 0, 0), recruit("blue", "infantry", 2, 0)],
    )

    assert {u.id for u in new_state.units} == {1, 2, 3}  # existing 1 + two new
    assert next(p.bank for p in new_state.players if p.id == "blue") == 3  # 9 - 3 - 3
    assert [e.unit_id for e in events if isinstance(e, UnitRecruited)] == [2, 3]


def test_recruit_at_town_is_rejected() -> None:
    state = build(
        ["t.."],
        [],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 5}],
    )
    with pytest.raises(EngineError, match="not a city"):
        resolve_recruitment(state, [recruit("blue", "infantry", 0, 0)])


def test_recruit_at_unfriendly_city_is_rejected() -> None:
    state = build(
        ["c.."],
        [],
        settlements=[{"q": 0, "r": 0, "owner": "red"}],
        players=[{"id": "blue", "bank": 5}, {"id": "red", "bank": 0}],
    )
    with pytest.raises(EngineError, match="not a friendly city"):
        resolve_recruitment(state, [recruit("blue", "infantry", 0, 0)])


def test_recruit_onto_occupied_city_is_rejected() -> None:
    state = build(
        ["c.."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 5}],
    )
    with pytest.raises(EngineError, match="occupied"):
        resolve_recruitment(state, [recruit("blue", "infantry", 0, 0)])


def test_recruit_beyond_unit_cap_is_rejected() -> None:
    state = build(
        ["c.."],
        [{"type": "infantry", "owner": "blue", "q": 1, "r": 0}],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 5}],
    )
    state.config.unit_cap = 1  # already at cap with the one starting unit
    with pytest.raises(EngineError, match="unit cap"):
        resolve_recruitment(state, [recruit("blue", "infantry", 0, 0)])


def test_unaffordable_recruit_is_rejected() -> None:
    state = build(
        ["c.."],
        [],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 2}],  # infantry costs 3
    )
    with pytest.raises(EngineError, match="costs 3 but bank is 2"):
        resolve_recruitment(state, [recruit("blue", "infantry", 0, 0)])
