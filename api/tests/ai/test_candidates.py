"""Tactical candidate tests (AI_SPEC §4.5, §7.2)."""

from __future__ import annotations

from app.ai.board import BoardIndex
from app.ai.candidates import unit_candidates
from app.ai.objectives import Objective, ObjectiveKind
from app.engine.hex_coords import Axial, is_adjacent
from app.engine.models import BarrageOrder, GameState, MoveOrder, Order, RestOrder, Unit
from engine.helpers import build

_NO_ELIM = {"turn_limit": 10, "city_elimination": False}


def _capture(q: int, r: int) -> Objective:
    return Objective(ObjectiveKind.CAPTURE, Axial(q=q, r=r), 6.0)


def _unit(state: GameState, unit_id: int) -> Unit:
    return next(u for u in state.units if u.id == unit_id)


def _moves(orders: list[Order]) -> list[MoveOrder]:
    return [o for o in orders if isinstance(o, MoveOrder)]


def test_advance_path_is_contiguous_passable_and_truncated() -> None:
    state = build(
        ["........"],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board = BoardIndex(state, "blue")
    cands = unit_candidates(board, _unit(state, 1), _capture(7, 0))
    advance = _moves(cands)[0]
    prev = Axial(q=0, r=0)
    for step in advance.path:
        assert is_adjacent(prev, step)
        assert board.passable(step)
        prev = step
    assert len(advance.path) <= 2  # infantry move allowance, no road bonus


def test_road_bonus_only_applies_to_all_road_paths() -> None:
    roaded = build(
        ["......"],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        roads=["######"],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    plain = build(
        ["......"],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board_road = BoardIndex(roaded, "blue")
    board_plain = BoardIndex(plain, "blue")
    road_move = _moves(unit_candidates(board_road, _unit(roaded, 1), _capture(5, 0)))[0]
    plain_move = _moves(unit_candidates(board_plain, _unit(plain, 1), _capture(5, 0)))[
        0
    ]
    assert len(road_move.path) == 3  # move 2 + 1 road bonus
    assert len(plain_move.path) == 2


def test_zoc_blocks_stepping_from_one_zoc_hex_into_another() -> None:
    # Blue starts engaged by red A at (0,0); red B at (3,0) makes (2,0) a ZOC hex.
    # The engaged blue unit must not be able to reach (2,0) (ZOC->ZOC, SPEC §8.4).
    state = build(
        [".........."],
        [
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
            {"type": "infantry", "owner": "red", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 3, "r": 0},
        ],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board = BoardIndex(state, "blue")
    cands = unit_candidates(board, _unit(state, 1), _capture(9, 0))
    for move in _moves(cands):
        assert Axial(q=2, r=0) not in move.path


def test_artillery_barrages_enemy_in_range_and_never_attacks_by_move() -> None:
    state = build(
        ["....."],
        [
            {"type": "artillery", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 2, "r": 0},
        ],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board = BoardIndex(state, "blue")
    cands = unit_candidates(board, _unit(state, 1), _capture(2, 0))
    barrages = [o for o in cands if isinstance(o, BarrageOrder)]
    assert any(b.target == Axial(q=2, r=0) for b in barrages)
    # No attack-by-move: no move order ends on the enemy hex (SPEC §5.4).
    for move in _moves(cands):
        assert move.path[-1] != Axial(q=2, r=0)


def test_artillery_no_barrage_when_no_enemy_in_range() -> None:
    state = build(
        ["........"],
        [
            {"type": "artillery", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 7, "r": 0},
        ],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board = BoardIndex(state, "blue")
    cands = unit_candidates(board, _unit(state, 1), _capture(7, 0))
    assert not any(isinstance(o, BarrageOrder) for o in cands)


def test_rest_only_when_damaged_and_safe() -> None:
    safe = build(
        ["........"],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0, "hp": 4},
            {"type": "infantry", "owner": "red", "q": 7, "r": 0},
        ],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board = BoardIndex(safe, "blue")
    cands = unit_candidates(board, _unit(safe, 1), _capture(7, 0))
    assert any(isinstance(o, RestOrder) for o in cands)

    threatened = build(
        ["...."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0, "hp": 4},
            {"type": "infantry", "owner": "red", "q": 2, "r": 0},
        ],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board_t = BoardIndex(threatened, "blue")
    cands_t = unit_candidates(board_t, _unit(threatened, 1), _capture(2, 0))
    assert not any(isinstance(o, RestOrder) for o in cands_t)
