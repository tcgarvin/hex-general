"""Tests for the capture phase (roadmap Phase 4; SPEC §14).

Capture is positional: any infantry or armor unit ending execution on a town or
city takes it; artillery cannot; an empty or already-owned settlement does not
change hands.
"""

from __future__ import annotations

from app.engine.capture import resolve_capture
from app.engine.events import SettlementCaptured
from app.engine.hex_coords import Axial
from app.engine.models import GameState, PlayerId
from engine.helpers import build


def _control(state: GameState, q: int, r: int) -> PlayerId | None:
    settlement = state.settlement_at(Axial(q=q, r=r))
    assert settlement is not None
    return settlement.controlled_by


def test_infantry_captures_neutral_town() -> None:
    state = build(
        ["t.."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
    )
    assert _control(state, 0, 0) is None

    new_state, events = resolve_capture(state)

    assert _control(new_state, 0, 0) == "blue"
    assert [e for e in events if isinstance(e, SettlementCaptured)] == [
        SettlementCaptured(
            coord=state.units[0].coord,
            settlement=state.map.hexes[0].terrain,
            captured_by="blue",
            previous_owner=None,
            unit_id=1,
        )
    ]


def test_armor_captures_enemy_city_recording_previous_owner() -> None:
    state = build(
        ["c.."],
        [{"type": "armor", "owner": "blue", "q": 0, "r": 0}],
        settlements=[{"q": 0, "r": 0, "owner": "red"}],
        players=["blue", "red"],
    )
    new_state, events = resolve_capture(state)

    assert _control(new_state, 0, 0) == "blue"
    captured = [e for e in events if isinstance(e, SettlementCaptured)]
    assert len(captured) == 1
    assert captured[0].previous_owner == "red"
    assert captured[0].captured_by == "blue"


def test_artillery_cannot_capture() -> None:
    state = build(
        ["t.."],
        [{"type": "artillery", "owner": "blue", "q": 0, "r": 0}],
    )
    new_state, events = resolve_capture(state)

    assert _control(new_state, 0, 0) is None
    assert events == []


def test_occupying_already_controlled_settlement_is_a_no_op() -> None:
    state = build(
        ["t.."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
    )
    new_state, events = resolve_capture(state)

    assert _control(new_state, 0, 0) == "blue"
    assert events == []


def test_empty_settlement_keeps_its_owner() -> None:
    # No unit on the town; control must not change (no abandonment, SPEC §14.2).
    state = build(
        ["t.c"],
        [{"type": "infantry", "owner": "blue", "q": 1, "r": 0}],
        settlements=[{"q": 0, "r": 0, "owner": "red"}],
        players=["blue", "red"],
    )
    new_state, events = resolve_capture(state)

    assert _control(new_state, 0, 0) == "red"
    assert events == []
