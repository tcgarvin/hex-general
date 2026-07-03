"""Apply fog of war to a player's view of the game (SPEC §9; roadmap Phase 6).

`app.engine.visibility` answers "which hexes can a player see?"; this module uses
that answer to build the redacted payload the API actually returns, so the browser
never receives information the player should not have (CLAUDE.md, roadmap decision
4). Two pure transforms:

  * `redact_state` drops enemy units standing on hexes the viewer cannot see. The
    viewer's own units are always kept. The static map, settlement control, and
    player economy are *public* in v0.1 — only enemy unit positions are secret —
    so the rest of the state passes through unchanged.
  * `filter_events` drops replay events the viewer could not witness. An event is
    kept iff it involves one of the viewer's own units or it happened on a hex the
    viewer can currently see. Per-player bookkeeping with no location (economy) is
    therefore private to its owner.

Both are pure: they return new values and never mutate their input. Visibility for
both is computed from the *given* state — for a replay that is the post-turn state,
so the viewer witnesses the turn through the fog as it stands once the dust
settles. This is a deliberate v0.1 simplification: an event at a hex the viewer
could see at the start of the turn but not the end (e.g. an enemy that fled into
the fog) is not reconstructed.
"""

from __future__ import annotations

from app.engine.events import (
    BarrageResolved,
    CombatResolved,
    EconomyResolved,
    Event,
    MoveBlocked,
    OrderMooted,
    RecruitmentVoided,
    SettlementCaptured,
    TrafficJam,
    UnitMoved,
    UnitRecruited,
    UnitRepaired,
)
from app.engine.hex_coords import Axial
from app.engine.models import GameState, PlayerId, UnitId
from app.engine.visibility import visible_hexes


def redact_state(state: GameState, viewer: PlayerId) -> GameState:
    """Return ``state`` with enemy units the ``viewer`` cannot see removed (SPEC §9).

    Keeps every unit the viewer owns plus every other unit standing on a hex the
    viewer can see; the rest of the state is unchanged (the map, settlement
    control, and banks are public in v0.1).
    """
    visible = visible_hexes(state, viewer)
    units = [u for u in state.units if u.owner == viewer or u.coord in visible]
    return state.model_copy(update={"units": units})


def filter_events(
    events: list[Event], state_after: GameState, viewer: PlayerId
) -> list[Event]:
    """Return only the events the ``viewer`` could witness (SPEC §9, §16.2).

    An event is kept iff it involves a unit the viewer owns or it occurred on a hex
    the viewer can see in ``state_after``. Events with no location that belong to a
    single player (economy accounting) are kept only for that player.
    """
    visible = visible_hexes(state_after, viewer)
    owner_of = {u.id: u.owner for u in state_after.units}
    coord_of = {u.id: u.coord for u in state_after.units}
    kept: list[Event] = []
    for event in events:
        owners, locations = _event_scope(event, owner_of, coord_of)
        if viewer in owners or not locations.isdisjoint(visible):
            kept.append(event)
    return kept


def _event_scope(
    event: Event,
    owner_of: dict[UnitId, PlayerId],
    coord_of: dict[UnitId, Axial],
) -> tuple[set[PlayerId], set[Axial]]:
    """Return the players involved in ``event`` and the hexes it touched.

    Ownership of a referenced unit is read from ``owner_of`` where the event does
    not carry it directly; a unit destroyed during the turn is absent from the map
    and contributes no owner, so such an event is gated purely on its location —
    which is the correct fog behaviour for an enemy unit the viewer never saw.
    """
    if isinstance(event, UnitMoved):
        return _owners(owner_of, event.unit_id), {event.origin, event.dest}
    if isinstance(event, MoveBlocked):
        return _owners(owner_of, event.unit_id), {event.origin, event.target}
    if isinstance(event, TrafficJam):
        return _owners(owner_of, *event.unit_ids), {event.coord}
    if isinstance(event, CombatResolved):
        return {p.owner for p in event.participants}, {event.coord}
    if isinstance(event, BarrageResolved):
        owners = _owners(owner_of, event.artillery_id)
        if event.hit is not None:
            owners.add(event.hit.owner)
        return owners, {event.target}
    if isinstance(event, SettlementCaptured):
        return {event.captured_by}, {event.coord}
    if isinstance(event, UnitRepaired):
        return _owners(owner_of, event.unit_id), {coord_of[event.unit_id]}
    if isinstance(event, EconomyResolved):
        # Economy is private bookkeeping with no place on the map.
        return {event.player}, set()
    if isinstance(event, UnitRecruited):
        return {event.owner}, {event.coord}
    if isinstance(event, OrderMooted):
        # Private bookkeeping for the dead unit's owner, like economy. The event
        # carries its own owner because the unit no longer exists post-turn.
        return {event.owner}, set()
    if isinstance(event, RecruitmentVoided):
        # Private to the requester, with NO location: a voided recruit changes
        # nothing on the board, and gating on the city hex would leak the enemy's
        # recruit *intent* to an opponent who can merely see the city.
        return {event.owner}, set()
    raise AssertionError(f"unhandled event kind for fog: {event!r}")


def _owners(owner_of: dict[UnitId, PlayerId], *unit_ids: UnitId) -> set[PlayerId]:
    """Resolve the owners of ``unit_ids`` that still exist in the post-turn state."""
    return {owner_of[uid] for uid in unit_ids if uid in owner_of}
