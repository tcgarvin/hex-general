"""Evaluation-function tests (AI_SPEC §4.3, §7.2)."""

from __future__ import annotations

from app.ai.evaluation import EvalWeights, evaluate
from engine.helpers import build

# City elimination ends a game the instant a side controls no city; disable it
# where a test isolates the non-victory terms on a settlement-light map.
_NO_ELIM = {"turn_limit": 10, "city_elimination": False}


def test_material_advantage_scores_positive() -> None:
    state = build(
        ["......"],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
            {"type": "infantry", "owner": "red", "q": 5, "r": 0},
        ],
        victory=_NO_ELIM,
    )
    # Blue has an extra infantry and no other asymmetry — it should read as ahead.
    assert evaluate(state, "blue") > 0
    assert evaluate(state, "red") < 0


def test_controlling_a_city_beats_controlling_a_town() -> None:
    city = build(
        ["c...."],
        [{"type": "infantry", "owner": "blue", "q": 2, "r": 0}],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    town = build(
        ["t...."],
        [{"type": "infantry", "owner": "blue", "q": 2, "r": 0}],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    assert evaluate(city, "blue") > evaluate(town, "blue")


def test_decided_victory_dominates_material() -> None:
    # Blue holds the only city; red controls none -> city-elimination win for blue,
    # regardless of red's larger material.
    state = build(
        ["c....."],
        [
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
            {"type": "armor", "owner": "red", "q": 3, "r": 0},
            {"type": "armor", "owner": "red", "q": 4, "r": 0},
        ],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue"}, {"id": "red"}],
    )
    weights = EvalWeights()
    assert evaluate(state, "blue") == weights.victory_big
    assert evaluate(state, "red") == -weights.victory_big


def test_antisymmetry_on_own_minus_enemy_terms() -> None:
    # Material/settlement/economy are strictly own-minus-enemy, so on those terms
    # evaluate(s, blue) == -evaluate(s, red). Position/exposure are per-side (not
    # antisymmetric), so they are zeroed for this check (AI_SPEC §4.3 "mirrored").
    state = build(
        ["c...t"],
        [
            {"type": "armor", "owner": "blue", "q": 1, "r": 0},
            {"type": "infantry", "owner": "red", "q": 3, "r": 0},
        ],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 4, "r": 0, "owner": "red"},
        ],
        players=[{"id": "blue", "bank": 4}, {"id": "red", "bank": 1}],
        victory=_NO_ELIM,
    )
    weights = EvalWeights(advance=0.0, exposure_in=0.0, exposure_out=0.0)
    blue = evaluate(state, "blue", weights)
    red = evaluate(state, "red", weights)
    assert blue != 0.0
    assert abs(blue + red) < 1e-9
