"""Typed, serializable domain model for the hex wargame (SPEC §3–6, roadmap P0).

`GameState` is the canonical state blob: one self-contained value holding the
map, settlements, units, and players for a single game at a single turn. It is
designed to round-trip losslessly through JSON (it is stored as JSONB in one
``games`` row, per the roadmap's architecture decisions) and to be the input
and output of the pure ``resolve_turn`` engine built in later phases.

`Order` is the planning-phase input union (SPEC §7); orders are *not* part of
`GameState` — they are passed alongside it into the engine.

Identity types:
    PlayerId: stable string id ("blue"/"red", later a session token).
    UnitId:   integer id; combat tie-breaking uses "lower unit ID" (SPEC §12.6).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from app.engine.economy_rules import Building, SiteKind, Stocks, WorldEconomy
from app.engine.hex_coords import Axial
from app.engine.stats import (
    COLUMN_RULES,
    MUSTER_BANK_CAP,
    SETTLEMENT_DEFENSE_BONUS,
    SETTLEMENT_TERRAINS,
    TERRAIN_STATS,
    UNIT_CAP,
    Terrain,
    UnitType,
)

PlayerId = str
UnitId = int

NEUTRAL: PlayerId = "neutral"
"""Owner of world garrisons: hostile to every player, never a `Player` itself.

Garrison units hold the neutral sites of a world (economy draft §04). They always
hold, never capture, and have no economy; settlement control stays ``None``
(neutral) while they stand on it."""


class Facing(StrEnum):
    """Which way a unit's sprite faces on the board: left or right only.

    Purely cosmetic render state — the game has no facing *mechanic* (movement is
    pulses, combat is omnidirectional). It exists so the client can mirror one
    sprite horizontally instead of authoring per-direction art (roadmap Phase 8,
    slice U0). Semantics: a unit faces the way it last moved — its final completed
    move step of the turn reduced to a net horizontal sign on the flat-top board's
    ``q`` axis (rightward → `RIGHT`, leftward → `LEFT`; a purely vertical step
    keeps the prior facing). No rotation, no 8-way. `RIGHT` is the default.
    """

    LEFT = "left"
    RIGHT = "right"


DEFAULT_FACING: Facing = Facing.RIGHT
"""The facing used when no better signal exists (no enemies, same column)."""


class Posture(StrEnum):
    """How a unit is set up (WORLD_SPEC §4).

    ``DEPLOYED`` is the v0.1 unit: one per hex, exerts and obeys ZOC, fights.
    ``COLUMN`` is packed for travel: fast, stacks with friendly columns, exerts no
    ZOC, cannot attack, and defends badly.
    """

    DEPLOYED = "deployed"
    COLUMN = "column"


class Hex(BaseModel):
    """A single map cell: its coordinate, base terrain, and road layer.

    ``road`` is a layer on top of terrain (SPEC §3.3, §4.1): a unit whose entire
    path follows connected road hexes gets +1 movement. A bridge hex (terrain
    ``BRIDGE``) is what a road becomes where it crosses a river (SPEC §3.4).
    Frozen: the map is static for the life of a game.
    """

    model_config = ConfigDict(frozen=True)

    coord: Axial
    terrain: Terrain
    road: bool = False


MAX_SITE_LEVEL = 3
"""Farms, oil fields, steel mills and factories come in levels 1–3 (user, 2026-11-10)."""

BASE_FOOTPRINT: tuple[Axial, ...] = (
    Axial(q=0, r=0),
    Axial(q=1, r=0),
    Axial(q=0, r=1),
    Axial(q=1, r=1),
)
"""Axial offsets, from a base's anchor hex, of the four hexes a base may grow over.

World generation keeps all four clear for every slot (user, 2026-11-10). Every base
is size one for now: only the anchor ``(0, 0)`` is the base. Base growth will hook
in here, with a stage on the base that says how many of these hexes it covers."""


def base_footprint(anchor: Axial) -> tuple[Axial, ...]:
    """The hexes of the footprint reserved for a base anchored at ``anchor``."""
    return tuple(Axial(q=anchor.q + d.q, r=anchor.r + d.r) for d in BASE_FOOTPRINT)


class MapSite(BaseModel):
    """A site's static role in a world economy, fixed at generation.

    A site is the ownership-bearing thing in a world (VISION §6): its `Settlement`
    record in the state says who holds it. A city site stands on its city hex; a
    farm, oil field, steel mill, factory or base stands on its own plains hex
    beside a town or a road (towns themselves hold no ownership in a world).
    ``garrison`` is the tier (1–4) of the neutral garrison the site starts with,
    or 0 for a base, which is never garrisoned. It stays on the map after the
    garrison is gone so the strategic view can always show how hard a site was.
    ``level`` (1–3) raises a farm's, oil field's, steel mill's or factory's
    income; cities and bases are always level 1. It defaults so maps stored
    before levels existed still decode.
    """

    model_config = ConfigDict(frozen=True)

    coord: Axial
    kind: SiteKind
    garrison: int = Field(ge=0, le=4)
    level: int = Field(default=1, ge=1, le=MAX_SITE_LEVEL)

    @model_validator(mode="after")
    def _levelled_kind(self) -> MapSite:
        if self.level != 1 and self.kind in (SiteKind.BASE, SiteKind.CITY):
            raise ValueError(f"a {self.kind} site is always level 1, got {self.level}")
        return self

    @property
    def plot(self) -> tuple[Axial, ...]:
        """The hexes this site keeps clear on the map: a base's whole footprint,
        any other site's own hex."""
        if self.kind is SiteKind.BASE:
            return base_footprint(self.coord)
        return (self.coord,)


class GameMap(BaseModel):
    """Static board geometry: dimensions and the terrain of every hex.

    Holds only content that does not change during play. Dynamic facts
    (settlement control, unit positions) live on `GameState`, not here.

    Frozen, so engine phases share one map between successive states instead of
    deep-copying it (a world map is ~10^5 hexes; WORLD_SPEC §10). `index` is a
    lazily built coord → hex lookup, safe to cache because the map never changes.
    """

    model_config = ConfigDict(frozen=True)

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    hexes: list[Hex]
    # World maps only: every site (city, farm, oil field, steel mill, factory),
    # plus the base site of every slot (WORLD_SPEC §6). Empty for v0.1 scenario maps.
    sites: list[MapSite] = Field(default_factory=list)
    _index: dict[Axial, Hex] | None = PrivateAttr(default=None)
    _site_index: dict[Axial, MapSite] | None = PrivateAttr(default=None)
    _plot_hexes: frozenset[Axial] | None = PrivateAttr(default=None)
    _column_costs: dict[tuple[int, int], int] | None = PrivateAttr(default=None)
    _sight_blockers: frozenset[Axial] | None = PrivateAttr(default=None)

    @property
    def index(self) -> dict[Axial, Hex]:
        """Coord → hex for every on-map hex (built once, then cached)."""
        if self._index is None:
            self._index = {h.coord: h for h in self.hexes}
        return self._index

    @property
    def site_index(self) -> dict[Axial, MapSite]:
        """Coord → site for every world site (built once, then cached)."""
        if self._site_index is None:
            self._site_index = {s.coord: s for s in self.sites}
        return self._site_index

    @property
    def plot_hexes(self) -> frozenset[Axial]:
        """Every hex a site keeps clear (`MapSite.plot`): no starting unit or
        garrison is placed there. Built once, then cached."""
        if self._plot_hexes is None:
            self._plot_hexes = frozenset(c for s in self.sites for c in s.plot)
        return self._plot_hexes

    @property
    def sight_blockers(self) -> frozenset[Axial]:
        """Every hex whose terrain blocks line of sight (SPEC §9.2). Built once,
        then cached, like `index`."""
        if self._sight_blockers is None:
            self._sight_blockers = frozenset(
                h.coord
                for h in self.hexes
                if TERRAIN_STATS[h.terrain].blocks_line_of_sight
            )
        return self._sight_blockers

    @property
    def column_costs(self) -> dict[tuple[int, int], int]:
        """``(q, r)`` → pulses for a column to enter that hex, passable hexes only.

        A road hex costs `ColumnRules.road_step_pulses`, any other passable hex
        `ColumnRules.offroad_step_pulses` (WORLD_SPEC §4.2). Keyed by plain tuples
        because route search touches tens of thousands of hexes per call. Built
        once, then cached, like `index`.
        """
        if self._column_costs is None:
            self._column_costs = {
                (h.coord.q, h.coord.r): (
                    COLUMN_RULES.road_step_pulses
                    if h.road
                    else COLUMN_RULES.offroad_step_pulses
                )
                for h in self.hexes
                if TERRAIN_STATS[h.terrain].passable
            }
        return self._column_costs


class Settlement(BaseModel):
    """Dynamic ownership of a settlement hex (SPEC §3.2, §14).

    The settlement's *kind* is static map data, not stored here, so there is one
    source of truth: in a v0.1 game it is the terrain at ``coord``
    (``Terrain.TOWN``/``Terrain.CITY``); in a world it is the `MapSite` at
    ``coord`` (every site but a vacant base slot has a settlement, and towns have
    none). This record carries only the part that changes during play:
    ``controlled_by``, where ``None`` means neutral / uncontrolled (a single,
    well-defined meaning). Control changes only via the capture phase (SPEC §14.1).
    """

    coord: Axial
    controlled_by: PlayerId | None = None


class Unit(BaseModel):
    """A single unit instance (SPEC §5).

    Static type stats (move, attack, vision, …) are looked up from
    `app.engine.stats.UNIT_STATS` by ``type``; only per-instance state lives
    here. ``hp`` is clamped to 0..10 (SPEC §5.2); a unit at 0 HP is destroyed.

    ``facing`` is cosmetic render state (see `Facing`): the left/right direction
    the sprite faces, set by the unit's last movement. It defaults so state JSONB
    persisted before this field existed still validates (older games load with
    the default facing).

    ``posture`` is deployed or column (WORLD_SPEC §4). ``disordered`` marks a unit
    that auto-deployed on contact: it defends worse and cannot attack until the
    end of the next turn's execution (WORLD_SPEC §4.5). Both default so older
    persisted states load as deployed, ordered units.
    """

    id: UnitId
    type: UnitType
    owner: PlayerId
    coord: Axial
    hp: int = Field(ge=0, le=10)
    facing: Facing = DEFAULT_FACING
    posture: Posture = Posture.DEPLOYED
    disordered: bool = False


def default_facing(coord: Axial, owner: PlayerId, units: Iterable[Unit]) -> Facing:
    """Cosmetic facing for a freshly placed unit: face the nearest enemy.

    Used for recruits and turn-0 scenario units, which have no movement to derive
    facing from. Deterministic: pick the enemy (any unit not owned by ``owner``)
    at the least hex distance from ``coord``, ties broken by lowest unit id, then
    face `RIGHT` if that enemy's ``q`` is greater than ``coord``'s, `LEFT` if less.
    With no enemies on the board, or an enemy in the same column, use
    `DEFAULT_FACING`.
    """
    # One pass with the distance inlined: recruitment asks this per recruit over
    # every unit on a world board. Same pick as `min` over (distance, id).
    q, r = coord.q, coord.r
    nearest: Unit | None = None
    best = (0, 0)
    for unit in units:
        if unit.owner == owner:
            continue
        dq = unit.coord.q - q
        dr = unit.coord.r - r
        key = ((abs(dq) + abs(dq + dr) + abs(dr)) // 2, unit.id)
        if nearest is None or key < best:
            nearest, best = unit, key
    if nearest is None:
        return DEFAULT_FACING
    if nearest.coord.q > coord.q:
        return Facing.RIGHT
    if nearest.coord.q < coord.q:
        return Facing.LEFT
    return DEFAULT_FACING


class Construction(BaseModel):
    """A building level under construction at a player's base.

    Paid for when it starts; it completes in the economy phase of turn
    ``completes_on``.
    """

    building: Building
    level: int = Field(ge=1)
    completes_on: int = Field(ge=0)


class Player(BaseModel):
    """A participant's mutable economy state (SPEC §6, economy draft).

    ``bank`` is the v0.1 muster bank, clamped to 0..`GameConfig.muster_bank_cap`
    by the economy phase (SPEC §6.3, §6.4). A world game uses ``stocks``,
    ``buildings`` and ``construction`` instead and leaves ``bank`` at 0.
    ``buildings`` maps a building to its completed level; a building never built
    is absent (level 0). ``construction`` is ``None`` when nothing is being built.
    Settlement control is not duplicated here — derive it from
    `GameState.settlements` to keep one source of truth.
    """

    id: PlayerId
    bank: int = Field(default=0, ge=0)
    stocks: Stocks = Field(default_factory=Stocks)
    buildings: dict[Building, int] = Field(default_factory=dict)
    construction: Construction | None = None

    def level(self, building: Building) -> int:
        """The completed level of ``building`` (0 if never built)."""
        return self.buildings.get(building, 0)

    def level_on(self, building: Building, turn: int) -> int:
        """The level of ``building`` once construction due by ``turn`` is done.

        The economy phase finishes that construction before recruitment, so this
        is the level a recruit ordered for turn ``turn`` meets.
        """
        work = self.construction
        if work is not None and work.building is building and work.completes_on <= turn:
            return work.level
        return self.level(building)


class VictoryConfig(BaseModel):
    """Scenario-defined victory parameters (SPEC §15).

    Defaults match the recommended v0.1 rules: lose at zero controlled cities,
    otherwise score settlements at the turn limit (town=1, city=3).
    """

    turn_limit: int = Field(gt=0)
    city_elimination: bool = True
    town_points: int = 1
    city_points: int = 3


class CommandCap(BaseModel):
    """A unit cap that grows with holdings (WORLD_SPEC §7).

    A player may field ``base + per_town × towns + per_city × cities`` units, up
    to ``ceiling``: land, not grinding, grows an army.
    """

    model_config = ConfigDict(frozen=True)

    base: int = Field(default=4, ge=0)
    per_town: int = Field(default=1, ge=0)
    per_city: int = Field(default=2, ge=0)
    ceiling: int = Field(default=15, ge=1)

    def cap(self, towns: int, cities: int) -> int:
        """The cap for a player holding ``towns`` and ``cities``."""
        return min(
            self.ceiling, self.base + self.per_town * towns + self.per_city * cities
        )


class GameConfig(BaseModel):
    """Per-game limits and victory config (SPEC §6.1, §6.4, §15).

    ``command_cap``, when set, replaces the flat ``unit_cap`` with a cap that grows
    with holdings (still never above ``unit_cap``). ``settlement_repair`` is the
    HP a unit resting in a town or city regains per turn. World games set both
    (WORLD_SPEC §7); v0.1 games keep the defaults.
    """

    unit_cap: int = UNIT_CAP
    muster_bank_cap: int = MUSTER_BANK_CAP
    command_cap: CommandCap | None = None
    settlement_repair: int = Field(default=2, ge=1)
    # Set for world games: the three-resource economy replaces muster, and army
    # size comes from barracks and sites instead of ``command_cap``.
    economy: WorldEconomy | None = None
    victory: VictoryConfig


class StandingTravel(BaseModel):
    """A travel order still in progress, carried from turn to turn (WORLD_SPEC §5).

    ``route`` is the hexes the unit has yet to enter, in order, fixed when the
    order was issued (routes are planned over the static map, so they never need
    recomputing). Each turn the unit has no fresh order, the engine marches it
    along ``route``; the order ends on arrival, contact, an explicit order, or the
    unit's destruction. ``on_arrival`` is the posture to finish in.
    """

    unit_id: UnitId
    destination: Axial
    route: list[Axial] = Field(min_length=1)
    on_arrival: Posture = Posture.DEPLOYED


class GameState(BaseModel):
    """The canonical, serializable state of one game at one turn.

    This is the blob stored in the ``games`` row and the value threaded through
    the engine: ``resolve_turn(state, orders) -> (new_state, event_log)``.
    Lookups (`hex_at`, `unit_at`, `settlement_at`) are convenience helpers built
    on demand; the stored representation is plain lists so it serializes cleanly
    (axial coords can't be JSON object keys).
    """

    model_config = ConfigDict(extra="forbid")

    turn: int = Field(ge=0)
    map: GameMap
    settlements: list[Settlement] = Field(default_factory=list)
    units: list[Unit] = Field(default_factory=list)
    players: list[Player] = Field(default_factory=list)
    # Slots that have conceded (SPEC §15). Public info; a resigned player is out
    # of contention for victory (`app.engine.victory`). Empty in a fresh state.
    resigned: list[PlayerId] = Field(default_factory=list)
    # Multi-turn travel orders in progress, one per unit, sorted by unit id
    # (WORLD_SPEC §5). Private to each unit's owner: fog redaction drops the rest.
    standing_orders: list[StandingTravel] = Field(default_factory=list)
    config: GameConfig

    @model_validator(mode="after")
    def _banks_within_cap(self) -> GameState:
        """Every bank is within this game's ceiling (SPEC §6.4, WORLD_SPEC §7)."""
        economy = self.config.economy
        for player in self.players:
            if player.bank > self.config.muster_bank_cap:
                raise ValueError(
                    f"player {player.id!r} bank {player.bank} exceeds the cap of "
                    f"{self.config.muster_bank_cap}"
                )
            if economy is None:
                continue
            limit = economy.storage_limit(player.level(Building.DEPOT))
            if player.stocks != player.stocks.clamped(limit):
                raise ValueError(
                    f"player {player.id!r} stocks {player.stocks} are outside "
                    f"0..{limit}"
                )
        return self

    def hex_at(self, coord: Axial) -> Hex | None:
        """Return the hex at ``coord``, or ``None`` if off-map."""
        return self.map.index.get(coord)

    def hostile(self, a: PlayerId, b: PlayerId) -> bool:
        """Whether players ``a`` and ``b`` are enemies (WORLD_SPEC §3).

        Every rule that says "enemy" goes through this one predicate. Today every
        pair of distinct players is hostile (a free-for-all); alliances will change
        only the data this consults, not the rules that call it.
        """
        return a != b

    def fork(self) -> GameState:
        """Return a copy whose dynamic parts are independent of this state.

        Units, settlements, players and the resignation list are deep-copied so a
        phase can mutate them freely; the frozen static map is shared, not copied.
        """
        return self.fork_with_units([u.model_copy() for u in self.units])

    def fork_with_units(self, units: list[Unit]) -> GameState:
        """`fork`, but with ``units`` as the new state's units instead of copies.

        For a phase that already copied every unit to work on: forking would copy
        them all again only to throw the copies away. ``units`` must not be
        shared with any other state.
        """
        return self.model_copy(
            update={
                "units": units,
                "settlements": [s.model_copy() for s in self.settlements],
                "players": [
                    p.model_copy(update={"buildings": dict(p.buildings)})
                    for p in self.players
                ],
                "resigned": list(self.resigned),
                "standing_orders": [o.model_copy() for o in self.standing_orders],
            }
        )

    def unit_at(self, coord: Axial) -> Unit | None:
        """Return the deployed unit at ``coord``, or ``None`` (SPEC §8.1).

        At most one deployed unit holds a hex; packed columns may share it
        (WORLD_SPEC §4.2) and are listed by `units_at`.
        """
        for unit in self.units:
            if unit.coord == coord and unit.posture is Posture.DEPLOYED:
                return unit
        return None

    def units_at(self, coord: Axial) -> list[Unit]:
        """Return every unit at ``coord``: the deployed one, if any, and columns."""
        return [u for u in self.units if u.coord == coord]

    def unit_cap_for(self, player: PlayerId) -> int:
        """How many units ``player`` may field right now (SPEC §6.1, WORLD_SPEC §7)."""
        return self._unit_cap(player, recruiting=False)

    def recruit_cap_for(self, player: PlayerId) -> int:
        """The army size this turn's recruits are checked against.

        As `unit_cap_for`, except that a barracks level finishing this turn already
        counts: the economy phase completes it before recruitment runs.
        """
        return self._unit_cap(player, recruiting=True)

    def _unit_cap(self, player: PlayerId, *, recruiting: bool) -> int:
        economy = self.config.economy
        if economy is not None:
            sites = self.map.site_index
            held = sum(
                1
                for s in self.settlements
                if s.controlled_by == player
                and sites[s.coord].kind is not SiteKind.BASE
            )
            owner = self.player(player)
            barracks = (
                owner.level_on(Building.BARRACKS, self.turn)
                if recruiting
                else owner.level(Building.BARRACKS)
            )
            return min(self.config.unit_cap, economy.army.size(barracks, held))
        rule = self.config.command_cap
        if rule is None:
            return self.config.unit_cap
        index = self.map.index
        towns = cities = 0
        for settlement in self.settlements:
            if settlement.controlled_by != player:
                continue
            if index[settlement.coord].terrain is Terrain.CITY:
                cities += 1
            else:
                towns += 1
        return min(self.config.unit_cap, rule.cap(towns, cities))

    def player(self, player_id: PlayerId) -> Player:
        """The `Player` record for ``player_id`` (raises if there is none)."""
        for player in self.players:
            if player.id == player_id:
                return player
        raise KeyError(f"no player {player_id!r}")

    def settlement_at(self, coord: Axial) -> Settlement | None:
        """Return the settlement at ``coord``, or ``None`` if none is there."""
        for settlement in self.settlements:
            if settlement.coord == coord:
                return settlement
        return None

    def settlement_ground(self, coord: Axial) -> bool:
        """Whether ``coord`` gives the settlement defence and repair bonus.

        Town and city terrain always does: that is the whole v0.1 rule, and in a
        world a town keeps its terrain bonus though it holds no ownership. A world
        site's hex does too once its settlement exists, which is every site but a
        vacant base slot (no settlement, nothing there yet). Every base is size
        one today, so a base covers only its anchor hex; when bases grow over
        `BASE_FOOTPRINT`, the other hexes a grown base covers belong here.

        Scans the settlements for a site hex; a phase asking about many hexes
        uses `ground` instead.
        """
        return _settlement_ground(
            self.map, coord, lambda c: self.settlement_at(c) is not None
        )

    def terrain_defense(self, coord: Axial) -> int:
        """The terrain defence bonus a stationary defender on ``coord`` gets
        (SPEC §4.2): its terrain's, or `SETTLEMENT_DEFENSE_BONUS` on settlement
        ground if that is higher."""
        return _terrain_defense(self.map, coord, self.settlement_ground(coord))

    def ground(self) -> Ground:
        """`settlement_ground` and `terrain_defense` for many hexes, from indexes."""
        return Ground(self.map, frozenset(s.coord for s in self.settlements))


class Ground:
    """Settlement ground and terrain defence for one state (`GameState.ground`).

    Answers what `GameState.settlement_ground` and `GameState.terrain_defense`
    do without scanning the settlement list per hex. It snapshots which hexes
    hold a settlement record. No phase of a turn adds or removes one (capture
    changes only who controls it, and ground does not depend on control), so a
    snapshot taken at the start of a phase holds for the whole phase.
    """

    def __init__(self, game_map: GameMap, settled: frozenset[Axial]) -> None:
        self._map = game_map
        self._settled = settled

    def settlement_ground(self, coord: Axial) -> bool:
        """`GameState.settlement_ground` for ``coord``."""
        return _settlement_ground(self._map, coord, self._settled.__contains__)

    def terrain_defense(self, coord: Axial) -> int:
        """`GameState.terrain_defense` for ``coord``."""
        return _terrain_defense(self._map, coord, self.settlement_ground(coord))


def _settlement_ground(
    game_map: GameMap, coord: Axial, has_settlement: Callable[[Axial], bool]
) -> bool:
    """The settlement-ground rule (`GameState.settlement_ground`), given a way to
    ask whether a hex holds a settlement record."""
    if game_map.index[coord].terrain in SETTLEMENT_TERRAINS:
        return True
    return coord in game_map.site_index and has_settlement(coord)


def _terrain_defense(game_map: GameMap, coord: Axial, on_ground: bool) -> int:
    """The terrain-defence rule (`GameState.terrain_defense`), given whether
    ``coord`` is settlement ground."""
    bonus = TERRAIN_STATS[game_map.index[coord].terrain].defense_bonus
    if on_ground:
        return max(bonus, SETTLEMENT_DEFENSE_BONUS)
    return bonus


class MoveOrder(BaseModel):
    """Follow an exact planned path of adjacent hexes (SPEC §7.1).

    ``path`` is the sequence of hexes to enter, in order, one per movement
    pulse. Combat occurs if enemy contact happens; the unit does not reroute if
    blocked.
    """

    kind: Literal["move"] = "move"
    unit_id: UnitId
    path: list[Axial] = Field(min_length=1)


class BarrageOrder(BaseModel):
    """Artillery fires at a visible target hex within range (SPEC §7.2)."""

    kind: Literal["barrage"] = "barrage"
    unit_id: UnitId
    target: Axial


class RestOrder(BaseModel):
    """Do not move; repair HP if undamaged during execution (SPEC §7.3)."""

    kind: Literal["rest"] = "rest"
    unit_id: UnitId


class HoldOrder(BaseModel):
    """Do not move; gain a defensive bonus if attacked (SPEC §7.4)."""

    kind: Literal["hold"] = "hold"
    unit_id: UnitId


class MarchOrder(BaseModel):
    """Travel as a column along an exact path (WORLD_SPEC §4.4, §5).

    A deployed unit packs first (the opening pulses of the turn) and then
    marches; a column marches from pulse 1. Steps cost pulses (road vs off-road)
    rather than one per pulse. With ``deploy_on_arrival`` the unit deploys, clean,
    at the end of the turn in which it completes the path.
    """

    kind: Literal["march"] = "march"
    unit_id: UnitId
    path: list[Axial] = Field(min_length=1)
    deploy_on_arrival: bool = False


class DeployOrder(BaseModel):
    """A column unpacks where it stands, ready to fight next turn (WORLD_SPEC §4.5)."""

    kind: Literal["deploy"] = "deploy"
    unit_id: UnitId


class TravelOrder(BaseModel):
    """Go to a destination as a column, over as many turns as it takes (WORLD_SPEC §5).

    The engine plans the route (cheapest column path over the static map,
    through the optional ``via`` waypoints in order), packs the unit if it is
    deployed, and keeps it marching every turn it has no fresh order. It finishes
    in the ``on_arrival`` posture. Contact ends the order early: the column
    deploys where it meets the enemy.
    """

    kind: Literal["travel"] = "travel"
    unit_id: UnitId
    destination: Axial
    via: list[Axial] = Field(default_factory=list, max_length=4)
    on_arrival: Posture = Posture.DEPLOYED


Order = Annotated[
    MoveOrder
    | BarrageOrder
    | RestOrder
    | HoldOrder
    | MarchOrder
    | DeployOrder
    | TravelOrder,
    Field(discriminator="kind"),
]
"""Tagged union of the order types, discriminated on ``kind``."""


class Recruitment(BaseModel):
    """A planning-phase request to recruit a new unit (SPEC §6.5).

    Recruitment is not a per-unit `Order` — it creates a unit that does not exist
    yet, so it has no ``unit_id``. A request names the owning player, the unit
    ``type`` to build, and the friendly-city ``coord`` to deploy it on; the
    recruitment phase validates affordability, the unit cap, and placement, then
    assigns the new unit's id (SPEC §11.3 Phase 6).
    """

    model_config = ConfigDict(extra="forbid")

    owner: PlayerId
    type: UnitType
    coord: Axial


class EconomyOrders(BaseModel):
    """One world player's base orders for a turn (economy draft §02, §05).

    ``build`` starts the next level of that building in this turn's economy
    phase, paid on the spot; ``None`` starts nothing. ``disband`` is the player's
    pick, in order of preference, of who goes if a shortage forces a disband this
    turn; with no qualifying pick the weakest qualifying unit goes.
    """

    model_config = ConfigDict(extra="forbid")

    owner: PlayerId
    build: Building | None = None
    disband: list[UnitId] = Field(default_factory=list)
