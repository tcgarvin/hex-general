"""Tests for board-mock's opt-in variety mode (`variety_picks`, `--variety`):
plains feature, homestead and roadside variants chosen by a deterministic per-hex
rule from static map data only.

Run: python3 -m pytest scripts/tests
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "board-mock.py"
TOWN_MAP = REPO / "scripts" / "board-mock-maps" / "variety-town.txt"
SHIPPED = REPO / "frontend" / "src" / "board" / "tiles"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("board_mock", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["board_mock"] = module
    spec.loader.exec_module(module)
    return module


bm = _load()


def _tile(path: Path, rgb: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (128, 128), rgb + (255,)).save(path)


@pytest.fixture()
def variety_dir(tmp_path: Path) -> Path:
    root = tmp_path / "variety"
    for i in range(3):
        _tile(root / "nature" / f"nature-{i:02d}.png", (40, 90 + i, 40))
        _tile(root / "home" / f"home-{i:02d}.png", (150, 80, 60 + i))
    for shape in (
        "straight_v",
        "straight_d",
        "turn_n_se",
        "turn_s_nw",
        "turn_se_sw",
        "turn_ne_nw",
    ):
        _tile(root / "road" / f"road-{shape}-A.png", (200, 180, 120))
    return root


def _big_map() -> object:
    """A 48x36 plains map with two crossing roads, scattered towns, woods and
    sites (farms, a base), deterministic."""
    width, height = 48, 36
    terrain = [["."] * width for _ in range(height)]
    roads = [["."] * width for _ in range(height)]
    for col in range(width):
        roads[12][col] = "#"
    for row in range(height):
        roads[row][30] = "#"
    for col, row in ((10, 12), (30, 12), (30, 28), (40, 5), (6, 30)):
        terrain[row][col] = "t"
    for col, row in ((20, 20), (21, 20), (22, 21), (5, 4), (44, 33)):
        terrain[row][col] = "w"
    text = "[terrain]\n" + "\n".join("".join(r) for r in terrain)
    text += "\n[roads]\n" + "\n".join("".join(r) for r in roads)
    text += "\n[sites]\n14,14 farm 1\n20,8 base 1\n36,20 factory 2\n8,24 base 2\n"
    return bm.parse_ascii(text, "big")


def _picks(game_map, variety_dir: Path, density: str):
    course = bm.road_course_edges(game_map.hexes, game_map.hex_at)
    refs = bm.road_tile_refs(game_map.hexes, course)
    return bm.variety_picks(
        game_map,
        refs,
        bm.VarietySet.discover(variety_dir),
        bm.VARIETY_DENSITIES[density],
    )


@pytest.mark.parametrize("density", sorted(bm.VARIETY_DENSITIES))
def test_picks_are_deterministic(variety_dir: Path, density: str) -> None:
    game_map = _big_map()
    first = _picks(game_map, variety_dir, density)
    again = _picks(_big_map(), variety_dir, density)
    assert first == again
    assert first, "the big map should get some decoration"


@pytest.mark.parametrize("density", sorted(bm.VARIETY_DENSITIES))
def test_never_decorates_site_or_base_hexes(variety_dir: Path, density: str) -> None:
    game_map = _big_map()
    covered, bases = bm.site_hexes(game_map)
    assert len(bases) == 3  # stage-1 base + stage-2 base (two hexes)
    picks = _picks(game_map, variety_dir, density)
    assert not covered & set(picks)
    for at, pick in picks.items():
        if pick.kind == "home":
            assert not any(n in bases for n in bm._neighbours(*at)), at


@pytest.mark.parametrize("density", sorted(bm.VARIETY_DENSITIES))
def test_no_two_decorated_hexes_touch(variety_dir: Path, density: str) -> None:
    picks = _picks(_big_map(), variety_dir, density)
    for at in picks:
        assert not any(n in picks for n in bm._neighbours(*at)), at


@pytest.mark.parametrize("density", sorted(bm.VARIETY_DENSITIES))
def test_kinds_land_on_their_own_hexes(variety_dir: Path, density: str) -> None:
    game_map = _big_map()
    picks = _picks(game_map, variety_dir, density)
    course = bm.road_course_edges(game_map.hexes, game_map.hex_at)
    refs = bm.road_tile_refs(game_map.hexes, course)
    for (q, r), pick in picks.items():
        h = game_map.hex_at(q, r)
        assert h.terrain == "plains"
        if pick.kind == "road":
            assert h.road
            shape = refs[f"{q},{r}"].lookup.removeprefix("road/plains_")
            assert pick.path.name.startswith(f"road-{shape}-")
        else:
            assert not h.road
        if pick.kind == "home":
            nbrs = [game_map.hex_at(*n) for n in bm._neighbours(q, r)]
            assert any(
                n is not None and (n.road or n.terrain in ("town", "city"))
                for n in nbrs
            )


def test_density_ordering(variety_dir: Path) -> None:
    game_map = _big_map()
    counts = [len(_picks(game_map, variety_dir, d)) for d in ("low", "medium", "high")]
    assert counts[0] < counts[1] < counts[2], counts
    real = bm.load_map(TOWN_MAP)
    real_counts = [len(_picks(real, variety_dir, d)) for d in ("low", "medium", "high")]
    assert real_counts[0] < real_counts[1] < real_counts[2], real_counts


def test_salted_index_is_stable_and_salt_dependent() -> None:
    values = {bm.salted_index(3, -7, salt, 1000) for salt in range(11, 16)}
    assert len(values) > 1
    assert bm.salted_index(3, -7, 11, 1000) == bm.salted_index(3, -7, 11, 1000)
    assert all(
        0 <= bm.salted_index(q, r, 12, 7) < 7
        for q in range(-5, 5)
        for r in range(-5, 5)
    )
    assert bm.salted_index(1, 2, 13, 1) == 0


def _render(game_map, variety: Path | None):
    library = bm.TileLibrary(bm.TileSet.discover(SHIPPED, "tiles"), None)
    return bm.render(
        game_map,
        library,
        32,
        False,
        None,
        draw_vector_roads=False,
        variety=None if variety is None else bm.VarietySet.discover(variety),
    )


def test_default_render_has_no_variety_and_undecorated_hexes_are_unchanged(
    variety_dir: Path,
) -> None:
    game_map = bm.load_map(TOWN_MAP)
    plain_image, plain = _render(game_map, None)
    var_image, varied = _render(game_map, variety_dir)
    assert plain.variety == []
    assert varied.variety
    assert [(h, c) for h, c in plain.choices] == [(h, c) for h, c in varied.choices]
    decorated = {(h.q, h.r) for h, _ in varied.variety}
    assert decorated == set(_picks(game_map, variety_dir, "medium"))
    assert plain_image.tobytes() != var_image.tobytes()


def test_variety_on_a_map_with_nothing_eligible_is_byte_identical(
    variety_dir: Path,
) -> None:
    game_map = bm.parse_ascii("www\nwhw\nwww\n", "woods")
    plain_image, _ = _render(game_map, None)
    var_image, varied = _render(game_map, variety_dir)
    assert varied.variety == []
    assert plain_image.tobytes() == var_image.tobytes()


def test_variety_dir_needs_nature_and_home(tmp_path: Path) -> None:
    _tile(tmp_path / "v" / "nature" / "n.png", (1, 2, 3))
    with pytest.raises(bm.TileSetError):
        bm.VarietySet.discover(tmp_path / "v")
    _tile(tmp_path / "v" / "home" / "h.png", (1, 2, 3))
    _tile(tmp_path / "v" / "road" / "bad.png", (1, 2, 3))
    with pytest.raises(bm.TileSetError, match="road-<shape>"):
        bm.VarietySet.discover(tmp_path / "v")
