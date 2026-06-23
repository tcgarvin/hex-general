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

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.engine.hex_coords import Axial
from app.engine.stats import (
    MUSTER_BANK_CAP,
    UNIT_CAP,
    Terrain,
    UnitType,
)

PlayerId = str
UnitId = int


class Hex(BaseModel):
    """A single map cell: its coordinate, base terrain, and road layer.

    ``road`` is a layer on top of terrain (SPEC §3.3, §4.1): a unit whose entire
    path follows connected road hexes gets +1 movement. A bridge hex (terrain
    ``BRIDGE``) is what a road becomes where it crosses a river (SPEC §3.4).
    """

    coord: Axial
    terrain: Terrain
    road: bool = False


class GameMap(BaseModel):
    """Static board geometry: dimensions and the terrain of every hex.

    Holds only content that does not change during play. Dynamic facts
    (settlement control, unit positions) live on `GameState`, not here.
    """

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    hexes: list[Hex]


class Settlement(BaseModel):
    """Dynamic ownership of a settlement hex (SPEC §3.2, §14).

    The settlement's *kind* (town vs city) is the terrain at ``coord`` —
    ``Terrain.TOWN``/``Terrain.CITY`` on the map — not stored here, so there is
    one source of truth. This record carries only the part that changes during
    play: ``controlled_by``, where ``None`` means neutral / uncontrolled (a
    single, well-defined meaning). Control changes only via the capture phase
    (SPEC §14.1).
    """

    coord: Axial
    controlled_by: PlayerId | None = None


class Unit(BaseModel):
    """A single unit instance (SPEC §5).

    Static type stats (move, attack, vision, …) are looked up from
    `app.engine.stats.UNIT_STATS` by ``type``; only per-instance state lives
    here. ``hp`` is clamped to 0..10 (SPEC §5.2); a unit at 0 HP is destroyed.
    """

    id: UnitId
    type: UnitType
    owner: PlayerId
    coord: Axial
    hp: int = Field(ge=0, le=10)


class Player(BaseModel):
    """A participant's mutable economy state (SPEC §6).

    ``bank`` is the muster bank, clamped to 0..`MUSTER_BANK_CAP` (SPEC §6.3,
    §6.4). Settlement control is not duplicated here — derive it from
    `GameState.settlements` to keep one source of truth.
    """

    id: PlayerId
    bank: int = Field(default=0, ge=0, le=MUSTER_BANK_CAP)


class VictoryConfig(BaseModel):
    """Scenario-defined victory parameters (SPEC §15).

    Defaults match the recommended v0.1 rules: lose at zero controlled cities,
    otherwise score settlements at the turn limit (town=1, city=3).
    """

    turn_limit: int = Field(gt=0)
    city_elimination: bool = True
    town_points: int = 1
    city_points: int = 3


class GameConfig(BaseModel):
    """Per-game limits and victory config (SPEC §6.1, §6.4, §15)."""

    unit_cap: int = UNIT_CAP
    muster_bank_cap: int = MUSTER_BANK_CAP
    victory: VictoryConfig


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
    config: GameConfig

    def hex_at(self, coord: Axial) -> Hex | None:
        """Return the hex at ``coord``, or ``None`` if off-map."""
        for hex_ in self.map.hexes:
            if hex_.coord == coord:
                return hex_
        return None

    def unit_at(self, coord: Axial) -> Unit | None:
        """Return the (at most one) unit at ``coord``, or ``None`` (SPEC §8.1)."""
        for unit in self.units:
            if unit.coord == coord:
                return unit
        return None

    def settlement_at(self, coord: Axial) -> Settlement | None:
        """Return the settlement at ``coord``, or ``None`` if none is there."""
        for settlement in self.settlements:
            if settlement.coord == coord:
                return settlement
        return None


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


Order = Annotated[
    MoveOrder | BarrageOrder | RestOrder | HoldOrder,
    Field(discriminator="kind"),
]
"""Tagged union of the four order types, discriminated on ``kind``."""


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
