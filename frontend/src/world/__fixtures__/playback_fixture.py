"""Record real fogged world replays for the turn-playback tests.

Run from ``api/`` (it imports the engine)::

    uv run python ../frontend/src/world/__fixtures__/playback_fixture.py

Writes ``frontend/src/world/__fixtures__/playback.json``. A small bot world is
played headless exactly as the world tick plays it (every bot plans on its
fog-redacted view, the plan is validated, the engine resolves), and for a few
(viewer, turn) pairs the script keeps what ``GET /worlds/{id}/turns/{turn}``
would serve that viewer: the witnessed events, redacted for the viewer
(`filter_events`), and the redacted ``state_after``
(`GameService.get_world_replay`). Each case also carries:

- ``before``: the same viewer's redacted ``state_after`` of the turn before,
  the start-of-turn picture the playback engine prefers (``null`` for turn 0,
  which has no earlier replay);
- ``visible_before`` / ``visible_after``: the server's visible hexes for the
  viewer at the start and end of the turn, so the client's visibility mirror
  can be checked against the engine.

The map is written once in the compact encoding (it is a 49x49 world, a few
KB). The cases are chosen so that between them they contain plain moves,
travel columns packing, marching and auto-deploying on contact, combat with
damage, a unit destroyed, an entrant taking a hex, barrages (one by a gun the
viewer cannot see, served without its ``artillery_id``), a capture, the
viewer's own recruits and another player's recruit (served without its cost),
and other players' units coming into and out of the viewer's sight mid-turn;
the script fails if the chosen cases stop covering those (`REQUIRED`).
``api/tests/test_frontend_fixtures.py`` reruns it and fails when the
committed JSON is stale. Regenerate after an engine, bot, fog or
world-generator change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from app.ai.world import WorldPolicy
from app.engine.events import Event
from app.engine.execution import resolve_execution
from app.engine.fog import ReplayEvent, filter_events, redact_state
from app.engine.hex_coords import Axial, clear_geometry_caches
from app.engine.map_codec import encode_map
from app.engine.models import NEUTRAL, GameState
from app.engine.visibility import visible_hexes
from app.games.schemas import WorldState
from app.world.generate import WorldParams, generate
from app.world.season import _plan_all

SLOTS = 4
SEED = 1
# (viewer, turn): a bot seat and the resolved turns to keep for it.
CASES: list[tuple[str, int]] = [
    # The opening turn: no earlier replay to start from.
    ("b002", 0),
    # Garrisons destroyed, entrants taking the hexes, captures, own recruits.
    ("b002", 3),
    ("b002", 9),
    # Columns stopped by contact; turn 11 also has the viewer's own barrage,
    # a fight won and the capture it brings (the case the scenario tests use).
    ("b001", 10),
    ("b001", 11),
    # Enemies coming into sight mid-turn and dropping out of it, and barrages
    # by guns the viewer cannot see.
    ("b003", 15),
    ("b003", 16),
    ("b000", 20),
    ("b003", 20),
    # Another player's recruit, seen without its cost.
    ("b001", 32),
]
TURNS = max(turn for _, turn in CASES) + 1
REQUIRED = {
    "unit_moved",
    "unit_packed",
    "deploy:contact",
    "combat_resolved",
    "destroyed",
    "entrant",
    "barrage_resolved",
    "barrage:unseen_gun",
    "settlement_captured",
    "world_unit_recruited",
    "recruit:foreign",
    "sight:enter",
    "sight:leave",
}
# Served events are the fog view models, not the engine's own union.
_EVENTS = TypeAdapter(list[ReplayEvent])
OUTPUT = Path(__file__).resolve().parent / "playback.json"


class StaleCasesError(Exception):
    """The chosen `CASES` no longer contain every `REQUIRED` feature."""


def _coords(hexes: set[Axial]) -> list[list[int]]:
    return sorted([c.q, c.r] for c in hexes)


def _features(events: list[ReplayEvent], viewer: str) -> set[str]:
    """The `REQUIRED` features one served event list shows ``viewer``."""
    found: set[str] = set()
    for event in events:
        found.add(event.kind)
        if event.kind == "unit_deployed":
            found.add(f"deploy:{event.cause}")
        if event.kind == "combat_resolved":
            if any(p.destroyed for p in event.participants):
                found.add("destroyed")
            if event.entrant_id is not None:
                found.add("entrant")
        if event.kind == "barrage_resolved" and event.artillery_id is None:
            found.add("barrage:unseen_gun")
        if event.kind == "world_unit_recruited" and event.owner != viewer:
            found.add("recruit:foreign")
    return found


def _sight_features(
    events: list[ReplayEvent],
    before: dict[str, Any],
    after: dict[str, Any],
    viewer: str,
) -> set[str]:
    """Whether another player's unit comes into or drops out of ``viewer``'s sight.

    A unit of another player drawn at the end but not at the start, and not
    recruited this turn, came into sight; one drawn at the start but not at the
    end, and not destroyed this turn, dropped out of it.
    """

    def others(state: dict[str, Any]) -> set[int]:
        return {u["id"] for u in state["units"] if u["owner"] not in (viewer, NEUTRAL)}

    recruited: set[int] = set()
    destroyed: set[int] = set()
    for event in events:
        if event.kind == "world_unit_recruited":
            recruited.add(event.unit_id)
        if event.kind == "combat_resolved":
            destroyed |= {p.unit_id for p in event.participants if p.destroyed}
        if event.kind == "barrage_resolved":
            hits = ([event.hit] if event.hit is not None else []) + event.extra_hits
            destroyed |= {h.unit_id for h in hits if h.destroyed}
    found: set[str] = set()
    if others(after) - others(before) - recruited:
        found.add("sight:enter")
    if others(before) - others(after) - destroyed:
        found.add("sight:leave")
    return found


def _replay(
    state_after: GameState, events: list[Event], viewer: str, turn: int
) -> dict[str, Any]:
    """What `GameService.get_world_replay` serves ``viewer`` for ``turn``."""
    witnessed = filter_events(events, state_after, viewer)
    fogged = WorldState.of(redact_state(state_after, viewer))
    return {
        "turn": turn,
        "events": [e.model_dump(mode="json") for e in witnessed],
        "state_after": fogged.model_dump(mode="json"),
    }


def build() -> tuple[str, set[str]]:
    """Play the world and return the fixture's JSON text and the features it covers.

    Raises `StaleCasesError` when the cases miss a `REQUIRED` feature.
    """
    world = generate(WorldParams.for_slots(SLOTS, SEED))
    players = [f"b{k:03d}" for k in range(SLOTS)]
    state = world.to_state(players)
    policy = WorldPolicy(seed=SEED)
    wanted = {turn for _, turn in CASES}
    viewers = sorted({viewer for viewer, _ in CASES})
    replays: dict[tuple[str, int], dict[str, Any]] = {}
    visible: dict[tuple[str, int], list[list[int]]] = {}
    for viewer in viewers:
        visible[(viewer, -1)] = _coords(visible_hexes(state, viewer))
    for _ in range(TURNS):
        turn = state.turn
        orders, recruitments, economy = _plan_all(policy, state, players)
        state, events = resolve_execution(state, orders, recruitments, economy)
        clear_geometry_caches()
        for viewer in viewers:
            visible[(viewer, turn)] = _coords(visible_hexes(state, viewer))
            if turn in wanted or turn + 1 in wanted:
                replays[(viewer, turn)] = _replay(state, events, viewer, turn)

    cases = []
    covered: set[str] = set()
    for viewer, turn in CASES:
        replay = replays[(viewer, turn)]
        before = replays.get((viewer, turn - 1))
        before_state = None if before is None else before["state_after"]
        cases.append(
            {
                "viewer": viewer,
                "turn": turn,
                "before": before_state,
                "replay": replay,
                "visible_before": visible[(viewer, turn - 1)],
                "visible_after": visible[(viewer, turn)],
            }
        )
        served = _EVENTS.validate_python(replay["events"])
        covered |= _features(served, viewer)
        if before_state is not None:
            covered |= _sight_features(
                served, before_state, replay["state_after"], viewer
            )
    missing = REQUIRED - covered
    if missing:
        raise StaleCasesError(
            f"fixture cases no longer cover {sorted(missing)}; pick new CASES"
        )
    compact = encode_map(world.game_map)
    text = json.dumps({"map": compact.model_dump(mode="json"), "cases": cases})
    return text + "\n", covered


def main() -> None:
    text, covered = build()
    OUTPUT.write_text(text)
    print(f"wrote {len(CASES)} cases to {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    print("covers", sorted(covered))


if __name__ == "__main__":
    main()
