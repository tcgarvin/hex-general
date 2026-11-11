"""Apply fog of war to a player's view of the game (SPEC §9; WORLD_SPEC §7).

`app.engine.visibility` answers "which hexes can a player see?"; this module uses
that answer to build the redacted payload the API actually returns, so the browser
never receives information the player should not have (CLAUDE.md, roadmap decision
4). Two pure transforms:

  * `redact_state` drops enemy units standing on hexes the viewer cannot see, and
    other players' standing orders. The static map and settlement control are
    public; in a world game other players' stocks, buildings and construction are
    private and come back blank (in v0.1 the banks were public).
  * `filter_events` turns the engine's replay log into what one viewer witnessed:
    each event is dropped, passed through, or passed through with its private
    fields withheld, per the table in `_witness`.

Both are pure: they return new values and never mutate their input. Visibility for
both is computed from the *given* state; for a replay that is the post-turn state,
so the viewer witnesses the turn through the fog as it stands once the dust
settles. This is a deliberate v0.1 simplification: an event at a hex the viewer
could see at the start of the turn but not the end (e.g. an enemy that fled into
the fog) is not reconstructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.engine.economy_rules import Stocks
from app.engine.events import (
    BarrageHit,
    BarrageResolved,
    ColumnHeld,
    ColumnStuck,
    CombatResolved,
    ConstructionCompleted,
    ConstructionStarted,
    ConstructionVoided,
    EconomyResolved,
    Event,
    MoveBlocked,
    OrderMooted,
    RecruitmentVoided,
    SettlementCaptured,
    StocksResolved,
    TrafficJam,
    TravelEnded,
    UnitDeployed,
    UnitDisbanded,
    UnitMoved,
    UnitPacked,
    UnitRecruited,
    UnitRepaired,
    WorldUnitRecruited,
)
from app.engine.hex_coords import Axial
from app.engine.models import GameState, Player, PlayerId, UnitId
from app.engine.stats import UnitType
from app.engine.visibility import visible_hexes

# --- What a viewer receives ----------------------------------------------------
#
# Three events carry fields that only their owner may read. The replay sends them
# as these view models, which mirror the engine's event (same ``kind``, same
# fields; `tests/engine/test_fog.py` keeps them in step) except that the private
# fields are optional. ``None`` there has exactly one meaning: withheld from this
# viewer by fog. The engine's own events never carry ``None``.


class BarrageResolvedView(BaseModel):
    """`BarrageResolved` as a viewer receives it.

    ``artillery_id`` is ``None`` when the gun that fired is not the viewer's and
    does not stand on a hex the viewer can see: the viewer sees the shells land,
    not who fired them.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["barrage_resolved"] = "barrage_resolved"
    artillery_id: UnitId | None
    target: Axial
    hit: BarrageHit | None = None
    extra_hits: list[BarrageHit] = Field(default_factory=list)


class UnitDisbandedView(BaseModel):
    """`UnitDisbanded` as a viewer receives it.

    ``shortage`` and ``chosen`` are ``None`` for anyone but the owner: others see
    the unit leave, not which stock ran out or whether it was the owner's pick.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["unit_disbanded"] = "unit_disbanded"
    unit_id: UnitId
    unit_type: UnitType
    owner: PlayerId
    coord: Axial
    shortage: Literal["fuel", "manpower"] | None
    chosen: bool | None


class WorldUnitRecruitedView(BaseModel):
    """`WorldUnitRecruited` as a viewer receives it.

    ``cost`` and ``stocks_after`` are ``None`` for anyone but the owner: others see
    a new unit appear at a site they can see, not what it cost or what is left.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["world_unit_recruited"] = "world_unit_recruited"
    unit_id: UnitId
    unit_type: UnitType
    owner: PlayerId
    coord: Axial
    cost: Stocks | None
    stocks_after: Stocks | None


ReplayEvent = Annotated[
    UnitMoved
    | MoveBlocked
    | TrafficJam
    | CombatResolved
    | BarrageResolvedView
    | OrderMooted
    | SettlementCaptured
    | UnitRepaired
    | EconomyResolved
    | UnitRecruited
    | RecruitmentVoided
    | UnitPacked
    | UnitDeployed
    | ColumnStuck
    | ColumnHeld
    | TravelEnded
    | StocksResolved
    | UnitDisbandedView
    | ConstructionStarted
    | ConstructionCompleted
    | ConstructionVoided
    | WorldUnitRecruitedView,
    Field(discriminator="kind"),
]
"""One witnessed replay event: the engine's `Event` with the view models above
standing in for the three events that carry private fields."""


def redact_state(state: GameState, viewer: PlayerId) -> GameState:
    """Return ``state`` with enemy units the ``viewer`` cannot see removed (SPEC §9).

    Keeps every unit the viewer owns plus every other unit standing on a hex the
    viewer can see, and only the viewer's own standing orders (a travel order is
    private intent, WORLD_SPEC §5). The map and settlement control pass through
    unchanged. In a world game, other players' stocks, buildings and construction
    are private and come back blank; in v0.1 the banks were public.
    """
    visible = visible_hexes(state, viewer)
    units = [u for u in state.units if u.owner == viewer or u.coord in visible]
    own = {u.id for u in state.units if u.owner == viewer}
    standing = [o for o in state.standing_orders if o.unit_id in own]
    update: dict[str, object] = {"units": units, "standing_orders": standing}
    if state.config.economy is not None:
        # A world player's stocks and base are private: other players appear with
        # an empty economy, which the view documents as unknown, not as zero.
        update["players"] = [
            p if p.id == viewer else Player(id=p.id) for p in state.players
        ]
    return state.model_copy(update=update)


def filter_events(
    events: list[Event], state_after: GameState, viewer: PlayerId
) -> list[ReplayEvent]:
    """Return the events the ``viewer`` witnessed, redacted for them (SPEC §9, §16.2).

    Order is preserved. What a viewer gets for each kind of event is the table in
    `_witness`.
    """
    sight = _Sight.of(events, state_after, viewer)
    kept: list[ReplayEvent] = []
    for event in events:
        seen = _witness(event, sight)
        if seen is not None:
            kept.append(seen)
    return kept


@dataclass(frozen=True)
class _Sight:
    """What one viewer knows when reading the turn's log.

    ``owner_of`` covers the units alive after the turn plus every unit an event
    names with its owner (combat participants, barrage hits, disbands, ...), so a
    unit that died this turn still counts as its owner's. ``coord_of`` is where each
    surviving unit stands after the turn; a dead unit has none.
    """

    viewer: PlayerId
    visible: frozenset[Axial]
    owner_of: dict[UnitId, PlayerId]
    coord_of: dict[UnitId, Axial]

    @classmethod
    def of(
        cls, events: list[Event], state_after: GameState, viewer: PlayerId
    ) -> _Sight:
        owner_of: dict[UnitId, PlayerId] = {}
        for event in events:
            for unit_id, owner in _named_owners(event):
                owner_of[unit_id] = owner
        owner_of.update({u.id: u.owner for u in state_after.units})
        return cls(
            viewer=viewer,
            visible=frozenset(visible_hexes(state_after, viewer)),
            owner_of=owner_of,
            coord_of={u.id: u.coord for u in state_after.units},
        )

    def owns(self, unit_id: UnitId) -> bool:
        return self.owner_of.get(unit_id) == self.viewer

    def sees(self, *coords: Axial) -> bool:
        return any(c in self.visible for c in coords)

    def sees_unit(self, unit_id: UnitId) -> bool:
        """The viewer's own unit, or one standing on a visible hex after the turn."""
        if self.owns(unit_id):
            return True
        coord = self.coord_of.get(unit_id)
        return coord is not None and coord in self.visible


def _named_owners(event: Event) -> list[tuple[UnitId, PlayerId]]:
    """The (unit, owner) pairs an event states outright."""
    if isinstance(event, CombatResolved):
        return [(p.unit_id, p.owner) for p in event.participants]
    if isinstance(event, BarrageResolved):
        hits = ([event.hit] if event.hit is not None else []) + event.extra_hits
        return [(h.unit_id, h.owner) for h in hits]
    if isinstance(
        event,
        OrderMooted | TravelEnded | UnitDisbanded | UnitRecruited | WorldUnitRecruited,
    ):
        return [(event.unit_id, event.owner)]
    if isinstance(event, SettlementCaptured):
        return [(event.unit_id, event.captured_by)]
    return []


def _witness(event: Event, sight: _Sight) -> ReplayEvent | None:
    """What ``sight.viewer`` receives of ``event``: ``None`` drops it.

    The rule for each kind of event, for a viewer who is not its owner (the owner
    always gets the whole event):

    | Event | A non-owner receives |
    | --- | --- |
    | unit_moved, unit_deployed | all of it, if either hex is visible |
    | move_blocked | all of it, if the hex the unit stood on is visible (the hex it tried to enter alone would place a hidden unit) |
    | unit_packed, column_stuck, column_held | all of it, if the unit's hex is visible |
    | traffic_jam | the hex and the jammed units it can see, if it sees the hex and any of them |
    | combat_resolved | all of it, if the hex is visible (a participant always gets it) |
    | barrage_resolved | the target and hits, if the target is visible or a hit unit is its own; the gun's id only if the gun stands in sight |
    | settlement_captured | all of it, if the hex is visible (control itself is public state) |
    | unit_repaired | all of it, if the unit stands in sight |
    | world_unit_recruited | unit, type and hex, if the hex is visible; never the cost or the stocks left |
    | unit_disbanded | unit, type and hex, if the hex is visible; never the shortage or whether it was picked |
    | unit_recruited | all of it, if the hex is visible (v0.1 only: banks were public there) |
    | stocks_resolved, economy_resolved, construction_* | nothing: private bookkeeping |
    | recruitment_voided, order_mooted, travel_ended | nothing: private intent |
    """
    viewer = sight.viewer
    if isinstance(event, UnitMoved | UnitDeployed):
        if sight.owns(event.unit_id) or sight.sees(event.origin, event.dest):
            return event
        return None
    if isinstance(event, MoveBlocked):
        if sight.owns(event.unit_id) or sight.sees(event.origin):
            return event
        return None
    if isinstance(event, UnitPacked | ColumnStuck | ColumnHeld):
        if sight.owns(event.unit_id) or sight.sees(event.coord):
            return event
        return None
    if isinstance(event, TrafficJam):
        # The jammed units are friends of one another: one owner for all of them.
        if any(sight.owns(u) for u in event.unit_ids):
            return event
        seen = [u for u in event.unit_ids if sight.sees_unit(u)]
        if not seen or not sight.sees(event.coord):
            return None
        return event.model_copy(update={"unit_ids": seen})
    if isinstance(event, CombatResolved):
        involved = any(p.owner == viewer for p in event.participants)
        return event if involved or sight.sees(event.coord) else None
    if isinstance(event, BarrageResolved):
        return _witness_barrage(event, sight)
    if isinstance(event, SettlementCaptured):
        if event.captured_by == viewer or sight.sees(event.coord):
            return event
        return None
    if isinstance(event, UnitRepaired):
        return event if sight.sees_unit(event.unit_id) else None
    if isinstance(event, WorldUnitRecruited):
        mine = event.owner == viewer
        if not mine and not sight.sees(event.coord):
            return None
        return WorldUnitRecruitedView(
            unit_id=event.unit_id,
            unit_type=event.unit_type,
            owner=event.owner,
            coord=event.coord,
            cost=event.cost if mine else None,
            stocks_after=event.stocks_after if mine else None,
        )
    if isinstance(event, UnitDisbanded):
        mine = event.owner == viewer
        if not mine and not sight.sees(event.coord):
            return None
        return UnitDisbandedView(
            unit_id=event.unit_id,
            unit_type=event.unit_type,
            owner=event.owner,
            coord=event.coord,
            shortage=event.shortage if mine else None,
            chosen=event.chosen if mine else None,
        )
    if isinstance(event, UnitRecruited):
        return event if event.owner == viewer or sight.sees(event.coord) else None
    if isinstance(
        event,
        EconomyResolved
        | StocksResolved
        | ConstructionStarted
        | ConstructionCompleted
        | ConstructionVoided,
    ):
        return event if event.player == viewer else None
    if isinstance(event, RecruitmentVoided | OrderMooted | TravelEnded):
        # A voided recruit changes nothing on the board, and gating it on the
        # site's hex would leak the recruit *intent* to anyone who can see it.
        return event if event.owner == viewer else None
    raise AssertionError(f"unhandled event kind for fog: {event!r}")


def _witness_barrage(
    event: BarrageResolved, sight: _Sight
) -> BarrageResolvedView | None:
    """A barrage for one viewer (see the table in `_witness`)."""
    hits = ([event.hit] if event.hit is not None else []) + event.extra_hits
    fired = sight.owns(event.artillery_id)
    struck = any(h.owner == sight.viewer for h in hits)
    if not (fired or struck or sight.sees(event.target)):
        return None
    return BarrageResolvedView(
        artillery_id=(
            event.artillery_id if sight.sees_unit(event.artillery_id) else None
        ),
        target=event.target,
        hit=event.hit,
        extra_hits=event.extra_hits,
    )
