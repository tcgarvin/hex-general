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
clash. Pure friendly cycles (2-unit swaps, rotations) still cannot resolve
without stacking, so those units stay put (as in Phase 2).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.engine.combat import ContestOutcome, compute_damage, resolve_contest
from app.engine.events import (
    CombatParticipant,
    CombatResolved,
    CombatRole,
    Event,
    MoveBlocked,
    MoveBlockedReason,
    TrafficJam,
    UnitMoved,
)
from app.engine.hex_coords import Axial, is_adjacent, neighbors
from app.engine.models import (
    GameState,
    Hex,
    HoldOrder,
    MoveOrder,
    Order,
    PlayerId,
    Unit,
    UnitId,
)
from app.engine.stats import TERRAIN_STATS, UNIT_STATS, UnitType


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
class _PulseState:
    """Shared mutable context threaded through one pulse's resolution helpers."""

    pulse: int
    hexes: dict[Axial, Hex]
    units: dict[UnitId, Unit]
    positions: dict[Axial, UnitId]
    movers: dict[UnitId, _Mover]
    hold_ids: set[UnitId]
    destroyed: set[UnitId]
    events: list[Event]
    start_pos: dict[Axial, UnitId] = field(default_factory=dict)


def resolve_movement(
    state: GameState, orders: list[Order]
) -> tuple[GameState, list[Event]]:
    """Resolve the movement phase (with combat), returning new state + event log.

    ``state`` is not mutated. Non-Move orders (Barrage/Rest/Hold) and units
    without an order do not move — they hold their hex and may fight as
    defenders. Raises `EngineError` if two orders target the same unit, an order
    references an unknown unit, or a move path is not a contiguous chain.
    """
    units: dict[UnitId, Unit] = {u.id: u.model_copy(deep=True) for u in state.units}
    hexes: dict[Axial, Hex] = {h.coord: h for h in state.map.hexes}

    movers = _build_movers(state, units, orders, hexes)
    positions: dict[Axial, UnitId] = {u.coord: u.id for u in units.values()}
    hold_ids: set[UnitId] = {o.unit_id for o in orders if isinstance(o, HoldOrder)}
    destroyed: set[UnitId] = set()
    events: list[Event] = []

    pulse_count = max((m.max_steps for m in movers.values()), default=0)
    for pulse in range(1, pulse_count + 1):
        ctx = _PulseState(
            pulse=pulse,
            hexes=hexes,
            units=units,
            positions=positions,
            movers=movers,
            hold_ids=hold_ids,
            destroyed=destroyed,
            events=events,
            start_pos=dict(positions),
        )
        _resolve_pulse(ctx)

    new_state = state.model_copy(deep=True)
    new_state.units = [units[u.id] for u in state.units if u.id not in destroyed]
    return new_state, events


# --- Order validation & movers ----------------------------------------------


def _build_movers(
    state: GameState,
    units: dict[UnitId, Unit],
    orders: list[Order],
    hexes: dict[Axial, Hex],
) -> dict[UnitId, _Mover]:
    """Validate orders and build a `_Mover` for each unit that will move."""
    seen: set[UnitId] = set()
    movers: dict[UnitId, _Mover] = {}
    for order in orders:
        if order.unit_id in seen:
            raise EngineError(f"unit {order.unit_id} has more than one order")
        seen.add(order.unit_id)
        if order.unit_id not in units:
            raise EngineError(f"order references unknown unit {order.unit_id}")
        if not isinstance(order, MoveOrder):
            continue
        unit = units[order.unit_id]
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
    return movers


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
        if occupant is not None and ctx.units[occupant].owner != mover.owner:
            # Attacking into an enemy hex (SPEC §12.1) — allowed even when engaged.
            if ctx.units[mover.unit_id].type is UnitType.ARTILLERY:
                _block(ctx, mover, target, MoveBlockedReason.ATTACK_FORBIDDEN)
            else:
                attacks[target].append(mover)
            continue

        # Empty or friendly-occupied target: a move. An engaged unit may not
        # step into another enemy-controlled hex (illegal ZOC→ZOC, SPEC §8.4).
        if _engaged(ctx, mover.unit_id) and _has_adjacent_enemy(
            target, mover.owner, ctx.units, ctx.start_pos
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
            target, ctx.units[defender_id], attacker_units, ctx.hold_ids, ctx.hexes
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
        owners = {m.owner for m in contenders}
        if len(owners) > 1:
            # Enemies racing for the same empty hex — a contest (SPEC §12.1/§12.8).
            attacker_units = [ctx.units[m.unit_id] for m in contenders]
            outcome = resolve_contest(
                target, None, attacker_units, ctx.hold_ids, ctx.hexes
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
            origin = ctx.units[unit_id].coord
            _complete_move(ctx, ctx.movers[unit_id], target)
            ctx.events.append(
                UnitMoved(pulse=ctx.pulse, unit_id=unit_id, origin=origin, dest=target)
            )
            pending.discard(unit_id)
            progressed = True

    for unit_id in sorted(pending):
        _block(ctx, ctx.movers[unit_id], singles[unit_id], MoveBlockedReason.OCCUPIED)


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
    if outcome.entrant_id is not None:
        continued = _complete_move(ctx, ctx.movers[outcome.entrant_id], coord)

    for mover in attacker_movers:
        if mover.unit_id != outcome.entrant_id and mover.unit_id not in ctx.destroyed:
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
            entrant_id=outcome.entrant_id,
            entrant_continued=continued,
        )
    )


def _complete_move(ctx: _PulseState, mover: _Mover, dest: Axial) -> bool:
    """Move ``mover`` into ``dest`` (which must be free), returning whether it may
    continue (it has movement pulses remaining, SPEC §11.2/§12.5)."""
    origin = ctx.units[mover.unit_id].coord
    if ctx.positions.get(origin) == mover.unit_id:
        del ctx.positions[origin]
    ctx.positions[dest] = mover.unit_id
    ctx.units[mover.unit_id].coord = dest
    mover.steps_taken += 1
    continued = mover.steps_taken < mover.max_steps
    if not continued:
        mover.stopped = True
    return continued


def _kill(ctx: _PulseState, unit_id: UnitId) -> None:
    """Remove a destroyed unit from the board, stopping its mover if any."""
    coord = ctx.units[unit_id].coord
    if ctx.positions.get(coord) == unit_id:
        del ctx.positions[coord]
    ctx.destroyed.add(unit_id)
    if unit_id in ctx.movers:
        ctx.movers[unit_id].stopped = True


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


# --- ZOC helpers ------------------------------------------------------------


def _engaged(ctx: _PulseState, unit_id: UnitId) -> bool:
    """Whether ``unit_id`` begins the pulse adjacent to an enemy (SPEC §8.4)."""
    unit = ctx.units[unit_id]
    return _has_adjacent_enemy(unit.coord, unit.owner, ctx.units, ctx.start_pos)


def _has_adjacent_enemy(
    coord: Axial,
    owner: PlayerId,
    units: dict[UnitId, Unit],
    positions: dict[Axial, UnitId],
) -> bool:
    """Whether any of ``coord``'s six neighbours holds an enemy unit."""
    for neighbor_coord in neighbors(coord):
        occupant = positions.get(neighbor_coord)
        if occupant is not None and units[occupant].owner != owner:
            return True
    return False
