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
    ColumnHeld,
    ColumnStuck,
    CombatResolved,
    DeployCause,
    Event,
    TrafficJam,
    TravelEnded,
    UnitDeployed,
)
from app.engine.execution import resolve_execution
from app.engine.fog import redact_state
from app.engine.hex_coords import Axial, distance, neighbors
from app.engine.models import (
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
    engagement_sizes: list[float] = Field(default_factory=list)  # median units/side
    eliminated: dict[PlayerId, int] = Field(default_factory=dict)
    think_ms_mean: float = 0.0
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

    for _ in range(turns):
        live = [p for p in ids if p not in report.eliminated]
        orders: list[Order] = []
        recruitments: list[Recruitment] = []
        think: list[float] = []
        for player in live:
            view = redact_state(state, player)
            start = time.perf_counter()
            plan = bots[player].plan(view, player)
            think.append((time.perf_counter() - start) * 1000.0)
            validate_submission(view, player, plan.orders, plan.recruitments)
            orders.extend(plan.orders)
            recruitments.extend(plan.recruitments)
        for order in orders:
            if isinstance(order, TravelOrder):
                report.travel_issued += 1
                issued_at[order.unit_id] = state.turn

        before = state
        start = time.perf_counter()
        state, events = resolve_execution(state, orders, recruitments)
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
    combats = 0
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
        elif isinstance(event, CombatResolved):
            combats += 1
            sides = Counter(p.owner for p in event.participants)
            report.engagement_sizes.append(statistics.median(sides.values()))
            if any(p.defense_penalty > 0 for p in event.participants):
                report.disordered_fights += 1
            owners = sorted(sides)
            for k, a in enumerate(owners):
                for b in owners[k + 1 :]:
                    report.first_contact.setdefault(f"{a}|{b}", after.turn)

    deployed = {u.coord: u.owner for u in after.units if u.posture is Posture.DEPLOYED}
    front = sum(
        1
        for coord, owner in deployed.items()
        if any(deployed.get(n, owner) != owner for n in neighbors(coord))
    )
    report.turn_stats.append(
        TurnStats(
            turn=after.turn,
            settlements=dict(
                Counter(s.controlled_by for s in after.settlements if s.controlled_by)
            ),
            units=dict(Counter(u.owner for u in after.units)),
            columns=sum(1 for u in after.units if u.posture is Posture.COLUMN),
            front_units=front,
            combats=combats,
            jams_near_contact=jams_near,
            jams_far=jams_far,
            resolve_ms=round(resolve_ms, 1),
            think_ms_max=round(think_ms_max, 1),
        )
    )


def _neighbor_pairs(world: GeneratedWorld, ids: list[PlayerId]) -> list[str]:
    """Pairs of seated players whose start regions share a border."""
    slot_player = {world.fill_order[k]: p for k, p in enumerate(ids)}
    coords = [h.coord for h in world.game_map.hexes]
    where: dict[Axial, int] = {c: i for i, c in enumerate(coords)}
    pairs: set[str] = set()
    for i, coord in enumerate(coords):
        a = slot_player.get(world.territory[i])
        if a is None:
            continue
        for n in neighbors(coord):
            j = where.get(n)
            if j is None:
                continue
            b = slot_player.get(world.territory[j])
            if b is not None and b != a:
                pairs.add("|".join(sorted((a, b))))
    return sorted(pairs)


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


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slots", type=int, default=12)
    parser.add_argument("--players", type=int, default=None)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--turns", type=int, default=60)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
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
    print(json.dumps(report.gates, indent=1))
    print(f"wall {elapsed:.1f} s")


if __name__ == "__main__":
    main()
