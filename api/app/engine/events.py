"""Execution event log — the replay (SPEC §16.2–16.3; roadmap Phase 2+).

The engine's resolution functions return an ordered list of `Event` objects
describing everything that happened during execution. The event log *is* the
replay: the frontend steps through it (SPEC §16.2), and combat events will later
carry the §16.3 explanation data. Events are serializable so the log can be
stored alongside the resolved state in a ``turns`` row.

Phase 2 defines only the movement-phase events. Combat, capture, repair, and
economy events join this union in later phases; the discriminated ``kind`` tag
keeps the log forward-compatible.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.engine.hex_coords import Axial
from app.engine.models import UnitId


class MoveBlockedReason(StrEnum):
    """Why a unit's attempted step failed and ended its movement (SPEC §11.3).

    ``ENEMY`` and ``CONTESTED`` are the Phase 2/3 seam: in Phase 2 a unit that
    runs into an enemy simply stops with no damage; Phase 3 replaces that branch
    with real combat resolution.
    """

    OFF_MAP = "off_map"  # the next hex is outside the map
    IMPASSABLE = "impassable"  # the next hex is a river (or other impassable terrain)
    OCCUPIED = "occupied"  # a friendly unit holds the hex and did not vacate it
    ENEMY = "enemy"  # an enemy unit holds the target hex (combat seam, Phase 3)
    CONTESTED = "contested"  # enemies entered the same empty hex this pulse (seam)


class UnitMoved(BaseModel):
    """A unit successfully stepped from ``origin`` to ``dest`` in ``pulse``."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["unit_moved"] = "unit_moved"
    pulse: int
    unit_id: UnitId
    origin: Axial
    dest: Axial


class MoveBlocked(BaseModel):
    """A unit's attempted step into ``target`` failed; it stops for the turn."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["move_blocked"] = "move_blocked"
    pulse: int
    unit_id: UnitId
    origin: Axial
    target: Axial
    reason: MoveBlockedReason


class TrafficJam(BaseModel):
    """Friendly units collided on ``coord`` in the same pulse (SPEC §8.2).

    None of them entered; all of them stop for the turn. ``unit_ids`` is sorted
    for deterministic replay.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["traffic_jam"] = "traffic_jam"
    pulse: int
    coord: Axial
    unit_ids: list[UnitId]


class ZoneOfControlStop(BaseModel):
    """A unit entered an enemy ZOC and halts further movement (SPEC §8.3).

    The triggering step itself still succeeded (a `UnitMoved` precedes this in
    the log); the unit simply takes no further steps this turn.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["zoc_stop"] = "zoc_stop"
    pulse: int
    unit_id: UnitId
    coord: Axial


Event = Annotated[
    UnitMoved | MoveBlocked | TrafficJam | ZoneOfControlStop,
    Field(discriminator="kind"),
]
"""Tagged union of all execution events, discriminated on ``kind``."""
