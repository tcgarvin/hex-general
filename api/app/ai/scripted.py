"""ScriptedPolicy — Tier 0: strategic + tactical layers, no simulation (§2.2, §10).

Composes the three rule-based pieces into a full turn:

1. `plan_recruitment` — spend muster before planning (recruits don't act).
2. `assign_objectives` — give every unit a strategic target.
3. `unit_candidates` — generate each unit's ordered candidates and take the top.

Fast, deterministic, and debuggable; with no engine simulation it is exactly the
starting plan that `SearchPolicy` refines (AI_SPEC §4.7). Every plan it returns is
statically legal.
"""

from __future__ import annotations

from app.ai.board import BoardIndex
from app.ai.candidates import unit_candidates
from app.ai.objectives import assign_objectives
from app.ai.policy import BotPlan
from app.ai.recruit import plan_recruitment
from app.engine.hex_coords import Axial
from app.engine.models import GameState, Order, PlayerId


class ScriptedPolicy:
    """Top-candidate-per-unit planner (AI_SPEC Tier 0)."""

    name = "scripted"

    def plan(self, view: GameState, slot: PlayerId) -> BotPlan:
        board = BoardIndex(view, slot)
        recruitments = plan_recruitment(board, slot)
        # Hold recruit cities open so no friendly unit paths onto them this turn
        # (recruitment fails on an occupied city, SPEC §6.5).
        reserved: frozenset[Axial] = frozenset(r.coord for r in recruitments)
        assignment = assign_objectives(board, slot)

        orders: list[Order] = []
        for unit in board.own_units:
            candidates = unit_candidates(board, unit, assignment[unit.id], reserved)
            orders.append(candidates[0])
        return BotPlan(orders=orders, recruitments=recruitments)
