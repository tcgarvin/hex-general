"""Seating a player in a running world (WORLD_SPEC §10.1, W5).

A generated world has one start region per slot, and `GeneratedWorld.fill_order`
spreads any prefix of slots across the map. The world stores its capitals (each
slot's base anchor hex, on open ground beside its seat town) in fill order
(``slots``), and the slot at fill position ``k`` is player `slot_id(k)`.
World creation seats the bots in the first slots with `GeneratedWorld.to_state`.
A player joining later takes the first slot that is still open, given the same
opening force on the same terms.

A slot is open when nobody has held its id, its base site is still vacant (no
settlement there yet), and no player's unit stands within `START_CLEARANCE` hexes
of it, so a newcomer never lands beside, or on top of, someone's army. Militia
(neutral garrisons) don't count: they never leave their own site's zone
(WORLD_SPEC §7.1), and sites are kept off the base plots. A site that reverts to
neutral when its player leaves needs nothing here: its militia muster back.
"""

from __future__ import annotations

from app.engine.hex_coords import Axial, distance
from app.engine.models import (
    NEUTRAL,
    GameState,
    Player,
    PlayerId,
    Settlement,
    Unit,
    default_facing,
)
from app.world.generate import STARTING_UNITS, nearest_open, seat_town

START_CLEARANCE = 3
"""No unit may stand this close to a capital for its slot to count as open."""


class WorldFull(Exception):
    """No slot in the world is open for a new player."""


def slot_id(fill_position: int) -> PlayerId:
    """The player id of the slot at ``fill_position`` (0-based) in fill order."""
    return f"s{fill_position + 1:03d}"


def open_slot(state: GameState, slots: list[Axial]) -> int | None:
    """The first open fill position in ``slots``, or ``None`` if every slot is shut."""
    held_ids = {p.id for p in state.players}
    founded = {s.coord for s in state.settlements}
    armies = [u.coord for u in state.units if u.owner != NEUTRAL]
    for position, capital in enumerate(slots):
        if slot_id(position) in held_ids or capital in founded:
            continue
        if any(distance(coord, capital) <= START_CLEARANCE for coord in armies):
            continue
        return position
    return None


def seat_player(state: GameState, slots: list[Axial]) -> tuple[GameState, PlayerId]:
    """Seat a new player in the first open slot; return the new state and their id.

    The player founds their base on the slot's base site (its anchor hex) and
    gets `STARTING_UNITS` on and around it, never on a site or base plot hex,
    with the economy's starting stocks, as at world creation. They also take
    the slot's seat town (`seat_town`) if it is neutral; one another player has
    taken stays theirs (seating never takes a hex from anyone). Raises
    `WorldFull` if no slot is open.
    """
    position = open_slot(state, slots)
    if position is None:
        raise WorldFull(f"all {len(slots)} slots are held or occupied")
    player = slot_id(position)
    capital = slots[position]
    seated = state.fork()
    occupied = {u.coord for u in seated.units}
    spots = [capital] + nearest_open(
        capital, seated.map.index, seated.map.plot_hexes, occupied
    )
    next_id = max((u.id for u in seated.units), default=0) + 1
    arrivals = [
        Unit(id=next_id + offset, type=unit_type, owner=player, coord=coord, hp=10)
        for offset, (unit_type, coord) in enumerate(
            zip(STARTING_UNITS, spots, strict=False)
        )
    ]
    seated.units.extend(arrivals)
    for unit in arrivals:
        unit.facing = default_facing(unit.coord, unit.owner, seated.units)
    seated.settlements.append(Settlement(coord=capital, controlled_by=player))
    town = seated.settlement_at(seat_town(seated.map, capital))
    if town is None:
        raise ValueError(f"the seat town of {capital} has no settlement")
    if town.controlled_by is None:
        town.controlled_by = player
    economy = seated.config.economy
    if economy is None:
        raise ValueError("seating needs a world economy")
    seated.players.append(Player(id=player, stocks=economy.starting_stocks))
    return seated, player


def unseat_player(state: GameState, slots: list[Axial], player: PlayerId) -> GameState:
    """Take ``player`` back out of the world, reopening their slot.

    The undo of `seat_player`, for a join whose players row could not be
    written: the player, their units and any standing orders of those units go,
    and the base they founded on their slot's capital is removed, so the base
    site is vacant again. Their seat town and anything else they came to
    control (a tick may have run in between) revert to neutral. ``state`` is
    left untouched.
    """
    capital = slots[[slot_id(k) for k in range(len(slots))].index(player)]
    unseated = state.fork()
    gone = {u.id for u in unseated.units if u.owner == player}
    unseated.units = [u for u in unseated.units if u.owner != player]
    unseated.standing_orders = [
        o for o in unseated.standing_orders if o.unit_id not in gone
    ]
    unseated.settlements = [
        s
        for s in unseated.settlements
        if not (s.coord == capital and s.controlled_by == player)
    ]
    for settlement in unseated.settlements:
        if settlement.controlled_by == player:
            settlement.controlled_by = None
    unseated.players = [p for p in unseated.players if p.id != player]
    unseated.resigned = [r for r in unseated.resigned if r != player]
    return unseated


def add_town_settlements(state: GameState) -> GameState:
    """Give every town of a world map that has none a neutral settlement.

    Compatibility step for world states stored before 2026-11-15, when towns
    held no ownership: run at the DB boundary (`app.games.repo.join_state`),
    after which every world town has a settlement. Every added town is neutral,
    a seated player's seat town too: the state does not say which slot a player
    holds (that is the map row's ``slots``), and a base can change hands, so its
    holder is no sure sign of whose seat it is. A player takes their seat town
    by walking in (towns have no militia). Returns ``state`` itself for a v0.1
    state or when no town is missing.
    """
    if state.config.economy is None:
        return state
    held = {s.coord for s in state.settlements}
    missing = [c for c in state.map.towns if c not in held]
    if not missing:
        return state
    upgraded = state.fork()
    upgraded.settlements.extend(Settlement(coord=c) for c in missing)
    return upgraded
