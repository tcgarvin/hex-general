"""Strategic layer: objective scoring + unit assignment (AI_SPEC §4.2).

Two products, both from the fog-redacted view:

* `enumerate_objectives` scores what matters on the map — settlements to *capture*
  (weighted by VP + income, discounted by observed defence) and own settlements to
  *defend* (when a visible enemy is inside their threat radius). Controlling
  exactly one city makes defending it (or taking the enemy's last city)
  overwhelming, since losing your last city loses the game (SPEC §15.1).
* `assign_objectives` greedily matches units to objectives by
  ``value / (1 + distance)``: combat units get a capture/defence target, artillery
  is assigned to *support* the nearest friendly effort (it cannot capture,
  SPEC §14.1). Multiple units may share an objective — that is how focus-fire and
  escorts emerge (§4.2).

These feed the tactical layer (`candidates`): each unit's assigned target coord is
the "advance toward" anchor for its candidate paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.ai.board import BoardIndex, is_combat_unit
from app.engine.hex_coords import Axial, distance
from app.engine.models import PlayerId, Unit
from app.engine.stats import UNIT_STATS, Terrain

# Value = victory points + income weight (AI_SPEC §4.2): city 3+3, town 1+2.
_CAPTURE_BASE: dict[Terrain, float] = {Terrain.CITY: 6.0, Terrain.TOWN: 3.0}
_DEFEND_BASE: dict[Terrain, float] = {Terrain.CITY: 6.0, Terrain.TOWN: 3.0}
# Overwhelming value when a *last city* is at stake (attack or defend, §15.1).
_LAST_CITY = 1000.0


class ObjectiveKind(StrEnum):
    CAPTURE = "capture"
    DEFEND = "defend"
    SUPPORT = "support"  # artillery: escort the nearest friendly effort


@dataclass(frozen=True)
class Objective:
    """A scored point of interest on the map for the planning slot."""

    kind: ObjectiveKind
    coord: Axial
    value: float


def _threat_strength(
    board: BoardIndex, coord: Axial, owner: PlayerId, radius: int
) -> float:
    """Sum of enemy attack power within ``radius`` of ``coord`` (visible only)."""
    total = 0.0
    for enemy in board.enemy_units:
        if enemy.owner != owner and distance(enemy.coord, coord) <= radius:
            total += UNIT_STATS[enemy.type].attack
    return total


def _defenders_on_or_adjacent(board: BoardIndex, coord: Axial, slot: PlayerId) -> float:
    """Enemy attack power sitting on or next to ``coord`` (capture discount)."""
    return _threat_strength(board, coord, slot, radius=1)


def enumerate_objectives(board: BoardIndex, slot: PlayerId) -> list[Objective]:
    """Score capture and defence objectives for ``slot`` (AI_SPEC §4.2)."""
    objectives: list[Objective] = []
    own_cities = board.city_count(slot)

    for settlement in board.view.settlements:
        terrain = board.hex_by_coord[settlement.coord].terrain
        if terrain not in _CAPTURE_BASE:
            continue

        if settlement.controlled_by != slot:
            base = _CAPTURE_BASE[terrain]
            defense = _defenders_on_or_adjacent(board, settlement.coord, slot)
            value = base / (1.0 + 0.5 * defense)
            # Taking an opponent's last city is game-winning: boost it hugely.
            if terrain is Terrain.CITY and settlement.controlled_by is not None:
                if _owner_city_count(board, settlement.controlled_by) == 1:
                    value += _LAST_CITY
            objectives.append(Objective(ObjectiveKind.CAPTURE, settlement.coord, value))
        else:
            threat_radius = _max_enemy_move(board) + 1
            threat = _threat_strength(board, settlement.coord, slot, threat_radius)
            if threat <= 0.0:
                continue
            base = _DEFEND_BASE[terrain]
            value = base * (1.0 + threat / 5.0)
            if terrain is Terrain.CITY and own_cities == 1:
                value += _LAST_CITY
            objectives.append(Objective(ObjectiveKind.DEFEND, settlement.coord, value))

    return objectives


def _owner_city_count(board: BoardIndex, owner: PlayerId) -> int:
    return len(
        [
            s
            for s in board.view.settlements
            if s.controlled_by == owner
            and board.hex_by_coord[s.coord].terrain is Terrain.CITY
        ]
    )


def _max_enemy_move(board: BoardIndex) -> int:
    """Largest move allowance among visible enemies (threat radius basis)."""
    if not board.enemy_units:
        return UNIT_STATS[list(UNIT_STATS)[0]].move
    return max(UNIT_STATS[u.type].move for u in board.enemy_units)


def assign_objectives(board: BoardIndex, slot: PlayerId) -> dict[int, Objective]:
    """Assign every own unit a target objective (AI_SPEC §4.2).

    Combat units are matched greedily to capture/defence objectives by
    ``value / (1 + distance)``; artillery is then assigned a SUPPORT objective at
    the position of the nearest friendly combat effort. Falls back to holding
    position (an objective at the unit's own hex) when no objective exists.
    """
    objectives = enumerate_objectives(board, slot)
    combat = [u for u in board.own_units if is_combat_unit(u)]
    artillery = [u for u in board.own_units if not is_combat_unit(u)]

    assignment: dict[int, Objective] = {}
    _assign_combat(board, combat, objectives, assignment)
    _assign_artillery(board, artillery, combat, assignment)
    return assignment


def _pair_score(unit: Unit, objective: Objective) -> float:
    return objective.value / (1.0 + distance(unit.coord, objective.coord))


def _assign_combat(
    board: BoardIndex,
    combat: list[Unit],
    objectives: list[Objective],
    assignment: dict[int, Objective],
) -> None:
    """Greedy best (unit, objective) pairs until every combat unit is assigned."""
    unassigned = {u.id: u for u in combat}
    if not objectives:
        for unit in combat:
            assignment[unit.id] = Objective(ObjectiveKind.SUPPORT, unit.coord, 0.0)
        return

    while unassigned:
        best_unit_id = -1
        best_obj = objectives[0]
        best_score = float("-inf")
        for unit in unassigned.values():
            for objective in objectives:
                score = _pair_score(unit, objective)
                if score > best_score:
                    best_score, best_unit_id, best_obj = score, unit.id, objective
        assignment[best_unit_id] = best_obj
        del unassigned[best_unit_id]


def _assign_artillery(
    board: BoardIndex,
    artillery: list[Unit],
    combat: list[Unit],
    assignment: dict[int, Objective],
) -> None:
    """Assign each artillery a SUPPORT objective at the nearest friendly effort."""
    for gun in artillery:
        anchor = _support_anchor(gun, combat, assignment)
        assignment[gun.id] = Objective(ObjectiveKind.SUPPORT, anchor, 0.0)


def _support_anchor(
    gun: Unit, combat: list[Unit], assignment: dict[int, Objective]
) -> Axial:
    """Where artillery should position: toward the nearest friendly combat unit's
    objective (so it advances with the army), else the nearest friendly unit, else
    its own hex."""
    if combat:
        nearest = min(combat, key=lambda u: distance(gun.coord, u.coord))
        objective = assignment.get(nearest.id)
        if objective is not None:
            return objective.coord
        return nearest.coord
    return gun.coord
