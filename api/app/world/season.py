"""Season recorder: play a headless bot world and write a compact playback record.

The record feeds the season viewer (``scripts/season-viewer.py``), an omniscient
observer page for judging bot quality and whether a world is fun (WORLD_SPEC §9).
It runs a world the way `app.world.sim` does: generate, seat bots, and each turn
plan every live bot on its fog-redacted view, validate the plan, and resolve.

Run: ``uv run python -m app.world.season --slots 200 --bots 100 --seed 1
--turns 60 --out season.json``.

The record is JSON with short keys and integer arrays (`SCHEMA_VERSION`). Hexes
are flat offset indices ``row * width + col``, the same row-major order as the
map's terrain string. Owners are codes: ``0`` is the neutral garrison owner (and,
for a site, "held by nobody"), ``k + 1`` is ``players[k]``; a vacant base slot is
``-1``. The static map is written once; each turn carries the units that changed
since the turn before (``u``), the ids that are gone (``x``), the site owner
changes (``s``), every player's numbers (``p``, fields in ``player_fields``), the
turn's located events (``e``, kinds in ``event_kinds``) and the paths of units
that moved (``m``). Turn 0 is the starting state, written in full.

Only the world-bot entry point (`WorldPolicy.plan` returning a `BotPlan`), world
generation and the engine are used, so the loop here stays independent of the
simulator's internals. The same arguments always give the same record.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai.world import WorldPolicy
from app.engine.economy_rules import Building, SiteKind
from app.engine.events import (
    BarrageHit,
    BarrageResolved,
    CombatResolved,
    CombatRole,
    ConstructionCompleted,
    Event,
    SettlementCaptured,
    UnitDeployed,
    UnitDisbanded,
    UnitMoved,
    WorldUnitRecruited,
)
from app.engine.execution import resolve_execution
from app.engine.fog import redact_state
from app.engine.hex_coords import Axial, axial_to_offset, clear_geometry_caches
from app.engine.map_codec import encode_map
from app.engine.models import (
    NEUTRAL,
    EconomyOrders,
    GameState,
    Order,
    PlayerId,
    Posture,
    Recruitment,
    Unit,
)
from app.engine.stats import UnitType
from app.games.validation import validate_submission
from app.world.collector import quiet_collector
from app.world.generate import GeneratedWorld, WorldParams, generate

SCHEMA_VERSION = 1

Json = dict[str, Any]

UNIT_TYPES: tuple[UnitType, ...] = tuple(UnitType)
SITE_KINDS: tuple[SiteKind, ...] = tuple(SiteKind)
BUILDINGS: tuple[Building, ...] = tuple(Building)

EVENT_KINDS: tuple[str, ...] = (
    "combat",  # [0, hex, [[uid, owner, type, role, taken, hp_after, destroyed], ...]]
    "barrage",  # [1, hex, gun_id, gun_owner, gun_hex, [[uid, owner, type, dmg, hp, dead]]]
    "capture",  # [2, hex, site, captor, previous, uid]
    "destroyed",  # [3, hex, uid, owner, type, cause (0 combat, 1 barrage)]
    "disband",  # [4, hex, uid, owner, type, shortage (0 fuel, 1 manpower)]
    "eliminated",  # [5, hex (their base), owner]
    "recruit",  # [6, hex, uid, owner, type]
    "built",  # [7, hex (their base), owner, building, level]
)
_COMBAT, _BARRAGE, _CAPTURE, _DESTROYED, _DISBAND, _ELIMINATED, _RECRUIT, _BUILT = (
    range(len(EVENT_KINDS))
)

PLAYER_FIELDS: tuple[str, ...] = (
    "units",
    "holdings",  # settlements held, the base included
    "manpower",
    "fuel",
    "steel",
    "barracks",
    "depot",
    "workshop",
    "cap",  # units the player may field (`GameState.unit_cap_for`)
)

# A unit as recorded: id, owner code, type, hp, flags (bit 0 column, bit 1
# disordered), hex.
_UnitRow = tuple[int, int, int, int, int, int]
UNIT_FIELDS: tuple[str, ...] = ("id", "owner", "type", "hp", "flags", "hex")


@dataclass(frozen=True)
class Season:
    """A recorded season: the JSON-ready ``record`` and the state it ended in."""

    record: Json
    final_state: GameState


class _Codes:
    """Index lookups shared by every turn of one season."""

    def __init__(
        self, state: GameState, players: list[PlayerId], bases: list[Axial]
    ) -> None:
        self.width = state.map.width
        self.owner = {NEUTRAL: 0} | {p: k + 1 for k, p in enumerate(players)}
        # Each player's own base slot: where their buildings stand, even after
        # the base itself has fallen (construction under way still completes).
        self.base = dict(zip(players, bases, strict=True))
        self.site = {s.coord: k for k, s in enumerate(state.map.sites)}
        self.unit_type = {t: k for k, t in enumerate(UNIT_TYPES)}
        self.building = {b: k for k, b in enumerate(BUILDINGS)}

    def hex(self, coord: Axial) -> int:
        col, row = axial_to_offset(coord)
        return row * self.width + col

    def unit(self, unit: Unit) -> _UnitRow:
        flags = (1 if unit.posture is Posture.COLUMN else 0) | (
            2 if unit.disordered else 0
        )
        return (
            unit.id,
            self.owner[unit.owner],
            self.unit_type[unit.type],
            unit.hp,
            flags,
            self.hex(unit.coord),
        )

    def holder(self, owner: PlayerId | None) -> int:
        """A settlement holder's code: 0 for nobody, else the player's."""
        return 0 if owner is None else self.owner[owner]


def record_season(
    slots: int,
    bots: int,
    seed: int,
    turns: int,
    world: GeneratedWorld | None = None,
    progress: Callable[[int, float], None] | None = None,
) -> Season:
    """Generate (or take) a world, seat ``bots`` world bots, play ``turns`` turns.

    ``progress`` is called after each resolved turn with the turn number and the
    seconds that turn took. Every plan is validated before it resolves, so an
    illegal bot order crashes the recording rather than the viewer showing it.
    """
    world = world or generate(WorldParams.for_slots(slots, seed))
    players = [f"b{k:03d}" for k in range(bots)]
    state = world.to_state(players)
    bases = [world.capitals[world.fill_order[k]] for k in range(bots)]
    codes = _Codes(state, players, bases)
    policy = WorldPolicy(seed=seed)

    record: Json = {
        "v": SCHEMA_VERSION,
        "slots": slots,
        "bots": bots,
        "seed": seed,
        "turns": turns,
        "players": players,
        "bases": [codes.hex(b) for b in bases],
        "unit_types": [t.value for t in UNIT_TYPES],
        "site_kinds": [k.value for k in SITE_KINDS],
        "buildings": [b.value for b in BUILDINGS],
        "event_kinds": list(EVENT_KINDS),
        "player_fields": list(PLAYER_FIELDS),
        "unit_fields": list(UNIT_FIELDS),
        "map": _map_record(state),
        "eliminated": [0] * bots,
        "frames": [],
    }
    previous: dict[int, _UnitRow] = {}
    holders = [-1] * len(state.map.sites)
    eliminated: dict[PlayerId, int] = {}
    record["frames"].append(
        _frame(codes, state, [], previous, holders, players, eliminated, [])
    )

    for _ in range(turns):
        # No collector pauses inside a turn, as in the server tick.
        with quiet_collector():
            started = time.perf_counter()
            live = [p for p in players if p not in eliminated]
            orders, recruitments, economy = _plan_all(policy, state, live)
            before = state
            state, events = resolve_execution(state, orders, recruitments, economy)
            # Bounds memory over a long run, as the world tick does.
            clear_geometry_caches()
            fallen = [
                p
                for p in live
                if not any(u.owner == p for u in state.units)
                and not any(s.controlled_by == p for s in state.settlements)
            ]
            for player in fallen:
                eliminated[player] = state.turn
                record["eliminated"][codes.owner[player] - 1] = state.turn
            located = _events(codes, before, events)
            for player in fallen:
                k = codes.owner[player]
                located.append([_ELIMINATED, codes.hex(bases[k - 1]), k])
            record["frames"].append(
                _frame(
                    codes,
                    state,
                    located,
                    previous,
                    holders,
                    players,
                    eliminated,
                    _paths(codes, events),
                )
            )
            if progress is not None:
                progress(state.turn, time.perf_counter() - started)
    return Season(record=record, final_state=state)


def _plan_all(
    policy: WorldPolicy, state: GameState, live: list[PlayerId]
) -> tuple[list[Order], list[Recruitment], list[EconomyOrders]]:
    """Every live bot's validated plan for this turn, merged."""
    orders: list[Order] = []
    recruitments: list[Recruitment] = []
    economy: list[EconomyOrders] = []
    for player in live:
        view = redact_state(state, player)
        plan = policy.plan(view, player)
        base_orders = plan.economy_orders(player)
        validate_submission(view, player, plan.orders, plan.recruitments, base_orders)
        orders.extend(plan.orders)
        recruitments.extend(plan.recruitments)
        if base_orders is not None:
            economy.append(base_orders)
    return orders, recruitments, economy


def _map_record(state: GameState) -> Json:
    """The static map: terrain and road strings plus a flat site table."""
    compact = encode_map(state.map)
    width = compact.width
    kind = {k: n for n, k in enumerate(SITE_KINDS)}
    sites: list[int] = []
    for site in compact.sites:
        col, row = axial_to_offset(site.coord)
        sites.extend((row * width + col, kind[site.kind], site.level, site.garrison))
    return {
        "w": width,
        "h": compact.height,
        "terrain": compact.terrain,
        "roads": compact.roads,
        "site_fields": ["hex", "kind", "level", "garrison"],
        "sites": sites,
    }


def _frame(
    codes: _Codes,
    state: GameState,
    events: list[list[Any]],
    previous: dict[int, _UnitRow],
    holders: list[int],
    players: list[PlayerId],
    eliminated: dict[PlayerId, int],
    paths: list[int],
) -> Json:
    """One turn's record, as a delta against ``previous`` and ``holders``.

    Updates ``previous`` and ``holders`` in place to this turn's values, so the
    next frame is a delta against this one.
    """
    current = {u.id: codes.unit(u) for u in state.units}
    changed = [
        value
        for uid in sorted(current)
        if previous.get(uid) != current[uid]
        for value in current[uid]
    ]
    removed = sorted(uid for uid in previous if uid not in current)
    previous.clear()
    previous.update(current)

    now = list(holders)
    for settlement in state.settlements:
        now[codes.site[settlement.coord]] = codes.holder(settlement.controlled_by)
    site_changes = [
        value
        for k, (old, new) in enumerate(zip(holders, now, strict=True))
        if old != new
        for value in (k, new)
    ]
    holders[:] = now

    units_of: dict[int, int] = {}
    for unit in state.units:
        code = codes.owner[unit.owner]
        units_of[code] = units_of.get(code, 0) + 1
    held: dict[int, int] = {}
    for settlement in state.settlements:
        if settlement.controlled_by is not None:
            code = codes.owner[settlement.controlled_by]
            held[code] = held.get(code, 0) + 1
    numbers: list[int] = []
    for player in state.players:
        code = codes.owner[player.id]
        numbers.extend(
            (
                units_of.get(code, 0),
                held.get(code, 0),
                player.stocks.manpower,
                player.stocks.fuel,
                player.stocks.steel,
                player.level(Building.BARRACKS),
                player.level(Building.DEPOT),
                player.level(Building.WORKSHOP),
                state.unit_cap_for(player.id),
            )
        )
    if [p.id for p in state.players] != players:
        raise RuntimeError("the engine reordered or changed the player list")
    return {
        "t": state.turn,
        "u": changed,
        "x": removed,
        "s": site_changes,
        "p": numbers,
        "e": events,
        "m": paths,
        "alive": len(players) - len(eliminated),
    }


def _events(codes: _Codes, before: GameState, events: list[Event]) -> list[list[Any]]:
    """The turn's notable events, located on the map, in resolution order."""
    owner = codes.owner
    unit_type = codes.unit_type
    start = {u.id: u for u in before.units}
    out: list[list[Any]] = []
    for event in events:
        if isinstance(event, CombatResolved):
            hex_ = codes.hex(event.coord)
            out.append(
                [
                    _COMBAT,
                    hex_,
                    [
                        [
                            p.unit_id,
                            owner[p.owner],
                            unit_type[p.unit_type],
                            0 if p.role is CombatRole.ATTACKER else 1,
                            p.damage_taken,
                            p.hp_after,
                            int(p.destroyed),
                        ]
                        for p in event.participants
                    ],
                ]
            )
            out.extend(
                [_DESTROYED, hex_, p.unit_id, owner[p.owner], unit_type[p.unit_type], 0]
                for p in event.participants
                if p.destroyed
            )
        elif isinstance(event, BarrageResolved):
            hits: list[BarrageHit] = (
                [] if event.hit is None else [event.hit]
            ) + event.extra_hits
            hex_ = codes.hex(event.target)
            gun = start[event.artillery_id]
            out.append(
                [
                    _BARRAGE,
                    hex_,
                    gun.id,
                    owner[gun.owner],
                    codes.hex(gun.coord),
                    [
                        [
                            h.unit_id,
                            owner[h.owner],
                            unit_type[h.unit_type],
                            h.damage,
                            h.hp_after,
                            int(h.destroyed),
                        ]
                        for h in hits
                    ],
                ]
            )
            out.extend(
                [_DESTROYED, hex_, h.unit_id, owner[h.owner], unit_type[h.unit_type], 1]
                for h in hits
                if h.destroyed
            )
        elif isinstance(event, SettlementCaptured):
            out.append(
                [
                    _CAPTURE,
                    codes.hex(event.coord),
                    codes.site[event.coord],
                    owner[event.captured_by],
                    codes.holder(event.previous_owner),
                    event.unit_id,
                ]
            )
        elif isinstance(event, UnitDisbanded):
            out.append(
                [
                    _DISBAND,
                    codes.hex(event.coord),
                    event.unit_id,
                    owner[event.owner],
                    unit_type[event.unit_type],
                    0 if event.shortage == "fuel" else 1,
                ]
            )
        elif isinstance(event, WorldUnitRecruited):
            out.append(
                [
                    _RECRUIT,
                    codes.hex(event.coord),
                    event.unit_id,
                    owner[event.owner],
                    unit_type[event.unit_type],
                ]
            )
        elif isinstance(event, ConstructionCompleted):
            out.append(
                [
                    _BUILT,
                    codes.hex(codes.base[event.player]),
                    owner[event.player],
                    codes.building[event.building],
                    event.level,
                ]
            )
    return out


def _paths(codes: _Codes, events: list[Event]) -> list[int]:
    """Flat ``[unit_id, n, hex_1 .. hex_n, ...]`` for every unit that moved.

    The hexes are entered in order; the unit's start is its hex the turn before.
    A column that deploys onto a free neighbour hex (a spill) counts as a step.
    """
    steps: dict[int, list[int]] = {}
    for event in events:
        if isinstance(event, UnitMoved) or (
            isinstance(event, UnitDeployed) and event.dest != event.origin
        ):
            steps.setdefault(event.unit_id, []).append(codes.hex(event.dest))
    out: list[int] = []
    for uid in sorted(steps):
        out.append(uid)
        out.append(len(steps[uid]))
        out.extend(steps[uid])
    return out


def write_season(season: Season, out: Path) -> int:
    """Write ``season.record`` as compact JSON; return the byte count."""
    text = json.dumps(season.record, separators=(",", ":"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return len(text)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--slots", type=int, default=12)
    parser.add_argument(
        "--bots", type=int, default=None, help="bots seated (default: every slot)"
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--turns", type=int, default=60)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    bots = args.slots if args.bots is None else args.bots
    if not 1 <= bots <= args.slots:
        parser.error(f"--bots must be 1..{args.slots}, got {bots}")
    if args.turns < 1:
        parser.error(f"--turns must be at least 1, got {args.turns}")

    started = time.perf_counter()

    def report(turn: int, seconds: float) -> None:
        total = time.perf_counter() - started
        print(f"turn {turn:3d}  {seconds:5.1f} s  ({total:6.1f} s)", file=sys.stderr)

    season = record_season(args.slots, bots, args.seed, args.turns, progress=report)
    size = write_season(season, args.out)
    elapsed = time.perf_counter() - started
    print(
        f"wrote {args.out} ({size / 1e6:.2f} MB): {args.slots} slots, {bots} bots, "
        f"seed {args.seed}, {args.turns} turns in {elapsed:.0f} s"
    )


if __name__ == "__main__":
    main()
