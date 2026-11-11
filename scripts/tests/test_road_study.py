"""scripts/road-study.py: board-mock export and the frontend-rule road check."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "road-study.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("road_study", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["road_study"] = module
    spec.loader.exec_module(module)
    return module


rs = _load()


@pytest.fixture(scope="module")
def worlds() -> dict:
    return {
        style: rs.generate(rs.WorldParams.for_slots(12, 1, road_style=style))
        for style in ("direct", "natural")
    }


def test_export_is_a_valid_board_mock_map_with_the_worlds_roads(worlds: dict) -> None:
    world = worlds["natural"]
    text = rs.ascii_map(world, (10, 4, 41, 25), sites=True, comment="test")
    piece = rs.BM.parse_ascii(text, "piece")
    assert (piece.width, piece.height) == (32, 22)
    hexes = world.game_map.hexes
    width = world.game_map.width
    for h in piece.hexes:
        source = hexes[(h.row + 4) * width + h.col + 10]
        assert h.road == source.road
        assert h.terrain == source.terrain.value
        assert (h.q + 10, h.r - 1) == (source.coord.q, source.coord.r)


def test_export_rejects_an_odd_left_column(worlds: dict) -> None:
    with pytest.raises(ValueError, match="even column"):
        rs.ascii_map(worlds["direct"], (3, 0, 10, 10))


def test_natural_roads_draw_with_the_real_tiles(worlds: dict) -> None:
    # Every natural bridge sits on a river shape with bridge art and its road
    # runs along the baked ramps; road shapes with no tile stay rare and no more
    # common than in the direct style.
    direct = rs.board_check(worlds["direct"])
    natural = rs.board_check(worlds["natural"])
    assert natural.bridges > 0
    assert natural.bridges_untiled == 0
    assert natural.bridges_off_ramps == 0
    assert natural.unhandled <= max(direct.unhandled, 0.005 * natural.road_tiles)
    assert natural.on_woods <= direct.on_woods


def test_the_board_window_holds_a_bridge_and_starts_on_an_even_column(
    worlds: dict,
) -> None:
    c0, r0, c1, r1 = rs.choose_window(list(worlds.values()))
    assert c0 % 2 == 0 and c1 - c0 + 1 == rs.WINDOW[0] and r1 - r0 + 1 == rs.WINDOW[1]
    hexes = worlds["natural"].game_map.hexes
    width = worlds["natural"].game_map.width
    inside = [
        hexes[row * width + col]
        for row in range(r0, r1 + 1)
        for col in range(c0, c1 + 1)
    ]
    assert any(h.terrain.value == "bridge" for h in inside)
