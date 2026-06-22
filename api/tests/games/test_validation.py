"""Tests for the order-validation boundary (roadmap Phase 5).

These prove the API rejects statically-illegal submissions before they are stored:
ownership violations, malformed paths, illegal barrages, and bad recruits.
"""

from __future__ import annotations

import pytest

from app.engine.models import GameState
from app.games.validation import OrderValidationError, validate_submission
from engine.helpers import barrage, build, hold, move, recruit

# A compact board: three cities on the top row (blue holds the outer two, red the
# middle), a river at (2,1), and blue/red infantry plus a blue artillery below.
_TERRAIN = [
    "c.c.c",
    "..~..",
    ".....",
]
_UNITS = [
    {"type": "infantry", "owner": "blue", "q": 1, "r": 2},  # id 1
    {"type": "infantry", "owner": "red", "q": 4, "r": 2},  # id 2
    {"type": "artillery", "owner": "blue", "q": 0, "r": 2},  # id 3
]
_SETTLEMENTS = [
    {"q": 0, "r": 0, "owner": "blue"},
    {"q": 2, "r": 0, "owner": "red"},
    {"q": 4, "r": 0, "owner": "blue"},
]


def _state() -> GameState:
    return build(
        _TERRAIN,
        _UNITS,
        settlements=_SETTLEMENTS,
        players=[{"id": "blue", "bank": 10}, "red"],
    )


def test_valid_submission_passes() -> None:
    validate_submission(_state(), "blue", [move(1, (1, 1), (1, 0))], [])


def test_move_for_enemy_unit_rejected() -> None:
    with pytest.raises(OrderValidationError, match="not owned"):
        validate_submission(_state(), "blue", [move(2, (3, 2))], [])


def test_order_for_unknown_unit_rejected() -> None:
    with pytest.raises(OrderValidationError, match="unknown unit"):
        validate_submission(_state(), "blue", [move(99, (1, 1))], [])


def test_two_orders_for_one_unit_rejected() -> None:
    with pytest.raises(OrderValidationError, match="more than one order"):
        validate_submission(_state(), "blue", [move(1, (1, 1)), hold(1)], [])


def test_noncontiguous_path_rejected() -> None:
    with pytest.raises(OrderValidationError, match="not adjacent"):
        validate_submission(_state(), "blue", [move(1, (3, 0))], [])


def test_off_map_path_rejected() -> None:
    with pytest.raises(OrderValidationError, match="off-map"):
        validate_submission(_state(), "blue", [move(1, (1, 1), (1, 0), (1, -1))], [])


def test_impassable_path_rejected() -> None:
    # (2,1) is a river hex, adjacent to the blue unit at (1,2).
    with pytest.raises(OrderValidationError, match="impassable"):
        validate_submission(_state(), "blue", [move(1, (2, 1))], [])


def test_barrage_by_non_artillery_rejected() -> None:
    with pytest.raises(OrderValidationError, match="cannot barrage"):
        validate_submission(_state(), "blue", [barrage(1, 2, 2)], [])


def test_barrage_out_of_range_rejected() -> None:
    # Artillery at (0,2) -> (4,0) is distance 4, beyond range 3.
    with pytest.raises(OrderValidationError, match="out of range"):
        validate_submission(_state(), "blue", [barrage(3, 4, 0)], [])


def test_valid_barrage_passes() -> None:
    validate_submission(_state(), "blue", [barrage(3, 2, 2)], [])


def test_recruit_for_other_slot_rejected() -> None:
    with pytest.raises(OrderValidationError, match="not owned by caller"):
        validate_submission(_state(), "blue", [], [recruit("red", "infantry", 0, 0)])


def test_recruit_on_non_city_rejected() -> None:
    with pytest.raises(OrderValidationError, match="not a city"):
        validate_submission(_state(), "blue", [], [recruit("blue", "infantry", 1, 1)])


def test_recruit_on_uncontrolled_city_rejected() -> None:
    with pytest.raises(OrderValidationError, match="not a city controlled"):
        validate_submission(_state(), "blue", [], [recruit("blue", "infantry", 2, 0)])


def test_recruit_beyond_bank_rejected() -> None:
    # Two armor cost 6 each = 12, but blue has only 10 banked.
    with pytest.raises(OrderValidationError, match="banked"):
        validate_submission(
            _state(),
            "blue",
            [],
            [recruit("blue", "armor", 0, 0), recruit("blue", "armor", 4, 0)],
        )


def test_recruit_beyond_unit_cap_rejected() -> None:
    state = _state()
    capped = state.model_copy(
        update={"config": state.config.model_copy(update={"unit_cap": 2})}
    )
    with pytest.raises(OrderValidationError, match="unit cap"):
        validate_submission(capped, "blue", [], [recruit("blue", "infantry", 0, 0)])


def test_valid_recruit_passes() -> None:
    validate_submission(_state(), "blue", [], [recruit("blue", "infantry", 0, 0)])
