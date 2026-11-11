"""Headless world simulator (WORLD_SPEC §9).

Generates a world, fills slots with world bots, and resolves turns on a manual
clock, measuring what the slice-1 acceptance gates ask about: transit (jams,
travel outcomes), contact, fronts, expansion and performance.

Run: ``uv run python -m app.world.sim --slots 12 --seed 1 --turns 60 [--json out.json]``.

Every plan is checked with `validate_submission` before it resolves, so an illegal
bot order crashes the run instead of skewing the numbers. The same arguments
always produce the same `SimReport` apart from its timing fields.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.ai.policy import Policy
from app.ai.world import WorldPolicy
from app.engine.events import (
    BarrageResolved,
    ColumnHeld,
    ColumnStuck,
    CombatResolved,
    CombatRole,
    DeployCause,
    Event,
    SettlementCaptured,
    TrafficJam,
    TravelEnded,
    UnitDeployed,
    UnitDisbanded,
)
from app.engine.execution import resolve_execution
from app.engine.fog import redact_state
from app.engine.hex_coords import Axial, distance, neighbors, within
from app.engine.economy_rules import Building
from app.engine.models import (
    NEUTRAL,
    EconomyOrders,
    GameState,
    Order,
    PlayerId,
    Posture,
    Recruitment,
    TravelOrder,
)
from app.games.validation import validate_submission
from app.world.generate import GeneratedWorld, WorldParams, generate

_NEAR_CONTACT = 6  # a jam within this many hexes of a hostile unit is "near contact"


class TurnStats(BaseModel):
    """Per-turn series for charts and the gates."""

    model_config = ConfigDict(extra="forbid")

    turn: int
    settlements: dict[PlayerId, int]
    units: dict[PlayerId, int]
    columns: int
    front_units: int  # deployed units adjacent to a hostile deployed unit
    combats: int
    jams_near_contact: int
    jams_far: int
    resolve_ms: float
    think_ms_max: float
    # World economy (economy draft): barracks level per player, neutral garrison
    # units left, units disbanded by shortages and fights against garrisons.
    barracks: dict[PlayerId, int] = Field(default_factory=dict)
    garrison_units: int = 0
    disbanded: int = 0
    garrison_fights: int = 0


class SimReport(BaseModel):
    """Everything a world run measured (WORLD_SPEC §9)."""

    model_config = ConfigDict(extra="forbid")

    slots: int
    players: int
    seed: int
    turns: int
    width: int
    height: int
    turn_stats: list[TurnStats] = Field(default_factory=list)
    travel_issued: int = 0
    travel_ended: dict[str, int] = Field(default_factory=dict)
    travel_open_at_end: int = 0
    travel_turns_median: float = 0.0
    column_held: int = 0
    column_stuck: int = 0
    auto_deploys: int = 0
    disordered_fights: int = 0
    first_contact: dict[str, int] = Field(default_factory=dict)  # "a|b" -> turn
    neighbor_pairs: list[str] = Field(default_factory=list)
    # Mean deployed units per side, per engagement per turn (see `_engagements`).
    engagement_sizes: list[float] = Field(default_factory=list)
    eliminated: dict[PlayerId, int] = Field(default_factory=dict)
    # Sieges of garrisoned neutral sites (see `_Sieges`): captures, turns from the
    # first attack on a site's garrison to its capture, player units destroyed
    # fighting garrisons, and attacking units per besieged site per turn.
    neutral_captures: int = 0
    player_captures: int = 0
    siege_turns: list[int] = Field(default_factory=list)
    losses_to_garrisons: int = 0
    siege_attackers: list[int] = Field(default_factory=list)
    # Melee attacks on another player's unit, and how many went in with more own
    # deployed units than the defender's within two hexes of the fight.
    player_attacks: int = 0
    player_attacks_superior: int = 0
    think_ms_mean: float = 0.0
    think_ms_max: float = 0.0
    think_ms_p95: float = 0.0
    resolve_ms_max: float = 0.0
    gates: dict[str, bool] = Field(default_factory=dict)


def run_sim(
    slots: int,
    seed: int,
    turns: int,
    players: int | None = None,
    world: GeneratedWorld | None = None,
    policies: dict[PlayerId, Policy] | None = None,
) -> SimReport:
    """Generate (or take) a world, play ``turns`` turns with bots, and report."""
    world = world or generate(WorldParams.for_slots(slots, seed))
    count = players if players is not None else slots
    ids = [f"b{k:03d}" for k in range(count)]
    state = world.to_state(ids)
    bots: dict[PlayerId, Policy] = policies or {p: WorldPolicy(seed=seed) for p in ids}
    report = SimReport(
        slots=slots,
        players=count,
        seed=seed,
        turns=turns,
        width=world.game_map.width,
        height=world.game_map.height,
        neighbor_pairs=_neighbor_pairs(world, ids),
    )
    issued_at: dict[int, int] = {}
    travel_turns: list[int] = []
    think_all: list[float] = []
    sieges = _Sieges(state)

    for _ in range(turns):
        live = [p for p in ids if p not in report.eliminated]
        orders: list[Order] = []
        recruitments: list[Recruitment] = []
        economy: list[EconomyOrders] = []
        think: list[float] = []
        for player in live:
            view = redact_state(state, player)
            start = time.perf_counter()
            plan = bots[player].plan(view, player)
            think.append((time.perf_counter() - start) * 1000.0)
            base_orders = plan.economy_orders(player)
            validate_submission(
                view, player, plan.orders, plan.recruitments, base_orders
            )
            orders.extend(plan.orders)
            recruitments.extend(plan.recruitments)
            if base_orders is not None:
                economy.append(base_orders)
        for order in orders:
            if isinstance(order, TravelOrder):
                report.travel_issued += 1
                issued_at[order.unit_id] = state.turn

        before = state
        start = time.perf_counter()
        state, events = resolve_execution(state, orders, recruitments, economy)
        resolve_ms = (time.perf_counter() - start) * 1000.0
        think_all.extend(think)

        for event in events:
            if isinstance(event, TravelEnded):
                report.travel_ended[event.reason.value] = (
                    report.travel_ended.get(event.reason.value, 0) + 1
                )
                if event.unit_id in issued_at:
                    travel_turns.append(state.turn - issued_at.pop(event.unit_id))
        _tally(report, before, state, events, resolve_ms, max(think, default=0.0))
        sieges.fold(report, before, state.turn, events)
        for player in live:
            if not any(u.owner == player for u in state.units) and not any(
                s.controlled_by == player for s in state.settlements
            ):
                report.eliminated[player] = state.turn

    report.travel_open_at_end = len(state.standing_orders)
    report.travel_turns_median = (
        statistics.median(travel_turns) if travel_turns else 0.0
    )
    report.think_ms_mean = round(statistics.fmean(think_all), 1) if think_all else 0.0
    report.think_ms_p95 = round(_percentile(think_all, 95), 1)
    report.think_ms_max = round(max(think_all, default=0.0), 1)
    report.resolve_ms_max = max((t.resolve_ms for t in report.turn_stats), default=0.0)
    report.gates = _gates(report)
    return report


def _tally(
    report: SimReport,
    before: GameState,
    after: GameState,
    events: list[Event],
    resolve_ms: float,
    think_ms_max: float,
) -> None:
    """Fold one resolved turn into the report."""
    owner_of = {u.id: u.owner for u in before.units}
    hostile_coords = [(u.coord, u.owner) for u in before.units]
    jams_near = jams_far = 0
    combats = disbanded = garrison_fights = 0
    for event in events:
        if isinstance(event, TrafficJam):
            owner = owner_of[event.unit_ids[0]]
            near = any(
                o != owner and distance(c, event.coord) <= _NEAR_CONTACT
                for c, o in hostile_coords
            )
            if near:
                jams_near += 1
            else:
                jams_far += 1
        elif isinstance(event, ColumnHeld):
            report.column_held += 1
        elif isinstance(event, ColumnStuck):
            report.column_stuck += 1
        elif isinstance(event, UnitDeployed) and event.cause in (
            DeployCause.CONTACT,
            DeployCause.BARRAGED,
        ):
            report.auto_deploys += 1
        elif isinstance(event, UnitDisbanded):
            disbanded += 1
        elif isinstance(event, CombatResolved):
            combats += 1
            sides = Counter(p.owner for p in event.participants)
            if NEUTRAL in sides:
                garrison_fights += 1
                continue
            if any(p.defense_penalty > 0 for p in event.participants):
                report.disordered_fights += 1
            owners = sorted(sides)
            for k, a in enumerate(owners):
                for b in owners[k + 1 :]:
                    report.first_contact.setdefault(f"{a}|{b}", after.turn)

    deployed = {
        u.coord: u.owner
        for u in after.units
        if u.posture is Posture.DEPLOYED and u.owner != NEUTRAL
    }
    front_hexes = {
        coord
        for coord, owner in deployed.items()
        if any(deployed.get(n, owner) != owner for n in neighbors(coord))
    }
    front = len(front_hexes)
    report.engagement_sizes.extend(_engagements(front_hexes, deployed))
    report.turn_stats.append(
        TurnStats(
            turn=after.turn,
            settlements=dict(
                Counter(s.controlled_by for s in after.settlements if s.controlled_by)
            ),
            units=dict(Counter(u.owner for u in after.units if u.owner != NEUTRAL)),
            columns=sum(1 for u in after.units if u.posture is Posture.COLUMN),
            front_units=front,
            combats=combats,
            jams_near_contact=jams_near,
            jams_far=jams_far,
            resolve_ms=round(resolve_ms, 1),
            think_ms_max=round(think_ms_max, 1),
            barracks={p.id: p.level(Building.BARRACKS) for p in after.players},
            garrison_units=sum(1 for u in after.units if u.owner == NEUTRAL),
            disbanded=disbanded,
            garrison_fights=garrison_fights,
        )
    )


_LOCAL_RADIUS = 3  # "local superiority" counts deployed units this close to a fight


class _Sieges:
    """Folds each turn's fights against neutral garrisons and other players.

    Each garrison unit belongs to the site it was placed around (the nearest
    site at turn 0). A site is under attack in a turn when a player's unit fights
    one of its garrison units in melee or barrages one; its siege runs from the
    first such turn to the turn it is captured.
    """

    def __init__(self, state: GameState) -> None:
        sites = [s.coord for s in state.map.sites]
        self.site_of: dict[int, Axial] = {
            u.id: min(sites, key=lambda c: (distance(c, u.coord), c.q, c.r))
            for u in state.units
            if u.owner == NEUTRAL
        }
        self.first_attack: dict[Axial, int] = {}

    def fold(
        self, report: SimReport, before: GameState, turn: int, events: list[Event]
    ) -> None:
        """Add one resolved turn's sieges, captures and attacks to ``report``."""
        owner_of = {u.id: u.owner for u in before.units}
        attackers: dict[Axial, set[int]] = {}
        for event in events:
            if isinstance(event, CombatResolved):
                garrison = [p for p in event.participants if p.owner == NEUTRAL]
                if garrison:
                    site = self.site_of[garrison[0].unit_id]
                    for p in event.participants:
                        if p.owner != NEUTRAL:
                            attackers.setdefault(site, set()).add(p.unit_id)
                            report.losses_to_garrisons += int(p.destroyed)
                else:
                    self._player_attack(report, before, event)
            elif isinstance(event, BarrageResolved):
                hits = ([event.hit] if event.hit is not None else []) + event.extra_hits
                struck = [h for h in hits if h.owner == NEUTRAL]
                if struck and owner_of[event.artillery_id] != NEUTRAL:
                    site = self.site_of[struck[0].unit_id]
                    attackers.setdefault(site, set()).add(event.artillery_id)
            elif isinstance(event, SettlementCaptured):
                if event.previous_owner is None:
                    report.neutral_captures += 1
                    if event.coord in self.first_attack:
                        report.siege_turns.append(
                            turn - self.first_attack.pop(event.coord) + 1
                        )
                else:
                    report.player_captures += 1
        for site in sorted(attackers, key=lambda c: (c.q, c.r)):
            self.first_attack.setdefault(site, turn)
            report.siege_attackers.append(len(attackers[site]))

    @staticmethod
    def _player_attack(
        report: SimReport, before: GameState, event: CombatResolved
    ) -> None:
        defenders = [p for p in event.participants if p.role is CombatRole.DEFENDER]
        if not defenders:
            return  # two moving units met in an empty hex: nobody attacked a position
        defender = defenders[0].owner
        near = Counter(
            u.owner
            for u in before.units
            if u.posture is Posture.DEPLOYED
            and distance(u.coord, event.coord) <= _LOCAL_RADIUS
        )
        for owner in sorted(
            {p.owner for p in event.participants if p.role is CombatRole.ATTACKER}
        ):
            if owner == defender:
                continue
            report.player_attacks += 1
            report.player_attacks_superior += int(near[owner] > near[defender])


def _engagements(front: set[Axial], deployed: dict[Axial, PlayerId]) -> list[float]:
    """Mean units per side in each engagement on the board.

    An engagement is a connected group of front units (deployed and adjacent to a
    hostile), joining units within two hexes of each other, so a line of
    brigades facing another counts as one engagement, not as many duels.
    """
    sizes: list[float] = []
    seen: set[Axial] = set()
    for start in sorted(front, key=lambda c: (c.q, c.r)):
        if start in seen:
            continue
        group = {start}
        queue = [start]
        while queue:
            here = queue.pop()
            for near in within(here, 2):
                if near in front and near not in group:
                    group.add(near)
                    queue.append(near)
        seen |= group
        sides = Counter(deployed[c] for c in group)
        if len(sides) > 1:
            sizes.append(sum(sides.values()) / len(sides))
    return sizes


def _neighbor_pairs(world: GeneratedWorld, ids: list[PlayerId]) -> list[str]:
    """Pairs of seated players whose start regions share a border."""
    slot_player = {world.fill_order[k]: p for k, p in enumerate(ids)}
    width = world.game_map.width
    height = world.game_map.height
    owner = [slot_player.get(t) for t in world.territory]
    pairs: set[str] = set()
    for i, a in enumerate(owner):
        if a is None:
            continue
        col, row = i % width, i // width
        # Right and down neighbours in odd-q offset cover every adjacency once.
        if col & 1:
            near = ((col + 1, row), (col + 1, row + 1), (col, row + 1))
        else:
            near = ((col + 1, row - 1), (col + 1, row), (col, row + 1))
        for ncol, nrow in near:
            if 0 <= ncol < width and 0 <= nrow < height:
                b = owner[nrow * width + ncol]
                if b is not None and b != a:
                    pairs.add("|".join(sorted((a, b))))
    return sorted(pairs)


def _median(values: list[int]) -> float:
    return statistics.median(values) if values else 0.0


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


def _gates(report: SimReport) -> dict[str, bool]:
    """The slice-1 acceptance gates this run can judge (WORLD_SPEC §9)."""
    ended = sum(report.travel_ended.values())
    good = report.travel_ended.get("arrived", 0) + report.travel_ended.get("contact", 0)
    replaced = report.travel_ended.get("replaced", 0)
    turn20 = next((t for t in report.turn_stats if t.turn == 20), None)
    jams_far = sum(t.jams_far for t in report.turn_stats)
    jams_near = sum(t.jams_near_contact for t in report.turn_stats)
    contact_by_30 = [
        pair
        for pair in report.neighbor_pairs
        if report.first_contact.get(pair, 10**9) <= 30
    ]
    return {
        "jams_mostly_near_contact": jams_far <= max(2, jams_near // 4),
        "travel_arrives_or_meets_contact": ended - replaced == 0
        or good / (ended - replaced) >= 0.95,
        "expansion_3_by_turn_20": turn20 is not None
        and all(
            turn20.settlements.get(f"b{k:03d}", 0) >= 3 for k in range(report.players)
        ),
        "neighbors_in_contact_by_30": len(contact_by_30) == len(report.neighbor_pairs),
        "median_engagement_3_per_side": bool(report.engagement_sizes)
        and statistics.median(report.engagement_sizes) >= 3,
        "resolve_under_5s": report.resolve_ms_max < 5000,
    }


def _batch(
    slots: int, seeds: int, turns: int, players: int | None, out: Path | None
) -> None:
    """Run seeds 1..``seeds`` and tally gate passes (WORLD_SPEC §9)."""
    passes: Counter[str] = Counter()
    gate_names: list[str] = []
    summary: list[dict[str, object]] = []
    for seed in range(1, seeds + 1):
        report = run_sim(slots, seed, turns, players)
        passes.update(name for name, ok in report.gates.items() if ok)
        gate_names = sorted(report.gates)
        last = report.turn_stats[-1]
        summary.append(
            {
                "seed": seed,
                "gates": report.gates,
                "eliminated": len(report.eliminated),
                "settlements_end": sorted(last.settlements.values(), reverse=True),
                "travel_ended": report.travel_ended,
                "engagement_median": (
                    statistics.median(report.engagement_sizes)
                    if report.engagement_sizes
                    else 0
                ),
                "think_ms_p95": report.think_ms_p95,
                "resolve_ms_max": report.resolve_ms_max,
            }
        )
        print(f"seed {seed}: {sum(report.gates.values())}/{len(report.gates)} gates")
    for name in gate_names:
        print(f"{name:34s} {passes[name]}/{seeds}")
    if out is not None:
        out.write_text(json.dumps(summary, indent=1))


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slots", type=int, default=12)
    parser.add_argument("--players", type=int, default=None)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--turns", type=int, default=60)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument(
        "--seeds",
        type=int,
        default=None,
        help="run seeds 1..N and print how many pass each gate (§9: over 20 seeds)",
    )
    args = parser.parse_args()
    if args.seeds is not None:
        _batch(args.slots, args.seeds, args.turns, args.players, args.json)
        return
    started = time.perf_counter()
    report = run_sim(args.slots, args.seed, args.turns, args.players)
    elapsed = time.perf_counter() - started
    if args.json is not None:
        args.json.write_text(report.model_dump_json(indent=1))
    last = report.turn_stats[-1]
    print(
        f"world {report.width}x{report.height}, {report.players} bots, {report.turns} turns"
    )
    print(f"settlements at end: {sorted(last.settlements.values(), reverse=True)}")
    print(
        f"travel issued {report.travel_issued}, ended {report.travel_ended}, open {report.travel_open_at_end}"
    )
    print(
        f"column held {report.column_held}, stuck {report.column_stuck}, auto-deploys {report.auto_deploys}"
    )
    print(
        f"contact pairs {len(report.first_contact)} / neighbour pairs {len(report.neighbor_pairs)}"
    )
    sizes = report.engagement_sizes
    print(
        f"engagements {len(sizes)}, median per side {statistics.median(sizes) if sizes else 0}"
    )
    print(
        f"think mean {report.think_ms_mean} ms, p95 {report.think_ms_p95} ms; resolve max {report.resolve_ms_max} ms"
    )
    print(f"eliminated {report.eliminated}")
    print(
        f"captures: neutral {report.neutral_captures}, player {report.player_captures}; "
        f"siege turns median {_median(report.siege_turns)}, attackers per besieged "
        f"site median {_median(report.siege_attackers)}; losses to garrisons "
        f"{report.losses_to_garrisons}"
    )
    print(
        f"attacks on players {report.player_attacks}, with local superiority "
        f"{report.player_attacks_superior}"
    )
    print(json.dumps(report.gates, indent=1))
    print(f"wall {elapsed:.1f} s")


if __name__ == "__main__":
    main()
