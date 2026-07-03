"""Tests for the recruitment phase (roadmap Phase 4; SPEC §6.5, §6.1).

A recruit deploys a new unit at an empty, friendly city the player can afford,
while below the unit cap. A *static* violation — an unknown player, or a hex that
is not a city — raises `EngineError` (guaranteed at submission time, so a bug
here). A *dynamic* violation — the city is no longer friendly, the hex is
occupied, the cap is reached, or the bank is short — is voided by the turn itself:
the request is skipped with a `RecruitmentVoided` event, its muster stays banked,
and later requests in the list still apply.
"""

from __future__ import annotations

import pytest

from app.engine.events import (
    RecruitmentVoided,
    RecruitmentVoidedReason,
    UnitRecruited,
)
from app.engine.models import GameState
from app.engine.movement import EngineError
from app.engine.recruitment import resolve_recruitment
from engine.helpers import at, build, recruit


def _bank(state: GameState, owner: str) -> int:
    return next(p.bank for p in state.players if p.id == owner)


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


# --- Static violations still raise (guaranteed at submission time) --------------


def test_recruit_at_town_is_rejected() -> None:
    state = build(
        ["t.."],
        [],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 5}],
    )
    with pytest.raises(EngineError, match="not a city"):
        resolve_recruitment(state, [recruit("blue", "infantry", 0, 0)])


def test_recruit_by_unknown_player_is_rejected() -> None:
    state = build(
        ["c.."],
        [],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 5}],
    )
    with pytest.raises(EngineError, match="unknown player"):
        resolve_recruitment(state, [recruit("green", "infantry", 0, 0)])


# --- Dynamic violations are voided mid-turn (muster stays banked) ----------------


def test_recruit_at_unfriendly_city_is_voided() -> None:
    # The city at (0,0) is red's; blue also holds a friendly city at (2,0). Blue's
    # voided recruit keeps its muster, and the later legal recruit still applies.
    state = build(
        ["c.c"],
        [],
        settlements=[
            {"q": 0, "r": 0, "owner": "red"},
            {"q": 2, "r": 0, "owner": "blue"},
        ],
        players=[{"id": "blue", "bank": 5}, {"id": "red", "bank": 0}],
    )
    new_state, events = resolve_recruitment(
        state,
        [recruit("blue", "infantry", 0, 0), recruit("blue", "infantry", 2, 0)],
    )

    assert at(new_state, 0, 0) is None  # nothing deployed at the unfriendly city
    assert at(new_state, 2, 0) == 1  # the later legal recruit still applied
    assert _bank(new_state, "blue") == 2  # only the applied recruit spent (5 - 3)
    voided = [e for e in events if isinstance(e, RecruitmentVoided)]
    assert len(voided) == 1
    assert voided[0].reason is RecruitmentVoidedReason.CITY_NOT_FRIENDLY
    assert voided[0].owner == "blue"


def test_recruit_onto_occupied_city_is_voided() -> None:
    # A unit ended its move on the city at (0,0); the recruit there is voided, but
    # the recruit at the empty friendly city (2,0) still applies.
    state = build(
        ["c.c"],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0, "owner": "blue"},
        ],
        players=[{"id": "blue", "bank": 5}],
    )
    new_state, events = resolve_recruitment(
        state,
        [recruit("blue", "infantry", 0, 0), recruit("blue", "infantry", 2, 0)],
    )

    assert at(new_state, 2, 0) == 2  # the later legal recruit still applied
    assert _bank(new_state, "blue") == 2  # only the applied recruit spent
    voided = [e for e in events if isinstance(e, RecruitmentVoided)]
    assert len(voided) == 1
    assert voided[0].reason is RecruitmentVoidedReason.HEX_OCCUPIED


def test_recruit_beyond_unit_cap_is_voided() -> None:
    state = build(
        ["c.."],
        [{"type": "infantry", "owner": "blue", "q": 1, "r": 0}],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 5}],
    )
    state.config.unit_cap = 1  # already at cap with the one starting unit

    new_state, events = resolve_recruitment(state, [recruit("blue", "infantry", 0, 0)])

    assert at(new_state, 0, 0) is None  # nothing deployed
    assert _bank(new_state, "blue") == 5  # muster stays banked
    voided = [e for e in events if isinstance(e, RecruitmentVoided)]
    assert len(voided) == 1
    assert voided[0].reason is RecruitmentVoidedReason.UNIT_CAP_REACHED


def test_unaffordable_recruit_is_voided() -> None:
    state = build(
        ["c.."],
        [],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 2}],  # infantry costs 3
    )
    new_state, events = resolve_recruitment(state, [recruit("blue", "infantry", 0, 0)])

    assert at(new_state, 0, 0) is None  # nothing deployed
    assert _bank(new_state, "blue") == 2  # muster stays banked
    voided = [e for e in events if isinstance(e, RecruitmentVoided)]
    assert len(voided) == 1
    assert voided[0].reason is RecruitmentVoidedReason.INSUFFICIENT_MUSTER
