"""Tactical layer: per-unit candidate order generation + pathfinding (AI_SPEC §4.5).

For each own unit this produces a small, ordered set (cap ~8) of plausible orders.
The list is *ordered by preference*, so `ScriptedPolicy` takes ``candidates[0]`` as
its move and `SearchPolicy` explores the rest as coordinate-ascent alternatives.

Pathfinding is a BFS from the unit over passable, unoccupied hexes, bounded by the
unit's move allowance (+1 when the whole path is road, SPEC §3.3) and truncated at
illegal ZOC→ZOC steps against the *current visible* enemy positions (SPEC §8.4) —
the engine would stop the unit there anyway, so planning past it is wasted. Enemy
hexes are never entered by BFS; an attack-by-move is the reached-adjacent path plus
a final step into the enemy hex (SPEC §7, §12.1). Artillery never generates
attack-by-move (SPEC §5.4) — it barrages instead.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from app.ai.board import BoardIndex
from app.ai.objectives import Objective
from app.engine.combat import compute_damage
from app.engine.hex_coords import Axial, distance, neighbors
from app.engine.models import (
    BarrageOrder,
    HoldOrder,
    MoveOrder,
    Order,
    RestOrder,
    Unit,
)
from app.engine.stats import TERRAIN_STATS, UNIT_STATS, UnitType

_MAX_CANDIDATES = 8
_KILL_BONUS = 5.0
# Our opponent model is "all visible enemies Hold" (AI_SPEC §4.4), so a struck
# enemy is a stationary defender getting terrain + Hold: we score attacks/barrages
# against that same assumption to stay consistent with the simulation.
_ENEMY_HOLD_BONUS = 1


@dataclass(frozen=True)
class _Reach:
    """BFS result from a unit: step distance and parent pointers over legal hexes."""

    dist: dict[Axial, int]
    parent: dict[Axial, Axial]


def _reach(board: BoardIndex, unit: Unit, reserved: frozenset[Axial]) -> _Reach:
    """BFS the hexes ``unit`` can legally reach this turn (SPEC §3.3, §3.4, §8.4).

    Steps are bounded by move allowance, extended to move+1 only along an
    all-road path; occupied, impassable, and ``reserved`` hexes are excluded (the
    latter are cities held open for recruitment); an engaged unit may not step
    from one enemy-ZOC hex into another.
    """
    move = UNIT_STATS[unit.type].move
    origin = unit.coord
    origin_road = board.hex_by_coord[origin].road

    dist: dict[Axial, int] = {origin: 0}
    all_road: dict[Axial, bool] = {origin: origin_road}
    parent: dict[Axial, Axial] = {}
    queue: deque[Axial] = deque([origin])

    while queue:
        cur = queue.popleft()
        step = dist[cur]
        if step >= move + 1:
            continue
        cur_engaged = board.adjacent_enemy(cur, unit.owner)
        for nb in neighbors(cur):
            if nb in dist or not board.passable(nb) or nb in board.unit_by_coord:
                continue
            if nb in reserved:
                continue
            if cur_engaged and board.adjacent_enemy(nb, unit.owner):
                continue  # illegal ZOC->ZOC (SPEC §8.4)
            nb_step = step + 1
            nb_all_road = all_road[cur] and board.hex_by_coord[nb].road
            if nb_step == move + 1 and not nb_all_road:
                continue  # move+1 only via an all-road path
            dist[nb] = nb_step
            all_road[nb] = nb_all_road
            parent[nb] = cur
            queue.append(nb)

    return _Reach(dist=dist, parent=parent)


def _path_to(reach: _Reach, goal: Axial) -> list[Axial]:
    """Reconstruct the step path (origin-exclusive) to ``goal`` from BFS parents."""
    path: list[Axial] = []
    cur = goal
    while cur in reach.parent:
        path.append(cur)
        cur = reach.parent[cur]
    path.reverse()
    return path


def _advance_order(
    board: BoardIndex, unit: Unit, objective: Objective, reach: _Reach
) -> MoveOrder | None:
    """A move toward ``objective``: the reachable hex nearest the objective."""
    origin_gap = distance(unit.coord, objective.coord)
    best: Axial | None = None
    best_key = (origin_gap, 0)
    for coord, steps in reach.dist.items():
        if coord == unit.coord:
            continue
        gap = distance(coord, objective.coord)
        key = (gap, steps)
        if key < best_key:
            best_key = key
            best = coord
    if best is None:
        return None
    return MoveOrder(unit_id=unit.id, path=_path_to(reach, best))


def _attack_score(attacker: Unit, defender: Unit, board: BoardIndex) -> float:
    """Net value of a melee attack under the enemy-Hold model (AI_SPEC §4.5)."""
    terrain = board.hex_by_coord[defender.coord].terrain
    mods = TERRAIN_STATS[terrain].defense_bonus + _ENEMY_HOLD_BONUS
    dealt = compute_damage(attacker.type, defender.type, mods)
    retaliation = compute_damage(defender.type, attacker.type, 0)
    lethal = dealt >= defender.hp
    value = float(dealt) - float(retaliation)
    value += 0.5 * UNIT_STATS[defender.type].recruit_cost
    if lethal:
        value += _KILL_BONUS
    return value


def _attack_orders(
    board: BoardIndex, unit: Unit, reach: _Reach
) -> list[tuple[float, MoveOrder]]:
    """Attack-by-move candidates onto the highest-value reachable enemies (§4.5)."""
    move = UNIT_STATS[unit.type].move
    scored: list[tuple[float, MoveOrder]] = []
    for enemy in board.enemy_units:
        approach: Axial | None = None
        approach_steps = move
        for nb in neighbors(enemy.coord):
            steps = reach.dist.get(nb)
            if steps is not None and steps <= move - 1 and steps < approach_steps:
                approach = nb
                approach_steps = steps
        if distance(unit.coord, enemy.coord) == 1:
            approach = unit.coord
            approach_steps = 0
        if approach is None:
            continue
        path = _path_to(reach, approach) + [enemy.coord]
        scored.append(
            (_attack_score(unit, enemy, board), MoveOrder(unit_id=unit.id, path=path))
        )
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:3]


def _withdraw_order(board: BoardIndex, unit: Unit, reach: _Reach) -> MoveOrder | None:
    """A one-step retreat to the best adjacent non-ZOC free hex (SPEC §8.4)."""
    best: Axial | None = None
    best_gap = -1
    for nb in neighbors(unit.coord):
        if reach.dist.get(nb) != 1:
            continue
        if board.adjacent_enemy(nb, unit.owner):
            continue
        # Prefer the retreat hex that maximises distance from the nearest enemy.
        gap = min((distance(nb, e.coord) for e in board.enemy_units), default=0)
        if gap > best_gap:
            best_gap = gap
            best = nb
    if best is None:
        return None
    return MoveOrder(unit_id=unit.id, path=[best])


def _threatened(board: BoardIndex, unit: Unit) -> bool:
    """Whether any visible enemy could strike ``unit``'s hex next turn (§4.5 rest)."""
    for enemy in board.enemy_units:
        reach = UNIT_STATS[enemy.type].attack_range
        if enemy.type is UnitType.ARTILLERY:
            if distance(enemy.coord, unit.coord) <= reach:
                return True
        elif distance(enemy.coord, unit.coord) <= UNIT_STATS[enemy.type].move + 1:
            return True
    return False


def _locally_outnumbered(board: BoardIndex, unit: Unit) -> bool:
    """More adjacent enemies than adjacent friendlies around ``unit`` (§4.5)."""
    adj_enemy = len(
        [e for e in board.enemy_units if distance(e.coord, unit.coord) == 1]
    )
    adj_friend = len(
        [
            f
            for f in board.own_units
            if f.id != unit.id and distance(f.coord, unit.coord) == 1
        ]
    )
    return adj_enemy > adj_friend + 1


def _barrage_orders(board: BoardIndex, unit: Unit) -> list[tuple[float, BarrageOrder]]:
    """Barrage candidates for each visible enemy in range (SPEC §7.2, range 3)."""
    reach = UNIT_STATS[UnitType.ARTILLERY].attack_range
    scored: list[tuple[float, BarrageOrder]] = []
    for enemy in board.enemy_units:
        if distance(unit.coord, enemy.coord) > reach:
            continue
        terrain = board.hex_by_coord[enemy.coord].terrain
        mods = TERRAIN_STATS[terrain].defense_bonus + _ENEMY_HOLD_BONUS
        dealt = compute_damage(UnitType.ARTILLERY, enemy.type, mods)
        value = float(dealt) + 0.5 * UNIT_STATS[enemy.type].recruit_cost
        if dealt >= enemy.hp:
            value += _KILL_BONUS
        scored.append((value, BarrageOrder(unit_id=unit.id, target=enemy.coord)))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def _dedupe(candidates: list[Order]) -> list[Order]:
    """Drop duplicate orders, preserving order (first occurrence wins)."""
    seen: set[str] = set()
    unique: list[Order] = []
    for order in candidates:
        key = order.model_dump_json()
        if key not in seen:
            seen.add(key)
            unique.append(order)
    return unique


def unit_candidates(
    board: BoardIndex,
    unit: Unit,
    objective: Objective,
    reserved: frozenset[Axial] = frozenset(),
) -> list[Order]:
    """Ordered candidate orders for ``unit`` (AI_SPEC §4.5); best first, Hold last.

    The head of the list is the scripted choice; the whole list is the search's
    alternative set. Always includes an advance (when it can move closer) and Hold.
    ``reserved`` hexes (cities held for recruitment) are excluded as move targets.
    """
    reach = _reach(board, unit, reserved)
    scored: list[tuple[float, Order]] = []

    if unit.type is UnitType.ARTILLERY:
        for value, barrage in _barrage_orders(board, unit):
            scored.append((1000.0 + value, barrage))
        advance = _advance_order(board, unit, objective, reach)
        if advance is not None:
            scored.append((500.0, advance))
    else:
        for value, attack in _attack_orders(board, unit, reach):
            base = 1000.0 if value > 0 else 100.0
            scored.append((base + value, attack))
        advance = _advance_order(board, unit, objective, reach)
        if advance is not None:
            scored.append((500.0 + 0.1 * objective.value, advance))
        engaged = board.adjacent_enemy(unit.coord, unit.owner)
        wounded = unit.hp < UNIT_STATS[unit.type].max_hp
        if engaged and (wounded or _locally_outnumbered(board, unit)):
            withdraw = _withdraw_order(board, unit, reach)
            if withdraw is not None:
                scored.append((400.0, withdraw))

    if unit.hp < UNIT_STATS[unit.type].max_hp and not _threatened(board, unit):
        scored.append((300.0, RestOrder(unit_id=unit.id)))

    scored.append((50.0, HoldOrder(unit_id=unit.id)))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return _dedupe([order for _score, order in scored])[:_MAX_CANDIDATES]
