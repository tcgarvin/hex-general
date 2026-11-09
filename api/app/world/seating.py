"""Seating a player in a running world (WORLD_SPEC §10.1, W5).

A generated world has one start region per slot, and `GeneratedWorld.fill_order`
spreads any prefix of slots across the map. The world stores its capitals in fill
order (``slots``), and the slot at fill position ``k`` is player `slot_id(k)`.
World creation seats the bots in the first slots with `GeneratedWorld.to_state`.
A player joining later takes the first slot that is still open, given the same
opening force on the same terms.

A slot is open when nobody has held its id, its base site is still vacant (no
settlement there yet), and no player's unit stands within `START_CLEARANCE` hexes
of it, so a newcomer never lands beside, or on top of, someone's army. Neutral
garrisons don't count: they hold their own sites and never move.
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
from app.world.generate import STARTING_UNITS, nearest_open

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

    The player founds their base on the slot's base site and gets
    `STARTING_UNITS` on and around it, with the economy's starting stocks, as at
    world creation. Raises `WorldFull` if no slot is open.
    """
    position = open_slot(state, slots)
    if position is None:
        raise WorldFull(f"all {len(slots)} slots are held or occupied")
    player = slot_id(position)
    capital = slots[position]
    seated = state.fork()
    settlement_hexes = {s.coord for s in seated.settlements}
    occupied = {u.coord for u in seated.units}
    spots = [capital] + nearest_open(
        capital, seated.map.index, settlement_hexes, occupied
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
    economy = seated.config.economy
    if economy is None:
        raise ValueError("seating needs a world economy")
    seated.players.append(Player(id=player, stocks=economy.starting_stocks))
    return seated, player
