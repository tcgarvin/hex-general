"""World bots: a strategist picks the plan, a tactical layer writes the orders (WORLD_SPEC §8).

A world bot's turn has two layers, split along the seam the later LLM commander
needs (VISION §9):

* A **strategist** (`Strategist`) reads the fog-redacted view and chooses
  `StrategistArgs`: a stance (expand, attack, defend), an optional focus hex, and
  how much of the army to keep home. Slice 1 ships `ScriptedStrategist`, a
  rule-based stand-in seeded with a `Personality`. An LLM strategist will
  implement the same protocol and never touch the layer below.
* The **tactical layer** (`plan_world_turn`) turns those args into a legal
  `BotPlan`. Units far from any visible player are *strategic*: they get Travel
  orders toward assigned objectives (or keep marching on their standing order),
  stopping short of known garrisons so a column never blunders into one. Units
  near contact or near their objective are *tactical*: they reuse the v0.1 bot's
  per-unit candidate generation (`app.ai.candidates`), which attacks, advances,
  withdraws or holds. Attacks on a garrisoned site go in as a group sized to the
  garrison, with artillery firing in support; units worn down to a few HP drop
  out to rest on settlement ground and come back.

Everything is deterministic given the seed and the view, and every plan is
statically legal (`validate_submission`), which the world simulator asserts.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.ai.base_plan import plan_base, site_needs
from app.ai.board import BoardIndex, is_combat_unit
from app.ai.candidates import moves_toward, unit_candidates
from app.ai.intel import Intel
from app.ai.objectives import Objective, ObjectiveKind
from app.ai.policy import BotPlan
from app.ai.recruit import plan_recruitment
from app.engine.economy_rules import SiteKind
from app.engine.hex_coords import Axial, distance, within
from app.engine.models import (
    DeployOrder,
    GameState,
    HoldOrder,
    MapSite,
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

# Settlements without a world site (a v0.1 map) are valued by terrain.
_CAPTURE_VALUE: dict[Terrain, float] = {Terrain.CITY: 6.0, Terrain.TOWN: 3.0}
# Units assigned to one objective at most: a neutral town needs one captor, a
# held city a proper attack.
_NEUTRAL_CAP: dict[Terrain, int] = {Terrain.CITY: 2, Terrain.TOWN: 1}
_HOSTILE_CAP = 4
_DEFEND_CAP = 3
# World sites (economy draft): what each kind is worth to a bot.
_SITE_VALUE: dict[SiteKind, float] = {
    SiteKind.BASE: 8.0,
    SiteKind.CITY: 5.0,
    SiteKind.FACTORY: 5.0,
    SiteKind.OIL_FIELD: 4.0,
    SiteKind.STEEL_MILL: 4.0,
    SiteKind.FARM: 3.0,
}
# Each site level above 1 adds this share of the kind's value (it earns more).
_SITE_LEVEL_VALUE = 0.25
# Sites a bot garrisons and guards first, as it does cities on a v0.1 map.
_KEY_SITES = frozenset({SiteKind.BASE, SiteKind.CITY})
# A garrison is worth this much less per HP it has left to wear down.
_GARRISON_HP_DISCOUNT = 0.15
# A hostile-held settlement out of view is assumed held by one unit (a base by
# two), and counts for more or less by stance: neutral land comes first while
# expanding.
_HELD_GUESS = 10
_HELD_GUESS_BASE = 20
# Beyond this many hexes an objective is reached by Travel, not by Move.
_TRAVEL_MIN = 6
# Within this many hexes of a visible player unit a unit fights tactically.
# Columns this close deploy on purpose (the engine auto-deploys them at 3,
# disordered).
_CONTACT_RADIUS = 6
# A unit this worn drops out to rest; on settlement ground it rests until it is
# back above `_HEALED_HP`.
_REST_HP = 4
_HEALED_HP = 8
_REST_SEARCH = 8  # how far a worn unit looks for settlement ground to rest on
_THREAT_RADIUS = 8  # a visible player unit this close to an own settlement threatens it
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


_HELD_STANCE: dict[Stance, float] = {
    Stance.ATTACK: 1.6,
    Stance.EXPAND: 0.5,
    Stance.DEFEND: 0.6,
}


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

    Defend when an own settlement is threatened by another player and the bot is
    cautious; expand while neutral land lies within its reach; otherwise attack
    the hostile-held settlement nearest its holdings. Aggression also tilts the
    tactical layer's scoring toward hostile targets (`_targets`). Neutral
    garrisons never threaten anything: they hold their sites and never move.
    """

    def __init__(self, personality: Personality) -> None:
        self.personality = personality

    def choose(self, view: GameState, slot: PlayerId) -> StrategistArgs:
        held = [s.coord for s in view.settlements if s.controlled_by == slot]
        threats = Intel.player_threats(view, slot)
        reserve = self.personality.garrison_share
        aggression = self.personality.aggression
        threatened = [
            c
            for c in held
            if any(distance(c, u.coord) <= _THREAT_RADIUS for u in threats)
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
    intel = Intel(view, slot)
    build = None
    if view.config.economy is None:
        recruitments = plan_recruitment(board, slot)
    else:
        base = plan_base(view, slot)
        recruitments, build = base.recruitments, base.build
    reserved = frozenset(r.coord for r in recruitments)
    standing = {o.unit_id: o for o in view.standing_orders}
    garrisons = _pick_garrisons(view, slot, board.own_units, args, intel)
    assignment = _assign(view, slot, board.own_units, intel, args, set(garrisons))

    held = {s.coord for s in view.settlements if s.controlled_by == slot}
    hostile_held = {
        s.coord
        for s in view.settlements
        if s.controlled_by is not None and view.hostile(s.controlled_by, slot)
    }
    ctx = _UnitContext(
        board,
        intel,
        reserved,
        hostile_held,
        _Claims(enemy_hexes=frozenset(u.coord for u in board.enemy_units)),
    )
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
        order = _unit_order(ctx, unit, objective, garrison, keep_marching)
        if unit.coord in reserved and _stays(order, unit, ctx.claims.enemy_hexes):
            # A recruit appears on this hex after movement: step off it.
            goal = objective.coord if objective is not None else unit.coord
            order = ctx.vacate(unit, goal)
        if order is not None:
            orders.append(order)
    return BotPlan(orders=orders, recruitments=recruitments, build=build)


@dataclass
class _Claims:
    """Hexes this turn's moves already use, so the bot doesn't jam itself.

    A deployed move claims each hex it enters at the pulse it enters it, and its
    final hex for the rest of the turn. A candidate that would enter a claimed
    hex at the same pulse, or end on a claimed final hex, is skipped. The last
    step of an attack (into an ``enemy_hexes`` hex) claims nothing: any number of
    units may strike one hex, and an attacker that doesn't break in stays where
    it struck from, so that hex is its final one.
    """

    enemy_hexes: frozenset[Axial]
    steps: set[tuple[Axial, int]] = field(default_factory=set)
    finals: set[Axial] = field(default_factory=set)

    def pick(self, unit: Unit, candidates: list[Order]) -> Order:
        """The first candidate that doesn't collide (Hold always fits)."""
        for order in candidates:
            if not isinstance(order, MoveOrder):
                self.finals.add(unit.coord)
                return order
            attack = order.path[-1] in self.enemy_hexes
            walk = order.path[:-1] if attack else order.path
            end = walk[-1] if walk else unit.coord
            steps = {(c, i) for i, c in enumerate(walk)}
            if steps & self.steps or end in self.finals:
                continue
            self.steps |= steps
            self.finals.add(end)
            return order
        self.finals.add(unit.coord)
        return HoldOrder(unit_id=unit.id)


@dataclass(frozen=True)
class _UnitContext:
    """What every per-unit decision needs, gathered once per turn."""

    board: BoardIndex
    intel: Intel
    reserved: frozenset[Axial]
    hostile_held: set[Axial]
    claims: _Claims

    def tactical(self, unit: Unit, objective: Objective) -> Order:
        """The best non-colliding v0.1 tactical candidate toward ``objective``."""
        return self.claims.pick(
            unit, unit_candidates(self.board, unit, objective, self.reserved)
        )

    def step_toward(
        self, unit: Unit, goal: Axial, fallback: Order | None = None
    ) -> Order:
        """A non-colliding move toward ``goal`` that stays out of combat, else
        ``fallback`` (Hold if none)."""
        moves: list[Order] = list(moves_toward(self.board, unit, goal, self.reserved))
        return self.claims.pick(unit, moves + ([fallback] if fallback else []))

    def travel(self, unit: Unit, goal: Axial) -> Order | None:
        """Travel toward ``goal`` as far as a column can go without contact.

        Settlement control is public and so is every garrisoned site, so a column
        stops before a known garrison (`Intel.column_safe`) and a few hexes short
        of a hostile-held settlement, and deploys there clean instead of
        auto-deploying disordered. When the stop is too close to be worth
        packing for, a deployed unit steps past on foot instead (or moves
        tactically if the garrison is its goal), and a column gets ``None`` (it
        should deploy).
        """
        route = find_route(self.board.view.map, unit.coord, goal)
        if route is None:
            return None if unit.posture is Posture.COLUMN else self._near(unit, goal)
        stop = len(route)
        if goal in self.hostile_held:
            stop = max(0, len(route) - _STAGE_BACK)
        for index in range(stop):
            if not self.intel.column_safe(route[index]):
                stop = index
                break
        if stop == len(route):
            return TravelOrder(unit_id=unit.id, destination=goal)
        if stop > UNIT_STATS[unit.type].move + 1:
            return TravelOrder(unit_id=unit.id, destination=route[stop - 1])
        if unit.posture is Posture.COLUMN:
            return None
        past = next((c for c in route[stop:] if self.intel.column_safe(c)), None)
        if past is None or goal in self.hostile_held:
            return self._near(unit, goal)
        return self.step_toward(unit, past)

    def vacate(self, unit: Unit, goal: Axial) -> Order:
        """A non-colliding move off the unit's hex, toward ``goal`` if it can."""
        return self.claims.pick(
            unit,
            list(moves_toward(self.board, unit, goal, self.reserved, stay_ok=False)),
        )

    def _near(self, unit: Unit, goal: Axial) -> Order:
        return self.tactical(unit, Objective(ObjectiveKind.CAPTURE, goal, 0.0))


def _stays(order: Order | None, unit: Unit, enemy_hexes: frozenset[Axial]) -> bool:
    """Whether ``unit`` may end the turn deployed on its own hex under ``order``:
    it holds, rests or fires, or strikes from where it stands (and stays there
    unless the strike clears the hex)."""
    if isinstance(order, TravelOrder) or unit.posture is Posture.COLUMN:
        return False
    if isinstance(order, MoveOrder):
        return len(order.path) == 1 and order.path[0] in enemy_hexes
    return True


def _unit_order(
    ctx: _UnitContext,
    unit: Unit,
    objective: Objective | None,
    garrison: Axial | None,
    keep_marching: bool,
) -> Order | None:
    """One unit's order, or ``None`` to let a standing travel order carry on."""
    threat = ctx.intel.threat_distance(unit.coord)
    if unit.posture is Posture.COLUMN:
        if threat <= _CONTACT_RADIUS:
            return DeployOrder(unit_id=unit.id)
        if keep_marching:
            return None
        goal = garrison or (objective.coord if objective is not None else None)
        if goal is None or goal == unit.coord:
            return DeployOrder(unit_id=unit.id)
        return ctx.travel(unit, goal) or DeployOrder(unit_id=unit.id)

    if _needs_rest(ctx, unit, threat, objective):
        return _recover(ctx, unit, threat)
    if garrison is not None:
        if garrison == unit.coord:
            ctx.claims.finals.add(unit.coord)
            return _rest_or_hold(unit)
        if distance(unit.coord, garrison) > _TRAVEL_MIN and threat > _CONTACT_RADIUS:
            order = ctx.travel(unit, garrison)
            if order is not None:
                return order
        objective = Objective(ObjectiveKind.DEFEND, garrison, 0.0)
    if objective is None:
        objective = Objective(ObjectiveKind.SUPPORT, unit.coord, 0.0)
    if threat > _CONTACT_RADIUS and distance(unit.coord, objective.coord) > _TRAVEL_MIN:
        order = ctx.travel(unit, objective.coord)
        if order is not None:
            return order
    return ctx.tactical(unit, objective)


def _needs_rest(
    ctx: _UnitContext, unit: Unit, threat: int, objective: Objective | None
) -> bool:
    """Whether ``unit`` should drop out and heal this turn.

    A unit worn to `_REST_HP` always does. On settlement ground (3 HP a turn) a
    unit keeps resting until it is back above `_HEALED_HP` unless a player's unit
    is close enough to strike it, and an idle damaged unit there rests to full.
    """
    if unit.hp <= _REST_HP:
        return True
    full = UNIT_STATS[unit.type].max_hp
    if unit.hp >= full or not ctx.board.view.settlement_ground(unit.coord):
        return False
    if threat <= 3:
        return False
    return unit.hp < _HEALED_HP or objective is None


def _recover(ctx: _UnitContext, unit: Unit, threat: int) -> Order:
    """Rest a worn unit on the nearest settlement ground it can reach.

    Town hexes heal any unit resting on them, as does an own site, 3 HP a turn
    (WORLD_SPEC §7). Neutral garrisons never strike, so only another player's
    unit in reach makes a hex unsafe to rest on. With no ground nearby the unit
    rests where it stands (1 HP a turn up to 7) or, if a player threatens it,
    heads for the nearest own settlement.
    """
    view = ctx.board.view
    if view.settlement_ground(unit.coord) and threat > 3:
        ctx.claims.finals.add(unit.coord)
        return RestOrder(unit_id=unit.id)
    ground = _rest_ground(ctx, unit)
    if ground is not None:
        stay = RestOrder(unit_id=unit.id) if threat > 3 else None
        return ctx.step_toward(unit, ground, stay)
    if threat > 3:
        ctx.claims.finals.add(unit.coord)
        return RestOrder(unit_id=unit.id)
    own = [s.coord for s in ctx.board.controlled_settlements(ctx.board.slot)]
    if not own:
        return ctx.tactical(unit, Objective(ObjectiveKind.SUPPORT, unit.coord, 0.0))
    home = min(own, key=lambda c: (distance(unit.coord, c), c.q, c.r))
    return ctx.tactical(unit, Objective(ObjectiveKind.SUPPORT, home, 0.0))


def _rest_ground(ctx: _UnitContext, unit: Unit) -> Axial | None:
    """The nearest free settlement-ground hex a worn unit could rest on.

    Free means no unit on it and not held by another player (stepping onto it
    would start a fight for it); no visible player unit may stand within 3.
    """
    view = ctx.board.view
    best: Axial | None = None
    best_key = (_NO_HOSTILE, 0, 0)
    for coord in within(unit.coord, _REST_SEARCH):
        if coord not in view.map.index or coord in ctx.board.unit_by_coord:
            continue
        if coord in ctx.hostile_held or coord in ctx.reserved:
            continue
        if coord in ctx.claims.finals or not view.settlement_ground(coord):
            continue
        if ctx.intel.threats_within(coord, 3):
            continue
        key = (distance(unit.coord, coord), coord.q, coord.r)
        if key < best_key:
            best, best_key = coord, key
    return best


def _rest_or_hold(unit: Unit) -> Order:
    if unit.hp < UNIT_STATS[unit.type].max_hp:
        return RestOrder(unit_id=unit.id)
    return HoldOrder(unit_id=unit.id)


def _pick_garrisons(
    view: GameState,
    slot: PlayerId,
    own: list[Unit],
    args: StrategistArgs,
    intel: Intel,
) -> dict[int, Axial]:
    """Keep ``reserve_share`` of combat units home, nearest own cities first.

    Cities (and in a world the base and city sites) are garrisoned before other
    settlements. At least one garrison is kept when the stance is DEFEND or a
    player's unit is near a held one; none while the bot has fewer than two
    combat units.
    """
    combat = [u for u in own if is_combat_unit(u)]
    homes = sorted(
        (s.coord for s in view.settlements if s.controlled_by == slot),
        key=lambda c: (not _is_key_settlement(view, c), c.q, c.r),
    )
    hostiles = [u.coord for u in intel.threats]
    count = round(args.reserve_share * len(combat))
    if args.stance is Stance.DEFEND or any(
        _nearest(c, hostiles) <= _THREAT_RADIUS
        for c in homes
        if _is_key_settlement(view, c)
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
    intel: Intel,
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
    targets = _targets(view, slot, intel, args, [u.coord for u in combat])
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
        # Too few units for a target just feed its defenders: drop such targets
        # and match again, so attacks go in as groups.
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


def siege_group(garrison_hp: int) -> tuple[int, int]:
    """How many combat units to send at a garrison of ``garrison_hp``, and the
    fewest worth starting with.

    Against a dug-in garrison nearly every hit lands for the 1-damage floor, so
    a siege takes about as many unit-attacks as the garrison has HP: more units
    take the site sooner and share the return fire. A site seen empty needs one
    captor.
    """
    if garrison_hp <= 0:
        return 1, 1
    if garrison_hp <= 5:
        return 2, 1
    if garrison_hp <= 10:
        return 3, 2
    if garrison_hp <= 20:
        return 4, 3
    return 5, 3


def _targets(
    view: GameState,
    slot: PlayerId,
    intel: Intel,
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
        site = view.map.site_index.get(settlement.coord)
        base = (
            _CAPTURE_VALUE[index[settlement.coord].terrain]
            if site is None
            else _site_value(site, needs)
        )
        owner = settlement.controlled_by
        if owner == slot:
            threat = len(intel.threats_within(settlement.coord, _THREAT_RADIUS))
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
        min_group = 1
        if owner is None and site is not None:
            garrison = intel.garrisons.get(settlement.coord)
            hp = garrison.hp if garrison is not None else 0
            value = base / (1.0 + _GARRISON_HP_DISCOUNT * hp)
            value *= 1.4 if args.stance is Stance.EXPAND else 1.0
            capacity, min_group = siege_group(hp)
        elif owner is None:
            defenders = len(
                [u for u in view.units if distance(u.coord, settlement.coord) <= 1]
            )
            value = base / (1.0 + 0.7 * defenders)
            value *= 1.4 if args.stance is Stance.EXPAND else 1.0
            capacity = _NEUTRAL_CAP[index[settlement.coord].terrain] + defenders
        else:
            hp = _expected_defence(view, intel, settlement.coord)
            value = base / (1.0 + _GARRISON_HP_DISCOUNT * hp)
            value *= _HELD_STANCE[args.stance] * (0.5 + args.aggression)
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


def _expected_defence(view: GameState, intel: Intel, coord: Axial) -> int:
    """The HP a bot expects to fight through to take a hostile-held settlement.

    In view, that is the visible units on and beside it. Out of view the bot
    assumes a defender, two at a base: its owner keeps it.
    """
    if coord in intel.visible:
        return sum(u.hp for u in intel.threats_within(coord, 1))
    site = view.map.site_index.get(coord)
    return (
        _HELD_GUESS_BASE
        if site is not None and site.kind is SiteKind.BASE
        else _HELD_GUESS
    )


def _site_value(site: MapSite, needs: dict[SiteKind, float]) -> float:
    """What a world site is worth to a bot: its kind, how much the bot needs that
    kind now, and its level (a higher level earns more)."""
    level_factor = 1.0 + _SITE_LEVEL_VALUE * (site.level - 1)
    return _SITE_VALUE[site.kind] * needs[site.kind] * level_factor


def _is_key_settlement(view: GameState, coord: Axial) -> bool:
    """Whether a settlement is one a bot guards first: a city on a v0.1 map, the
    base or a city site in a world."""
    site = view.map.site_index.get(coord)
    if site is None:
        return view.map.index[coord].terrain is Terrain.CITY
    return site.kind in _KEY_SITES


def _nearest(coord: Axial, others: list[Axial]) -> int:
    """Hex distance from ``coord`` to the nearest of ``others`` (huge if none)."""
    return min((distance(coord, o) for o in others), default=_NO_HOSTILE)
