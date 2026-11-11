"""A world bot's base orders: what to build and what to recruit (economy draft).

Rule-based, like v0.1 recruitment (`app.ai.recruit`), and kept deliberately
simple until the user's first playtest (bots fill the map; better bots come
after). Each turn:

* **Recruit** while under army size, steering toward ~60% infantry, 20% guns, 20%
  tanks, at a held base or city (tanks at a held factory), preferring one nobody
  stands on; a unit standing on the chosen site steps off this turn. A recruit is skipped if
  its upkeep would leave fuel or manpower running at a loss that the stock can't
  carry for `_DEFICIT_TURNS` more turns, so the bot rarely forces a disband.
* **Build** with what is left: Barracks 1, then the Workshop, then barracks levels,
  each preceded by the depot level needed to store its cost.

Everything here is checked against the bot's *current* stocks, as
`validate_submission` checks them, so every plan it returns is legal.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine.economy_rules import Building, SiteKind, Stocks, WorldEconomy
from app.engine.hex_coords import Axial
from app.engine.models import GameState, MapSite, Player, PlayerId, Posture, Recruitment
from app.engine.recruitment import WORLD_RECRUIT_NEEDS, WORLD_RECRUIT_SITES
from app.engine.stats import UnitType

_TARGET_SHARE: dict[UnitType, float] = {
    UnitType.INFANTRY: 0.6,
    UnitType.ARTILLERY: 0.2,
    UnitType.ARMOR: 0.2,
}
_DEFICIT_TURNS = 10


@dataclass(frozen=True)
class BasePlan:
    """A bot's recruits and build order for the turn."""

    recruitments: list[Recruitment]
    build: Building | None


@dataclass
class _Budget:
    """What the bot can still spend this turn, and what it will earn per turn."""

    stocks: Stocks
    net: Stocks

    def can_carry(self, upkeep: Stocks) -> bool:
        """Whether adding ``upkeep`` keeps fuel and manpower sustainable."""
        for have, rate, extra in (
            (self.stocks.fuel, self.net.fuel, upkeep.fuel),
            (self.stocks.manpower, self.net.manpower, upkeep.manpower),
        ):
            after = rate - extra
            if after < 0 and have < _DEFICIT_TURNS * -after:
                return False
        return True


def plan_base(view: GameState, slot: PlayerId) -> BasePlan:
    """Recruit, then build with what's left (see the module docstring)."""
    economy = view.config.economy
    if economy is None:
        raise ValueError("plan_base needs a world economy")
    player = view.player(slot)
    sites = view.map.site_index
    held = [s.coord for s in view.settlements if s.controlled_by == slot]
    own = [u for u in view.units if u.owner == slot]

    income = Stocks()
    for coord in held:
        income += economy.income(sites[coord].kind, sites[coord].level)
    upkeep = Stocks()
    for unit in own:
        upkeep += economy.upkeep[unit.type]
    budget = _Budget(stocks=player.stocks, net=income - upkeep)

    # Recruits appear after movement, so a site an own unit stands on is free
    # if that unit steps off this turn (the tactical layer moves it). Sites with
    # nobody on them come first.
    standing = {
        u.coord for u in view.units if u.posture is Posture.DEPLOYED and u.owner == slot
    }
    free = sorted(held, key=lambda c: (c in standing, c.q, c.r))
    counts = {t: sum(1 for u in own if u.type is t) for t in UnitType}
    cap = view.unit_cap_for(slot)
    recruitments: list[Recruitment] = []
    while len(own) + len(recruitments) < cap:
        pick = _next_recruit(economy, player, sites, free, counts, budget)
        if pick is None:
            break
        unit_type, coord = pick
        recruitments.append(Recruitment(owner=slot, type=unit_type, coord=coord))
        free.remove(coord)
        budget.stocks = budget.stocks - economy.recruit_cost[unit_type]
        budget.net = budget.net - economy.upkeep[unit_type]
        counts[unit_type] += 1

    return BasePlan(
        recruitments=recruitments,
        build=_next_build(view, economy, player, budget.stocks),
    )


def _next_recruit(
    economy: WorldEconomy,
    player: Player,
    sites: dict[Axial, MapSite],
    free: list[Axial],
    counts: dict[UnitType, int],
    budget: _Budget,
) -> tuple[UnitType, Axial] | None:
    """The most-needed unit type that is unlocked, placeable and affordable."""
    total = max(1, sum(counts.values()))
    by_need = sorted(
        _TARGET_SHARE, key=lambda t: (counts[t] / total - _TARGET_SHARE[t], t.value)
    )
    for unit_type in by_need:
        needs = WORLD_RECRUIT_NEEDS.get(unit_type)
        if needs is not None and player.level(needs) < 1:
            continue
        spots = [c for c in free if sites[c].kind in WORLD_RECRUIT_SITES[unit_type]]
        if not spots:
            continue
        if not budget.stocks.covers(economy.recruit_cost[unit_type]):
            continue
        if not budget.can_carry(economy.upkeep[unit_type]):
            continue
        return unit_type, spots[0]
    return None


def _next_build(
    view: GameState, economy: WorldEconomy, player: Player, stocks: Stocks
) -> Building | None:
    """The next building level the bot wants, if it can pay for it now."""
    work = player.construction
    if work is not None and work.completes_on > view.turn:
        return None
    levels = dict(player.buildings)
    if work is not None:
        levels[work.building] = work.level  # finishes this turn
    barracks = levels.get(Building.BARRACKS, 0)
    depot = levels.get(Building.DEPOT, 0)
    if barracks >= 1 and levels.get(Building.WORKSHOP, 0) == 0:
        want: Building | None = Building.WORKSHOP
    elif barracks < economy.max_level(Building.BARRACKS):
        cost = economy.buildings[Building.BARRACKS][barracks].cost
        too_big = max(cost.manpower, cost.fuel, cost.steel) > economy.storage_limit(
            depot
        )
        want = Building.DEPOT if too_big else Building.BARRACKS
    else:
        want = None
    if want is None:
        return None
    level = levels.get(want, 0)
    if level >= economy.max_level(want):
        return None
    if not stocks.covers(economy.buildings[want][level].cost):
        return None
    return want


def site_needs(view: GameState, slot: PlayerId) -> dict[SiteKind, float]:
    """How much ``slot`` wants each kind of site right now (1.0 is neutral).

    Farms count double while manpower barely covers upkeep; oil counts double
    while fuel runs at a loss and little when the stock is near full; steel mills
    count more until the bot holds two; the first factory is worth a lot.
    """
    economy = view.config.economy
    if economy is None:
        raise ValueError("site_needs needs a world economy")
    player = view.player(slot)
    sites = view.map.site_index
    held_sites = [sites[s.coord] for s in view.settlements if s.controlled_by == slot]
    held = [site.kind for site in held_sites]
    income = Stocks()
    for site in held_sites:
        income += economy.income(site.kind, site.level)
    upkeep = Stocks()
    for unit in view.units:
        if unit.owner == slot:
            upkeep += economy.upkeep[unit.type]
    net = income - upkeep
    limit = economy.storage_limit(player.level(Building.DEPOT))
    needs = {kind: 1.0 for kind in SiteKind}
    if net.manpower <= 1:
        needs[SiteKind.FARM] = 2.0
    if net.fuel < 0:
        needs[SiteKind.OIL_FIELD] = 2.0
    elif player.stocks.fuel >= limit * 0.8:
        needs[SiteKind.OIL_FIELD] = 0.5
    if held.count(SiteKind.STEEL_MILL) < 2:
        needs[SiteKind.STEEL_MILL] = 1.5
    if SiteKind.FACTORY not in held:
        needs[SiteKind.FACTORY] = 1.8
    return needs
