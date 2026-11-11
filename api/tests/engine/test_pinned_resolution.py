"""Pinned turn outcomes: the engine must resolve a recorded world exactly as before.

The determinism tests run the same code twice, so they cannot see an outcome
drift between versions. This one replays a recorded 6-slot, 6-bot world (seed 3,
24 turns: combat, barrage, capture, travel, columns, repair, recruitment and
construction) from its starting state with the orders the bots gave, and checks
each turn's resolved state and event log against the SHA-256 recorded when the
fixture was made (2026-11-11, before the engine performance pass).

The orders are stored, not planned, so bot and world-generator changes never
touch this test. A deliberate rule change will: regenerate the fixture then
(record a bot world's starting state, per-turn orders and output hashes in the
same format) and say why in the commit.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from app.engine.events import Event
from app.engine.execution import resolve_execution
from app.engine.hex_coords import clear_geometry_caches
from app.engine.models import EconomyOrders, GameState, Order, Recruitment

_FIXTURE = Path(__file__).parent / "data" / "pinned_world_6slot.json.gz"
_EVENTS = TypeAdapter(list[Event])
_ORDERS = TypeAdapter(list[Order])
_RECRUITMENTS = TypeAdapter(list[Recruitment])
_ECONOMY = TypeAdapter(list[EconomyOrders])


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_recorded_world_resolves_to_the_pinned_states_and_events() -> None:
    with gzip.open(_FIXTURE, "rt") as handle:
        fixture: dict[str, Any] = json.load(handle)
    state = GameState.model_validate(fixture["start"])
    assert len(fixture["turns"]) == 24
    for record in fixture["turns"]:
        turn = state.turn
        state, events = resolve_execution(
            state,
            _ORDERS.validate_python(record["orders"]),
            _RECRUITMENTS.validate_python(record["recruitments"]),
            _ECONOMY.validate_python(record["economy"]),
        )
        clear_geometry_caches()
        assert (
            _sha(state.model_dump_json().encode()) == record["state_sha256"]
        ), f"turn {turn}: resolved state drifted"
        assert (
            _sha(_EVENTS.dump_json(events)) == record["events_sha256"]
        ), f"turn {turn}: event log drifted"
