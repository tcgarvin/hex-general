"""Record real fogged world replays for the turn-playback tests.

Run from ``api/`` (it imports the engine)::

    uv run python ../frontend/src/world/__fixtures__/playback_fixture.py

Writes ``frontend/src/world/__fixtures__/playback.json``. A small bot world is
played headless exactly as the world tick plays it (every bot plans on its
fog-redacted view, the plan is validated, the engine resolves), and for a few
(viewer, turn) pairs the script keeps what ``GET /worlds/{id}/turns/{turn}``
would serve that viewer: the witnessed events and the redacted ``state_after``
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
damage, a unit destroyed, an entrant taking a hex, barrages, a capture, a
recruit, and other players' units coming into and out of the viewer's sight
mid-turn; the script fails if the chosen cases stop covering those.
Regenerate after an engine, bot or world-generator change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from app.ai.world import WorldPolicy
from app.engine.events import Event
from app.engine.execution import resolve_execution
from app.engine.fog import filter_events, redact_state
from app.engine.hex_coords import Axial, clear_geometry_caches
from app.engine.map_codec import encode_map
from app.engine.models import GameState
from app.engine.visibility import visible_hexes
from app.games.schemas import WorldState
from app.world.generate import WorldParams, generate
from app.world.season import _plan_all

SLOTS = 4
SEED = 1
TURNS = 16
# (viewer, turn): a bot seat and the resolved turns to keep for it.
CASES: list[tuple[str, int]] = [
    ("b002", 0),
    ("b002", 3),
    ("b002", 6),
    ("b002", 7),
    ("b001", 3),
    # Enemies coming into sight mid-turn, and one dropping out of it.
    ("b000", 4),
    ("b002", 8),
    ("b002", 9),
    ("b002", 14),
    ("b003", 15),
]
REQUIRED = {
    "unit_moved",
    "unit_packed",
    "deploy:contact",
    "combat_resolved",
    "destroyed",
    "entrant",
    "barrage_resolved",
    "settlement_captured",
    "world_unit_recruited",
}
_EVENTS = TypeAdapter(list[Event])
OUTPUT = Path(__file__).resolve().parent / "playback.json"


def _coords(hexes: set[Axial]) -> list[list[int]]:
    return sorted([c.q, c.r] for c in hexes)


def _features(events: list[Event]) -> set[str]:
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


def main() -> None:
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
        cases.append(
            {
                "viewer": viewer,
                "turn": turn,
                "before": None if before is None else before["state_after"],
                "replay": replay,
                "visible_before": visible[(viewer, turn - 1)],
                "visible_after": visible[(viewer, turn)],
            }
        )
        covered |= _features(_validate(replay["events"]))
    missing = REQUIRED - covered
    if missing:
        raise SystemExit(
            f"fixture cases no longer cover {sorted(missing)}; pick new CASES"
        )
    compact = encode_map(world.game_map)
    OUTPUT.write_text(
        json.dumps({"map": compact.model_dump(mode="json"), "cases": cases}) + "\n"
    )
    print(f"wrote {len(cases)} cases to {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    print("covers", sorted(covered))


def _validate(raw: list[dict[str, Any]]) -> list[Event]:
    return _EVENTS.validate_python(raw)


if __name__ == "__main__":
    main()
