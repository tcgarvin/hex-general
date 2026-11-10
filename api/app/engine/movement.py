"""Movement-pulse execution with combat (SPEC §7.1, §8, §11, §12; roadmap P2–P3).

The deterministic execution engine's movement phase: it threads units along their
exact planned paths, one step per *pulse*, resolving Zone-of-Control movement
restrictions (SPEC §8.4) and combat (SPEC §12) as it goes. ``resolve_movement``
is a pure function — it copies the input state, never mutates it, performs no
I/O, and uses no randomness — so it is fully unit-testable headless and its event
log doubles as the replay (`app.engine.events`).

Resolution model (SPEC §11):

  * Each Move order names an exact path of adjacent hexes (SPEC §7.1). A unit
    attempts one step per pulse and never reroutes. Its step budget is its type's
    ``move`` plus 1 if its *entire* path follows road hexes (SPEC §3.3).
  * **Zones of Control restrict movement; they never cause combat (SPEC §8.3).**
    A unit whose hex is adjacent to an enemy at the start of a pulse is *engaged*
    and may not step into another enemy-controlled hex (an illegal ZOC→ZOC move,
    SPEC §8.4); it may still attack an adjacent enemy or withdraw to a free hex.
    Engagement is recomputed from the board at the start of every pulse (§8.5).
  * **Combat does not end movement (SPEC §11.2).** A unit that attacks, destroys
    all opposition, and enters the contested hex may keep moving along its path —
    breakthroughs fall out naturally, with no armor-only rule. Units that are
    blocked, jam, lose a contest, or are pinned defending, stop for the turn.
  * Combat itself is delegated to `app.engine.combat.resolve_contest` (simul-
    taneous damage, multi-unit/multi-side occupation). Edge swaps (SPEC §12.9)
    are handled here as a dedicated two-unit clash.

Documented v0.1 limitations (rare simultaneity corners): a unit whose own hex is
attacked is *pinned* — it defends and its own move is cancelled even in the edge
case where its attacker is itself cancelled; and a third unit attacking a hex
already involved in an edge swap is simply blocked rather than merged into the
clash.

Friendly cycles resolve (WORLD_SPEC §4.7, replacing SPEC §8.2's swap jam): two
friendly units stepping into each other's hex in the same pulse swap, and a
closed rotation of three or more all step together. This is how a fresh unit
relieves one in the line: both order a move into the other's hex. A cycle with a
third unit also contending for one of its hexes is a jam, not a cycle.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from app.engine.combat import ContestOutcome, compute_damage, resolve_contest
from app.engine.events import (
    ColumnHeld,
    ColumnStuck,
    CombatParticipant,
    CombatResolved,
    CombatRole,
    DeployCause,
    Event,
    MoveBlocked,
    MoveBlockedReason,
    TrafficJam,
    UnitDeployed,
    UnitMoved,
    UnitPacked,
)
from app.engine.hex_coords import Axial, distance, is_adjacent, neighbors, within
from app.engine.models import (
    DeployOrder,
    Facing,
    GameState,
    Hex,
    HoldOrder,
    MarchOrder,
    MoveOrder,
    Order,
    PlayerId,
    Posture,
    Unit,
    UnitId,
)
from app.engine.stats import COLUMN_RULES, TERRAIN_STATS, UNIT_STATS, UnitType


class EngineError(Exception):
    """An order or path violates an engine invariant (a caller/programming bug).

    Order *legality* against game rules (range, costs, ownership) is validated
    upstream in Phase 5; this guards the structural contract the pure engine
    relies on: one order per unit, referencing a real unit, with a contiguous
    path. Distinct from `app.engine.scenario.ScenarioError` (load-time content).
    """


@dataclass
class _Mover:
    """Mutable per-unit movement bookkeeping for the duration of resolution."""

    unit_id: UnitId
    owner: PlayerId
    path: list[Axial]
    max_steps: int
    steps_taken: int = 0
    stopped: bool = False

    @property
    def active(self) -> bool:
        """Whether the unit still has a step to attempt this turn."""
        return not self.stopped and self.steps_taken < self.max_steps

    def next_target(self) -> Axial:
        """The hex this unit will attempt to enter on its next pulse."""
        return self.path[self.steps_taken]


@dataclass
class _Marcher:
    """Per-unit bookkeeping for a March order (WORLD_SPEC §4.3–4.5).

    A deployed marcher first packs: it stays deployed until ``ready_from``, then
    becomes a column. Each pulse from ``ready_from`` on adds one movement credit;
    a step costs the target hex's step price (road vs off-road) in credits.
    """

    unit_id: UnitId
    path: list[Axial]
    deploy_on_arrival: bool
    ready_from: int
    packing: bool
    steps_taken: int = 0
    credit: int = 0
    stopped: bool = False
    arrived: bool = False

    @property
    def marching(self) -> bool:
        """Whether the column still has steps to attempt this turn."""
        return not (self.packing or self.stopped or self.arrived)


@dataclass
class _PulseState:
    """Shared mutable context threaded through one pulse's resolution helpers.

    ``positions`` holds deployed units only (one per hex); packed columns live in
    ``columns`` (a stack per hex, id-sorted) and exert no ZOC.
    """

    pulse: int
    hexes: dict[Axial, Hex]
    units: dict[UnitId, Unit]
    positions: dict[Axial, UnitId]
    columns: dict[Axial, list[UnitId]]
    movers: dict[UnitId, _Mover]
    marchers: dict[UnitId, _Marcher]
    hold_ids: set[UnitId]
    destroyed: set[UnitId]
    events: list[Event]
    hostile: Callable[[PlayerId, PlayerId], bool]
    # A stationary defender's terrain bonus at a hex (`GameState.terrain_defense`).
    terrain_defense: Callable[[Axial], int]
    start_pos: dict[Axial, UnitId] = field(default_factory=dict)


def resolve_movement(
    state: GameState,
    orders: list[Order],
    barraged: frozenset[UnitId] = frozenset(),
) -> tuple[GameState, list[Event]]:
    """Resolve the movement phase (with combat), returning new state + event log.

    ``state`` is not mutated. Non-Move orders (Barrage/Rest/Hold) and units
    without an order do not move — they hold their hex and may fight as
    defenders. ``barraged`` names units struck by this turn's barrage; a packed
    survivor among them deploys, disordered, before the first pulse (WORLD_SPEC
    §4.5). Raises `EngineError` if two orders target the same unit, an order
    references an unknown unit, an order does not fit the unit's posture, or a
    path is not a contiguous chain.

    With no columns on the board and no March orders, resolution is exactly the
    v0.1 pulse loop. Otherwise the turn runs the full column pulse count so packed
    units keep checking for contact while deployed units fight in the opening
    pulses (WORLD_SPEC §4.3).
    """
    units: dict[UnitId, Unit] = {u.id: u.model_copy() for u in state.units}
    hexes: dict[Axial, Hex] = state.map.index

    movers, marchers, deploy_ids = _build_movers(units, orders, hexes)
    positions: dict[Axial, UnitId] = {
        u.coord: u.id for u in units.values() if u.posture is Posture.DEPLOYED
    }
    columns: dict[Axial, list[UnitId]] = defaultdict(list)
    for unit in sorted(units.values(), key=lambda u: u.id):
        if unit.posture is Posture.COLUMN:
            columns[unit.coord].append(unit.id)
    hold_ids: set[UnitId] = {o.unit_id for o in orders if isinstance(o, HoldOrder)}
    destroyed: set[UnitId] = set()
    events: list[Event] = []

    pulse_count = max((m.max_steps for m in movers.values()), default=0)
    if columns or marchers:
        pulse_count = max(pulse_count, COLUMN_RULES.pulses_per_turn)
    ctx: _PulseState | None = None
    for pulse in range(1, pulse_count + 1):
        ctx = _PulseState(
            pulse=pulse,
            hexes=hexes,
            units=units,
            positions=positions,
            columns=columns,
            movers=movers,
            marchers=marchers,
            hold_ids=hold_ids,
            destroyed=destroyed,
            events=events,
            hostile=state.hostile,
            terrain_defense=state.terrain_defense,
            start_pos=dict(positions),
        )
        if pulse == 1:
            _begin_columns(ctx, barraged)
        _column_pulse_start(ctx)
        ctx.start_pos = dict(positions)
        _resolve_pulse(ctx)
        _march_pulse(ctx)
    if ctx is not None:
        _finish_deploys(ctx, deploy_ids)

    new_state = state.fork()
    new_state.units = [units[u.id] for u in state.units if u.id not in destroyed]
    return new_state, events


# --- Order validation & movers ----------------------------------------------


def _build_movers(
    units: dict[UnitId, Unit],
    orders: list[Order],
    hexes: dict[Axial, Hex],
) -> tuple[dict[UnitId, _Mover], dict[UnitId, _Marcher], set[UnitId]]:
    """Validate orders; build deployed movers, column marchers and deploy ids.

    Posture is fixed at planning time (barrage never changes it), so an order
    that doesn't fit the unit's posture is a caller bug: Move and Barrage need a
    deployed unit, Deploy needs a column (WORLD_SPEC §4).
    """
    seen: set[UnitId] = set()
    movers: dict[UnitId, _Mover] = {}
    marchers: dict[UnitId, _Marcher] = {}
    deploy_ids: set[UnitId] = set()
    for order in orders:
        if order.unit_id in seen:
            raise EngineError(f"unit {order.unit_id} has more than one order")
        seen.add(order.unit_id)
        if order.unit_id not in units:
            raise EngineError(f"order references unknown unit {order.unit_id}")
        unit = units[order.unit_id]
        if isinstance(order, DeployOrder):
            if unit.posture is not Posture.COLUMN:
                raise EngineError(f"unit {unit.id} is not packed and cannot deploy")
            deploy_ids.add(unit.id)
            continue
        if isinstance(order, MarchOrder):
            _validate_path(unit, order.path)
            packing = unit.posture is Posture.DEPLOYED
            marchers[unit.id] = _Marcher(
                unit_id=unit.id,
                path=list(order.path),
                deploy_on_arrival=order.deploy_on_arrival,
                ready_from=COLUMN_RULES.pack_pulses + 1 if packing else 1,
                packing=packing,
            )
            continue
        if not isinstance(order, MoveOrder):
            continue
        if unit.posture is Posture.COLUMN:
            raise EngineError(f"unit {unit.id} is packed; columns march, not move")
        _validate_path(unit, order.path)
        budget = _move_budget(unit, order.path, hexes)
        max_steps = min(budget, len(order.path))
        if max_steps > 0:
            movers[unit.id] = _Mover(
                unit_id=unit.id,
                owner=unit.owner,
                path=list(order.path),
                max_steps=max_steps,
            )
    return movers, marchers, deploy_ids


def _validate_path(unit: Unit, path: list[Axial]) -> None:
    """Ensure every step of a move path is adjacent to the previous hex."""
    prev = unit.coord
    for step in path:
        if not is_adjacent(prev, step):
            raise EngineError(
                f"unit {unit.id} path step {step} is not adjacent to {prev}"
            )
        prev = step


def _move_budget(unit: Unit, path: list[Axial], hexes: dict[Axial, Hex]) -> int:
    """Return the unit's step budget, applying the road bonus (SPEC §3.3)."""
    base = UNIT_STATS[unit.type].move
    if _path_is_all_road(unit.coord, path, hexes):
        return base + 1
    return base


def _path_is_all_road(
    origin: Axial, path: list[Axial], hexes: dict[Axial, Hex]
) -> bool:
    """Whether the origin and every path hex carry a road (off-map → False)."""
    for coord in (origin, *path):
        hex_ = hexes.get(coord)
        if hex_ is None or not hex_.road:
            return False
    return True


# --- Pulse resolution -------------------------------------------------------


def _resolve_pulse(ctx: _PulseState) -> None:
    """Resolve one movement pulse: legality, combat, then clean moves.

    Units are processed in id order wherever ordering is observable, so the
    event log is deterministic regardless of dict insertion order.
    """
    active = sorted(
        (m for m in ctx.movers.values() if m.active), key=lambda m: m.unit_id
    )

    attacks, moves = _classify(ctx, active)
    swap_hexes = _resolve_swaps(ctx, attacks)
    combat_hexes = set(attacks.keys()) | swap_hexes
    _resolve_attacks(ctx, attacks)
    _resolve_moves(ctx, moves, combat_hexes)


def _classify(
    ctx: _PulseState, active: list[_Mover]
) -> tuple[dict[Axial, list[_Mover]], list[tuple[_Mover, Axial]]]:
    """Split active movers into attacks (vs. enemy hexes) and ordinary moves.

    Emits the terrain, ZOC, and artillery-attack blocks inline; the returned
    structures hold only movers whose step is legal so far.
    """
    attacks: dict[Axial, list[_Mover]] = defaultdict(list)
    moves: list[tuple[_Mover, Axial]] = []
    for mover in active:
        target = mover.next_target()
        hex_ = ctx.hexes.get(target)
        if hex_ is None:
            _block(ctx, mover, target, MoveBlockedReason.OFF_MAP)
            continue
        if not TERRAIN_STATS[hex_.terrain].passable:
            _block(ctx, mover, target, MoveBlockedReason.IMPASSABLE)
            continue

        occupant = ctx.start_pos.get(target)
        if occupant is not None and ctx.hostile(ctx.units[occupant].owner, mover.owner):
            # Attacking into an enemy hex (SPEC §12.1) — allowed even when engaged.
            if ctx.units[mover.unit_id].type is UnitType.ARTILLERY:
                _block(ctx, mover, target, MoveBlockedReason.ATTACK_FORBIDDEN)
            elif ctx.units[mover.unit_id].disordered:
                _block(ctx, mover, target, MoveBlockedReason.DISORDERED)
            else:
                attacks[target].append(mover)
            continue
        if occupant is None and _hostile_columns_at(ctx, target, mover.owner):
            # Only reachable for a column stuck without room to deploy (§4.6).
            _block(ctx, mover, target, MoveBlockedReason.COLUMN_SHELTERED)
            continue

        # Empty or friendly-occupied target: a move. An engaged unit may not
        # step into another enemy-controlled hex (illegal ZOC→ZOC, SPEC §8.4).
        if _engaged(ctx, mover.unit_id) and _has_adjacent_enemy(
            target, mover.owner, ctx.units, ctx.start_pos, ctx.hostile
        ):
            _block(ctx, mover, target, MoveBlockedReason.ZOC_LOCKED)
            continue
        moves.append((mover, target))
    return attacks, moves


def _resolve_swaps(ctx: _PulseState, attacks: dict[Axial, list[_Mover]]) -> set[Axial]:
    """Resolve edge swaps (SPEC §12.9), mutating ``attacks`` and returning hexes.

    A swap is a mutual pair of enemy movers each attacking the other's hex. Each
    pair is resolved as a two-unit clash; both hexes are removed from ``attacks``
    and any third unit attacking either hex is blocked (a documented v0.1 corner).
    """
    pairs = _find_swap_pairs(ctx, attacks)
    swap_hexes: set[Axial] = set()
    for first, second in pairs:
        h_first = ctx.units[first].coord
        h_second = ctx.units[second].coord
        for hex_coord in (h_first, h_second):
            for mover in attacks.get(hex_coord, []):
                if mover.unit_id not in (first, second):
                    _block(ctx, mover, hex_coord, MoveBlockedReason.OCCUPIED)
            attacks.pop(hex_coord, None)
        _resolve_one_swap(ctx, first, second)
        swap_hexes.update({h_first, h_second})
    return swap_hexes


def _find_swap_pairs(
    ctx: _PulseState, attacks: dict[Axial, list[_Mover]]
) -> list[tuple[UnitId, UnitId]]:
    """Find unique mutual-attack pairs (each unit attacking the other's hex)."""
    pairs: list[tuple[UnitId, UnitId]] = []
    seen: set[frozenset[UnitId]] = set()
    for target, attackers in attacks.items():
        occupant = ctx.start_pos[target]
        for mover in attackers:
            mover_coord = ctx.units[mover.unit_id].coord
            counter = attacks.get(mover_coord, [])
            if any(a.unit_id == occupant for a in counter):
                key = frozenset({mover.unit_id, occupant})
                if key not in seen:
                    seen.add(key)
                    pairs.append((mover.unit_id, occupant))
    return pairs


def _resolve_one_swap(ctx: _PulseState, first: UnitId, second: UnitId) -> None:
    """Resolve a single edge-swap clash between two enemy units (SPEC §12.9)."""
    unit_a = ctx.units[first]
    unit_b = ctx.units[second]
    dmg_to_b = compute_damage(unit_a.type, unit_b.type, 0)
    dmg_to_a = compute_damage(unit_b.type, unit_a.type, 0)
    hp_a = max(0, unit_a.hp - dmg_to_a)
    hp_b = max(0, unit_b.hp - dmg_to_b)
    unit_a.hp = hp_a
    unit_b.hp = hp_b
    a_dead = hp_a == 0
    b_dead = hp_b == 0

    a_target = ctx.movers[first].next_target()
    b_target = ctx.movers[second].next_target()
    if a_dead:
        _kill(ctx, first)
    if b_dead:
        _kill(ctx, second)

    entrant_id: UnitId | None = None
    continued = False
    if not a_dead and not b_dead:
        ctx.movers[first].stopped = True
        ctx.movers[second].stopped = True
    elif a_dead and not b_dead:
        entrant_id = second
        continued = _complete_move(ctx, ctx.movers[second], b_target)
    elif b_dead and not a_dead:
        entrant_id = first
        continued = _complete_move(ctx, ctx.movers[first], a_target)

    participants = [
        _swap_participant(unit_a, dmg_to_b, dmg_to_a, hp_a, a_dead),
        _swap_participant(unit_b, dmg_to_a, dmg_to_b, hp_b, b_dead),
    ]
    coord = a_target if first < second else b_target
    ctx.events.append(
        CombatResolved(
            pulse=ctx.pulse,
            coord=coord,
            participants=participants,
            entrant_id=entrant_id,
            entrant_continued=continued,
        )
    )


def _swap_participant(
    unit: Unit, dealt: int, taken: int, hp_after: int, destroyed: bool
) -> CombatParticipant:
    """Build the combat-event record for one edge-swap participant (no terrain)."""
    return CombatParticipant(
        unit_id=unit.id,
        unit_type=unit.type,
        owner=unit.owner,
        role=CombatRole.ATTACKER,
        terrain_bonus=0,
        hold_bonus=0,
        damage_dealt=dealt,
        damage_taken=taken,
        hp_after=hp_after,
        destroyed=destroyed,
    )


def _resolve_attacks(ctx: _PulseState, attacks: dict[Axial, list[_Mover]]) -> None:
    """Resolve every into-enemy attack (SPEC §12.4/§12.5/§12.8).

    Each attacked occupant is *pinned*: it defends its hex and its own move is
    cancelled. Attackers whose own hex is being attacked cannot also attack, so
    their attacks are dropped before resolution.
    """
    pinned = {ctx.start_pos[target] for target in attacks}
    for target in list(attacks):
        live = [m for m in attacks[target] if m.unit_id not in pinned]
        if live:
            attacks[target] = live
        else:
            del attacks[target]

    for target in sorted(attacks, key=lambda c: (c.q, c.r)):
        defender_id = ctx.start_pos[target]
        attacker_movers = attacks[target]
        attacker_units = [ctx.units[m.unit_id] for m in attacker_movers]
        outcome = resolve_contest(
            target,
            ctx.units[defender_id],
            attacker_units,
            ctx.hold_ids,
            ctx.terrain_defense(target),
        )
        _settle_contest(ctx, target, defender_id, attacker_movers, outcome)

    # Pin any attacked occupant whose combat evaporated (documented over-stop).
    for unit_id in pinned:
        if (
            unit_id not in ctx.destroyed
            and unit_id in ctx.movers
            and not ctx.movers[unit_id].stopped
        ):
            ctx.movers[unit_id].stopped = True
    # An attacked unit still packing is interrupted and stays deployed (§4.4).
    for unit_id in sorted(pinned):
        marcher = ctx.marchers.get(unit_id)
        if (
            marcher is not None
            and marcher.packing
            and not marcher.stopped
            and unit_id not in ctx.destroyed
        ):
            marcher.stopped = True
            _emit_blocked(
                ctx, unit_id, marcher.path[0], MoveBlockedReason.PACK_INTERRUPTED
            )


def _resolve_moves(
    ctx: _PulseState, moves: list[tuple[_Mover, Axial]], combat_hexes: set[Axial]
) -> None:
    """Resolve ordinary moves: empty-hex contests, traffic jams, follow chains."""
    by_target: dict[Axial, list[_Mover]] = defaultdict(list)
    for mover, target in moves:
        if target in combat_hexes:
            _block(ctx, mover, target, MoveBlockedReason.OCCUPIED)
        else:
            by_target[target].append(mover)

    singles: dict[UnitId, Axial] = {}
    for target, contenders in by_target.items():
        if any(ctx.hostile(a.owner, b.owner) for a in contenders for b in contenders):
            # Enemies racing for the same empty hex — a contest (SPEC §12.1/§12.8).
            attacker_units = [ctx.units[m.unit_id] for m in contenders]
            outcome = resolve_contest(
                target, None, attacker_units, ctx.hold_ids, ctx.terrain_defense(target)
            )
            _settle_contest(ctx, target, None, contenders, outcome)
        elif len(contenders) == 1:
            singles[contenders[0].unit_id] = target
        else:
            ctx.events.append(
                TrafficJam(
                    pulse=ctx.pulse,
                    coord=target,
                    unit_ids=sorted(m.unit_id for m in contenders),
                )
            )
            for mover in contenders:
                mover.stopped = True

    _resolve_follow_chains(ctx, singles)


def _resolve_follow_chains(ctx: _PulseState, singles: dict[UnitId, Axial]) -> None:
    """Apply lone moves, settling follow-the-leader chains by fixpoint.

    A move lands only when its target is free; vacating an origin can free a
    follower's target, so iterate until no further progress. True cycles never
    free up and fall through to the blocked sweep. Successful movers are not
    stopped — they continue next pulse (SPEC §11.2 continuation).
    """
    pending = set(singles)
    progressed = True
    while progressed:
        progressed = False
        for unit_id in sorted(pending):
            target = singles[unit_id]
            if target in ctx.positions:
                continue
            _step_into(ctx, unit_id, target)
            pending.discard(unit_id)
            progressed = True

    for cycle in _friendly_cycles(ctx, singles, pending):
        # Relief in place / rotation (WORLD_SPEC §4.7): every member steps at once.
        for unit_id in cycle:
            _step_into(ctx, unit_id, singles[unit_id])
            pending.discard(unit_id)

    for unit_id in sorted(pending):
        _block(ctx, ctx.movers[unit_id], singles[unit_id], MoveBlockedReason.OCCUPIED)


def _step_into(ctx: _PulseState, unit_id: UnitId, target: Axial) -> None:
    """Complete one lone, uncontested step and log it."""
    origin = ctx.units[unit_id].coord
    _complete_move(ctx, ctx.movers[unit_id], target)
    ctx.events.append(
        UnitMoved(pulse=ctx.pulse, unit_id=unit_id, origin=origin, dest=target)
    )


def _friendly_cycles(
    ctx: _PulseState, singles: dict[UnitId, Axial], pending: set[UnitId]
) -> list[list[UnitId]]:
    """Closed loops of stalled movers, each stepping into the next one's hex.

    After the follow-chain fixpoint, a pending mover's target is held by another
    unit. Following "whose hex do I want" from mover to occupant either reaches a
    unit that isn't moving (blocked) or closes a loop of pending movers: a
    head-on swap (two units) or a rotation (three or more). Only units owned by
    non-hostile players reach this point (a hostile occupant is an attack), so
    loops are friendly by construction. Each loop is returned starting from its
    lowest id, loops ordered by that id.
    """
    cycles: list[list[UnitId]] = []
    settled: set[UnitId] = set()
    for start in sorted(pending):
        if start in settled:
            continue
        trail: list[UnitId] = []
        current: UnitId | None = start
        while current is not None and current in pending and current not in settled:
            if current in trail:
                loop = trail[trail.index(current) :]
                first = loop.index(min(loop))
                cycles.append(loop[first:] + loop[:first])
                break
            trail.append(current)
            current = ctx.positions.get(singles[current])
        settled.update(trail)
    return sorted(cycles, key=lambda loop: loop[0])


# --- Combat application -----------------------------------------------------


def _settle_contest(
    ctx: _PulseState,
    coord: Axial,
    defender_id: UnitId | None,
    attacker_movers: list[_Mover],
    outcome: ContestOutcome,
) -> None:
    """Apply a contest outcome: damage, removals, occupation, and the event."""
    for unit_id, result in outcome.results.items():
        ctx.units[unit_id].hp = result.hp_after
    for unit_id, result in outcome.results.items():
        if result.destroyed:
            _kill(ctx, unit_id)

    continued = False
    entrant_id = outcome.entrant_id
    if entrant_id is not None and _hostile_columns_at(
        ctx, coord, ctx.units[entrant_id].owner
    ):
        # "All opposition destroyed" includes sheltered columns (§4.6).
        entrant_id = None
    if entrant_id is not None:
        continued = _complete_move(ctx, ctx.movers[entrant_id], coord)

    for mover in attacker_movers:
        if mover.unit_id != entrant_id and mover.unit_id not in ctx.destroyed:
            mover.stopped = True
    if (
        defender_id is not None
        and defender_id in ctx.movers
        and defender_id not in ctx.destroyed
    ):
        ctx.movers[defender_id].stopped = True

    participants = [
        CombatParticipant(
            unit_id=unit_id,
            unit_type=ctx.units[unit_id].type,
            owner=ctx.units[unit_id].owner,
            role=result.role,
            terrain_bonus=result.terrain_bonus,
            hold_bonus=result.hold_bonus,
            defense_penalty=result.defense_penalty,
            damage_dealt=result.damage_dealt,
            damage_taken=result.damage_taken,
            hp_after=result.hp_after,
            destroyed=result.destroyed,
        )
        for unit_id, result in sorted(outcome.results.items())
    ]
    ctx.events.append(
        CombatResolved(
            pulse=ctx.pulse,
            coord=coord,
            participants=participants,
            entrant_id=entrant_id,
            entrant_continued=continued,
        )
    )


def _complete_move(ctx: _PulseState, mover: _Mover, dest: Axial) -> bool:
    """Move ``mover`` into ``dest`` (which must be free), returning whether it may
    continue (it has movement pulses remaining, SPEC §11.2/§12.5).

    This is the single point where a unit's coord changes, so it also updates the
    unit's cosmetic ``facing`` from the net horizontal sign of the step (roadmap
    U0): the board is flat-top, so rightward on the ``q`` axis faces `RIGHT`,
    leftward faces `LEFT`, and a purely vertical step keeps the prior facing.
    Because each completed step overwrites facing, the final step of the turn wins.
    """
    unit = ctx.units[mover.unit_id]
    origin = unit.coord
    _face(unit, dest)
    if ctx.positions.get(origin) == mover.unit_id:
        del ctx.positions[origin]
    ctx.positions[dest] = mover.unit_id
    unit.coord = dest
    mover.steps_taken += 1
    continued = mover.steps_taken < mover.max_steps
    if not continued:
        mover.stopped = True
    return continued


def _kill(ctx: _PulseState, unit_id: UnitId) -> None:
    """Remove a destroyed unit from the board, stopping its mover if any."""
    unit = ctx.units[unit_id]
    coord = unit.coord
    if unit.posture is Posture.COLUMN:
        _remove_column(ctx, unit_id, coord)
    elif ctx.positions.get(coord) == unit_id:
        del ctx.positions[coord]
    ctx.destroyed.add(unit_id)
    if unit_id in ctx.movers:
        ctx.movers[unit_id].stopped = True
    if unit_id in ctx.marchers:
        ctx.marchers[unit_id].stopped = True


def _block(
    ctx: _PulseState, mover: _Mover, target: Axial, reason: MoveBlockedReason
) -> None:
    """Record that ``mover``'s step into ``target`` failed; it stops for the turn."""
    ctx.events.append(
        MoveBlocked(
            pulse=ctx.pulse,
            unit_id=mover.unit_id,
            origin=ctx.units[mover.unit_id].coord,
            target=target,
            reason=reason,
        )
    )
    mover.stopped = True


# --- Columns (WORLD_SPEC §4) ---------------------------------------------------


def _begin_columns(ctx: _PulseState, barraged: frozenset[UnitId]) -> None:
    """Start-of-turn column checks, before the first pulse.

    A deployed unit that begins the turn in hostile ZOC cannot pack (it must
    withdraw first, §4.4). A packed survivor of this turn's barrage deploys,
    disordered (§4.5).
    """
    for marcher in sorted(ctx.marchers.values(), key=lambda m: m.unit_id):
        if marcher.packing and _engaged(ctx, marcher.unit_id):
            marcher.stopped = True
            _emit_blocked(
                ctx,
                marcher.unit_id,
                marcher.path[0],
                MoveBlockedReason.ENGAGED_CANNOT_PACK,
            )
    for unit_id in sorted(barraged):
        unit = ctx.units[unit_id]
        if unit.posture is Posture.COLUMN:
            _deploy(ctx, unit_id, DeployCause.BARRAGED, disordered=True)


def _column_pulse_start(ctx: _PulseState) -> None:
    """Finish packing that completes this pulse, then auto-deploy on contact.

    A unit whose packing would complete with a hostile unit inside the deploy
    distance stays deployed (its packing is interrupted, not disordered). Every
    column — marching or not — with a hostile unit inside the deploy distance
    halts and deploys, disordered (§4.5).
    """
    for marcher in sorted(ctx.marchers.values(), key=lambda m: m.unit_id):
        if not marcher.packing or marcher.stopped or ctx.pulse != marcher.ready_from:
            continue
        unit = ctx.units[marcher.unit_id]
        if _hostile_within(ctx, unit.coord, unit.owner, COLUMN_RULES.deploy_distance):
            marcher.stopped = True
            _emit_blocked(
                ctx, unit.id, marcher.path[0], MoveBlockedReason.PACK_INTERRUPTED
            )
            continue
        del ctx.positions[unit.coord]
        _add_column(ctx, unit.id, unit.coord)
        unit.posture = Posture.COLUMN
        marcher.packing = False
        ctx.events.append(
            UnitPacked(pulse=ctx.pulse, unit_id=unit.id, coord=unit.coord)
        )

    for unit_id in sorted(uid for stack in ctx.columns.values() for uid in stack):
        unit = ctx.units[unit_id]
        if _hostile_within(ctx, unit.coord, unit.owner, COLUMN_RULES.deploy_distance):
            _deploy(ctx, unit_id, DeployCause.CONTACT, disordered=True)


def _march_pulse(ctx: _PulseState) -> None:
    """Advance every marching column by its credit budget for this pulse.

    Each column gains one credit per pulse from ``ready_from``; a step costs the
    target's price (road or off-road). A step that would enter a hostile hex or
    come within the deploy distance of a hostile unit halts the column, which
    deploys disordered. A full friendly stack makes the column wait (it keeps its
    order). Columns are processed in id order.
    """
    for marcher in sorted(ctx.marchers.values(), key=lambda m: m.unit_id):
        if not marcher.marching or ctx.pulse < marcher.ready_from:
            continue
        unit = ctx.units[marcher.unit_id]
        target = marcher.path[marcher.steps_taken]
        hex_ = ctx.hexes.get(target)
        if hex_ is None:
            marcher.stopped = True
            _emit_blocked(ctx, unit.id, target, MoveBlockedReason.OFF_MAP)
            continue
        if not TERRAIN_STATS[hex_.terrain].passable:
            marcher.stopped = True
            _emit_blocked(ctx, unit.id, target, MoveBlockedReason.IMPASSABLE)
            continue

        cost = (
            COLUMN_RULES.road_step_pulses
            if hex_.road
            else COLUMN_RULES.offroad_step_pulses
        )
        marcher.credit = min(marcher.credit + 1, cost)
        if marcher.credit < cost:
            continue
        if _hostile_within(ctx, target, unit.owner, COLUMN_RULES.deploy_distance):
            _deploy(ctx, unit.id, DeployCause.CONTACT, disordered=True)
            continue
        if len(ctx.columns.get(target, [])) >= COLUMN_RULES.stack_cap:
            ctx.events.append(
                ColumnHeld(
                    pulse=ctx.pulse, unit_id=unit.id, coord=unit.coord, target=target
                )
            )
            continue

        origin = unit.coord
        _remove_column(ctx, unit.id, origin)
        _add_column(ctx, unit.id, target)
        _face(unit, target)
        unit.coord = target
        marcher.credit = 0
        marcher.steps_taken += 1
        if marcher.steps_taken == len(marcher.path):
            marcher.arrived = True
        ctx.events.append(
            UnitMoved(pulse=ctx.pulse, unit_id=unit.id, origin=origin, dest=target)
        )


def _finish_deploys(ctx: _PulseState, deploy_ids: set[UnitId]) -> None:
    """After the last pulse, deploy (clean) ordered and arrived columns."""
    arrived = {
        m.unit_id for m in ctx.marchers.values() if m.arrived and m.deploy_on_arrival
    }
    for unit_id in sorted(deploy_ids | arrived):
        if unit_id in ctx.destroyed:
            continue
        if ctx.units[unit_id].posture is not Posture.COLUMN:
            continue  # already deployed on contact this turn
        cause = DeployCause.ORDERED if unit_id in deploy_ids else DeployCause.ARRIVED
        _deploy(ctx, unit_id, cause, disordered=False)


def _deploy(
    ctx: _PulseState, unit_id: UnitId, cause: DeployCause, *, disordered: bool
) -> None:
    """Unpack a column where it stands, or spill to the nearest free hex.

    The column keeps its own hex unless a deployed unit already holds it; then it
    takes the nearest hex within the spill radius that is on-map, passable, free
    of deployed units and hostile columns, and outside hostile ZOC (ties by
    ``q``, then ``r``). With no such hex it stays packed (`ColumnStuck`). Either
    way its march ends for the turn.
    """
    unit = ctx.units[unit_id]
    if unit_id in ctx.marchers:
        ctx.marchers[unit_id].stopped = True
    origin = unit.coord
    dest = origin if origin not in ctx.positions else _spill_hex(ctx, unit)
    if dest is None:
        ctx.events.append(
            ColumnStuck(pulse=ctx.pulse, unit_id=unit_id, coord=origin, cause=cause)
        )
        return
    _remove_column(ctx, unit_id, origin)
    ctx.positions[dest] = unit_id
    unit.coord = dest
    unit.posture = Posture.DEPLOYED
    if disordered:
        unit.disordered = True
    ctx.events.append(
        UnitDeployed(
            pulse=ctx.pulse,
            unit_id=unit_id,
            origin=origin,
            dest=dest,
            cause=cause,
            disordered=disordered,
        )
    )


def _spill_hex(ctx: _PulseState, unit: Unit) -> Axial | None:
    """The nearest free hex a crowded column may deploy into, if any."""
    candidates = sorted(
        (c for c in within(unit.coord, COLUMN_RULES.spill_radius) if c != unit.coord),
        key=lambda c: (distance(unit.coord, c), c.q, c.r),
    )
    for coord in candidates:
        hex_ = ctx.hexes.get(coord)
        if hex_ is None or not TERRAIN_STATS[hex_.terrain].passable:
            continue
        if coord in ctx.positions:
            continue
        if any(
            ctx.hostile(ctx.units[c].owner, unit.owner)
            for c in ctx.columns.get(coord, [])
        ):
            continue
        if _has_adjacent_enemy(
            coord, unit.owner, ctx.units, ctx.positions, ctx.hostile
        ):
            continue
        return coord
    return None


def _hostile_within(
    ctx: _PulseState, coord: Axial, owner: PlayerId, radius: int
) -> bool:
    """Whether any hostile unit, deployed or packed, is within ``radius`` of ``coord``."""
    for near in within(coord, radius):
        occupant = ctx.positions.get(near)
        if occupant is not None and ctx.hostile(ctx.units[occupant].owner, owner):
            return True
        for column_id in ctx.columns.get(near, []):
            if ctx.hostile(ctx.units[column_id].owner, owner):
                return True
    return False


def _hostile_columns_at(ctx: _PulseState, coord: Axial, owner: PlayerId) -> bool:
    """Whether packed columns hostile to ``owner`` occupy ``coord``."""
    return any(
        ctx.hostile(ctx.units[c].owner, owner) for c in ctx.columns.get(coord, [])
    )


def _add_column(ctx: _PulseState, unit_id: UnitId, coord: Axial) -> None:
    """Add a column to ``coord``'s stack, keeping the stack id-sorted."""
    stack = ctx.columns[coord]
    stack.append(unit_id)
    stack.sort()


def _remove_column(ctx: _PulseState, unit_id: UnitId, coord: Axial) -> None:
    """Remove a column from ``coord``'s stack, dropping the stack when empty."""
    stack = ctx.columns[coord]
    stack.remove(unit_id)
    if not stack:
        del ctx.columns[coord]


def _emit_blocked(
    ctx: _PulseState, unit_id: UnitId, target: Axial, reason: MoveBlockedReason
) -> None:
    """Record a failed step or pack for a unit (without touching any mover)."""
    ctx.events.append(
        MoveBlocked(
            pulse=ctx.pulse,
            unit_id=unit_id,
            origin=ctx.units[unit_id].coord,
            target=target,
            reason=reason,
        )
    )


def _face(unit: Unit, dest: Axial) -> None:
    """Update cosmetic facing from the horizontal sign of a step (roadmap U0)."""
    if dest.q > unit.coord.q:
        unit.facing = Facing.RIGHT
    elif dest.q < unit.coord.q:
        unit.facing = Facing.LEFT


# --- ZOC helpers ------------------------------------------------------------


def _engaged(ctx: _PulseState, unit_id: UnitId) -> bool:
    """Whether ``unit_id`` begins the pulse adjacent to an enemy (SPEC §8.4)."""
    unit = ctx.units[unit_id]
    return _has_adjacent_enemy(
        unit.coord, unit.owner, ctx.units, ctx.start_pos, ctx.hostile
    )


def _has_adjacent_enemy(
    coord: Axial,
    owner: PlayerId,
    units: dict[UnitId, Unit],
    positions: dict[Axial, UnitId],
    hostile: Callable[[PlayerId, PlayerId], bool],
) -> bool:
    """Whether any of ``coord``'s six neighbours holds a hostile unit."""
    for neighbor_coord in neighbors(coord):
        occupant = positions.get(neighbor_coord)
        if occupant is not None and hostile(units[occupant].owner, owner):
            return True
    return False
