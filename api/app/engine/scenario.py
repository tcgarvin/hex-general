"""Scenario authoring format and loader (SPEC §3, §4, §15; roadmap Phase 1).

A *scenario* is the authored starting content for a game: the map terrain, the
road layer, which settlements each player begins controlling, the starting
units, the player roster, and the victory configuration. It is the only thing a
human writes by hand, so the format favours readability over machine-friendliness:
the map is two parallel grids of single characters (one for terrain, one for
roads), not a list of 1600 hex objects.

This module is an **I/O boundary**, not part of the pure engine: it parses and
*validates* untrusted external JSON and emits a clean, turn-0 `GameState`. Any
malformed input raises `ScenarioError` with context describing exactly what is
wrong; the loader never returns a partially-valid state.

Coordinate convention
---------------------
A scenario is authored as a rectangular **offset** grid: ``rows[row][col]`` is the
character for grid cell ``(col, row)`` (``col`` 0..width-1, ``row`` 0..height-1),
and ``settlements``/``units`` give their ``q``/``r`` in the same offset space. At
load we convert each authored cell to canonical **axial** via
`offset_to_axial` (flat-top odd-q), so the rectangle becomes a rectangular
*region* of the hex plane — the board renders it as a flat-top rectangle and the
engine keeps clean axial math. (Pass ``offset_layout=False`` to take the authored
coords as raw axial unchanged; engine test fixtures use this to build synthetic
topology directly. See `offset_to_axial`.)
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.engine.hex_coords import Axial, neighbors, offset_to_axial
from app.engine.models import (
    GameConfig,
    GameMap,
    GameState,
    Hex,
    Player,
    PlayerId,
    Settlement,
    Unit,
    VictoryConfig,
    default_facing,
)
from app.engine.stats import (
    SETTLEMENT_TERRAINS,
    TERRAIN_STATS,
    UNIT_STATS,
    Terrain,
    UnitType,
)

# Single-character terrain codes for the authored grid (SPEC §4.1).
TERRAIN_CHARS: dict[str, Terrain] = {
    ".": Terrain.PLAINS,
    "w": Terrain.WOODS,
    "h": Terrain.HILLS,
    "~": Terrain.RIVER,
    "=": Terrain.BRIDGE,
    "t": Terrain.TOWN,
    "c": Terrain.CITY,
}
"""Map from terrain grid character to `Terrain`."""

TERRAIN_TO_CHAR: dict[Terrain, str] = {v: k for k, v in TERRAIN_CHARS.items()}
"""Reverse of `TERRAIN_CHARS`, for rendering a `GameMap` back to a grid."""

ROAD_CHAR = "#"
"""Character marking a road in the ``roads`` grid; any other allowed char is none."""

NO_ROAD_CHAR = "."
"""Character marking the absence of a road in the ``roads`` grid."""


class ScenarioError(Exception):
    """Raised when a scenario fails structural or semantic validation.

    Carries a human-readable message naming the offending hex or entry so the
    author can find and fix the problem.
    """


class ScenarioSettlement(BaseModel):
    """Starting control of one settlement hex (SPEC §14).

    Only ownership is authored here; the settlement's *kind* (town/city) is read
    from the map terrain at ``(q, r)``. ``owner`` of ``None`` means the
    settlement starts neutral.
    """

    model_config = ConfigDict(extra="forbid")

    q: int
    r: int
    owner: PlayerId | None = None


class ScenarioUnit(BaseModel):
    """A starting unit (SPEC §5). ``hp`` defaults to the type's max when omitted."""

    model_config = ConfigDict(extra="forbid")

    type: UnitType
    owner: PlayerId
    q: int
    r: int
    hp: int | None = None


class ScenarioPlayer(BaseModel):
    """A player in the roster, with starting muster bank (SPEC §6).

    Accepts either an object ``{"id": "blue", "bank": 5}`` or a bare string
    ``"blue"`` (bank defaults to 0) for terse authoring.
    """

    model_config = ConfigDict(extra="forbid")

    id: PlayerId
    bank: int = 0

    @field_validator("id", mode="before")
    @classmethod
    def _reject_empty(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("player id must be non-empty")
        return value


class Scenario(BaseModel):
    """The authored scenario file schema (parsed, not yet validated against rules).

    Structural constraints that Pydantic can express live here; cross-field and
    grid-geometry rules (row sizes, bridges on rivers, units on passable terrain,
    …) are enforced by `load_scenario`, which raises `ScenarioError`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    terrain: list[str]
    roads: list[str] = Field(default_factory=list)
    settlements: list[ScenarioSettlement] = Field(default_factory=list)
    units: list[ScenarioUnit] = Field(default_factory=list)
    players: list[ScenarioPlayer] = Field(min_length=1)
    victory: VictoryConfig

    @field_validator("players", mode="before")
    @classmethod
    def _coerce_player_strings(cls, value: object) -> object:
        """Allow bare strings in the ``players`` list as a shorthand for ids."""
        if isinstance(value, list):
            return [{"id": item} if isinstance(item, str) else item for item in value]
        return value


def _authored_axial(col: int, row: int, *, offset_layout: bool) -> Axial:
    """Map an authored offset cell to canonical axial (or raw axial if disabled)."""
    if offset_layout:
        return offset_to_axial(col, row)
    return Axial(q=col, r=row)


def _validate_grid(grid: list[str], width: int, height: int, name: str) -> None:
    """Check a character grid has exactly ``height`` rows of ``width`` chars each."""
    if len(grid) != height:
        raise ScenarioError(
            f"{name} grid has {len(grid)} rows, expected height={height}"
        )
    for r, row in enumerate(grid):
        if len(row) != width:
            raise ScenarioError(
                f"{name} grid row {r} has {len(row)} chars, expected width={width}"
            )


def _build_hexes(scenario: Scenario, *, offset_layout: bool) -> list[Hex]:
    """Decode the terrain and road grids into `Hex` objects, validating chars."""
    _validate_grid(scenario.terrain, scenario.width, scenario.height, "terrain")

    roads = scenario.roads
    if roads:
        _validate_grid(roads, scenario.width, scenario.height, "roads")

    hexes: list[Hex] = []
    for r in range(scenario.height):
        terrain_row = scenario.terrain[r]
        road_row = roads[r] if roads else ""
        for q in range(scenario.width):
            char = terrain_row[q]
            if char not in TERRAIN_CHARS:
                raise ScenarioError(
                    f"unknown terrain char {char!r} at hex (q={q}, r={r})"
                )
            road = False
            if road_row:
                road_char = road_row[q]
                if road_char not in (ROAD_CHAR, NO_ROAD_CHAR):
                    raise ScenarioError(
                        f"unknown road char {road_char!r} at hex (q={q}, r={r}); "
                        f"use {ROAD_CHAR!r} for road or {NO_ROAD_CHAR!r} for none"
                    )
                road = road_char == ROAD_CHAR
            hexes.append(
                Hex(
                    coord=_authored_axial(q, r, offset_layout=offset_layout),
                    terrain=TERRAIN_CHARS[char],
                    road=road,
                )
            )
    return hexes


def _validate_river_bridges(hexes: list[Hex]) -> None:
    """Enforce the river/bridge/road rules of SPEC §3.4.

    - A road may not run over an (impassable) river hex — it must be a bridge.
    - A bridge hex must carry a road (a bridge *is* a road crossing a river).
    - A bridge hex must sit on the river line (be adjacent to a river hex).
    """
    terrain_by_coord = {h.coord: h.terrain for h in hexes}
    for hex_ in hexes:
        coord = hex_.coord
        if hex_.terrain is Terrain.RIVER and hex_.road:
            raise ScenarioError(
                f"road runs over impassable river at (q={coord.q}, r={coord.r}); "
                "use a bridge instead"
            )
        if hex_.terrain is Terrain.BRIDGE:
            if not hex_.road:
                raise ScenarioError(
                    f"bridge at (q={coord.q}, r={coord.r}) has no road; "
                    "a bridge is a road crossing a river"
                )
            on_river = any(
                terrain_by_coord.get(n) is Terrain.RIVER for n in neighbors(coord)
            )
            if not on_river:
                raise ScenarioError(
                    f"bridge at (q={coord.q}, r={coord.r}) is not adjacent to any "
                    "river hex; bridges sit on rivers"
                )


def _build_settlements(
    scenario: Scenario,
    terrain_by_coord: dict[Axial, Terrain],
    player_ids: set[PlayerId],
    *,
    offset_layout: bool,
) -> list[Settlement]:
    """Create a `Settlement` for every town/city hex, applying authored owners.

    Settlement kind comes from the map terrain (one source of truth); the
    scenario's ``settlements`` list contributes only ownership.
    """
    owners: dict[Axial, PlayerId | None] = {}
    for entry in scenario.settlements:
        coord = _authored_axial(entry.q, entry.r, offset_layout=offset_layout)
        terrain = terrain_by_coord.get(coord)
        if terrain is None:
            raise ScenarioError(f"settlement at (q={entry.q}, r={entry.r}) is off-map")
        if terrain not in SETTLEMENT_TERRAINS:
            raise ScenarioError(
                f"settlement entry at (q={entry.q}, r={entry.r}) is on {terrain} "
                "terrain, not a town or city"
            )
        if coord in owners:
            raise ScenarioError(
                f"duplicate settlement entry for (q={entry.q}, r={entry.r})"
            )
        if entry.owner is not None and entry.owner not in player_ids:
            raise ScenarioError(
                f"settlement at (q={entry.q}, r={entry.r}) owned by undeclared "
                f"player {entry.owner!r}"
            )
        owners[coord] = entry.owner

    settlements: list[Settlement] = []
    for coord, terrain in terrain_by_coord.items():
        if terrain in SETTLEMENT_TERRAINS:
            settlements.append(Settlement(coord=coord, controlled_by=owners.get(coord)))
    settlements.sort(key=lambda s: (s.coord.r, s.coord.q))
    return settlements


def _build_units(
    scenario: Scenario,
    terrain_by_coord: dict[Axial, Terrain],
    player_ids: set[PlayerId],
    *,
    offset_layout: bool,
) -> list[Unit]:
    """Build units with sequential ids, validating placement and ownership."""
    units: list[Unit] = []
    occupied: set[Axial] = set()
    for index, entry in enumerate(scenario.units):
        unit_id = index + 1
        coord = _authored_axial(entry.q, entry.r, offset_layout=offset_layout)
        terrain = terrain_by_coord.get(coord)
        if terrain is None:
            raise ScenarioError(
                f"unit {unit_id} ({entry.type}) is off-map at (q={entry.q}, r={entry.r})"
            )
        if not TERRAIN_STATS[terrain].passable:
            raise ScenarioError(
                f"unit {unit_id} ({entry.type}) starts on impassable {terrain} "
                f"at (q={entry.q}, r={entry.r})"
            )
        if entry.owner not in player_ids:
            raise ScenarioError(
                f"unit {unit_id} ({entry.type}) owned by undeclared player "
                f"{entry.owner!r}"
            )
        if coord in occupied:
            raise ScenarioError(
                f"unit {unit_id} ({entry.type}) stacks on another unit at "
                f"(q={entry.q}, r={entry.r}); only one unit per hex (SPEC §8.1)"
            )
        occupied.add(coord)
        hp = entry.hp if entry.hp is not None else UNIT_STATS[entry.type].max_hp
        units.append(
            Unit(id=unit_id, type=entry.type, owner=entry.owner, coord=coord, hp=hp)
        )

    # Second pass: point each turn-0 unit at the nearest enemy (all units must be
    # built first, since a unit's enemies aren't known mid-loop). Cosmetic only.
    for unit in units:
        unit.facing = default_facing(unit.coord, unit.owner, units)
    return units


def load_scenario(data: dict[str, object], *, offset_layout: bool = True) -> GameState:
    """Validate a parsed scenario mapping and build the turn-0 `GameState`.

    Authored coordinates are offset (flat-top odd-q) and converted to canonical
    axial at load (see the module's *Coordinate convention*). Pass
    ``offset_layout=False`` to keep authored coords as raw axial — engine test
    fixtures use this to build synthetic topology directly.

    Raises `ScenarioError` (with context) on any structural or semantic problem;
    Pydantic `ValidationError` surfaces malformed field types/shapes.
    """
    scenario = Scenario.model_validate(data)

    hexes = _build_hexes(scenario, offset_layout=offset_layout)
    _validate_river_bridges(hexes)

    terrain_by_coord = {h.coord: h.terrain for h in hexes}
    player_ids = {p.id for p in scenario.players}
    if len(player_ids) != len(scenario.players):
        raise ScenarioError("duplicate player ids in roster")

    settlements = _build_settlements(
        scenario, terrain_by_coord, player_ids, offset_layout=offset_layout
    )
    units = _build_units(
        scenario, terrain_by_coord, player_ids, offset_layout=offset_layout
    )
    players = [Player(id=p.id, bank=p.bank) for p in scenario.players]

    return GameState(
        turn=0,
        map=GameMap(width=scenario.width, height=scenario.height, hexes=hexes),
        settlements=settlements,
        units=units,
        players=players,
        config=GameConfig(victory=scenario.victory),
    )


def load_scenario_file(path: Path) -> GameState:
    """Read a scenario JSON file from ``path`` and build its `GameState`."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScenarioError(f"cannot read scenario file {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"scenario file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ScenarioError(f"scenario file {path} must contain a JSON object")
    return load_scenario(data)
