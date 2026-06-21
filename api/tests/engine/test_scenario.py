"""Tests for the scenario format, loader, and validation (roadmap Phase 1).

The headline guarantee: a hand-written scenario loads into a valid turn-0
`GameState`, the authored starter scenario file loads and is self-consistent, and
every malformed input fails loudly with a `ScenarioError` rather than silently
producing a broken state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.engine.hex_coords import Axial
from app.engine.scenario import (
    ScenarioError,
    load_scenario,
    load_scenario_file,
)
from app.engine.stats import SETTLEMENT_TERRAINS, TERRAIN_STATS, Terrain

STARTER_PATH = Path(__file__).parents[2] / "app" / "scenarios" / "twin_rivers.json"


def base_scenario() -> dict[str, Any]:
    """A small but structurally complete scenario for happy-path + mutation tests.

    A 4x3 map: a city and town on the north row, a river across the middle with a
    single bridged road corridor at column 1, hills in the south-east corner.
    """
    return {
        "name": "Tiny Crossing",
        "description": "A minimal valid scenario.",
        "width": 4,
        "height": 3,
        "terrain": [
            "c.t.",
            "~=~~",
            "...h",
        ],
        "roads": [
            ".#..",
            ".#..",
            ".#..",
        ],
        "settlements": [
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 2, "r": 0},
        ],
        "units": [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 2},
            {"type": "armor", "owner": "red", "q": 3, "r": 0},
        ],
        "players": ["blue", {"id": "red", "bank": 3}],
        "victory": {"turn_limit": 10},
    }


def test_happy_path_builds_turn_zero_state() -> None:
    state = load_scenario(base_scenario())

    assert state.turn == 0
    assert state.map.width == 4 and state.map.height == 3
    assert len(state.map.hexes) == 12

    # Player roster: bare string and object forms both accepted.
    banks = {p.id: p.bank for p in state.players}
    assert banks == {"blue": 0, "red": 3}

    # Settlements auto-created for every town/city hex, owners applied.
    by_coord = {s.coord: s.controlled_by for s in state.settlements}
    assert by_coord == {
        Axial(q=0, r=0): "blue",
        Axial(q=2, r=0): None,
    }

    # Units get sequential ids in list order, hp defaults to max.
    assert [u.id for u in state.units] == [1, 2]
    assert state.units[0].hp == 10 and state.units[1].hp == 10


def test_bridge_decoded_with_road_and_river_neighbours() -> None:
    state = load_scenario(base_scenario())
    bridge = state.hex_at(Axial(q=1, r=1))
    assert bridge is not None
    assert bridge.terrain is Terrain.BRIDGE and bridge.road is True


def test_omitted_unit_hp_defaults_to_type_max() -> None:
    scenario = base_scenario()
    scenario["units"][1]["hp"] = 5
    state = load_scenario(scenario)
    assert state.units[0].hp == 10  # infantry default
    assert state.units[1].hp == 5  # explicit override honoured


# --- The authored starter scenario -----------------------------------------


def test_starter_scenario_loads_and_is_self_consistent() -> None:
    state = load_scenario_file(STARTER_PATH)

    assert state.turn == 0
    assert state.map.width == 40 and state.map.height == 40
    assert len(state.map.hexes) == 1600

    player_ids = {p.id for p in state.players}

    terrain_by_coord = {h.coord: h.terrain for h in state.map.hexes}

    # Every settlement sits on town/city terrain and is owned by a real player.
    for settlement in state.settlements:
        assert terrain_by_coord[settlement.coord] in SETTLEMENT_TERRAINS
        if settlement.controlled_by is not None:
            assert settlement.controlled_by in player_ids

    # Every town/city hex has exactly one settlement record.
    settlement_coords = {s.coord for s in state.settlements}
    town_city = {c for c, t in terrain_by_coord.items() if t in SETTLEMENT_TERRAINS}
    assert settlement_coords == town_city

    # Units: unique sequential ids, on passable terrain, no stacking, real owners.
    assert [u.id for u in state.units] == list(range(1, len(state.units) + 1))
    occupied = set()
    for unit in state.units:
        assert unit.owner in player_ids
        assert TERRAIN_STATS[terrain_by_coord[unit.coord]].passable
        assert unit.coord not in occupied
        occupied.add(unit.coord)


# --- Failure modes ----------------------------------------------------------


def test_wrong_terrain_row_count_raises() -> None:
    scenario = base_scenario()
    scenario["terrain"] = scenario["terrain"][:-1]
    with pytest.raises(ScenarioError, match="terrain grid has 2 rows"):
        load_scenario(scenario)


def test_wrong_terrain_row_length_raises() -> None:
    scenario = base_scenario()
    scenario["terrain"][0] = "c.t"
    with pytest.raises(ScenarioError, match="row 0 has 3 chars"):
        load_scenario(scenario)


def test_unknown_terrain_char_raises() -> None:
    scenario = base_scenario()
    scenario["terrain"][0] = "X.t."
    with pytest.raises(ScenarioError, match="unknown terrain char"):
        load_scenario(scenario)


def test_unknown_road_char_raises() -> None:
    scenario = base_scenario()
    scenario["roads"][0] = "X#.."
    with pytest.raises(ScenarioError, match="unknown road char"):
        load_scenario(scenario)


def test_road_over_river_raises() -> None:
    scenario = base_scenario()
    # Put a road on a plain river hex (q=2, r=1) instead of a bridge.
    scenario["roads"][1] = ".##."
    with pytest.raises(ScenarioError, match="road runs over impassable river"):
        load_scenario(scenario)


def test_bridge_without_road_raises() -> None:
    scenario = base_scenario()
    scenario["roads"] = ["....", "....", "...."]
    with pytest.raises(ScenarioError, match="has no road"):
        load_scenario(scenario)


def test_bridge_not_on_river_raises() -> None:
    scenario = base_scenario()
    # Move the bridge to a row with no adjacent river hex (south row).
    scenario["terrain"] = ["c.t.", "....", ".=.h"]
    scenario["roads"] = [".#..", ".#..", ".#.."]
    with pytest.raises(ScenarioError, match="not adjacent to any river"):
        load_scenario(scenario)


def test_settlement_on_non_settlement_terrain_raises() -> None:
    scenario = base_scenario()
    scenario["settlements"].append({"q": 0, "r": 2})  # plains hex
    with pytest.raises(ScenarioError, match="not a town or city"):
        load_scenario(scenario)


def test_settlement_off_map_raises() -> None:
    scenario = base_scenario()
    scenario["settlements"].append({"q": 9, "r": 9})
    with pytest.raises(ScenarioError, match="off-map"):
        load_scenario(scenario)


def test_settlement_undeclared_owner_raises() -> None:
    scenario = base_scenario()
    scenario["settlements"][1]["owner"] = "green"
    with pytest.raises(ScenarioError, match="undeclared player"):
        load_scenario(scenario)


def test_unit_on_impassable_terrain_raises() -> None:
    scenario = base_scenario()
    scenario["units"][0] = {"type": "infantry", "owner": "blue", "q": 0, "r": 1}
    with pytest.raises(ScenarioError, match="impassable"):
        load_scenario(scenario)


def test_unit_off_map_raises() -> None:
    scenario = base_scenario()
    scenario["units"][0] = {"type": "infantry", "owner": "blue", "q": 9, "r": 9}
    with pytest.raises(ScenarioError, match="off-map"):
        load_scenario(scenario)


def test_stacked_units_raise() -> None:
    scenario = base_scenario()
    scenario["units"].append(
        {"type": "armor", "owner": "blue", "q": 0, "r": 2}
    )  # same hex as unit 1
    with pytest.raises(ScenarioError, match="stacks on another unit"):
        load_scenario(scenario)


def test_unit_undeclared_owner_raises() -> None:
    scenario = base_scenario()
    scenario["units"][0]["owner"] = "green"
    with pytest.raises(ScenarioError, match="undeclared player"):
        load_scenario(scenario)


def test_duplicate_player_ids_raise() -> None:
    scenario = base_scenario()
    scenario["players"] = ["blue", "blue"]
    with pytest.raises(ScenarioError, match="duplicate player ids"):
        load_scenario(scenario)


def test_load_scenario_file_rejects_non_object(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ScenarioError, match="must contain a JSON object"):
        load_scenario_file(bad)


def test_load_scenario_file_rejects_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ScenarioError, match="not valid JSON"):
        load_scenario_file(bad)


def test_deepcopy_base_does_not_leak() -> None:
    # Guard: base_scenario() returns a fresh mutable dict each call.
    a = base_scenario()
    a["units"].clear()
    assert len(base_scenario()["units"]) == 2
