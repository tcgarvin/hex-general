"""Execution event log — the replay (SPEC §16.2–16.3; roadmap Phase 2+).

The engine's resolution functions return an ordered list of `Event` objects
describing everything that happened during execution. The event log *is* the
replay: the frontend steps through it (SPEC §16.2) and renders combat
explanations (SPEC §16.3) entirely from these records. Events are serializable so
the log can be stored alongside the resolved state in a ``turns`` row.

Phase 2 defined the movement-phase events; Phase 3 added the barrage and combat
events; Phase 4 adds the end-of-turn events (capture, repair, economy,
recruitment). The discriminated ``kind`` tag keeps the log forward-compatible.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.engine.hex_coords import Axial
from app.engine.models import PlayerId, UnitId
from app.engine.stats import Terrain, UnitType


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


class OrderMootedReason(StrEnum):
    """Why a statically-legal order became moot mid-turn (SPEC §11.3)."""

    DESTROYED_IN_BARRAGE = "destroyed_in_barrage"  # the ordered unit died in Phase 1


class OrderMooted(BaseModel):
    """A unit's order was dropped because the turn voided it before it applied.

    Barrage (§11.3 Phase 1) removes destroyed units before movement (Phase 2), so a
    statically-legal Move/Hold/Rest order can name a unit that no longer exists by
    the time movement resolves. That is defined simultaneity, not a caller bug: the
    order is skipped and this event records it for the replay. Emitted between the
    barrage events and the movement events. (An order naming a unit that *never*
    existed remains a caller bug and still raises `EngineError` in movement. A
    Barrage order whose artillery died in the exchange is *not* mooted — barrage
    resolves simultaneously, so that order already applied in Phase 1.)

    ``owner`` is carried on the event because the unit is destroyed: fog filtering
    runs against the post-turn state, where the dead unit can no longer be resolved
    to its owner.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["order_mooted"] = "order_mooted"
    unit_id: UnitId
    owner: PlayerId
    reason: OrderMootedReason


class SettlementCaptured(BaseModel):
    """An infantry/armor unit captured a town or city (SPEC §14.1, §11.3 Phase 3).

    Emitted only when control actually changes — a unit sitting on a settlement
    it already controls produces no event. ``previous_owner`` is ``None`` when the
    settlement was neutral before capture (a single, well-defined meaning).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["settlement_captured"] = "settlement_captured"
    coord: Axial
    settlement: Terrain
    captured_by: PlayerId
    previous_owner: PlayerId | None
    unit_id: UnitId


class UnitRepaired(BaseModel):
    """A resting, undamaged unit recovered HP (SPEC §7.3, §11.3 Phase 4).

    Emitted only when HP actually increases. ``in_settlement`` records whether the
    unit rested on a town/city (the +2-up-to-10 tier) versus anywhere else (the
    +1-up-to-7 tier).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["unit_repaired"] = "unit_repaired"
    unit_id: UnitId
    hp_before: int
    hp_after: int
    in_settlement: bool


class EconomyResolved(BaseModel):
    """One player's muster accounting for the turn (SPEC §6.2–6.4, §11.3 Phase 5).

    ``net`` is ``income - upkeep`` (may be negative; the shortfall is paid from the
    bank, which floors at 0). ``lost_to_cap`` is muster discarded because the bank
    would exceed its cap (SPEC §6.4). ``bank_after`` is the resulting bank.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["economy_resolved"] = "economy_resolved"
    player: PlayerId
    income: int
    upkeep: int
    net: int
    bank_before: int
    bank_after: int
    lost_to_cap: int


class UnitRecruited(BaseModel):
    """A player recruited a new unit at a friendly city (SPEC §6.5, §11.3 Phase 6).

    ``unit_id`` is the id assigned to the new unit; ``bank_after`` is the player's
    bank once ``cost`` was deducted.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["unit_recruited"] = "unit_recruited"
    unit_id: UnitId
    unit_type: UnitType
    owner: PlayerId
    coord: Axial
    cost: int
    bank_after: int


class RecruitmentVoidedReason(StrEnum):
    """Why a submission-legal recruit was voided by the turn (SPEC §6.5, §11.3)."""

    CITY_NOT_FRIENDLY = "city_not_friendly"  # the city was captured/lost mid-turn
    HEX_OCCUPIED = "hex_occupied"  # a unit ended its move on the target hex
    UNIT_CAP_REACHED = "unit_cap_reached"  # the running cap filled first
    INSUFFICIENT_MUSTER = "insufficient_muster"  # the post-economy bank fell short


class RecruitmentVoided(BaseModel):
    """A recruit request was skipped because the turn voided it (SPEC §6.5, §11.3).

    Recruitment resolves last (§11.3 Phase 6), after movement/capture/economy, so a
    statically-legal recruit can be voided by the turn itself: the city was captured
    (possibly by a fog-hidden enemy), a unit ended its move on the hex, the running
    unit cap filled, or the post-economy bank fell short. The muster stays banked and
    the request is skipped rather than crashing the turn.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["recruitment_voided"] = "recruitment_voided"
    owner: PlayerId
    coord: Axial
    unit_type: UnitType
    reason: RecruitmentVoidedReason


Event = Annotated[
    UnitMoved
    | MoveBlocked
    | TrafficJam
    | CombatResolved
    | BarrageResolved
    | OrderMooted
    | SettlementCaptured
    | UnitRepaired
    | EconomyResolved
    | UnitRecruited
    | RecruitmentVoided,
    Field(discriminator="kind"),
]
"""Tagged union of all execution events, discriminated on ``kind``."""
