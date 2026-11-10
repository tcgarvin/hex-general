"""World economy: stocks, shortages, construction, army size, recruitment, garrisons."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.engine.economy_rules import Building, SiteKind, Stocks, WorldEconomy
from app.engine.events import (
    CombatResolved,
    Event,
    ConstructionCompleted,
    ConstructionStarted,
    ConstructionVoided,
    RecruitmentVoided,
    RecruitmentVoidedReason,
    SettlementCaptured,
    StocksResolved,
    UnitDisbanded,
    UnitRepaired,
    WorldUnitRecruited,
)
from app.engine.execution import resolve_execution
from app.engine.hex_coords import Axial, axial_to_offset, offset_to_axial
from app.engine.models import (
    NEUTRAL,
    Construction,
    EconomyOrders,
    GameConfig,
    GameMap,
    GameState,
    Hex,
    MapSite,
    MoveOrder,
    Player,
    Recruitment,
    RestOrder,
    Settlement,
    Unit,
    VictoryConfig,
)
from app.engine.movement import EngineError
from app.engine.stats import Terrain, UnitType

INF, ARM, ART = UnitType.INFANTRY, UnitType.ARMOR, UnitType.ARTILLERY

BASE = offset_to_axial(1, 1)
FARM = offset_to_axial(4, 1)
OIL = offset_to_axial(6, 1)
MILL = offset_to_axial(1, 5)
FACTORY = offset_to_axial(4, 5)
CITY = offset_to_axial(6, 5)

# A vacant base slot (a base site nobody has founded: no settlement) and a town
# (terrain only: in a world a town holds no ownership).
VACANT = offset_to_axial(6, 7)
TOWN = offset_to_axial(1, 7)
OPEN = offset_to_axial(3, 6)  # plain plains

SITES = {
    BASE: SiteKind.BASE,
    FARM: SiteKind.FARM,
    OIL: SiteKind.OIL_FIELD,
    MILL: SiteKind.STEEL_MILL,
    FACTORY: SiteKind.FACTORY,
    CITY: SiteKind.CITY,
    VACANT: SiteKind.BASE,
}


def world(
    held: tuple[Axial, ...] = (BASE,),
    units: list[Unit] | None = None,
    stocks: Stocks = Stocks(),
    buildings: dict[Building, int] | None = None,
    construction: Construction | None = None,
    turn: int = 1,
    levels: dict[Axial, int] | None = None,
) -> GameState:
    """An 8×8 world for player ``p`` holding ``held`` sites (the rest neutral).

    As the generator builds them, the city site is on city terrain and every
    other site on plains; ``levels`` sets site levels (default 1).
    """
    hexes = []
    for row in range(8):
        for col in range(8):
            coord = offset_to_axial(col, row)
            terrain = (
                Terrain.CITY
                if coord == CITY
                else Terrain.TOWN if coord == TOWN else Terrain.PLAINS
            )
            hexes.append(Hex(coord=coord, terrain=terrain))
    levels = levels or {}
    game_map = GameMap(
        width=8,
        height=8,
        hexes=hexes,
        sites=[
            MapSite(
                coord=c,
                kind=k,
                garrison=0 if k is SiteKind.BASE else 2,
                level=levels.get(c, 1),
            )
            for c, k in SITES.items()
        ],
    )
    return GameState(
        turn=turn,
        map=game_map,
        settlements=[
            Settlement(coord=c, controlled_by="p" if c in held else None)
            for c in SITES
            if c != VACANT
        ],
        units=units or [],
        players=[
            Player(
                id="p",
                stocks=stocks,
                buildings=buildings or {},
                construction=construction,
            )
        ],
        config=GameConfig(
            economy=WorldEconomy(),
            victory=VictoryConfig(turn_limit=10**6, city_elimination=False),
        ),
    )


def unit(
    uid: int, kind: UnitType, col: int, row: int, hp: int = 10, owner: str = "p"
) -> Unit:
    return Unit(id=uid, type=kind, owner=owner, coord=offset_to_axial(col, row), hp=hp)


def stocks_event(events: list[Event]) -> StocksResolved:
    return next(e for e in events if isinstance(e, StocksResolved))


# --- income, upkeep, storage --------------------------------------------------


def test_sites_earn_their_kind_and_units_cost_upkeep() -> None:
    state = world(
        held=(BASE, FARM, OIL, MILL),
        units=[unit(1, INF, 2, 2), unit(2, ARM, 3, 3)],
        stocks=Stocks(manpower=5, fuel=5, steel=5),
    )
    after, events = resolve_execution(state, [])
    resolved = stocks_event(events)
    assert resolved.income == Stocks(manpower=7, fuel=6, steel=3)
    assert resolved.upkeep == Stocks(manpower=2, fuel=2)
    assert after.players[0].stocks == Stocks(manpower=10, fuel=9, steel=8)


def test_site_income_rises_by_level() -> None:
    state = world(
        held=(FARM, OIL, MILL, FACTORY),
        levels={FARM: 3, OIL: 2, MILL: 3, FACTORY: 2},
        stocks=Stocks(manpower=5),
    )
    _, events = resolve_execution(state, [])
    # Farm 2+2 manpower, oil 3+1 fuel, mill 2+2 and factory 1+1 steel.
    assert stocks_event(events).income == Stocks(manpower=4, fuel=4, steel=6)


def test_income_by_level_is_economy_data() -> None:
    economy = WorldEconomy()
    assert [economy.income(SiteKind.FARM, n).manpower for n in (1, 2, 3)] == [2, 3, 4]
    assert [economy.income(SiteKind.OIL_FIELD, n).fuel for n in (1, 2, 3)] == [3, 4, 5]
    assert [economy.income(SiteKind.STEEL_MILL, n).steel for n in (1, 2, 3)] == [
        2,
        3,
        4,
    ]
    assert [economy.income(SiteKind.FACTORY, n).steel for n in (1, 2, 3)] == [1, 2, 3]
    assert economy.income(SiteKind.CITY, 1) == economy.site_income[SiteKind.CITY]
    assert economy.income(SiteKind.BASE, 1) == economy.site_income[SiteKind.BASE]


def test_cities_and_bases_are_always_level_one() -> None:
    with pytest.raises(ValidationError, match="always level 1"):
        MapSite(coord=BASE, kind=SiteKind.BASE, garrison=0, level=2)
    with pytest.raises(ValidationError):
        MapSite(coord=FARM, kind=SiteKind.FARM, garrison=1, level=4)


def test_storage_limits_each_resource_and_the_depot_raises_it() -> None:
    state = world(held=(BASE, FARM), stocks=Stocks(manpower=28))
    after, events = resolve_execution(state, [])
    assert after.players[0].stocks.manpower == 30
    assert stocks_event(events).lost_to_storage == Stocks(manpower=5)

    depot = world(
        held=(BASE, FARM), stocks=Stocks(manpower=28), buildings={Building.DEPOT: 1}
    )
    after, _ = resolve_execution(depot, [])
    assert after.players[0].stocks.manpower == 35


def test_stocks_beyond_storage_are_rejected_at_the_boundary() -> None:
    with pytest.raises(ValidationError, match="outside"):
        world(stocks=Stocks(steel=31))


# --- shortages ----------------------------------------------------------------


def test_fuel_shortage_disbands_the_players_pick() -> None:
    units = [unit(1, ARM, 2, 2), unit(2, ARM, 3, 3, hp=3), unit(3, INF, 2, 4)]
    state = world(
        held=(FARM,), units=units, stocks=Stocks(manpower=10)
    )  # no fuel income
    after, events = resolve_execution(
        state, [], economy_orders=[EconomyOrders(owner="p", disband=[3, 1])]
    )
    disbanded = [e for e in events if isinstance(e, UnitDisbanded)]
    # Infantry 3 burns no fuel, so the first qualifying pick is tank 1.
    assert [(e.unit_id, e.shortage, e.chosen) for e in disbanded] == [(1, "fuel", True)]
    assert {u.id for u in after.units} == {2, 3}
    assert after.players[0].stocks.fuel == 0


def test_without_a_pick_the_weakest_qualifying_unit_goes() -> None:
    units = [unit(1, ARM, 2, 2), unit(2, ART, 3, 3, hp=4), unit(3, ARM, 2, 4, hp=4)]
    after, events = resolve_execution(
        world(held=(FARM,), units=units, stocks=Stocks(manpower=10)), []
    )
    disbanded = [e for e in events if isinstance(e, UnitDisbanded)]
    # Lowest HP first, then the newest (highest id).
    assert [(e.unit_id, e.chosen) for e in disbanded] == [(3, False)]


def test_manpower_and_fuel_shortages_each_cost_one_unit() -> None:
    units = [unit(1, ARM, 2, 2), unit(2, INF, 3, 3), unit(3, INF, 2, 4)]
    after, events = resolve_execution(world(held=(), units=units), [])
    disbanded = [
        (e.unit_id, e.shortage) for e in events if isinstance(e, UnitDisbanded)
    ]
    assert disbanded == [(1, "fuel"), (3, "manpower")]
    assert [u.id for u in after.units] == [2]


def test_stock_covers_a_deficit_without_disbanding() -> None:
    units = [unit(1, ARM, 2, 2)]
    state = world(held=(), units=units, stocks=Stocks(manpower=1, fuel=2))
    after, events = resolve_execution(state, [])
    assert not [e for e in events if isinstance(e, UnitDisbanded)]
    assert after.players[0].stocks == Stocks()


# --- construction -------------------------------------------------------------


def test_build_order_pays_and_completes_after_its_turns() -> None:
    state = world(stocks=Stocks(manpower=10, steel=10), turn=3)
    after, events = resolve_execution(
        state, [], economy_orders=[EconomyOrders(owner="p", build=Building.BARRACKS)]
    )
    started = next(e for e in events if isinstance(e, ConstructionStarted))
    assert (started.level, started.completes_on) == (1, 5)
    player = after.players[0]
    # +5 manpower, +1 steel from the base, then 5 manpower and 8 steel paid.
    assert player.stocks == Stocks(manpower=10, steel=3, fuel=3)
    assert player.construction == Construction(
        building=Building.BARRACKS, level=1, completes_on=5
    )

    after, events = resolve_execution(after, [])
    assert after.players[0].level(Building.BARRACKS) == 0
    after, events = resolve_execution(after, [])
    assert [e for e in events if isinstance(e, ConstructionCompleted)]
    assert after.players[0].level(Building.BARRACKS) == 1
    assert after.players[0].construction is None


def test_unaffordable_build_is_voided_and_costs_nothing() -> None:
    after, events = resolve_execution(
        world(stocks=Stocks(steel=2)),
        [],
        economy_orders=[EconomyOrders(owner="p", build=Building.WORKSHOP)],
    )
    assert [e for e in events if isinstance(e, ConstructionVoided)]
    assert after.players[0].construction is None
    assert after.players[0].stocks.steel == 3


def test_building_while_busy_or_past_the_top_raises() -> None:
    busy = world(
        stocks=Stocks(steel=30),
        construction=Construction(building=Building.DEPOT, level=1, completes_on=9),
    )
    with pytest.raises(EngineError, match="under construction"):
        resolve_execution(
            busy, [], economy_orders=[EconomyOrders(owner="p", build=Building.BARRACKS)]
        )
    top = world(stocks=Stocks(steel=30), buildings={Building.WORKSHOP: 1})
    with pytest.raises(EngineError, match="top level"):
        resolve_execution(
            top, [], economy_orders=[EconomyOrders(owner="p", build=Building.WORKSHOP)]
        )


def test_a_level_finishing_this_turn_frees_the_queue_for_the_next() -> None:
    state = world(
        stocks=Stocks(steel=20, manpower=10),
        construction=Construction(building=Building.BARRACKS, level=1, completes_on=1),
    )
    after, _ = resolve_execution(
        state, [], economy_orders=[EconomyOrders(owner="p", build=Building.BARRACKS)]
    )
    player = after.players[0]
    assert player.level(Building.BARRACKS) == 1
    assert player.construction is not None and player.construction.level == 2


def test_forked_buildings_do_not_leak_back() -> None:
    state = world(
        construction=Construction(building=Building.DEPOT, level=1, completes_on=1)
    )
    resolve_execution(state, [])
    assert state.players[0].buildings == {}


# --- army size ----------------------------------------------------------------


def test_army_size_counts_barracks_and_held_sites_but_not_the_base() -> None:
    assert world(held=(BASE,)).unit_cap_for("p") == 4
    assert world(held=(BASE, FARM, OIL, MILL)).unit_cap_for("p") == 4
    assert world(held=(BASE, FARM, OIL, MILL, CITY)).unit_cap_for("p") == 5
    assert world(held=(BASE,), buildings={Building.BARRACKS: 3}).unit_cap_for("p") == 10


# --- recruitment --------------------------------------------------------------


def test_infantry_recruits_at_the_base_paying_stocks() -> None:
    state = world(stocks=Stocks(manpower=5))
    after, events = resolve_execution(
        state, [], [Recruitment(owner="p", type=INF, coord=BASE)]
    )
    recruited = next(e for e in events if isinstance(e, WorldUnitRecruited))
    assert recruited.cost == Stocks(manpower=3)
    assert recruited.stocks_after == Stocks(manpower=7, fuel=3, steel=1)
    assert [u.coord for u in after.units] == [BASE]


def test_tanks_come_only_from_a_held_factory() -> None:
    rich = Stocks(manpower=20, steel=20)
    after, events = resolve_execution(
        world(held=(BASE, FACTORY), stocks=rich),
        [],
        [Recruitment(owner="p", type=ARM, coord=FACTORY)],
    )
    assert [u.type for u in after.units] == [ARM]
    with pytest.raises(EngineError, match="cannot be recruited"):
        resolve_execution(
            world(stocks=rich), [], [Recruitment(owner="p", type=ARM, coord=BASE)]
        )
    after, events = resolve_execution(
        world(held=(BASE,), stocks=rich),
        [],
        [Recruitment(owner="p", type=ARM, coord=FACTORY)],
    )
    voided = next(e for e in events if isinstance(e, RecruitmentVoided))
    assert voided.reason is RecruitmentVoidedReason.CITY_NOT_FRIENDLY


def test_artillery_needs_a_workshop() -> None:
    rich = Stocks(manpower=20, steel=20)
    with pytest.raises(EngineError, match="workshop"):
        resolve_execution(
            world(stocks=rich), [], [Recruitment(owner="p", type=ART, coord=BASE)]
        )
    after, _ = resolve_execution(
        world(stocks=rich, buildings={Building.WORKSHOP: 1}),
        [],
        [Recruitment(owner="p", type=ART, coord=BASE)],
    )
    assert [u.type for u in after.units] == [ART]


def test_recruit_short_of_stocks_or_army_size_is_voided() -> None:
    after, events = resolve_execution(
        world(held=(BASE, FACTORY), stocks=Stocks(manpower=20, steel=2)),
        [],
        [Recruitment(owner="p", type=ARM, coord=FACTORY)],
    )
    assert next(e for e in events if isinstance(e, RecruitmentVoided)).reason is (
        RecruitmentVoidedReason.INSUFFICIENT_STOCKS
    )
    full = [unit(k, INF, 3 + k % 3, 2 + k // 3) for k in range(4)]
    after, events = resolve_execution(
        world(units=full, stocks=Stocks(manpower=20)),
        [],
        [Recruitment(owner="p", type=INF, coord=BASE)],
    )
    assert next(e for e in events if isinstance(e, RecruitmentVoided)).reason is (
        RecruitmentVoidedReason.UNIT_CAP_REACHED
    )


# --- garrisons ----------------------------------------------------------------


def test_garrisons_hold_and_never_take_control() -> None:
    guard = unit(9, INF, 4, 1, owner=NEUTRAL)  # on the farm
    after, events = resolve_execution(
        world(units=[guard], stocks=Stocks(manpower=5)), []
    )
    assert not [e for e in events if isinstance(e, SettlementCaptured)]
    farm = next(s for s in after.settlements if s.coord == FARM)
    assert farm.controlled_by is None
    assert after.units == [guard]


def test_no_player_can_order_a_garrison_unit() -> None:
    guard = unit(9, INF, 4, 1, owner=NEUTRAL)
    with pytest.raises(EngineError, match="neutral garrison"):
        resolve_execution(
            world(units=[guard]),
            [MoveOrder(unit_id=9, path=[offset_to_axial(4, 2)])],
        )


def test_clearing_a_garrison_lets_the_attacker_take_the_site() -> None:
    guard = unit(9, INF, 4, 1, hp=1, owner=NEUTRAL)
    attackers = [unit(1, INF, 3, 1), unit(2, INF, 5, 1)]
    state = world(units=[guard, *attackers], stocks=Stocks(manpower=10))
    after, events = resolve_execution(state, [MoveOrder(unit_id=1, path=[FARM])])
    assert NEUTRAL not in {u.owner for u in after.units}
    farm = next(s for s in after.settlements if s.coord == FARM)
    assert farm.controlled_by == "p"


def test_capturing_a_site_reports_its_kind() -> None:
    guard = unit(9, INF, 4, 1, hp=1, owner=NEUTRAL)
    state = world(units=[guard, unit(1, INF, 3, 1)], stocks=Stocks(manpower=10))
    _, events = resolve_execution(state, [MoveOrder(unit_id=1, path=[FARM])])
    captured = next(e for e in events if isinstance(e, SettlementCaptured))
    assert (captured.coord, captured.site) == (FARM, SiteKind.FARM)
    assert captured.settlement is Terrain.PLAINS


# --- settlement ground --------------------------------------------------------


def test_founded_sites_and_towns_are_settlement_ground_but_a_vacant_base_is_not() -> (
    None
):
    state = world()
    for coord in (BASE, FARM, CITY, TOWN):
        assert state.settlement_ground(coord), coord
        assert state.terrain_defense(coord) == 2, coord
    for coord in (VACANT, OPEN):
        assert not state.settlement_ground(coord), coord
        assert state.terrain_defense(coord) == 0, coord


@pytest.mark.parametrize(
    ("where", "bonus"), [(FARM, 2), (TOWN, 2), (VACANT, 0), (OPEN, 0)]
)
def test_a_defender_gets_the_settlement_bonus_only_on_settlement_ground(
    where: Axial, bonus: int
) -> None:
    col, row = axial_to_offset(where)
    defender = unit(9, INF, col, row, owner=NEUTRAL)
    attacker = unit(1, ARM, col, row - 1)
    state = world(units=[defender, attacker], stocks=Stocks(manpower=10, fuel=10))
    _, events = resolve_execution(state, [MoveOrder(unit_id=1, path=[where])])
    combat = next(e for e in events if isinstance(e, CombatResolved))
    by_id = {p.unit_id: p for p in combat.participants}
    assert by_id[9].terrain_bonus == bonus
    assert by_id[1].terrain_bonus == 0


@pytest.mark.parametrize(
    ("where", "healed"), [(FARM, 7), (BASE, 7), (TOWN, 7), (VACANT, 6), (OPEN, 6)]
)
def test_resting_on_settlement_ground_repairs_faster(where: Axial, healed: int) -> None:
    col, row = axial_to_offset(where)
    state = world(units=[unit(1, INF, col, row, hp=5)], stocks=Stocks(manpower=5))
    after, events = resolve_execution(state, [RestOrder(unit_id=1)])
    assert after.units[0].hp == healed
    repaired = next(e for e in events if isinstance(e, UnitRepaired))
    assert repaired.in_settlement is (healed == 7)


def test_economy_orders_need_a_world() -> None:
    state = world()
    v01 = state.model_copy(
        update={"config": state.config.model_copy(update={"economy": None})}
    )
    with pytest.raises(EngineError, match="world economy"):
        resolve_execution(v01, [], economy_orders=[EconomyOrders(owner="p")])
