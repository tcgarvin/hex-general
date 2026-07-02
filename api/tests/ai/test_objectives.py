"""Strategic-layer tests: objective scoring + assignment (AI_SPEC §4.2, §7.2)."""

from __future__ import annotations

from app.ai.board import BoardIndex
from app.ai.objectives import ObjectiveKind, assign_objectives, enumerate_objectives
from engine.helpers import build

_NO_ELIM = {"turn_limit": 10, "city_elimination": False}


def test_undefended_nearby_city_outranks_defended_distant_town() -> None:
    # A neutral city two hexes from our unit vs a neutral town far away, ringed by
    # enemies. Our unit should be assigned the city.
    state = build(
        ["..c......t..", "............", "............"],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 9, "r": 1},
            {"type": "armor", "owner": "red", "q": 10, "r": 1},
        ],
        settlements=[{"q": 2, "r": 0}, {"q": 9, "r": 0}],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board = BoardIndex(state, "blue")
    assignment = assign_objectives(board, "blue")
    target = assignment[1]  # blue infantry has unit id 1
    assert target.kind is ObjectiveKind.CAPTURE
    assert target.coord.q == 2 and target.coord.r == 0


def test_last_city_defense_dominates() -> None:
    # Blue controls exactly one city with an enemy adjacent -> the defend objective
    # for that city must be the highest-valued objective by a wide margin.
    state = build(
        ["c.....c"],
        [
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
            {"type": "armor", "owner": "red", "q": 5, "r": 0},
        ],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 6, "r": 0, "owner": "red"},
        ],
        players=[{"id": "blue"}, {"id": "red"}],
    )
    board = BoardIndex(state, "blue")
    objectives = enumerate_objectives(board, "blue")
    top = max(objectives, key=lambda o: o.value)
    assert top.kind is ObjectiveKind.DEFEND
    assert top.coord.q == 0 and top.coord.r == 0
    assert top.value >= 1000.0


def test_artillery_supports_rather_than_captures() -> None:
    state = build(
        ["c....t"],
        [
            {"type": "artillery", "owner": "blue", "q": 1, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 2, "r": 0},
        ],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}, {"q": 5, "r": 0}],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board = BoardIndex(state, "blue")
    assignment = assign_objectives(board, "blue")
    assert assignment[1].kind is ObjectiveKind.SUPPORT  # artillery is unit id 1
