"""Behavioral micro-scenario tests for `ScriptedPolicy` (AI_SPEC §7.2).

Small hand-built states, asserting on the *chosen* plan and tolerant to the exact
path taken. Covers §7.2 scenarios 1 (advance/attack a lone town), 2 (artillery
barrages the highest-value target), 3 (a wounded engaged unit is not sent
ZOC→ZOC), and 5 (recruit with a rich bank + free city). Scenario 4 (anti-self-jam
via refinement) belongs to `SearchPolicy` and is tested in Phase 3.

Plans are validated for static legality here too, so every scenario doubles as a
legality check on `ScriptedPolicy`.
"""

from __future__ import annotations

from app.ai.board import BoardIndex
from app.ai.candidates import unit_candidates
from app.ai.objectives import assign_objectives
from app.ai.policy import BotPlan
from app.ai.scripted import ScriptedPolicy
from app.engine.hex_coords import Axial, distance
from app.engine.models import BarrageOrder, MoveOrder, Order
from app.games.validation import validate_submission
from engine.helpers import build

_NO_ELIM = {"turn_limit": 20, "city_elimination": False}


def _order_for(plan: BotPlan, unit_id: int) -> Order:
    return next(o for o in plan.orders if o.unit_id == unit_id)


def test_superior_force_advances_on_lone_enemy_town() -> None:
    # Red holds a town at (4,0) with a lone defender; blue has an armor and an
    # infantry nearby. The armor's plan should head onto or up to the town.
    town = Axial(q=4, r=0)
    state = build(
        ["....t", "....."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 0, "r": 1},
            {"type": "infantry", "owner": "red", "q": 4, "r": 0},
        ],
        settlements=[{"q": 4, "r": 0, "owner": "red"}],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    plan = ScriptedPolicy().plan(state, "blue")
    validate_submission(state, "blue", plan.orders, plan.recruitments)
    armor_order = _order_for(plan, 1)
    assert isinstance(armor_order, MoveOrder)
    dest = armor_order.path[-1]
    # Ends on the town (attack-by-move) or adjacent to it, and strictly closer.
    assert distance(dest, town) <= 1
    assert distance(dest, town) < distance(Axial(q=0, r=0), town)


def test_artillery_barrages_highest_value_target() -> None:
    # Artillery + infantry spotter face two enemies in range: an infantry at (2,0)
    # and a costlier armor at (3,0). The gun should barrage the armor.
    state = build(
        [".....", ".....", "....."],
        [
            {"type": "artillery", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 0, "r": 1},
            {"type": "infantry", "owner": "red", "q": 2, "r": 0},
            {"type": "armor", "owner": "red", "q": 3, "r": 0},
        ],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    plan = ScriptedPolicy().plan(state, "blue")
    validate_submission(state, "blue", plan.orders, plan.recruitments)
    gun_order = _order_for(plan, 1)
    assert isinstance(gun_order, BarrageOrder)
    assert gun_order.target == Axial(q=3, r=0)


def test_wounded_engaged_unit_is_not_sent_zoc_to_zoc() -> None:
    # A wounded blue infantry engaged by a red unit, with a safe hex behind it.
    # A withdraw candidate must be offered, and the chosen order must not be a
    # wasteful move onto another ZOC hex (SPEC §8.4).
    state = build(
        [".........."],
        [
            {"type": "infantry", "owner": "blue", "q": 2, "r": 0, "hp": 2},
            {"type": "infantry", "owner": "red", "q": 3, "r": 0},
            {"type": "infantry", "owner": "red", "q": 5, "r": 0},
        ],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    board = BoardIndex(state, "blue")
    assignment = assign_objectives(board, "blue")
    unit = next(u for u in board.own_units if u.id == 1)
    cands = unit_candidates(board, unit, assignment[1])
    # A one-step withdraw to a non-ZOC hex is available and offered.
    withdraws = [
        o
        for o in cands
        if isinstance(o, MoveOrder)
        and len(o.path) == 1
        and not board.adjacent_enemy(o.path[-1], "blue")
    ]
    assert withdraws, "a withdraw candidate should be offered"

    plan = ScriptedPolicy().plan(state, "blue")
    validate_submission(state, "blue", plan.orders, plan.recruitments)
    chosen = _order_for(plan, 1)
    if isinstance(chosen, MoveOrder):
        dest = chosen.path[-1]
        enemy_hex = dest in board.unit_by_coord
        # Not a ZOC→ZOC waste: a non-attack move must land clear of enemy ZOC.
        assert enemy_hex or not board.adjacent_enemy(dest, "blue")


def test_rich_bank_and_free_city_recruits() -> None:
    state = build(
        ["c............"],
        [
            {"type": "infantry", "owner": "blue", "q": 2, "r": 0},
            {"type": "infantry", "owner": "red", "q": 12, "r": 0},
        ],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 12}, {"id": "red"}],
        victory=_NO_ELIM,
    )
    plan = ScriptedPolicy().plan(state, "blue")
    validate_submission(state, "blue", plan.orders, plan.recruitments)
    assert plan.recruitments
    assert all(r.coord == Axial(q=0, r=0) for r in plan.recruitments)
