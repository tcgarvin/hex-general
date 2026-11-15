"""Militia: the neutral garrisons' fixed script (WORLD_SPEC §7.1).

Every neutral unit in a world is a militia unit belonging to one site for life
(`Unit.post`, the site's hex). Its **zone** is the site hex and the six hexes
around it (the **ring**); an **intruder** is a deployed player unit standing in
the zone at the start of the turn. This module is the whole script, in four
pure steps the turn composer (`app.engine.execution`) calls:

* `militia_orders` — one order per militia unit for this turn, from start-of-turn
  positions only (no randomness, no knowledge of the players' orders). Roles are
  derived from positions, not stored:

  1. **Keeper:** the militia infantry on its post Holds.
  2. **Going home:** with no militia unit on the post, the militia infantry
     nearest to it (ties: lowest id) steps onto it, attacking a player unit
     there. If that step is not legal it acts as a picket instead.
  3. **Picket:** every other militia infantry in the zone Holds until there is
     an intruder, then attacks one (on the post first, else fewest steps, then
     lowest HP, then lowest id), moving only through free ring hexes, at most
     its move, ZOC-legal step by step; out of reach, it closes in. Pickets plan
     in id order and never path through a hex an earlier picket already uses.
  4. **Gun:** Holds until an intruder is in range, then Barrages one (on the
     post first, else lowest HP, then lowest id). It never moves.

  A militia infantry outside its zone only Holds (a gun there still fires).
* `retake` — after capture: a militia infantry standing on its own post while a
  player holds the site returns it to neutral.
* `recover` — after repair: each militia unit that lost no HP this turn regains
  `MilitiaRules.recover` HP, up to its starting HP, while its site is neutral.
* `muster` — after recruitment: a neutral site under strength counts quiet turns
  (`Settlement.muster_clock`) and after `MilitiaRules.muster_turns` of them one
  lost unit returns.

A unit's starting HP and which units a site has lost both come from its tier's
`GARRISON_FORCES`, matched by unit type against the living units posted there.

States stored before posts existed (before 2026-11-15) are upgraded at the DB
boundary by `assign_posts`. With no posted units and no garrisoned sites (every
v0.1 game) all of this is a no-op.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass

from app.engine.economy_rules import GARRISON_FORCES, MilitiaRules
from app.engine.events import (
    Event,
    MilitiaMustered,
    SettlementCaptured,
    UnitRepaired,
)
from app.engine.hex_coords import Axial, distance, neighbors, within
from app.engine.models import (
    NEUTRAL,
    BarrageOrder,
    GameState,
    HoldOrder,
    MapSite,
    MoveOrder,
    Order,
    Posture,
    Unit,
    UnitId,
    default_facing,
)
from app.engine.movement import EngineError
from app.engine.stats import TERRAIN_STATS, UNIT_STATS, Terrain, UnitType

ZONE_RADIUS = 1
"""A site's zone is its hex and every hex this close to it."""

# Terrain a mustered unit may appear on, as at generation (`nearest_open`).
_MUSTER_TERRAIN = frozenset({Terrain.PLAINS, Terrain.WOODS, Terrain.HILLS})


def zone(post: Axial) -> list[Axial]:
    """The hexes of ``post``'s zone: the site hex and its ring."""
    return within(post, ZONE_RADIUS)


def militia_orders(state: GameState) -> list[Order]:
    """One order per militia unit for this turn, in unit-id order."""
    militia = sorted((u for u in state.units if u.owner == NEUTRAL), key=_unit_id)
    if not militia:
        return []
    board = _Board.of(state)
    by_post: dict[Axial, list[Unit]] = defaultdict(list)
    for unit in militia:
        by_post[post_of(unit)].append(unit)
    orders: list[Order] = []
    for post in sorted(by_post, key=lambda c: (c.q, c.r)):
        orders.extend(_site_orders(board, post, by_post[post]))
    return sorted(orders, key=lambda o: o.unit_id)


def retake(state: GameState) -> tuple[GameState, list[Event]]:
    """Return to neutral every player-held site whose militia infantry stands on it."""
    keepers = {
        u.coord: u
        for u in state.units
        if u.owner == NEUTRAL
        and u.type is UnitType.INFANTRY
        and u.posture is Posture.DEPLOYED
        and u.coord == u.post
    }
    if not keepers:
        return state, []
    new_state = state.fork()
    sites = new_state.map.site_index
    hexes = new_state.map.index
    events: list[Event] = []
    for settlement in new_state.settlements:
        keeper = keepers.get(settlement.coord)
        if keeper is None or settlement.controlled_by is None:
            continue
        events.append(
            SettlementCaptured(
                coord=settlement.coord,
                settlement=hexes[settlement.coord].terrain,
                site=sites[settlement.coord].kind,
                captured_by=NEUTRAL,
                previous_owner=settlement.controlled_by,
                unit_id=keeper.id,
            )
        )
        settlement.controlled_by = None
    return new_state, events


def recover(state: GameState, damaged: set[UnitId]) -> tuple[GameState, list[Event]]:
    """Heal each militia unit not in ``damaged`` by `MilitiaRules.recover` HP.

    ``damaged`` is the set of units that lost HP this turn. Only militia whose
    site is neutral heal: once a player holds it they have lost their base (until
    they retake it). A unit never heals past its starting HP. Units heal in
    stored order for a deterministic log.
    """
    if not any(u.owner == NEUTRAL for u in state.units):
        return state, []
    rules = _rules(state)
    new_state = state.fork()
    sites = new_state.map.site_index
    held = {s.coord for s in new_state.settlements if s.controlled_by is not None}
    ground = new_state.ground()
    events: list[Event] = []
    for unit in new_state.units:
        if unit.owner != NEUTRAL or unit.id in damaged or post_of(unit) in held:
            continue
        ceiling = starting_hp(sites[post_of(unit)], unit.type)
        new_hp = min(unit.hp + rules.recover, max(unit.hp, ceiling))
        if new_hp == unit.hp:
            continue
        events.append(
            UnitRepaired(
                unit_id=unit.id,
                hp_before=unit.hp,
                hp_after=new_hp,
                in_settlement=ground.settlement_ground(unit.coord),
            )
        )
        unit.hp = new_hp
    return new_state, events


def muster(state: GameState) -> tuple[GameState, list[Event]]:
    """Count each neutral site's quiet turns and muster lost units back.

    A garrisoned site that is neutral and under strength counts a quiet turn
    (no player unit, deployed or packed, in its zone); a turn that is not quiet
    leaves the count alone. Once the count reaches `MilitiaRules.muster_turns`,
    the first lost unit (tier order) returns at its starting HP and the count
    restarts; with no room for it, it waits with the count held. A player-held or
    full-strength site keeps its count at 0. New ids continue from the current
    maximum, so this runs after recruitment.
    """
    garrisoned = [s for s in state.map.sites if s.garrison > 0]
    if not garrisoned:
        return state, []
    rules = _rules(state)
    new_state = state.fork()
    settlement_of = {s.coord: s for s in new_state.settlements}
    posted: dict[Axial, list[Unit]] = defaultdict(list)
    for unit in new_state.units:
        if unit.owner == NEUTRAL:
            posted[post_of(unit)].append(unit)
    player_hexes = {u.coord for u in new_state.units if u.owner != NEUTRAL}
    occupied = {u.coord for u in new_state.units}
    next_id = max((u.id for u in new_state.units), default=0) + 1
    events: list[Event] = []
    for site in garrisoned:
        settlement = settlement_of[site.coord]
        missing = missing_units(site, posted[site.coord])
        if settlement.controlled_by is not None or not missing:
            settlement.muster_clock = 0
            continue
        if any(c in player_hexes for c in zone(site.coord)):
            continue
        settlement.muster_clock = min(settlement.muster_clock + 1, rules.muster_turns)
        if settlement.muster_clock < rules.muster_turns:
            continue
        unit_type, hp = missing[0]
        spot = _muster_spot(new_state, site.coord, unit_type, occupied)
        if spot is None:
            continue
        unit = Unit(
            id=next_id,
            type=unit_type,
            owner=NEUTRAL,
            coord=spot,
            hp=hp,
            post=site.coord,
        )
        unit.facing = default_facing(spot, NEUTRAL, new_state.units)
        new_state.units.append(unit)
        posted[site.coord].append(unit)
        occupied.add(spot)
        next_id += 1
        settlement.muster_clock = 0
        events.append(
            MilitiaMustered(
                unit_id=unit.id,
                unit_type=unit_type,
                coord=spot,
                post=site.coord,
                hp=hp,
            )
        )
    return new_state, events


def starting_hp(site: MapSite, unit_type: UnitType) -> int:
    """The HP a militia unit of ``unit_type`` starts with at ``site``."""
    return next(hp for t, hp in GARRISON_FORCES[site.garrison] if t is unit_type)


def missing_units(site: MapSite, posted: list[Unit]) -> list[tuple[UnitType, int]]:
    """The tier's units ``site`` has lost, in tier order, as (type, starting HP).

    ``posted`` are the living units whose post is ``site``; they are matched to
    the tier's units by type.
    """
    alive = Counter(u.type for u in posted)
    missing: list[tuple[UnitType, int]] = []
    for unit_type, hp in GARRISON_FORCES[site.garrison]:
        if alive[unit_type] > 0:
            alive[unit_type] -= 1
        else:
            missing.append((unit_type, hp))
    return missing


def assign_posts(state: GameState) -> GameState:
    """Give every neutral unit without a post the nearest garrisoned site.

    Compatibility step for states stored before 2026-11-15, when militia had no
    post: run at the DB boundary (`app.games.repo.join_state`), after which every
    neutral unit has one. Ties go to the site listed first on the map, matching
    how the generator placed garrisons around their sites. Returns ``state``
    itself when nothing needs a post.
    """
    if not any(u.owner == NEUTRAL and u.post is None for u in state.units):
        return state
    sites = [s.coord for s in state.map.sites if s.garrison > 0]
    if not sites:
        raise EngineError("neutral units in a state with no garrisoned site")
    units = [u.model_copy() for u in state.units]
    for unit in units:
        if unit.owner == NEUTRAL and unit.post is None:
            unit.post = min(sites, key=lambda c: distance(c, unit.coord))
    return state.fork_with_units(units)


def _muster_spot(
    state: GameState, post: Axial, unit_type: UnitType, occupied: set[Axial]
) -> Axial | None:
    """Where a mustered unit appears, or ``None`` when there is no room.

    An infantry returns on the site hex if no unit stands there; otherwise, and
    always for a gun, on the first free ring hex of open ground off every plot
    (as generation places garrisons), by axial order.
    """
    if unit_type is UnitType.INFANTRY and post not in occupied:
        return post
    index = state.map.index
    plots = state.map.plot_hexes
    ring = sorted(
        (
            c
            for c in zone(post)
            if c != post
            and c in index
            and index[c].terrain in _MUSTER_TERRAIN
            and c not in plots
            and c not in occupied
        ),
        key=lambda c: (c.q, c.r),
    )
    return ring[0] if ring else None


# --- The script ---------------------------------------------------------------


@dataclass(frozen=True)
class _Board:
    """Start-of-turn positions the script reads.

    ``deployed`` maps each hex to its deployed unit; ``occupied`` is every hex
    holding any unit (deployed or packed); ``player_zoc`` is every hex beside a
    deployed player unit (where an engaged step may not go, SPEC §8.4).
    """

    state: GameState
    deployed: dict[Axial, Unit]
    occupied: frozenset[Axial]
    player_zoc: frozenset[Axial]

    @classmethod
    def of(cls, state: GameState) -> _Board:
        deployed = {u.coord: u for u in state.units if u.posture is Posture.DEPLOYED}
        zoc = {
            nb
            for u in deployed.values()
            if u.owner != NEUTRAL
            for nb in neighbors(u.coord)
        }
        return cls(
            state=state,
            deployed=deployed,
            occupied=frozenset(u.coord for u in state.units),
            player_zoc=frozenset(zoc),
        )

    def free(self, coord: Axial) -> bool:
        """An on-map, passable hex with no unit on it."""
        hex_ = self.state.map.index.get(coord)
        return (
            hex_ is not None
            and TERRAIN_STATS[hex_.terrain].passable
            and coord not in self.occupied
        )


def _site_orders(board: _Board, post: Axial, units: list[Unit]) -> list[Order]:
    """The orders of the militia posted at ``post`` (``units`` in id order)."""
    in_zone = set(zone(post))
    intruders = sorted(
        (
            board.deployed[c]
            for c in in_zone
            if c in board.deployed and board.deployed[c].owner != NEUTRAL
        ),
        key=_unit_id,
    )
    on_post = board.deployed.get(post)
    infantry = [u for u in units if u.type is UnitType.INFANTRY and u.coord in in_zone]
    plans: list[Order] = []
    claimed: set[Axial] = set()
    pickets: list[Unit] = []

    homeward: Unit | None = None
    if on_post is None or on_post.owner != NEUTRAL:
        away = [u for u in infantry if u.coord != post]
        if away:
            homeward = min(away, key=lambda u: (distance(u.coord, post), u.id))
            if _step_legal(board, homeward.coord, post):
                plans.append(MoveOrder(unit_id=homeward.id, path=[post]))
                if post not in board.deployed:
                    claimed.add(post)
            else:
                homeward = None

    for unit in units:
        if unit is homeward:
            continue
        if unit.type is UnitType.ARTILLERY:
            plans.append(_gun_plan(unit, post, intruders))
        elif unit.coord not in in_zone or unit.coord == post or not intruders:
            plans.append(_hold(unit))
        else:
            pickets.append(unit)

    for picket in pickets:
        plans.append(_picket_plan(board, picket, post, intruders, claimed))
    return plans


def _gun_plan(gun: Unit, post: Axial, intruders: list[Unit]) -> Order:
    """Barrage the best intruder in range, else Hold."""
    reach = UNIT_STATS[gun.type].attack_range
    in_range = [u for u in intruders if distance(gun.coord, u.coord) <= reach]
    if not in_range:
        return _hold(gun)
    target = min(in_range, key=lambda u: (u.coord != post, u.hp, u.id))
    return BarrageOrder(unit_id=gun.id, target=target.coord)


def _picket_plan(
    board: _Board,
    picket: Unit,
    post: Axial,
    intruders: list[Unit],
    claimed: set[Axial],
) -> Order:
    """Attack (or close in on) the best intruder this picket can reach."""
    ring = set(zone(post)) - {post}
    targets = {u.coord for u in intruders}
    parent = _search(board, picket.coord, ring, targets, claimed)
    reachable = [u for u in intruders if u.coord in parent]
    if not reachable:
        return _hold(picket)
    steps = {u.id: len(_path(parent, u.coord)) for u in reachable}
    target = min(reachable, key=lambda u: (u.coord != post, steps[u.id], u.hp, u.id))
    path = _path(parent, target.coord)[: UNIT_STATS[picket.type].move]
    claimed.update(c for c in path if c != target.coord)
    return MoveOrder(unit_id=picket.id, path=path)


def _search(
    board: _Board,
    start: Axial,
    ring: set[Axial],
    targets: set[Axial],
    claimed: set[Axial],
) -> dict[Axial, Axial]:
    """Breadth-first search from ``start`` over legal steps; parent of each hex.

    A step onto a target hex is an attack (always legal, and the path ends
    there); a step onto a free, unclaimed ring hex is a move, illegal from a hex
    in player ZOC into another one. Neighbours are tried in `neighbors` order, so
    the shortest path found is deterministic.
    """
    parent: dict[Axial, Axial] = {}
    frontier = deque([start])
    seen = {start}
    while frontier:
        here = frontier.popleft()
        for nb in neighbors(here):
            if nb in seen:
                continue
            if nb in targets:
                seen.add(nb)
                parent[nb] = here
            elif (
                nb in ring
                and nb not in claimed
                and board.free(nb)
                and _step_legal(board, here, nb)
            ):
                seen.add(nb)
                parent[nb] = here
                frontier.append(nb)
    return parent


def _path(parent: dict[Axial, Axial], end: Axial) -> list[Axial]:
    """The hexes from the search's start (excluded) to ``end`` (included)."""
    path = [end]
    while path[-1] in parent:
        path.append(parent[path[-1]])
    path.pop()  # the start hex
    return path[::-1]


def _step_legal(board: _Board, here: Axial, there: Axial) -> bool:
    """Whether a militia unit may step ``here`` → ``there`` at start positions.

    Attacking a player's deployed unit is always legal; entering a hex with any
    other unit is not; an empty hex is barred only from player ZOC into player
    ZOC (SPEC §8.4).
    """
    occupant = board.deployed.get(there)
    if occupant is not None and occupant.owner != NEUTRAL:
        return True
    if not board.free(there):
        return False
    return not (here in board.player_zoc and there in board.player_zoc)


def _hold(unit: Unit) -> Order:
    return HoldOrder(unit_id=unit.id)


def _unit_id(unit: Unit) -> UnitId:
    return unit.id


def post_of(unit: Unit) -> Axial:
    """A militia unit's post: every neutral unit has one (`assign_posts`)."""
    if unit.post is None:
        raise EngineError(f"neutral unit {unit.id} has no post")
    return unit.post


def _rules(state: GameState) -> MilitiaRules:
    economy = state.config.economy
    if economy is None:
        raise EngineError("militia need a world economy")
    return economy.militia
