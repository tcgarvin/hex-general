"""Small hand-built world states for behavioural bot tests.

A plains rectangle in offset coordinates, with world sites, a world economy and
whatever units a test places. Player ``p`` is the bot under test; ``q`` is
another player; neutral garrison units are placed by the test, as the generator
would place them, so each test shows exactly what the bot is up against.
"""

from __future__ import annotations

from app.engine.economy_rules import Building, SiteKind, Stocks, WorldEconomy
from app.engine.hex_coords import Axial, offset_to_axial
from app.engine.models import (
    NEUTRAL,
    GameConfig,
    GameMap,
    GameState,
    Hex,
    MapSite,
    Player,
    PlayerId,
    Settlement,
    Unit,
    VictoryConfig,
)
from app.engine.stats import Terrain, UnitType

INF, ARM, ART = UnitType.INFANTRY, UnitType.ARMOR, UnitType.ARTILLERY


def at(col: int, row: int) -> Axial:
    """The axial coordinate of offset ``(col, row)``."""
    return offset_to_axial(col, row)


def make_unit(
    uid: int,
    kind: UnitType,
    coord: Axial,
    owner: PlayerId = "p",
    hp: int = 10,
) -> Unit:
    """A deployed unit (neutral garrisons use ``owner=NEUTRAL``)."""
    return Unit(id=uid, type=kind, owner=owner, coord=coord, hp=hp)


def garrison(uid: int, coord: Axial, hp: int = 10) -> Unit:
    """A neutral garrison infantry unit."""
    return make_unit(uid, INF, coord, owner=NEUTRAL, hp=hp)


def small_world(
    sites: dict[Axial, tuple[SiteKind, int]],
    units: list[Unit],
    held: dict[Axial, PlayerId] | None = None,
    width: int = 16,
    height: int = 12,
    towns: tuple[Axial, ...] = (),
    stocks: Stocks = Stocks(manpower=20, fuel=20, steel=20),
    buildings: dict[Building, int] | None = None,
) -> GameState:
    """A plains world of ``width`` × ``height`` hexes for players ``p`` and ``q``.

    ``sites`` maps each site to its kind and garrison tier; ``held`` says who
    holds which (the rest are neutral). ``towns`` are town-terrain hexes.
    """
    held = held or {}
    hexes = [
        Hex(
            coord=at(col, row),
            terrain=Terrain.TOWN if at(col, row) in towns else Terrain.PLAINS,
        )
        for row in range(height)
        for col in range(width)
    ]
    game_map = GameMap(
        width=width,
        height=height,
        hexes=hexes,
        sites=[
            MapSite(coord=c, kind=kind, garrison=tier)
            for c, (kind, tier) in sites.items()
        ],
    )
    return GameState(
        turn=5,
        map=game_map,
        settlements=[Settlement(coord=c, controlled_by=held.get(c)) for c in sites],
        units=units,
        players=[
            Player(id=pid, stocks=stocks, buildings=buildings or {})
            for pid in ("p", "q")
        ],
        config=GameConfig(
            unit_cap=15,
            settlement_repair=3,
            economy=WorldEconomy(),
            victory=VictoryConfig(turn_limit=10**6, city_elimination=False),
        ),
    )
