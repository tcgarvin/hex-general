"""Execution event log — the replay (SPEC §16.2–16.3; roadmap Phase 2+).

The engine's resolution functions return an ordered list of `Event` objects
describing everything that happened during execution. The event log *is* the
replay: the frontend steps through it (SPEC §16.2) and renders combat
explanations (SPEC §16.3) entirely from these records. Events are serializable so
the log can be stored alongside the resolved state in a ``turns`` row.

Phase 2 defined the movement-phase events; Phase 3 adds the barrage and combat
events. Capture, repair, and economy events join this union in later phases; the
discriminated ``kind`` tag keeps the log forward-compatible.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.engine.hex_coords import Axial
from app.engine.models import PlayerId, UnitId
from app.engine.stats import UnitType


class MoveBlockedReason(StrEnum):
    """Why a unit's attempted step failed and ended its movement (SPEC §11.3)."""

    OFF_MAP = "off_map"  # the next hex is outside the map
    IMPASSABLE = "impassable"  # the next hex is a river (or other impassable terrain)
    OCCUPIED = "occupied"  # a friendly unit holds the hex and did not vacate it
    ZOC_LOCKED = "zoc_locked"  # engaged unit tried an illegal ZOC->ZOC step (SPEC §8.4)
    ATTACK_FORBIDDEN = "attack_forbidden"  # artillery tried to attack by moving (§5.4)


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


class CombatRole(StrEnum):
    """A unit's role in a combat event (SPEC §12).

    ``DEFENDER`` is the stationary occupant of the contested hex — the only
    participant that receives terrain/Hold modifiers (SPEC §12.10). ``ATTACKER``
    covers every unit moving into the hex (an into-enemy attack, an empty-hex
    contest, or an edge swap); attackers never get terrain/Hold bonuses.
    """

    ATTACKER = "attacker"
    DEFENDER = "defender"


class CombatParticipant(BaseModel):
    """One unit's contribution to and outcome in a combat event (SPEC §16.3).

    Damage is reported as totals: ``damage_dealt`` is summed across every
    opposing unit this one struck, ``damage_taken`` across every opponent that
    struck it. For a 1v1 these are the single exchanged values in the §16.3
    example; in a dogpile each attacker's ``damage_dealt`` is its hit on the
    defender, and the defender's ``damage_dealt`` is the sum of its full
    retaliation against every attacker (SPEC §12.4).
    """

    model_config = ConfigDict(extra="forbid")

    unit_id: UnitId
    unit_type: UnitType
    owner: PlayerId
    role: CombatRole
    terrain_bonus: int
    hold_bonus: int
    damage_dealt: int
    damage_taken: int
    hp_after: int
    destroyed: bool


class CombatResolved(BaseModel):
    """A single combat over one contested ``coord`` in ``pulse`` (SPEC §12).

    Holds everything needed to render the §16.3 explanation: every participant
    with its role, modifiers, damage, and fate, plus the occupation outcome.
    ``entrant_id`` is the surviving lead entrant that occupied the hex, or
    ``None`` if nobody entered (opposing survivors remained, or all entrants
    died — SPEC §12.5). ``entrant_continued`` records whether that entrant had
    movement pulses left and kept moving (SPEC §11.2/§12.5).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["combat_resolved"] = "combat_resolved"
    pulse: int
    coord: Axial
    participants: list[CombatParticipant]
    entrant_id: UnitId | None = None
    entrant_continued: bool = False


class BarrageHit(BaseModel):
    """The unit struck by a barrage and its outcome (SPEC §7.2, §13.1).

    A barrage targets a hex; this records the unit that happened to occupy it.
    The occupant is a stationary defender, so terrain/Hold modifiers apply
    (SPEC §12.10); artillery fires at range, so there is no retaliation.
    """

    model_config = ConfigDict(extra="forbid")

    unit_id: UnitId
    unit_type: UnitType
    owner: PlayerId
    terrain_bonus: int
    hold_bonus: int
    damage: int
    hp_after: int
    destroyed: bool


class BarrageResolved(BaseModel):
    """An artillery Barrage order resolved against ``target`` (SPEC §7.2, §11.3).

    ``hit`` is the struck unit, or ``None`` when the target hex was empty (a
    single, well-defined meaning: barrage targets a hex, which may hold nobody).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["barrage_resolved"] = "barrage_resolved"
    artillery_id: UnitId
    target: Axial
    hit: BarrageHit | None = None


Event = Annotated[
    UnitMoved | MoveBlocked | TrafficJam | CombatResolved | BarrageResolved,
    Field(discriminator="kind"),
]
"""Tagged union of all execution events, discriminated on ``kind``."""
