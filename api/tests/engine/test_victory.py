"""Tests for the victory check (roadmap Phase 4; SPEC §15).

City elimination (§15.1) is checked before the turn limit (§15.2): a player with
zero cities is eliminated, and once a single city-holder remains they win. At the
turn limit, settlements are scored (town 1, city 3) and the top score wins; ties
are draws.
"""

from __future__ import annotations

from app.engine.victory import VictoryReason, victory_status
from engine.helpers import build


def test_game_is_ongoing_while_both_hold_a_city() -> None:
    state = build(
        ["c.c"],
        [],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0, "owner": "red"},
        ],
        players=["blue", "red"],
    )
    status = victory_status(state)

    assert status.reason is VictoryReason.ONGOING
    assert status.winner is None
    assert status.eliminated == []
    assert not status.decided


def test_city_elimination_yields_a_winner() -> None:
    # Red controls only a town (no city); blue controls a city → red is out.
    state = build(
        ["c.t"],
        [],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0, "owner": "red"},
        ],
        players=["blue", "red"],
    )
    status = victory_status(state)

    assert status.reason is VictoryReason.CITY_ELIMINATION
    assert status.winner == "blue"
    assert status.eliminated == ["red"]
    assert status.decided


def test_mutual_city_elimination_is_a_draw() -> None:
    state = build(
        ["t.t"],
        [],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0, "owner": "red"},
        ],
        players=["blue", "red"],
    )
    status = victory_status(state)

    assert status.reason is VictoryReason.CITY_ELIMINATION
    assert status.winner is None
    assert set(status.eliminated) == {"blue", "red"}


def test_turn_limit_scores_settlements() -> None:
    # Both hold a city, so elimination does not fire; blue's extra town wins it.
    state = build(
        ["c.c.t"],
        [],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0, "owner": "red"},
            {"q": 4, "r": 0, "owner": "blue"},
        ],
        players=["blue", "red"],
        victory={"turn_limit": 1},
    )
    state.turn = 1
    status = victory_status(state)

    assert status.reason is VictoryReason.TURN_LIMIT
    assert status.winner == "blue"
    assert status.scores == {"blue": 4, "red": 3}


def test_turn_limit_tie_is_a_draw() -> None:
    state = build(
        ["c.c"],
        [],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0, "owner": "red"},
        ],
        players=["blue", "red"],
        victory={"turn_limit": 1},
    )
    state.turn = 1
    status = victory_status(state)

    assert status.reason is VictoryReason.TURN_LIMIT
    assert status.winner is None
    assert status.scores == {"blue": 3, "red": 3}


def test_resignation_leaves_last_player_standing_the_winner() -> None:
    # Both still hold a city, so no board condition fires — red's concession decides
    # it, awarding blue the win regardless of the scenario's other conditions.
    state = build(
        ["c.c"],
        [],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0, "owner": "red"},
        ],
        players=["blue", "red"],
    )
    state.resigned = ["red"]
    status = victory_status(state)

    assert status.reason is VictoryReason.RESIGNATION
    assert status.winner == "blue"
    assert status.eliminated == ["red"]
    assert status.decided


def test_mutual_resignation_is_a_draw() -> None:
    state = build(
        ["c.c"],
        [],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0, "owner": "red"},
        ],
        players=["blue", "red"],
    )
    state.resigned = ["blue", "red"]
    status = victory_status(state)

    assert status.reason is VictoryReason.RESIGNATION
    assert status.winner is None
    assert set(status.eliminated) == {"blue", "red"}


def test_resignation_decides_even_with_city_elimination_off() -> None:
    state = build(
        ["c.c"],
        [],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0, "owner": "red"},
        ],
        players=["blue", "red"],
        victory={"turn_limit": 10, "city_elimination": False},
    )
    state.resigned = ["blue"]
    status = victory_status(state)

    assert status.reason is VictoryReason.RESIGNATION
    assert status.winner == "red"


def test_city_elimination_can_be_disabled() -> None:
    # Red has no city, but with elimination off the game runs to the turn limit.
    state = build(
        ["c.t"],
        [],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0, "owner": "red"},
        ],
        players=["blue", "red"],
        victory={"turn_limit": 10, "city_elimination": False},
    )
    status = victory_status(state)

    assert status.reason is VictoryReason.ONGOING
    assert status.eliminated == []
