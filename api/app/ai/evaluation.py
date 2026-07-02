"""Static evaluation of a game state from one slot's perspective (AI_SPEC §4.3).

`evaluate(state, slot)` returns a scalar: higher is better for ``slot``. It is the
objective the search maximises, and the direction-giver for the one-turn
simulation lookahead. A decided victory dominates every other term. All other
terms are "own minus enemy" so the function is roughly antisymmetric between the
two slots (`evaluate(s, blue) ≈ −evaluate(s, red)` on mirrored terms), which the
tests assert.

Weights live in `EvalWeights` with hand-set v1 defaults (AI_SPEC §2.4 leaves
gradient-free tuning to the optional Phase 5). Under fog the ``state`` is already
redacted, so "enemy" means *visible* enemy — acceptable and inherent (§4.3).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.ai.board import BoardIndex, is_combat_unit, unit_material
from app.engine.combat import compute_damage
from app.engine.hex_coords import Axial, distance
from app.engine.models import GameState, PlayerId, Unit
from app.engine.stats import TERRAIN_STATS, UNIT_STATS, Terrain, UnitType
from app.engine.victory import victory_status

_VICTORY_BIG = 1_000_000.0


class EvalWeights(BaseModel):
    """Tunable weights for `evaluate` (AI_SPEC §4.3), with v1 hand-set defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    victory_big: float = _VICTORY_BIG
    material: float = 1.0
    city: float = 9.0
    town: float = 3.0
    economy: float = 0.1
    advance: float = 0.35  # per-hex penalty toward nearest capture objective
    exposure_in: float = 0.06  # our units' incoming potential damage (penalty)
    exposure_out: float = 0.05  # our potential damage onto visible enemies (bonus)


def _settlement_value(
    board: BoardIndex, owner: PlayerId, weights: EvalWeights
) -> float:
    """Weighted value of the settlements ``owner`` controls (city 9 / town 3)."""
    total = 0.0
    for settlement in board.controlled_settlements(owner):
        terrain = board.hex_by_coord[settlement.coord].terrain
        total += weights.city if terrain is Terrain.CITY else weights.town
    return total


def _material_value(units: list[Unit]) -> float:
    """Cost-weighted HP-scaled material of a unit list (AI_SPEC §4.3)."""
    return sum(unit_material(u) for u in units)


def _nearest_capture_distance(board: BoardIndex, unit: Unit, slot: PlayerId) -> int:
    """Hex distance from ``unit`` to the nearest settlement it does not control."""
    targets = [s.coord for s in board.view.settlements if s.controlled_by != slot]
    if not targets:
        return 0
    return min(distance(unit.coord, coord) for coord in targets)


def _can_strike(attacker: Unit, target_coord: Axial) -> bool:
    """Whether ``attacker`` could damage a unit at ``target_coord`` next turn.

    A cheap reachability stand-in (AI_SPEC §4.3 exposure): artillery within its
    barrage range, or a melee unit that could close to an adjacent hex within its
    move allowance (+1 to reach adjacency). Terrain/ZOC are ignored — this is a
    depth-2 approximation, not the real engine.
    """
    dist = distance(attacker.coord, target_coord)
    stats = UNIT_STATS[attacker.type]
    if attacker.type is UnitType.ARTILLERY:
        return dist <= stats.attack_range
    return dist <= stats.move + 1


def _potential_damage(attacker: Unit, defender: Unit, board: BoardIndex) -> int:
    """SPEC §13.1 damage of ``attacker`` onto ``defender`` on its current hex.

    The defender is treated as a stationary occupant, so its terrain defense
    bonus applies (Hold is unknown and omitted).
    """
    terrain = board.hex_by_coord[defender.coord].terrain
    mods = TERRAIN_STATS[terrain].defense_bonus
    return compute_damage(attacker.type, defender.type, mods)


def _exposure_terms(board: BoardIndex, slot: PlayerId) -> tuple[float, float]:
    """Return (incoming, outgoing) potential damage totals (AI_SPEC §4.3).

    ``incoming`` sums, over our units, the damage every visible enemy that could
    reach it would deal; ``outgoing`` sums, over visible enemies, the damage every
    one of our units that could reach it would deal.
    """
    incoming = 0.0
    outgoing = 0.0
    for own in board.own_units:
        for enemy in board.enemy_units:
            if _can_strike(enemy, own.coord):
                incoming += _potential_damage(enemy, own, board)
    for enemy in board.enemy_units:
        for own in board.own_units:
            if _can_strike(own, enemy.coord):
                outgoing += _potential_damage(own, enemy, board)
    return incoming, outgoing


def evaluate(
    state: GameState, slot: PlayerId, weights: EvalWeights | None = None
) -> float:
    """Score ``state`` for ``slot``; higher is better (AI_SPEC §4.3)."""
    weights = weights if weights is not None else EvalWeights()

    status = victory_status(state)
    if status.decided:
        if status.winner == slot:
            return weights.victory_big
        if status.winner is None:
            return 0.0
        return -weights.victory_big

    board = BoardIndex(state, slot)
    enemy_ids = {u.owner for u in board.enemy_units}

    own_material = _material_value(board.own_units)
    enemy_material = _material_value(board.enemy_units)
    material_term = weights.material * (own_material - enemy_material)

    own_settlements = _settlement_value(board, slot, weights)
    enemy_settlements = sum(
        _settlement_value(board, enemy, weights) for enemy in enemy_ids
    )
    settlement_term = own_settlements - enemy_settlements

    own_bank = sum(p.bank for p in state.players if p.id == slot)
    enemy_bank = sum(p.bank for p in state.players if p.id != slot)
    economy_term = weights.economy * (own_bank - enemy_bank)

    position_term = 0.0
    for unit in board.own_units:
        if is_combat_unit(unit):
            position_term -= weights.advance * _nearest_capture_distance(
                board, unit, slot
            )

    incoming, outgoing = _exposure_terms(board, slot)
    exposure_term = weights.exposure_out * outgoing - weights.exposure_in * incoming

    return (
        material_term + settlement_term + economy_term + position_term + exposure_term
    )
