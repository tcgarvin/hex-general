"""The season recorder (app.world.season): shape, determinism, consistency."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.engine.hex_coords import axial_to_offset
from app.engine.models import NEUTRAL, Posture
from app.world.generate import GeneratedWorld, WorldParams, generate
from app.world.season import (
    EVENT_KINDS,
    PLAYER_FIELDS,
    SCHEMA_VERSION,
    UNIT_FIELDS,
    Season,
    record_season,
    write_season,
)

_TURNS = 6
_BOTS = 4


@pytest.fixture(scope="module")
def world() -> GeneratedWorld:
    return generate(WorldParams.for_slots(6, seed=3))


@pytest.fixture(scope="module")
def season(world: GeneratedWorld) -> Season:
    return record_season(6, _BOTS, 3, _TURNS, world=world)


def _replay_units(record: dict[str, Any]) -> dict[int, list[int]]:
    """Apply every frame's unit delta in order: the units after the last turn."""
    width = len(UNIT_FIELDS)
    units: dict[int, list[int]] = {}
    for frame in record["frames"]:
        for uid in frame["x"]:
            del units[uid]
        rows = frame["u"]
        for k in range(0, len(rows), width):
            units[rows[k]] = rows[k : k + width]
    return units


def _replay_holders(record: dict[str, Any]) -> list[int]:
    holders = [-1] * (len(record["map"]["sites"]) // 4)
    for frame in record["frames"]:
        changes = frame["s"]
        for k in range(0, len(changes), 2):
            holders[changes[k]] = changes[k + 1]
    return holders


def test_record_has_the_map_once_and_a_frame_per_turn(
    season: Season, world: GeneratedWorld
) -> None:
    record = season.record
    game_map = world.game_map
    assert record["v"] == SCHEMA_VERSION
    assert record["players"] == [f"b{k:03d}" for k in range(_BOTS)]
    assert record["map"]["w"] == game_map.width
    assert len(record["map"]["terrain"]) == game_map.width * game_map.height
    assert len(record["map"]["roads"]) == len(record["map"]["terrain"])
    assert len(record["map"]["sites"]) == 4 * len(game_map.sites)
    assert [f["t"] for f in record["frames"]] == list(range(_TURNS + 1))
    first = record["frames"][0]
    assert first["x"] == [] and first["e"] == [] and first["m"] == []
    for frame in record["frames"]:
        assert len(frame["p"]) == len(PLAYER_FIELDS) * _BOTS
        for event in frame["e"]:
            assert 0 <= event[0] < len(EVENT_KINDS)
            assert 0 <= event[1] < len(record["map"]["terrain"])


def test_the_first_frame_holds_every_starting_unit(
    season: Season, world: GeneratedWorld
) -> None:
    start = world.to_state(season.record["players"])
    rows = season.record["frames"][0]["u"]
    assert len(rows) == len(UNIT_FIELDS) * len(start.units)


def test_same_arguments_give_the_same_record(
    season: Season, world: GeneratedWorld
) -> None:
    again = record_season(6, _BOTS, 3, _TURNS, world=world)
    assert json.dumps(again.record) == json.dumps(season.record)


def test_replayed_units_match_the_final_state(season: Season) -> None:
    record = season.record
    state = season.final_state
    width = record["map"]["w"]
    owner = {NEUTRAL: 0} | {p: k + 1 for k, p in enumerate(record["players"])}
    types = record["unit_types"]
    expected = {}
    for unit in state.units:
        col, row = axial_to_offset(unit.coord)
        flags = (1 if unit.posture is Posture.COLUMN else 0) | (
            2 if unit.disordered else 0
        )
        expected[unit.id] = [
            unit.id,
            owner[unit.owner],
            types.index(unit.type.value),
            unit.hp,
            flags,
            row * width + col,
        ]
    assert _replay_units(record) == expected


def test_replayed_site_owners_match_the_final_state(season: Season) -> None:
    record = season.record
    state = season.final_state
    owner = {p: k + 1 for k, p in enumerate(record["players"])}
    site_of = {s.coord: k for k, s in enumerate(state.map.sites)}
    expected = [-1] * len(state.map.sites)
    for settlement in state.settlements:
        held = settlement.controlled_by
        expected[site_of[settlement.coord]] = 0 if held is None else owner[held]
    assert _replay_holders(record) == expected


def test_player_numbers_match_the_final_state(season: Season) -> None:
    record = season.record
    state = season.final_state
    fields = len(PLAYER_FIELDS)
    numbers = record["frames"][-1]["p"]
    for k, player in enumerate(record["players"]):
        row = dict(zip(PLAYER_FIELDS, numbers[k * fields : (k + 1) * fields]))
        assert row["units"] == sum(1 for u in state.units if u.owner == player)
        assert row["holdings"] == sum(
            1 for s in state.settlements if s.controlled_by == player
        )
        stocks = state.player(player).stocks
        assert (row["manpower"], row["fuel"], row["steel"]) == (
            stocks.manpower,
            stocks.fuel,
            stocks.steel,
        )
        assert row["cap"] == state.unit_cap_for(player)


def test_paths_end_where_surviving_units_stand(season: Season) -> None:
    record = season.record
    units: dict[int, list[int]] = {}
    width = len(UNIT_FIELDS)
    moved_any = False
    for frame in record["frames"]:
        for uid in frame["x"]:
            del units[uid]
        rows = frame["u"]
        for k in range(0, len(rows), width):
            units[rows[k]] = rows[k : k + width]
        paths = frame["m"]
        k = 0
        while k < len(paths):
            uid, n = paths[k], paths[k + 1]
            assert n >= 1
            moved_any = True
            if uid in units:
                assert units[uid][5] == paths[k + 1 + n]
            k += 2 + n
    assert moved_any


def test_write_season_writes_compact_json(season: Season, tmp_path: Path) -> None:
    out = tmp_path / "season.json"
    size = write_season(season, out)
    text = out.read_text()
    assert size == len(text)
    assert json.loads(text) == season.record
    assert ", " not in text[:200]
