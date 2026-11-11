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
    want = _wanted_build(view, economy, player)
    if want is None:
        return None
    if not stocks.covers(_build_cost(view, economy, player, want)):
        return None
    return want


def _levels(view: GameState, player: Player) -> dict[Building, int]:
    """Building levels, counting a level that finishes this turn as built."""
    levels = dict(player.buildings)
    work = player.construction
    if work is not None and work.completes_on <= view.turn:
        levels[work.building] = work.level
    return levels


def _wanted_build(
    view: GameState, economy: WorldEconomy, player: Player
) -> Building | None:
    """The building level the bot works toward next, affordable or not.

    Barracks 1, then the Workshop, then barracks levels, each preceded by the
    depot level needed to store its cost.
    """
    levels = _levels(view, player)
    barracks = levels.get(Building.BARRACKS, 0)
    depot = levels.get(Building.DEPOT, 0)
    if barracks >= 1 and levels.get(Building.WORKSHOP, 0) == 0:
        return Building.WORKSHOP
    if barracks >= economy.max_level(Building.BARRACKS):
        return None
    cost = economy.buildings[Building.BARRACKS][barracks].cost
    too_big = max(cost.manpower, cost.fuel, cost.steel) > economy.storage_limit(depot)
    want = Building.DEPOT if too_big else Building.BARRACKS
    if levels.get(want, 0) >= economy.max_level(want):
        return None
    return want


def _build_cost(
    view: GameState, economy: WorldEconomy, player: Player, building: Building
) -> Stocks:
    """What the next level of ``building`` costs."""
    return economy.buildings[building][_levels(view, player).get(building, 0)].cost


# Average fuel upkeep of a recruit in the target mix (60/20/20).
_MIX_FUEL = sum(
    share * fuel
    for share, fuel in (
        (_TARGET_SHARE[UnitType.ARTILLERY], 1.0),
        (_TARGET_SHARE[UnitType.ARMOR], 2.0),
    )
)
_FULL_STOCK = 0.8  # a stock this close to its storage limit needs no more income
_PLAN_TURNS = 8  # horizon for "can the bot afford its next building"


def site_needs(view: GameState, slot: PlayerId) -> dict[SiteKind, float]:
    """How much ``slot`` wants each kind of site right now (1.0 is neutral).

    Each resource gets a need from the bot's actual flows, looking one army
    ahead (the units it may still recruit under its army size, each eating 1
    manpower and the mix's fuel):

    * manpower and fuel: high when the flow would turn negative with the army
      filled (disbands follow), raised while it would be thin, low when the
      stock is near its storage limit and the flow covers upkeep;
    * steel: by how many turns of income the next building still needs.

    Farms, oil fields and steel mills follow their resource; a factory follows
    steel and counts more until the bot holds one (it unlocks tanks); a city
    makes manpower and steel and is a recruiting site; a base is always worth
    the same.
    """
    economy = view.config.economy
    if economy is None:
        raise ValueError("site_needs needs a world economy")
    player = view.player(slot)
    sites = view.map.site_index
    held_sites = [sites[s.coord] for s in view.settlements if s.controlled_by == slot]
    income = Stocks()
    for site in held_sites:
        income += economy.income(site.kind, site.level)
    own = [u for u in view.units if u.owner == slot]
    upkeep = Stocks()
    for unit in own:
        upkeep += economy.upkeep[unit.type]
    net = income - upkeep
    limit = economy.storage_limit(player.level(Building.DEPOT))
    room = max(0, view.unit_cap_for(slot) - len(own))

    def flow_need(stock: int, flow: float) -> float:
        if flow < 0:
            return 2.5
        if stock >= _FULL_STOCK * limit:
            return 0.5
        return 1.6 if flow < 2 else 1.0

    manpower = flow_need(player.stocks.manpower, net.manpower - room)
    fuel = flow_need(player.stocks.fuel, net.fuel - _MIX_FUEL * room)
    want = _wanted_build(view, economy, player)
    steel_short = (
        _build_cost(view, economy, player, want).steel - player.stocks.steel
        if want is not None
        else 0
    )
    turns = steel_short / max(net.steel, 0.5)
    steel = 1.0 + min(1.5, max(0.0, turns) / _PLAN_TURNS)
    if player.stocks.steel >= _FULL_STOCK * limit:
        steel = 0.5
    factory = SiteKind.FACTORY in {site.kind for site in held_sites}
    return {
        SiteKind.BASE: 1.0,
        SiteKind.FARM: manpower,
        SiteKind.OIL_FIELD: fuel,
        SiteKind.STEEL_MILL: steel,
        SiteKind.FACTORY: 0.5 * steel + (0.3 if factory else 1.3),
        SiteKind.CITY: (3.0 * manpower + 1.5 * steel) / 4.5 * 1.2,
    }
