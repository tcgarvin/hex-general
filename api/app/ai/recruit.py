"""Recruitment rules (AI_SPEC §4.6).

Rule-based, applied before order planning (recruits do not act this turn). We
spend banked muster at controlled, unoccupied cities — front cities first — while
affordable and under the unit cap, steering the force toward a rough
infantry-heavy mix (~50% infantry / 30% armor / 20% artillery by count). When the
most-needed type is armor and it is one turn's income away, we bank rather than
buy a cheaper unit.

Every recruit produced here satisfies `validate_submission`: affordability
accumulates across the submission, one recruit per city, unit cap respected.
"""

from __future__ import annotations

from app.ai.board import BoardIndex
from app.engine.hex_coords import Axial, distance
from app.engine.models import PlayerId, Recruitment
from app.engine.stats import SETTLEMENT_STATS, UNIT_STATS, UnitType

# Target force composition by count (AI_SPEC §4.6), tunable.
_TARGET_SHARE: dict[UnitType, float] = {
    UnitType.INFANTRY: 0.5,
    UnitType.ARMOR: 0.3,
    UnitType.ARTILLERY: 0.2,
}
_RESERVE = 0  # never spend the bank below this (a tuning knob)


def _net_income(board: BoardIndex, slot: PlayerId) -> int:
    """This turn's settlement income minus current upkeep for ``slot`` (SPEC §6)."""
    income = 0
    for settlement in board.controlled_settlements(slot):
        terrain = board.hex_by_coord[settlement.coord].terrain
        if terrain in SETTLEMENT_STATS:
            income += SETTLEMENT_STATS[terrain].income
    upkeep = sum(UNIT_STATS[u.type].upkeep for u in board.own_units)
    return income - upkeep


def _enemy_can_reach(board: BoardIndex, coord: Axial) -> bool:
    """Whether any visible enemy could move onto ``coord`` this turn (SPEC §7.1).

    A recruit at a city an enemy can occupy would fail the engine's end-of-turn
    occupancy check, so we only deploy at cities out of enemy reach (rear cities).
    """
    for enemy in board.enemy_units:
        if distance(enemy.coord, coord) <= UNIT_STATS[enemy.type].move + 1:
            return True
    return False


def _front_distance(board: BoardIndex, slot: PlayerId, coord: Axial) -> int:
    """Distance from a city to the nearest enemy / capture objective (front-ness)."""
    targets = [u.coord for u in board.enemy_units] + [
        s.coord for s in board.view.settlements if s.controlled_by != slot
    ]
    if not targets:
        return 0
    return min(distance(coord, t) for t in targets)


def _pick_type(
    counts: dict[UnitType, int], total: int, bank: int, net_income: int
) -> UnitType | None:
    """Choose the affordable type whose share is furthest below target (§4.6).

    If armor is the neediest type and is affordable within one turn's income, we
    hold the muster (return ``None``) rather than buy a cheaper unit now.
    """
    deficits: list[tuple[float, UnitType]] = []
    for unit_type, target in _TARGET_SHARE.items():
        share = counts[unit_type] / total if total else 0.0
        deficits.append((target - share, unit_type))
    deficits.sort(reverse=True)

    neediest = deficits[0][1]
    armor_cost = UNIT_STATS[UnitType.ARMOR].recruit_cost
    if (
        neediest is UnitType.ARMOR
        and bank < armor_cost
        and bank + net_income >= armor_cost
    ):
        return None  # bank toward the armor we most need

    for _deficit, unit_type in deficits:
        if UNIT_STATS[unit_type].recruit_cost <= bank - _RESERVE:
            return unit_type
    return None


def plan_recruitment(board: BoardIndex, slot: PlayerId) -> list[Recruitment]:
    """Produce a legal, composition-aware recruitment list for ``slot`` (§4.6)."""
    player = next((p for p in board.view.players if p.id == slot), None)
    if player is None:
        return []

    cities = [
        c
        for c in board.city_coords(slot)
        if board.is_free(c) and not _enemy_can_reach(board, c)
    ]
    cities.sort(key=lambda c: _front_distance(board, slot, c))

    counts: dict[UnitType, int] = {t: 0 for t in UnitType}
    for unit in board.own_units:
        counts[unit.type] += 1
    total_units = len(board.view.units)
    own_total = len(board.own_units)

    bank = player.bank
    net_income = _net_income(board, slot)
    unit_cap = board.view.config.unit_cap

    recruits: list[Recruitment] = []
    for city in cities:
        if total_units >= unit_cap:
            break
        unit_type = _pick_type(counts, own_total, bank, net_income)
        if unit_type is None:
            break
        recruits.append(Recruitment(owner=slot, type=unit_type, coord=city))
        bank -= UNIT_STATS[unit_type].recruit_cost
        counts[unit_type] += 1
        own_total += 1
        total_units += 1

    return recruits
