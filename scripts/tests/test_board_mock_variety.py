"""Tests for board-mock's opt-in variety mode (`variety_picks`, `--variety`):
plains feature, homestead and roadside variants chosen by a deterministic per-hex
rule from static map data only.

Run: python3 -m pytest scripts/tests
"""

from __future__ import annotations

import importlib.util
import sys
import json
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
    # every base counts with its full stage-4 footprint, whatever its stage
    assert len(bases) == 8
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


# --- size mix (small round-1 set + medium round-2 set) -------------------------


@pytest.fixture()
def medium_dir(tmp_path: Path) -> Path:
    root = tmp_path / "medium"
    for i in range(2):
        _tile(root / "nature" / f"nature-m{i:02d}.png", (30, 70 + i, 30))
        _tile(root / "home" / f"home-m{i:02d}.png", (120, 60, 50 + i))
    # straights and one fork only: turns must fall back to the small set
    for shape in ("straight_v", "straight_d", "fork_se_n_sw"):
        _tile(root / "road" / f"road-{shape}-mA.png", (210, 190, 130))
    return root


def _mixed(game_map, small: Path | None, medium: Path, mix: str, density="medium"):
    course = bm.road_course_edges(game_map.hexes, game_map.hex_at)
    refs = bm.road_tile_refs(game_map.hexes, course)
    return bm.variety_picks(
        game_map,
        refs,
        None if small is None else bm.VarietySet.discover(small),
        bm.VARIETY_DENSITIES[density],
        medium=bm.VarietySet.discover(medium),
        medium_share=bm.VARIETY_MIXES[mix],
    )


def test_small_mix_with_a_medium_set_equals_the_small_only_rule(
    variety_dir: Path, medium_dir: Path
) -> None:
    # a shape only the medium set has (a fork) would add road candidates and
    # draw from the medium set even at mix "small"; without one the rule is
    # exactly the small-only rule
    (medium_dir / "road" / "road-fork_se_n_sw-mA.png").unlink()
    game_map = _big_map()
    assert _mixed(game_map, variety_dir, medium_dir, "small") == _picks(
        game_map, variety_dir, "medium"
    )


def test_mix_changes_sizes_not_which_hexes_are_decorated(
    variety_dir: Path, medium_dir: Path
) -> None:
    game_map = _big_map()
    by_mix = {m: _mixed(game_map, variety_dir, medium_dir, m) for m in bm.VARIETY_MIXES}
    hexes = {m: set(p) for m, p in by_mix.items()}
    assert len({frozenset(h) for h in hexes.values()}) == 1
    medium_counts = [
        sum(p.size == "medium" for p in by_mix[m].values()) for m in bm.VARIETY_MIXES
    ]
    assert medium_counts == sorted(medium_counts)
    assert medium_counts[0] == 0
    assert medium_counts[0] < medium_counts[2] < medium_counts[-1]
    again = _mixed(_big_map(), variety_dir, medium_dir, "even")
    assert again == by_mix["even"]


def test_medium_mix_falls_back_per_road_shape(
    variety_dir: Path, medium_dir: Path
) -> None:
    game_map = _big_map()
    picks = _mixed(game_map, variety_dir, medium_dir, "medium", density="high")
    medium_set = bm.VarietySet.discover(medium_dir)
    for pick in picks.values():
        if pick.kind != "road":
            assert pick.size == "medium"
            continue
        shape = pick.path.name.split("-")[1]
        assert pick.size == ("medium" if shape in medium_set.road else "small")
    assert any(p.kind == "road" and p.size == "small" for p in picks.values())


def test_medium_only_needs_no_small_set(medium_dir: Path) -> None:
    game_map = _big_map()
    picks = _mixed(game_map, None, medium_dir, "medium")
    assert picks and all(p.size == "medium" for p in picks.values())
    # road hexes whose shape only the (absent) small set would have stay undecorated
    for (q, r), pick in picks.items():
        if pick.kind == "road":
            assert pick.path.parent.parent == medium_dir


@pytest.mark.parametrize("mix", ["small", "even", "medium"])
def test_nearby_decorated_hexes_do_not_repeat_a_tile_while_another_is_free(
    variety_dir: Path, medium_dir: Path, mix: str
) -> None:
    game_map = _big_map()
    picks = _mixed(game_map, variety_dir, medium_dir, mix, density="high")
    radius = bm.VARIETY_REPEAT_RADIUS
    for at, pick in picks.items():
        higher = [
            picks[n]
            for n in bm._within(at[0], at[1], radius)
            if n in picks and bm._rank(*n) > bm._rank(*at)
        ]
        if any(p.path == pick.path for p in higher):
            group = sorted(pick.path.parent.glob("*.png"))
            if pick.kind == "road":
                shape = pick.path.name.split("-")[1]
                group = [g for g in group if g.name.split("-")[1] == shape]
            assert set(group) <= {p.path for p in higher}, at


def test_within_radius_counts() -> None:
    assert len(bm._within(0, 0, 1)) == 6
    assert len(bm._within(4, -2, 3)) == 36


# --- shipped set: the mock's default equals the game ---------------------------

SETTINGS = json.loads(
    (REPO / "frontend" / "src" / "board" / "varietySettings.json").read_text()
)


def test_mock_settings_are_the_frontends() -> None:
    assert bm.VARIETY_DEFAULT_DENSITY == SETTINGS["density"] == "medium"
    assert bm.VARIETY_DEFAULT_MIX == SETTINGS["mix"] == "mostly-medium"
    assert bm.VARIETY_MIXES == SETTINGS["mixes"]
    assert bm.VARIETY_REPEAT_RADIUS == SETTINGS["repeatRadius"]
    for name, d in SETTINGS["densities"].items():
        assert bm.VARIETY_DENSITIES[name] == bm.VarietyDensity(
            nature=d["nature"],
            home_road=d["homeRoad"],
            home_town=d["homeTown"],
            road_open=d["roadOpen"],
            road_town=d["roadTown"],
        )


def test_shipped_tiles_have_both_sizes() -> None:
    small = bm.VarietySet.from_tiles(SHIPPED, "small")
    medium = bm.VarietySet.from_tiles(SHIPPED, "medium")
    assert small is not None and medium is not None
    assert (len(small.nature), len(small.home)) == (11, 8)
    assert (len(medium.nature), len(medium.home)) == (8, 8)
    assert sum(map(len, small.road.values())) == 13
    assert sum(map(len, medium.road.values())) == 18
    assert all(p.name.startswith("s") for p in small.nature + small.home)
    assert (
        bm.variety_key(medium.road["fork_se_n_sw"][1]) == "road_variety/fork_se_n_sw_m2"
    )


def test_tiles_without_variety_folders_have_none(tmp_path: Path) -> None:
    _tile(tmp_path / "plains" / "00.png", (1, 2, 3))
    assert bm.VarietySet.from_tiles(tmp_path, "small") is None


def _cli(tmp_path: Path, *extra: str) -> tuple[bytes, str]:
    import contextlib
    import io

    out = tmp_path / f"out{len(extra)}.png"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bm.main(["--map", str(TOWN_MAP), "--out", str(out), "--hex-px", "32", *extra])
    return out.read_bytes(), buf.getvalue()


def test_cli_default_draws_the_shipped_variety_and_no_variety_turns_it_off(
    tmp_path: Path,
) -> None:
    default, report = _cli(tmp_path)
    assert "variety " in report and "medium x" in report
    off, off_report = _cli(tmp_path, "--no-variety")
    assert "variety " not in off_report
    assert default != off
    # --no-variety is the plain render (no variety sets)
    library = bm.TileLibrary(bm.TileSet.discover(SHIPPED, "tiles"), None)
    image, _ = bm.render(bm.load_map(TOWN_MAP), library, 32, True, None)
    assert Image.open(tmp_path / "out1.png").tobytes() == image.tobytes()


def test_cli_default_equals_the_shipped_rule(tmp_path: Path) -> None:
    game_map = bm.load_map(TOWN_MAP)
    course = bm.road_course_edges(game_map.hexes, game_map.hex_at)
    refs = bm.road_tile_refs(game_map.hexes, course)
    picks = bm.variety_picks(
        game_map,
        refs,
        bm.VarietySet.from_tiles(SHIPPED, "small"),
        bm.VARIETY_DENSITIES[SETTINGS["density"]],
        medium=bm.VarietySet.from_tiles(SHIPPED, "medium"),
        medium_share=SETTINGS["mixes"][SETTINGS["mix"]],
    )
    library = bm.TileLibrary(bm.TileSet.discover(SHIPPED, "tiles"), None)
    _, report = bm.render(
        game_map,
        library,
        32,
        True,
        None,
        variety=bm.VarietySet.from_tiles(SHIPPED, "small"),
        variety_density=SETTINGS["density"],
        variety_medium=bm.VarietySet.from_tiles(SHIPPED, "medium"),
        variety_mix=SETTINGS["mix"],
    )
    assert {(h.q, h.r): p for h, p in report.variety} == picks
    cli, _ = _cli(tmp_path)
    assert cli == _cli(tmp_path)[0]
