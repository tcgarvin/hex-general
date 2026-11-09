"""World bots: a strategist picks the plan, a tactical layer writes the orders (WORLD_SPEC §8).

A world bot's turn has two layers, split along the seam the later LLM commander
needs (VISION §9):

* A **strategist** (`Strategist`) reads the fog-redacted view and chooses
  `StrategistArgs`: a stance (expand, attack, defend), an optional focus hex, and
  how much of the army to keep home. Slice 1 ships `ScriptedStrategist`, a
  rule-based stand-in seeded with a `Personality`. An LLM strategist will
  implement the same protocol and never touch the layer below.
* The **tactical layer** (`plan_world_turn`) turns those args into a legal
  `BotPlan`. Units far from any visible hostile are *strategic*: they get Travel
  orders toward assigned objectives (or keep marching on their standing order).
  Units near contact are *tactical*: they reuse the v0.1 bot's per-unit candidate
  generation (`app.ai.candidates`), which attacks, advances, withdraws or holds.
  Columns that come near the enemy deploy deliberately rather than blunder into
  the auto-deploy and its disorder.

Everything is deterministic given the seed and the view, and every plan is
statically legal (`validate_submission`), which the world simulator asserts.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.ai.board import BoardIndex, is_combat_unit
from app.ai.candidates import unit_candidates
from app.ai.objectives import Objective, ObjectiveKind
from app.ai.policy import BotPlan
from app.ai.base_plan import plan_base, site_needs
from app.ai.recruit import plan_recruitment
from app.engine.hex_coords import Axial, distance
from app.engine.economy_rules import GARRISON_FORCES, SiteKind
from app.engine.models import (
    NEUTRAL,
    MapSite,
    DeployOrder,
    GameState,
    HoldOrder,
    MoveOrder,
    Order,
    PlayerId,
    Posture,
    RestOrder,
    TravelOrder,
    Unit,
)
from app.engine.routing import find_route
from app.engine.stats import UNIT_STATS, Terrain

_CAPTURE_VALUE: dict[Terrain, float] = {Terrain.CITY: 6.0, Terrain.TOWN: 3.0}
# Units assigned to one objective at most: a neutral town needs one captor, a
# held city a proper attack.
_NEUTRAL_CAP: dict[Terrain, int] = {Terrain.CITY: 2, Terrain.TOWN: 1}
_HOSTILE_CAP = 4
_DEFEND_CAP = 3
# World sites (economy draft): what each kind is worth to a bot. Garrisoned sites
# need an attack group one bigger than the garrison, and room for one more.
_SITE_VALUE: dict[SiteKind, float] = {
    SiteKind.BASE: 8.0,
    SiteKind.CITY: 5.0,
    SiteKind.FACTORY: 5.0,
    SiteKind.OIL_FIELD: 4.0,
    SiteKind.STEEL_MILL: 4.0,
    SiteKind.FARM: 3.0,
}
_GARRISON_SIGHT = 3  # an own unit this close sees whether a garrison is still there
# Beyond this many hexes an objective is reached by Travel, not by Move.
_TRAVEL_MIN = 6
# Within this many hexes of a visible hostile a unit fights tactically. Columns
# this close deploy on purpose (the engine auto-deploys them at 3, disordered).
_CONTACT_RADIUS = 6
_WOUNDED_HP = 5
_THREAT_RADIUS = 8  # a visible hostile this close to an own settlement threatens it
_FOCUS_RADIUS = 14
_NO_HOSTILE = 10**6
_STAGE_BACK = 5  # columns bound for a held settlement deploy this far short
_MIN_ATTACK_GROUP = 2  # never send fewer at a hostile-held settlement
_HORIZON = 45  # hexes: about three turns of road travel


class Stance(StrEnum):
    """What the strategist wants this turn."""

    EXPAND = "expand"  # take neutral land first
    ATTACK = "attack"  # take hostile-held settlements, around the focus if set
    DEFEND = "defend"  # hold what we have; counter threats


class StrategistArgs(BaseModel):
    """The strategist's choice for one turn: the tactical layer's arguments.

    ``focus`` concentrates effort: objectives within `_FOCUS_RADIUS` of it are
    worth more. ``reserve_share`` is the share of combat units kept back to
    garrison own settlements. ``aggression`` weighs hostile-held targets against
    neutral ones.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stance: Stance
    focus: Axial | None = None
    reserve_share: float = Field(default=0.0, ge=0.0, le=0.8)
    aggression: float = Field(default=0.5, ge=0.0, le=1.0)


class Personality(BaseModel):
    """Per-bot weights so a world of bots isn't a world of clones (WORLD_SPEC §8).

    ``aggression`` tilts toward attacking held settlements over expanding;
    ``reach`` is how far (in hexes beyond its holdings) the bot looks for neutral
    land; ``garrison_share`` is its reserve.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    aggression: float = Field(ge=0.0, le=1.0)
    reach: int = Field(ge=4)
    garrison_share: float = Field(ge=0.0, le=0.5)

    @classmethod
    def seeded(cls, seed: int, slot: PlayerId) -> Personality:
        """A deterministic personality for ``slot`` in a world seeded ``seed``."""
        rng = random.Random(f"personality:{seed}:{slot}")
        return cls(
            aggression=round(rng.uniform(0.2, 0.9), 2),
            reach=rng.randint(12, 30),
            garrison_share=round(rng.uniform(0.0, 0.3), 2),
        )


class Strategist(Protocol):
    """Chooses a turn's `StrategistArgs` from the fog-redacted view."""

    def choose(self, view: GameState, slot: PlayerId) -> StrategistArgs: ...


class ScriptedStrategist:
    """The rule-based stand-in for the LLM commander (WORLD_SPEC §8).

    Defend when an own settlement is threatened and the bot is cautious; expand
    while neutral land lies within its reach; otherwise attack the hostile-held
    settlement nearest its holdings. Aggression also tilts the tactical layer's
    scoring toward hostile targets (`_targets`).
    """

    def __init__(self, personality: Personality) -> None:
        self.personality = personality

    def choose(self, view: GameState, slot: PlayerId) -> StrategistArgs:
        held = [s.coord for s in view.settlements if s.controlled_by == slot]
        hostiles = [u for u in view.units if view.hostile(u.owner, slot)]
        reserve = self.personality.garrison_share
        aggression = self.personality.aggression
        threatened = [
            c
            for c in held
            if any(distance(c, u.coord) <= _THREAT_RADIUS for u in hostiles)
        ]
        if threatened and aggression < 0.5:
            return StrategistArgs(
                stance=Stance.DEFEND,
                focus=threatened[0],
                reserve_share=reserve,
                aggression=aggression,
            )
        anchors = held or [u.coord for u in view.units if u.owner == slot]
        neutral_near = any(
            s.controlled_by is None
            and _nearest(s.coord, anchors) <= self.personality.reach
            for s in view.settlements
        )
        if neutral_near:
            return StrategistArgs(
                stance=Stance.EXPAND, reserve_share=reserve, aggression=aggression
            )
        targets = [
            s.coord
            for s in view.settlements
            if s.controlled_by is not None and view.hostile(s.controlled_by, slot)
        ]
        if not targets:
            return StrategistArgs(
                stance=Stance.EXPAND, reserve_share=reserve, aggression=aggression
            )
        focus = min(targets, key=lambda c: (_nearest(c, anchors), c.q, c.r))
        return StrategistArgs(
            stance=Stance.ATTACK,
            focus=focus,
            reserve_share=reserve,
            aggression=aggression,
        )


class WorldPolicy:
    """A world bot: `ScriptedStrategist` (or any `Strategist`) + `plan_world_turn`."""

    name = "world"

    def __init__(self, seed: int = 0, strategist: Strategist | None = None) -> None:
        self.seed = seed
        self._strategist = strategist

    def plan(self, view: GameState, slot: PlayerId) -> BotPlan:
        strategist = self._strategist or ScriptedStrategist(
            Personality.seeded(self.seed, slot)
        )
        return plan_world_turn(view, slot, strategist.choose(view, slot))


# --- Tactical layer -------------------------------------------------------------


@dataclass(frozen=True)
class _Target:
    """A settlement worth sending units to, and how many it can absorb."""

    objective: Objective
    capacity: int
    min_group: int = 1
    near_focus: bool = False


def plan_world_turn(view: GameState, slot: PlayerId, args: StrategistArgs) -> BotPlan:
    """Turn the strategist's ``args`` into a legal plan for ``slot``."""
    board = BoardIndex(view, slot)
    build = None
    if view.config.economy is None:
        recruitments = plan_recruitment(board, slot)
    else:
        base = plan_base(view, slot)
        recruitments, build = base.recruitments, base.build
    reserved = frozenset(r.coord for r in recruitments)
    hostiles = [u for u in view.units if view.hostile(u.owner, slot)]
    standing = {o.unit_id: o for o in view.standing_orders}
    garrisons = _pick_garrisons(view, slot, board.own_units, args)
    assignment = _assign(view, slot, board.own_units, hostiles, args, set(garrisons))

    held = {s.coord for s in view.settlements if s.controlled_by == slot}
    hostile_held = {
        s.coord
        for s in view.settlements
        if s.controlled_by is not None and view.hostile(s.controlled_by, slot)
    }
    hostile_coords = [h.coord for h in hostiles]
    claims = _Claims()
    orders: list[Order] = []
    for unit in sorted(board.own_units, key=lambda u: u.id):
        objective = assignment.get(unit.id)
        garrison = garrisons.get(unit.id)
        travel = standing.get(unit.id)
        # A column keeps marching while its destination is still worth reaching:
        # re-planning every turn would churn routes for no gain.
        keep_marching = travel is not None and (
            travel.destination not in held
            or travel.destination == garrison
            or (objective is not None and travel.destination == objective.coord)
        )
        order = _unit_order(
            _UnitContext(board, reserved, hostile_held, claims),
            unit,
            _nearest(unit.coord, hostile_coords),
            objective,
            garrison,
            keep_marching,
        )
        if order is not None:
            orders.append(order)
    return BotPlan(orders=orders, recruitments=recruitments, build=build)


@dataclass
class _Claims:
    """Hexes this turn's moves already use, so the bot doesn't jam itself.

    A deployed move claims each hex it enters at the pulse it enters it, and its
    final hex for the rest of the turn. A candidate that would enter a claimed
    hex at the same pulse, or end on a claimed final hex, is skipped.
    """

    steps: set[tuple[Axial, int]] = field(default_factory=set)
    finals: set[Axial] = field(default_factory=set)

    def pick(self, unit: Unit, candidates: list[Order]) -> Order:
        """The first candidate that doesn't collide (the last one, Hold, always fits)."""
        for order in candidates:
            if not isinstance(order, MoveOrder):
                self.finals.add(unit.coord)
                return order
            steps = {(c, i) for i, c in enumerate(order.path)}
            if steps & self.steps or order.path[-1] in self.finals:
                continue
            self.steps |= steps
            self.finals.add(order.path[-1])
            return order
        self.finals.add(unit.coord)
        return HoldOrder(unit_id=unit.id)


@dataclass(frozen=True)
class _UnitContext:
    """What every per-unit decision needs, gathered once per turn."""

    board: BoardIndex
    reserved: frozenset[Axial]
    hostile_held: set[Axial]
    claims: _Claims

    def tactical(self, unit: Unit, objective: Objective) -> Order:
        """The best non-colliding v0.1 tactical candidate toward ``objective``."""
        return self.claims.pick(
            unit, unit_candidates(self.board, unit, objective, self.reserved)
        )

    def travel(self, unit: Unit, goal: Axial) -> Order | None:
        """Travel toward ``goal``, stopping short of a hostile-held settlement.

        Settlement control is public, so the bot stages a few hexes out and
        deploys there clean, instead of marching into the garrison's contact
        range and deploying disordered. Too close to stage, a deployed unit moves
        tactically and a column gets ``None`` (it should deploy).
        """
        if goal not in self.hostile_held:
            return TravelOrder(unit_id=unit.id, destination=goal)
        route = find_route(self.board.view.map, unit.coord, goal)
        if route is not None and len(route) > _STAGE_BACK + 1:
            return TravelOrder(unit_id=unit.id, destination=route[-_STAGE_BACK - 1])
        if unit.posture is Posture.COLUMN:
            return None
        return self.tactical(unit, Objective(ObjectiveKind.CAPTURE, goal, 0.0))


def _unit_order(
    ctx: _UnitContext,
    unit: Unit,
    threat: int,
    objective: Objective | None,
    garrison: Axial | None,
    keep_marching: bool,
) -> Order | None:
    """One unit's order, or ``None`` to let a standing travel order carry on."""
    if unit.posture is Posture.COLUMN:
        if threat <= _CONTACT_RADIUS:
            return DeployOrder(unit_id=unit.id)
        if keep_marching:
            return None
        goal = garrison or (objective.coord if objective is not None else None)
        if goal is None or goal == unit.coord:
            return DeployOrder(unit_id=unit.id)
        return ctx.travel(unit, goal) or DeployOrder(unit_id=unit.id)

    if unit.hp <= _WOUNDED_HP and threat > 3:
        return _recover(ctx, unit)
    if garrison is not None:
        if garrison == unit.coord:
            ctx.claims.finals.add(unit.coord)
            return _rest_or_hold(unit)
        if distance(unit.coord, garrison) > _TRAVEL_MIN and threat > _CONTACT_RADIUS:
            return TravelOrder(unit_id=unit.id, destination=garrison)
        objective = Objective(ObjectiveKind.DEFEND, garrison, 0.0)
    if objective is None:
        objective = Objective(ObjectiveKind.SUPPORT, unit.coord, 0.0)
    if threat > _CONTACT_RADIUS and distance(unit.coord, objective.coord) > _TRAVEL_MIN:
        order = ctx.travel(unit, objective.coord)
        if order is not None:
            return order
    return ctx.tactical(unit, objective)


def _recover(ctx: _UnitContext, unit: Unit) -> Order:
    """Send a wounded unit to rest in the nearest own settlement."""
    own = [s.coord for s in ctx.board.controlled_settlements(ctx.board.slot)]
    if unit.coord in own or not own:
        ctx.claims.finals.add(unit.coord)
        return RestOrder(unit_id=unit.id)
    home = min(own, key=lambda c: (distance(unit.coord, c), c.q, c.r))
    if distance(unit.coord, home) > _TRAVEL_MIN:
        return TravelOrder(unit_id=unit.id, destination=home)
    return ctx.tactical(unit, Objective(ObjectiveKind.SUPPORT, home, 0.0))


def _rest_or_hold(unit: Unit) -> Order:
    if unit.hp < UNIT_STATS[unit.type].max_hp:
        return RestOrder(unit_id=unit.id)
    return HoldOrder(unit_id=unit.id)


def _pick_garrisons(
    view: GameState, slot: PlayerId, own: list[Unit], args: StrategistArgs
) -> dict[int, Axial]:
    """Keep ``reserve_share`` of combat units home, nearest own cities first.

    Cities are garrisoned before towns. At least one garrison is kept when the
    stance is DEFEND or a hostile is near a held city; none while the bot has
    fewer than two combat units.
    """
    combat = [u for u in own if is_combat_unit(u)]
    index = view.map.index
    homes = sorted(
        (s.coord for s in view.settlements if s.controlled_by == slot),
        key=lambda c: (index[c].terrain is not Terrain.CITY, c.q, c.r),
    )
    # Garrisons hold their own sites and never move, so they threaten nothing.
    hostiles = [
        u.coord
        for u in view.units
        if view.hostile(u.owner, slot) and u.owner != NEUTRAL
    ]
    count = round(args.reserve_share * len(combat))
    if args.stance is Stance.DEFEND or any(
        _nearest(c, hostiles) <= _THREAT_RADIUS
        for c in homes
        if index[c].terrain is Terrain.CITY
    ):
        count = max(count, 1)
    if count == 0 or len(combat) < 2:
        return {}
    focus = args.focus
    if focus is not None and args.stance is Stance.DEFEND:
        homes.sort(key=lambda c: (distance(c, focus), c.q, c.r))
    garrisons: dict[int, Axial] = {}
    free = sorted(combat, key=lambda u: u.id)
    for home in homes[:count]:
        if not free:
            break
        unit = min(free, key=lambda u: (distance(u.coord, home), u.id))
        garrisons[unit.id] = home
        free.remove(unit)
    return garrisons


def _assign(
    view: GameState,
    slot: PlayerId,
    own: list[Unit],
    hostiles: list[Unit],
    args: StrategistArgs,
    garrisoned: set[int],
) -> dict[int, Objective]:
    """Greedy unit → objective matching by value over distance, with capacities.

    Combat units take capture/defend targets; artillery follows the nearest
    assigned combat unit's objective.
    """
    combat = sorted(
        (u for u in own if is_combat_unit(u) and u.id not in garrisoned),
        key=lambda u: u.id,
    )
    targets = _targets(view, slot, hostiles, args, [u.coord for u in combat])
    load: dict[Axial, int] = {}
    assignment: dict[int, Objective] = {}
    pairs = sorted(
        (
            (
                -t.objective.value / (1.0 + distance(u.coord, t.objective.coord) / 4.0),
                u.id,
                k,
            )
            for u in combat
            for k, t in enumerate(targets)
            if t.near_focus or _within_horizon(u.coord, t.objective.coord)
        ),
    )
    dropped: set[Axial] = set()
    for _ in range(3):
        assignment = {}
        load = {}
        for _, unit_id, k in pairs:
            target = targets[k]
            coord = target.objective.coord
            if unit_id in assignment or coord in dropped:
                continue
            if load.get(coord, 0) >= target.capacity:
                continue
            assignment[unit_id] = target.objective
            load[coord] = load.get(coord, 0) + 1
        # A lone unit sent at a held settlement just feeds the garrison: drop
        # such targets and match again, so attacks go in as groups.
        lone = {
            t.objective.coord
            for t in targets
            if t.min_group > 1 and 0 < load.get(t.objective.coord, 0) < t.min_group
        }
        if not lone:
            break
        dropped |= lone
    for gun in (u for u in own if not is_combat_unit(u)):
        escorts = [u for u in combat if u.id in assignment]
        if escorts:
            lead = min(escorts, key=lambda u: (distance(gun.coord, u.coord), u.id))
            assignment[gun.id] = Objective(
                ObjectiveKind.SUPPORT, assignment[lead.id].coord, 0.0
            )
    return assignment


def _within_horizon(a: Axial, b: Axial) -> bool:
    """Whether ``b`` is close enough to ``a`` to be worth planning for."""
    dq = a.q - b.q
    dr = a.r - b.r
    if abs(dq) > _HORIZON or abs(dr) > _HORIZON:
        return False
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2 <= _HORIZON


def _targets(
    view: GameState,
    slot: PlayerId,
    hostiles: list[Unit],
    args: StrategistArgs,
    origins: list[Axial],
) -> list[_Target]:
    """Score every settlement within the horizon of ``origins`` worth a unit.

    Settlements farther than `_HORIZON` hexes from every origin are skipped
    unless they lie near the strategist's focus: a world has thousands, and a bot
    plans for the ones its army could reach in a few turns.
    """
    index = view.map.index
    needs = site_needs(view, slot) if view.config.economy is not None else {}
    targets: list[_Target] = []
    for settlement in view.settlements:
        near_focus = (
            args.focus is not None
            and distance(settlement.coord, args.focus) <= _FOCUS_RADIUS
        )
        if not near_focus and not any(
            _within_horizon(o, settlement.coord) for o in origins
        ):
            continue
        terrain = index[settlement.coord].terrain
        site = view.map.site_index.get(settlement.coord)
        base = (
            _CAPTURE_VALUE[terrain]
            if site is None
            else _SITE_VALUE[site.kind] * needs[site.kind]
        )
        owner = settlement.controlled_by
        if owner == slot:
            threat = sum(
                1
                for h in hostiles
                if h.owner != NEUTRAL
                and distance(h.coord, settlement.coord) <= _THREAT_RADIUS
            )
            if threat == 0:
                continue
            value = (
                base
                * (1.0 + 0.3 * threat)
                * (1.5 if args.stance is Stance.DEFEND else 1.0)
            )
            targets.append(
                _Target(
                    Objective(ObjectiveKind.DEFEND, settlement.coord, value),
                    _DEFEND_CAP,
                )
            )
            continue
        defenders = sum(1 for h in hostiles if distance(h.coord, settlement.coord) <= 1)
        if owner is None and site is not None:
            defenders = _expected_garrison(settlement.coord, site, defenders, origins)
        value = base / (1.0 + 0.7 * defenders)
        min_group = 1
        if owner is None:
            value *= 1.4 if args.stance is Stance.EXPAND else 1.0
            capacity = _NEUTRAL_CAP[terrain] + defenders
            if site is not None and defenders:
                min_group = defenders + 1
                capacity = max(capacity, defenders + 2)
        else:
            value *= (1.6 if args.stance is Stance.ATTACK else 0.8) * (
                0.5 + args.aggression
            )
            capacity = _HOSTILE_CAP
            min_group = _MIN_ATTACK_GROUP
        if near_focus:
            value *= 2.0
        targets.append(
            _Target(
                Objective(ObjectiveKind.CAPTURE, settlement.coord, value),
                capacity,
                min_group,
                near_focus,
            )
        )
    return targets


def _expected_garrison(
    coord: Axial, site: MapSite, seen: int, origins: list[Axial]
) -> int:
    """How many garrison units a bot should plan to fight at a neutral site.

    Up close it counts what it sees; farther off it goes by the site's tier, which
    the map always shows.
    """
    if any(distance(o, coord) <= _GARRISON_SIGHT for o in origins):
        return seen
    return len(GARRISON_FORCES.get(site.garrison, ()))


def _nearest(coord: Axial, others: list[Axial]) -> int:
    """Hex distance from ``coord`` to the nearest of ``others`` (huge if none)."""
    return min((distance(coord, o) for o in others), default=_NO_HOSTILE)
