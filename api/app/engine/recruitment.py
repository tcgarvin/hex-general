"""Recruitment phase — deploying new units (SPEC §6.5, §6.1, §11.3 Phase 6).

The final end-of-turn phase: players spend banked muster to deploy new units at
friendly cities. A recruit is legal only when (SPEC §6.5, §6.1):

  * the target hex is a **city** the player controls (towns cannot deploy in v0.1);
  * the hex is empty — no unit may stack onto it (SPEC §8.1);
  * the player is below the unit cap (SPEC §6.1); and
  * the player can afford the recruit cost (SPEC §6.5) from their bank.

Per the engine's boundary contract (CLAUDE.md), this validates that structural
contract and raises `EngineError` on any violation — Phase 5's API layer screens
requests before they reach here, so an illegal recruit at this point is a bug to
surface loudly, not to silently drop. Requests are applied in order, each one
spending from the running bank and counting against the running cap and the hexes
already claimed by earlier recruits this phase.

``resolve_recruitment`` is pure: it copies the input state, mutates nothing else,
and performs no I/O.
"""

from __future__ import annotations

from collections import defaultdict

from app.engine.events import Event, UnitRecruited
from app.engine.models import GameState, PlayerId, Recruitment, Unit
from app.engine.movement import EngineError
from app.engine.stats import UNIT_STATS, Terrain


def resolve_recruitment(
    state: GameState, recruitments: list[Recruitment]
) -> tuple[GameState, list[Event]]:
    """Deploy each requested unit, deducting its cost (SPEC §6.5).

    ``state`` is not mutated. Raises `EngineError` if a request names an unknown
    player, a hex that is not a friendly city, an occupied hex, exceeds the unit
    cap, or is unaffordable. New unit ids continue from the current maximum.
    """
    new_state = state.model_copy(deep=True)
    if not recruitments:
        return new_state, []

    players = {p.id: p for p in new_state.players}
    terrain_at = {h.coord: h.terrain for h in new_state.map.hexes}
    control_at = {s.coord: s.controlled_by for s in new_state.settlements}
    occupied = {u.coord for u in new_state.units}

    unit_counts: dict[PlayerId, int] = defaultdict(int)
    for unit in new_state.units:
        unit_counts[unit.owner] += 1

    next_id = max((u.id for u in new_state.units), default=0) + 1
    unit_cap = new_state.config.unit_cap

    events: list[Event] = []
    for request in recruitments:
        player = players.get(request.owner)
        if player is None:
            raise EngineError(f"recruitment by unknown player {request.owner!r}")
        if terrain_at.get(request.coord) is not Terrain.CITY:
            raise EngineError(
                f"recruitment for {request.owner!r} at {request.coord} is not a city"
            )
        if control_at.get(request.coord) != request.owner:
            raise EngineError(
                f"recruitment for {request.owner!r} at {request.coord} is not a "
                "friendly city"
            )
        if request.coord in occupied:
            raise EngineError(
                f"recruitment for {request.owner!r} at {request.coord} is occupied"
            )
        if unit_counts[request.owner] >= unit_cap:
            raise EngineError(
                f"recruitment for {request.owner!r} exceeds the unit cap of {unit_cap}"
            )
        cost = UNIT_STATS[request.type].recruit_cost
        if player.bank < cost:
            raise EngineError(
                f"recruitment of {request.type} for {request.owner!r} costs {cost} "
                f"but bank is {player.bank}"
            )

        unit = Unit(
            id=next_id,
            type=request.type,
            owner=request.owner,
            coord=request.coord,
            hp=UNIT_STATS[request.type].max_hp,
        )
        new_state.units.append(unit)
        player.bank -= cost
        occupied.add(request.coord)
        unit_counts[request.owner] += 1
        events.append(
            UnitRecruited(
                unit_id=next_id,
                unit_type=request.type,
                owner=request.owner,
                coord=request.coord,
                cost=cost,
                bank_after=player.bank,
            )
        )
        next_id += 1

    return new_state, events
