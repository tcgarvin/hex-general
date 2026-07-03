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
from app.engine.models import GameState, PlayerId, Unit, UnitId
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
    # Per-hex closeness bonus toward the nearest capture objective (see
    # `_position_bonus` for why it is a bonus, not a distance penalty). Must
    # dominate the exposure hesitation: with a timid setting the one-turn
    # search swaps advances for safe holds — exposure prices the *cost* of
    # closing on the enemy now while the settlement payoff lands turns later —
    # and the bot stalls and loses on VP (arena-measured, Phase 3).
    advance: float = 0.5
    # Distance at which the closeness bonus bottoms out at zero; also bounds
    # the bonus (advance × cap) a single unit can carry, keeping it comparable
    # to a unit's material value so losing a unit is never a net positive.
    position_cap: float = 20.0
    exposure_in: float = 0.03  # our units' incoming potential damage (penalty)
    exposure_out: float = 0.05  # our potential damage onto visible enemies (bonus)
    # Fraction of a settlement's value at risk when a visible enemy can reach it
    # and no friendly unit garrisons its hex. The §4.4 Hold opponent model makes
    # defence invisible to the one-turn simulation — a simulated enemy never
    # advances, so abandoning a contested settlement reads as free and the
    # searcher redeploys garrisons that the real opponent then punishes
    # (arena-measured). This term makes an unheld, threatened settlement a
    # liability *now*, not only after it is lost.
    settlement_threat: float = 0.6
    # Fraction of a unit's material treated as already lost when the visible
    # enemies able to strike its hex can jointly deal at least its remaining HP
    # next turn (the exposure term's kill-threshold big brother — a stand-in
    # for the depth-2 outcome "this unit dies where it stands"). Defaults to
    # off: arena measurement showed positive settings make the searcher cede
    # contested ground wholesale (it flees any massed front) and lose worse.
    # Kept as a knob for the Phase 5 tuner.
    doomed: float = 0.0


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


def _nearest_anchor_distance(unit: Unit, anchors: list[Axial]) -> int | None:
    """Distance to the nearest anchor, or ``None`` when there are no anchors."""
    if not anchors:
        return None
    return min(distance(unit.coord, coord) for coord in anchors)


def _closeness_bonus(dist: int, weights: EvalWeights) -> float:
    """Closeness bonus at ``dist`` hexes from a unit's anchor (AI_SPEC §4.3).

    Formulated as ``advance × max(0, cap − dist)`` — a *bonus* for being close,
    not a penalty for being far — for two reasons the penalty form gets wrong
    when comparing simulated turn outcomes:

    * a destroyed unit stops paying a distance penalty, so the penalty form
      *rewards* losing distant units; a lost bonus never does, and
    * with anchors fixed per planning turn (see `evaluate`'s ``unit_anchors``)
      a unit standing on the objective it just captured keeps a dist-0 bonus
      instead of being re-measured against the next-farther settlement.
    """
    return weights.advance * max(0.0, weights.position_cap - dist)


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


def _settlement_threat(
    board: BoardIndex, slot: PlayerId, weights: EvalWeights
) -> float:
    """Value of our settlements that are enemy-reachable and ungarrisoned (§4.3).

    A settlement flips only when an enemy ends its move on the hex, so a
    friendly unit standing on it blocks capture outright. For each settlement
    we control with a visible enemy inside its reach (enemy move + 1) and no
    garrison on the hex, its weighted value counts as at-risk.
    """
    at_risk = 0.0
    for settlement in board.controlled_settlements(slot):
        if settlement.coord in board.unit_by_coord:
            continue  # garrisoned: cannot be captured without winning combat
        threatened = any(
            distance(enemy.coord, settlement.coord) <= UNIT_STATS[enemy.type].move + 1
            for enemy in board.enemy_units
        )
        if threatened:
            terrain = board.hex_by_coord[settlement.coord].terrain
            value = weights.city if terrain is Terrain.CITY else weights.town
            at_risk += value
    return at_risk


def _exposure_terms(board: BoardIndex, slot: PlayerId) -> tuple[float, float, float]:
    """Return (incoming, outgoing, doomed-material) totals (AI_SPEC §4.3).

    ``incoming`` sums, over our units, the damage every visible enemy that could
    reach it would deal; ``outgoing`` sums, over visible enemies, the damage every
    one of our units that could reach it would deal. ``doomed`` sums the material
    of our units whose accumulated incoming potential meets their remaining HP —
    units that die where they stand if the visible force commits.
    """
    incoming = 0.0
    doomed = 0.0
    outgoing = 0.0
    for own in board.own_units:
        unit_incoming = 0
        for enemy in board.enemy_units:
            if _can_strike(enemy, own.coord):
                unit_incoming += _potential_damage(enemy, own, board)
        incoming += unit_incoming
        if unit_incoming >= own.hp:
            doomed += unit_material(own)
    for enemy in board.enemy_units:
        for own in board.own_units:
            if _can_strike(own, enemy.coord):
                outgoing += _potential_damage(own, enemy, board)
    return incoming, outgoing, doomed


def evaluate(
    state: GameState,
    slot: PlayerId,
    weights: EvalWeights | None = None,
    unit_anchors: dict[UnitId, Axial] | None = None,
) -> float:
    """Score ``state`` for ``slot``; higher is better (AI_SPEC §4.3).

    ``unit_anchors`` fixes, per unit id, the objective coord the position term
    measures that unit's closeness against. The planner passes its strategic
    assignment (§4.2) computed once from the pre-turn view, which keeps every
    simulated outcome scored against the same targets and keeps the position
    gradient aligned with where each unit is actually being sent — measuring
    against the nearest-of-all objectives instead punishes a unit for marching
    toward an assigned objective that is not its closest, and re-deriving
    anchors from the simulated state punishes capturing (a captured settlement
    leaves the target set, re-measuring nearby units against farther ones).
    Units without an anchor (e.g. units recruited inside a simulation)
    contribute no position term. When omitted, every combat unit measures
    against its nearest not-ours settlement in ``state`` (single-state scoring).
    """
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
    if unit_anchors is not None:
        for unit in board.own_units:
            if is_combat_unit(unit) and unit.id in unit_anchors:
                dist = distance(unit.coord, unit_anchors[unit.id])
                position_term += _closeness_bonus(dist, weights)
    else:
        default_anchors = [
            s.coord for s in state.settlements if s.controlled_by != slot
        ]
        for unit in board.own_units:
            if is_combat_unit(unit):
                dist_opt = _nearest_anchor_distance(unit, default_anchors)
                if dist_opt is not None:
                    position_term += _closeness_bonus(dist_opt, weights)

    incoming, outgoing, doomed = _exposure_terms(board, slot)
    exposure_term = (
        weights.exposure_out * outgoing
        - weights.exposure_in * incoming
        - weights.doomed * doomed
    )

    threat_term = -weights.settlement_threat * _settlement_threat(board, slot, weights)

    return (
        material_term
        + settlement_term
        + economy_term
        + position_term
        + exposure_term
        + threat_term
    )
