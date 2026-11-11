"""scripts/staff-map-mock.py renders a season record in each far-zoom style."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "staff-map-mock.py"
W, H = 12, 10


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("staff_map_mock", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["staff_map_mock"] = module
    spec.loader.exec_module(module)
    return module


def _hex(col: int, row: int) -> int:
    return row * W + col


def _tiny_record() -> dict:
    """A 12x10 map with every terrain: a river under a road bridge, woods, hills,
    a lake, a town and a city; two players, a garrison, a vacant base slot."""
    terrain = [["."] * W for _ in range(H)]
    roads = [["."] * W for _ in range(H)]
    for row in range(H):
        terrain[row][3] = "~"
    for col in range(W):
        roads[5][col] = "#"
    terrain[5][3] = "="
    for col, row in ((6, 1), (7, 1), (8, 1), (6, 2), (7, 2)):
        terrain[row][col] = "w"
    for col, row in ((9, 7), (10, 7), (10, 8)):
        terrain[row][col] = "h"
    for col, row in ((0, 8), (1, 8), (0, 9)):
        terrain[row][col] = "o"
    terrain[5][5] = "t"
    terrain[5][9] = "c"
    kinds = ["base", "city", "farm", "oil_field", "steel_mill", "factory"]
    sites = [
        _hex(6, 7),
        0,
        1,
        2,  # base, held by player 1
        _hex(1, 3),
        2,
        2,
        1,  # farm, held by player 2
        _hex(9, 5),
        1,
        1,
        3,  # city, unheld (garrisoned)
        _hex(10, 2),
        0,
        1,
        2,  # vacant base slot
        _hex(8, 4),
        5,
        3,
        1,  # factory, held by player 2
    ]
    units = [
        1,
        1,
        0,
        10,
        0,
        _hex(6, 6),
        2,
        2,
        1,
        9,
        0,
        _hex(7, 6),
        3,
        0,
        0,
        10,
        0,
        _hex(9, 5),
        4,
        1,
        2,
        10,
        1,
        _hex(4, 5),
    ]
    return {
        "v": 1,
        "slots": 3,
        "bots": 2,
        "seed": 1,
        "turns": 1,
        "players": ["b000", "b001"],
        "bases": [_hex(6, 7), _hex(10, 2)],
        "unit_types": ["infantry", "armor", "artillery"],
        "site_kinds": kinds,
        "buildings": ["barracks", "depot", "workshop"],
        "event_kinds": [],
        "player_fields": [],
        "unit_fields": ["id", "owner", "type", "hp", "flags", "hex"],
        "map": {
            "w": W,
            "h": H,
            "terrain": "".join("".join(r) for r in terrain),
            "roads": "".join("".join(r) for r in roads),
            "site_fields": ["hex", "kind", "level", "garrison"],
            "sites": sites,
        },
        "eliminated": [0, 0],
        "frames": [
            {
                "t": 0,
                "u": units,
                "x": [],
                "s": [0, 1, 1, 2, 2, 0, 4, 2],
                "p": [],
                "e": [],
                "m": [],
                "alive": 2,
            },
            {
                "t": 1,
                "u": [2, 2, 1, 6, 0, _hex(7, 6)],
                "x": [],
                "s": [],
                "p": [],
                "e": [[0, _hex(7, 6), []]],
                "m": [],
                "alive": 2,
            },
        ],
    }


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    return _load()


@pytest.fixture(scope="module")
def world(tool: ModuleType, tmp_path_factory: pytest.TempPathFactory):
    path = tmp_path_factory.mktemp("season") / "tiny.json"
    path.write_text(json.dumps(_tiny_record()))
    snap = tool.snapshot(tool.load_record(path))
    return snap, tool.derive(snap)


def test_snapshot_replays_frames_to_the_last_turn(world) -> None:
    snap, derived = world
    assert snap.turn == 1
    assert [int(h) for h in snap.holders] == [1, 2, 0, -1, 2]
    assert {u.id: u.hp for u in snap.units}[2] == 6
    assert snap.fights == (_hex(7, 6),)
    assert derived.garrisons == {_hex(9, 5): 1}
    assert derived.rivers and derived.roads and len(derived.bridges) == 1


def test_province_owners_follow_the_anchor_site(world) -> None:
    snap, derived = world
    assert derived.owner[_hex(6, 7)] == 1
    assert derived.owner[_hex(1, 3)] == 2
    assert derived.owner[_hex(9, 5)] == 0  # unheld city province


@pytest.mark.parametrize(
    "style", ["current", "staff-map", "staff-map-night", "painted-far", "hybrid"]
)
@pytest.mark.parametrize("view", ["world", "near"])
def test_each_style_renders_to_size_and_is_deterministic(
    tool, world, style, view
) -> None:
    snap, derived = world
    if view == "world":
        v = tool.make_view(snap, "world", None, None, (160, 0))
    else:
        v = tool.make_view(snap, "near", (6, 5), 22.0, (200, 120))
    scene = tool.styles.make_scene(snap, derived, v, 1)
    first = tool.styles.render(style, scene)
    second = tool.styles.render(style, tool.styles.make_scene(snap, derived, v, 1))
    assert first.size == (v.width, v.height)
    if view == "near":
        assert first.size == (200, 120)
    assert np.array_equal(np.asarray(first), np.asarray(second))
    # Not a blank page: the map draws something besides the background.
    assert np.asarray(first).std() > 2.0


def test_render_command_writes_a_png(tool, tmp_path: Path) -> None:
    season = tmp_path / "tiny.json"
    season.write_text(json.dumps(_tiny_record()))
    out = tmp_path / "out.png"
    assert (
        tool.main(
            [
                "render",
                str(season),
                "--style",
                "staff-map",
                "--view",
                "near",
                "--center",
                "6,5",
                "--size",
                "160x100",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    from PIL import Image

    assert Image.open(out).size == (160, 100)
