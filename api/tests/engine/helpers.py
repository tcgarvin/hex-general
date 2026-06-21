"""Shared builders for engine tests (movement, combat, barrage, execution).

Maps are built through the real scenario loader so terrain/road parsing is
exercised end to end; unit ids are assigned 1..N in declaration order.
"""

from __future__ import annotations

from typing import Any

from app.engine.hex_coords import Axial
from app.engine.models import (
    BarrageOrder,
    GameState,
    HoldOrder,
    MoveOrder,
    Recruitment,
    RestOrder,
)
from app.engine.scenario import load_scenario
from app.engine.stats import UnitType


def build(
    terrain: list[str],
    units: list[dict[str, Any]],
    roads: list[str] | None = None,
    settlements: list[dict[str, Any]] | None = None,
    players: list[Any] | None = None,
    victory: dict[str, Any] | None = None,
) -> GameState:
    """Build a turn-0 state from compact terrain/road grids and a unit list.

    ``players`` overrides the roster (e.g. ``[{"id": "blue", "bank": 10}, "red"]``)
    when a test needs starting muster or a player that owns no starting units;
    otherwise it is derived from the unit owners. ``victory`` overrides the
    `VictoryConfig` fields (e.g. ``{"turn_limit": 2}``).
    """
    width = len(terrain[0])
    height = len(terrain)
    scenario: dict[str, Any] = {
        "name": "Engine Fixture",
        "description": "Synthetic map for engine tests.",
        "width": width,
        "height": height,
        "terrain": terrain,
        "settlements": settlements if settlements is not None else [],
        "units": units,
        "players": (
            players
            if players is not None
            else sorted({u["owner"] for u in units}) or ["blue"]
        ),
        "victory": victory if victory is not None else {"turn_limit": 10},
    }
    if roads is not None:
        scenario["roads"] = roads
    return load_scenario(scenario)


def at(state: GameState, q: int, r: int) -> int | None:
    """Return the id of the unit at ``(q, r)``, or None."""
    unit = state.unit_at(Axial(q=q, r=r))
    return unit.id if unit is not None else None


def hp_of(state: GameState, unit_id: int) -> int:
    """Return the HP of the unit with ``unit_id`` (raises if it was destroyed)."""
    return next(u.hp for u in state.units if u.id == unit_id)


def alive(state: GameState, unit_id: int) -> bool:
    """Whether a unit with ``unit_id`` still exists in the state."""
    return any(u.id == unit_id for u in state.units)


def ax(q: int, r: int) -> Axial:
    return Axial(q=q, r=r)


def move(unit_id: int, *steps: tuple[int, int]) -> MoveOrder:
    return MoveOrder(unit_id=unit_id, path=[ax(q, r) for q, r in steps])


def barrage(unit_id: int, q: int, r: int) -> BarrageOrder:
    return BarrageOrder(unit_id=unit_id, target=ax(q, r))


def hold(unit_id: int) -> HoldOrder:
    return HoldOrder(unit_id=unit_id)


def rest(unit_id: int) -> RestOrder:
    return RestOrder(unit_id=unit_id)


def recruit(owner: str, unit_type: str, q: int, r: int) -> Recruitment:
    return Recruitment(owner=owner, type=UnitType(unit_type), coord=ax(q, r))
