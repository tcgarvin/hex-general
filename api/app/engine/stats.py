"""Static reference data from SPEC §3–6: terrain, unit, and settlement tables.

These are prototype defaults (SPEC §5.3 notes they should be tuned after
playtesting in Phase 10). They live as data, separate from the dynamic
`GameState`, so the engine reads them but never mutates them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Terrain(StrEnum):
    """Terrain of a hex (SPEC §4.1).

    Town and City are terrain types in their own right — a settlement hex *is*
    town/city terrain, not a marker layered on plains/woods. Their dynamic
    ownership is tracked separately by `app.engine.models.Settlement`, but the
    terrain identity, defense bonus, and income all live here. Road, by
    contrast, is a boolean modifier layered onto terrain (`Hex.road`).
    """

    PLAINS = "plains"
    WOODS = "woods"
    HILLS = "hills"
    RIVER = "river"
    BRIDGE = "bridge"
    TOWN = "town"
    CITY = "city"


# The terrain types that are settlements (SPEC §3.2). No villages in v0.1.
SETTLEMENT_TERRAINS: frozenset[Terrain] = frozenset({Terrain.TOWN, Terrain.CITY})


def is_settlement(terrain: Terrain) -> bool:
    """Return whether a terrain type is a capturable settlement (town/city)."""
    return terrain in SETTLEMENT_TERRAINS


class UnitType(StrEnum):
    """The three v0.1 unit types (SPEC §5.1)."""

    INFANTRY = "infantry"
    ARMOR = "armor"
    ARTILLERY = "artillery"


class TerrainStats(BaseModel):
    """Per-terrain rules data (SPEC §3.4, §4.2, §9.2)."""

    model_config = ConfigDict(frozen=True)

    passable: bool
    defense_bonus: int
    blocks_line_of_sight: bool


class UnitStats(BaseModel):
    """Per-unit-type prototype stats (SPEC §5.3, §6.3, §6.5, §9.3)."""

    model_config = ConfigDict(frozen=True)

    max_hp: int
    move: int
    attack: int
    defense: int
    attack_range: int
    vision: int
    recruit_cost: int
    upkeep: int
    can_capture: bool


class SettlementStats(BaseModel):
    """Per-settlement economy and repair data (SPEC §3.2, §6.2).

    Settlement defense is not stored here — it lives with the terrain in
    `TERRAIN_STATS` (town/city both grant +2), keeping defense in one place.
    ``repair_bonus`` is the bonus to a unit Resting on the settlement (SPEC §7.3).
    """

    model_config = ConfigDict(frozen=True)

    income: int
    repair_bonus: int


# Defense bonus applies only to stationary defenders (SPEC §4.2, §12.10).
TERRAIN_STATS: dict[Terrain, TerrainStats] = {
    Terrain.PLAINS: TerrainStats(
        passable=True, defense_bonus=0, blocks_line_of_sight=False
    ),
    Terrain.WOODS: TerrainStats(
        passable=True, defense_bonus=2, blocks_line_of_sight=True
    ),
    Terrain.HILLS: TerrainStats(
        passable=True, defense_bonus=1, blocks_line_of_sight=False
    ),
    Terrain.RIVER: TerrainStats(
        passable=False, defense_bonus=0, blocks_line_of_sight=False
    ),
    Terrain.BRIDGE: TerrainStats(
        passable=True, defense_bonus=0, blocks_line_of_sight=False
    ),
    Terrain.TOWN: TerrainStats(
        passable=True, defense_bonus=2, blocks_line_of_sight=False
    ),
    Terrain.CITY: TerrainStats(
        passable=True, defense_bonus=2, blocks_line_of_sight=False
    ),
}

UNIT_STATS: dict[UnitType, UnitStats] = {
    UnitType.INFANTRY: UnitStats(
        max_hp=10,
        move=2,
        attack=4,
        defense=5,
        attack_range=1,
        vision=3,
        recruit_cost=3,
        upkeep=1,
        can_capture=True,
    ),
    UnitType.ARMOR: UnitStats(
        max_hp=10,
        move=4,
        attack=7,
        defense=4,
        attack_range=1,
        vision=3,
        recruit_cost=6,
        upkeep=2,
        can_capture=True,
    ),
    UnitType.ARTILLERY: UnitStats(
        max_hp=10,
        move=1,
        attack=5,
        defense=2,
        attack_range=3,
        vision=3,
        recruit_cost=5,
        upkeep=2,
        can_capture=False,
    ),
}

SETTLEMENT_STATS: dict[Terrain, SettlementStats] = {
    Terrain.TOWN: SettlementStats(income=2, repair_bonus=2),
    Terrain.CITY: SettlementStats(income=3, repair_bonus=2),
}

# Locked v0.1 defaults (SPEC §6.1, §6.4, §19).
UNIT_CAP = 20
MUSTER_BANK_CAP = 20
MAX_UNIT_HP = 10
