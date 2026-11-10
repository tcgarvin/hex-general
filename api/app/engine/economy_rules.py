"""World economy rules data: sites, resources, buildings (VISION §6, economy draft).

A world game replaces v0.1's single muster bank with three resources —
**manpower**, **fuel** and **steel** — earned from captured *sites* and spent on
recruits, upkeep and base buildings. Every number lives in `WorldEconomy`, which a
world's `GameConfig` carries, so a world can be tuned (and simulated) without code
changes. The rules that read these tables are in `app.engine.world_economy` and
`app.engine.recruitment`.

Reviewed design: https://claude.ai/artifact/BjDgptUsyxZn7aLBW5pxd4 (revision 2).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.engine.stats import UnitType


class SiteKind(StrEnum):
    """What a site is in a world economy, fixed when the map is generated.

    The kind decides what a site produces and what may be recruited there. A
    `CITY` stands on its city hex; every other kind stands on its own plains hex
    beside a town or a road. A `BASE` is a player's home: not a city, one per slot,
    on open ground with a four-hex plot kept clear around it.
    """

    BASE = "base"
    CITY = "city"
    FARM = "farm"
    OIL_FIELD = "oil_field"
    STEEL_MILL = "steel_mill"
    FACTORY = "factory"


class Building(StrEnum):
    """A building at a player's base, upgraded level by level."""

    BARRACKS = "barracks"
    DEPOT = "depot"
    WORKSHOP = "workshop"


class Stocks(BaseModel):
    """An amount of each resource: a stockpile, an income, a cost or an upkeep."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manpower: int = 0
    fuel: int = 0
    steel: int = 0

    def __add__(self, other: Stocks) -> Stocks:
        return Stocks(
            manpower=self.manpower + other.manpower,
            fuel=self.fuel + other.fuel,
            steel=self.steel + other.steel,
        )

    def __sub__(self, other: Stocks) -> Stocks:
        return Stocks(
            manpower=self.manpower - other.manpower,
            fuel=self.fuel - other.fuel,
            steel=self.steel - other.steel,
        )

    def covers(self, cost: Stocks) -> bool:
        """Whether this stockpile can pay ``cost`` in full."""
        return (
            self.manpower >= cost.manpower
            and self.fuel >= cost.fuel
            and self.steel >= cost.steel
        )

    def scaled(self, factor: int) -> Stocks:
        """Each resource multiplied by ``factor``."""
        return Stocks(
            manpower=self.manpower * factor,
            fuel=self.fuel * factor,
            steel=self.steel * factor,
        )

    def clamped(self, ceiling: int) -> Stocks:
        """Each resource limited to ``0..ceiling``."""
        return Stocks(
            manpower=max(0, min(ceiling, self.manpower)),
            fuel=max(0, min(ceiling, self.fuel)),
            steel=max(0, min(ceiling, self.steel)),
        )


class BuildingLevel(BaseModel):
    """What one level of a building costs and how many turns it takes to build."""

    model_config = ConfigDict(frozen=True)

    cost: Stocks
    turns: int = Field(ge=1)


class ArmySize(BaseModel):
    """The army size rule: ``base + per_barracks × barracks level + held sites ÷
    sites_per_point``, never above ``ceiling``. Your own base is not a held site."""

    model_config = ConfigDict(frozen=True)

    base: int = Field(default=4, ge=0)
    per_barracks: int = Field(default=2, ge=0)
    sites_per_point: int = Field(default=4, ge=1)
    ceiling: int = Field(default=15, ge=1)

    def size(self, barracks: int, held_sites: int) -> int:
        """The army size for a barracks level and a count of held sites."""
        return min(
            self.ceiling,
            self.base
            + self.per_barracks * barracks
            + held_sites // self.sites_per_point,
        )


GARRISON_FORCES: dict[int, tuple[tuple[UnitType, int], ...]] = {
    1: ((UnitType.INFANTRY, 5),),
    2: ((UnitType.INFANTRY, 10),),
    3: ((UnitType.INFANTRY, 10), (UnitType.ARTILLERY, 10)),
    4: ((UnitType.INFANTRY, 10), (UnitType.INFANTRY, 10), (UnitType.ARTILLERY, 10)),
}
"""Each garrison tier's units and HP, the first on the site (economy draft §04).

A generator places these at world creation; bots read them to size attacks."""


def _levels(*levels: tuple[Stocks, int]) -> list[BuildingLevel]:
    return [BuildingLevel(cost=cost, turns=turns) for cost, turns in levels]


class WorldEconomy(BaseModel):
    """Every tunable number of the world economy (defaults: draft revision 2).

    A site earns ``site_income[kind]`` at level 1 and ``level_income[kind]`` more
    per level above 1 (`income`); cities and bases are always level 1.
    ``buildings[b][k]`` is level ``k + 1`` of building ``b``. ``storage[k]`` is
    the per-resource stockpile limit at depot level ``k``, so it has one more
    entry than the depot has levels. Artillery needs a workshop and armor a held
    factory; those unlock rules are fixed in the recruitment phase.
    """

    model_config = ConfigDict(frozen=True)

    site_income: dict[SiteKind, Stocks] = Field(
        default_factory=lambda: {
            SiteKind.BASE: Stocks(manpower=5, steel=1, fuel=3),
            SiteKind.CITY: Stocks(manpower=3, steel=1),
            SiteKind.FARM: Stocks(manpower=2),
            SiteKind.OIL_FIELD: Stocks(fuel=3),
            SiteKind.STEEL_MILL: Stocks(steel=2),
            SiteKind.FACTORY: Stocks(steel=1),
        }
    )
    # Provisional tuning (2026-11-10): +1 of what the site already produces per
    # level, so a farm earns 2/3/4 manpower at levels 1/2/3.
    level_income: dict[SiteKind, Stocks] = Field(
        default_factory=lambda: {
            SiteKind.BASE: Stocks(),
            SiteKind.CITY: Stocks(),
            SiteKind.FARM: Stocks(manpower=1),
            SiteKind.OIL_FIELD: Stocks(fuel=1),
            SiteKind.STEEL_MILL: Stocks(steel=1),
            SiteKind.FACTORY: Stocks(steel=1),
        }
    )
    recruit_cost: dict[UnitType, Stocks] = Field(
        default_factory=lambda: {
            UnitType.INFANTRY: Stocks(manpower=3),
            UnitType.ARTILLERY: Stocks(manpower=2, steel=3),
            UnitType.ARMOR: Stocks(manpower=2, steel=6),
        }
    )
    upkeep: dict[UnitType, Stocks] = Field(
        default_factory=lambda: {
            UnitType.INFANTRY: Stocks(manpower=1),
            UnitType.ARTILLERY: Stocks(manpower=1, fuel=1),
            UnitType.ARMOR: Stocks(manpower=1, fuel=2),
        }
    )
    buildings: dict[Building, list[BuildingLevel]] = Field(
        default_factory=lambda: {
            Building.BARRACKS: _levels(
                (Stocks(steel=8, manpower=5), 2),
                (Stocks(steel=13, manpower=5), 3),
                (Stocks(steel=23, manpower=8), 4),
                (Stocks(steel=36, manpower=10), 5),
                (Stocks(steel=52, manpower=13), 6),
            ),
            Building.DEPOT: _levels((Stocks(steel=12), 2), (Stocks(steel=24), 3)),
            Building.WORKSHOP: _levels((Stocks(steel=10, manpower=4), 3)),
        }
    )
    storage: list[int] = Field(default_factory=lambda: [30, 50, 80])
    army: ArmySize = Field(default_factory=ArmySize)
    starting_stocks: Stocks = Field(
        default_factory=lambda: Stocks(manpower=10, fuel=20, steel=10)
    )

    @model_validator(mode="after")
    def _complete(self) -> WorldEconomy:
        """Every site, unit and building has an entry; storage matches the depot."""
        for table, keys in (
            (self.site_income, SiteKind),
            (self.level_income, SiteKind),
            (self.recruit_cost, UnitType),
            (self.upkeep, UnitType),
            (self.buildings, Building),
        ):
            missing = set(keys) - set(table)
            if missing:
                raise ValueError(f"economy table lacks {sorted(missing)}")
        depot_levels = len(self.buildings[Building.DEPOT])
        if len(self.storage) != depot_levels + 1:
            raise ValueError(
                f"storage needs {depot_levels + 1} entries (depot levels 0.."
                f"{depot_levels}), got {len(self.storage)}"
            )
        return self

    def income(self, kind: SiteKind, level: int) -> Stocks:
        """What a held site of ``kind`` at ``level`` earns per turn."""
        return self.site_income[kind] + self.level_income[kind].scaled(level - 1)

    def storage_limit(self, depot_level: int) -> int:
        """The per-resource stockpile limit at ``depot_level``."""
        return self.storage[depot_level]

    def max_level(self, building: Building) -> int:
        """The highest level ``building`` can reach."""
        return len(self.buildings[building])

    def uses_fuel(self, unit_type: UnitType) -> bool:
        """Whether ``unit_type`` burns fuel (so a fuel shortage can disband it)."""
        return self.upkeep[unit_type].fuel > 0
