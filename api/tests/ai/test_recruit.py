"""Recruitment-rule tests (AI_SPEC §4.6, §7.2).

The recruiter must only ever emit submissions that pass `validate_submission`
(legal city, affordability accumulating across recruits, unit cap, one per city)
and, played forward across turns, steer the force toward the target mix.
"""

from __future__ import annotations

from app.ai.board import BoardIndex
from app.ai.recruit import plan_recruitment
from app.engine.hex_coords import Axial
from app.engine.models import GameState, Unit
from app.engine.stats import UNIT_STATS, UnitType
from app.games.validation import validate_submission
from engine.helpers import build

_NO_ELIM = {"turn_limit": 20, "city_elimination": False}


def test_recruits_are_statically_legal() -> None:
    # Rich bank, one controlled city out of enemy reach -> a legal recruit.
    state = build(
        ["c............"],
        [{"type": "infantry", "owner": "blue", "q": 1, "r": 0}],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 12}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board = BoardIndex(state, "blue")
    recruits = plan_recruitment(board, "blue")
    assert recruits
    # Never raises: the whole submission is statically legal.
    validate_submission(state, "blue", [], recruits)


def test_occupied_city_is_skipped() -> None:
    # The only city is occupied by our own unit -> no recruit there (would fail the
    # engine's occupancy check, SPEC §6.5).
    state = build(
        ["c............"],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 12}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board = BoardIndex(state, "blue")
    assert plan_recruitment(board, "blue") == []


def test_bank_accumulation_is_respected() -> None:
    # Two controlled cities but only enough bank for one infantry (cost 3, bank 4):
    # the recruiter must not spend the same coin twice.
    state = build(
        ["c......c......"],
        [{"type": "infantry", "owner": "blue", "q": 3, "r": 0}],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 7, "r": 0, "owner": "blue"},
        ],
        players=[{"id": "blue", "bank": 4}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board = BoardIndex(state, "blue")
    recruits = plan_recruitment(board, "blue")
    spent = sum(UNIT_STATS[r.type].recruit_cost for r in recruits)
    assert spent <= 4
    validate_submission(state, "blue", [], recruits)


def test_unit_cap_is_respected() -> None:
    # A tiny cap the roster already meets -> no recruit even with a full bank.
    state = build(
        ["c............"],
        [
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 2, "r": 0},
        ],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 20}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board = BoardIndex(state, "blue")
    board.view.config.unit_cap = 2
    assert plan_recruitment(board, "blue") == []


def test_composition_converges_toward_target_mix() -> None:
    # Start with an all-infantry force and a permanent income stream; recruiting
    # over many turns should introduce armor and artillery (never stay 100%
    # infantry), moving toward the ~50/30/20 target.
    state = build(
        ["c............"],
        [{"type": "infantry", "owner": "blue", "q": 1, "r": 0}],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 20}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    counts = {t: 0 for t in UnitType}
    counts[UnitType.INFANTRY] = 1
    bank = 20
    # Simulate several recruit-only turns: apply each pick, refill the bank.
    for _ in range(8):
        board = BoardIndex(state, "blue")
        # Reflect the accumulated roster on the board for the share calculation.
        board.own_units = _synthetic_units(counts)
        recruits = plan_recruitment(board, "blue")
        if not recruits:
            bank = min(20, bank + 6)
            _set_bank(state, bank)
            continue
        for r in recruits:
            counts[r.type] += 1
            bank -= UNIT_STATS[r.type].recruit_cost
        bank = min(20, bank + 6)
        _set_bank(state, bank)
    assert counts[UnitType.ARMOR] >= 1
    assert counts[UnitType.ARTILLERY] >= 1


def _synthetic_units(counts: dict[UnitType, int]) -> list[Unit]:
    """A roster of `Unit`s matching ``counts`` (positions irrelevant here)."""
    units: list[Unit] = []
    uid = 1
    for unit_type, n in counts.items():
        for _ in range(n):
            units.append(
                Unit(
                    id=uid,
                    type=unit_type,
                    owner="blue",
                    coord=Axial(q=50 + uid, r=50),
                    hp=UNIT_STATS[unit_type].max_hp,
                )
            )
            uid += 1
    return units


def _set_bank(state: GameState, amount: int) -> None:
    for player in state.players:
        if player.id == "blue":
            player.bank = amount
