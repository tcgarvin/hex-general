"""Tests for scripts/board-mock.py: the ported tile-selection logic is checked
against the frontend's own expectations (frontend/src/board/{tiles,riverTiles,
roadTiles}.test.ts), plus map parsing, tile-set fallback and a render smoke test.

Run: python3 -m pytest scripts/tests
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "board-mock.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("board_mock", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["board_mock"] = module  # dataclasses resolve annotations via this
    spec.loader.exec_module(module)
    return module


bm = _load()

# The six axial neighbour offsets, in hex.ts AXIAL_DIRECTIONS order.
DIRS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]


def terrain_probe(cells: dict[tuple[int, int], str]):
    return lambda q, r: cells.get((q, r))


def neighbours(dirs: list[int], terrain: str) -> dict[tuple[int, int], str]:
    return {DIRS[i]: terrain for i in dirs}


def make_hex(q: int, r: int, terrain: str, road: bool = False):
    return bm.Hex(col=q, row=r, q=q, r=r, terrain=terrain, road=road)


def hex_probe(hexes):
    by = {(h.q, h.r): h for h in hexes}
    return lambda q, r: by.get((q, r))


def road_neighbours(dirs: list[int], terrain: str, road: bool):
    return [make_hex(*DIRS[i], terrain, road) for i in dirs]


# --- tiles.test.ts ------------------------------------------------------------


def test_oriented_key_groups_numbered_variants() -> None:
    assert bm.oriented_key("straight_v_00") == "straight_v"
    assert bm.oriented_key("straight_v_01") == "straight_v"
    assert bm.oriented_key("straight_d_12") == "straight_d"


def test_oriented_key_leaves_suffixless_names_alone() -> None:
    for name in ("straight_v", "straight_d", "00", "03"):
        assert bm.oriented_key(name) == name


# (q, r, count) -> index, captured by running tiles.ts variantIndex under node.
VARIANT_INDEX_CASES = [
    (0, 0, 4, 0),
    (1, 0, 3, 1),
    (1, 0, 5, 2),
    (1, 0, 7, 4),
    (0, 1, 5, 1),
    (3, -2, 2, 1),
    (3, -2, 7, 6),
    (-7, 11, 5, 3),
    (128, 512, 3, 1),
    (39, -19, 3, 2),
    (-5, -5, 4, 2),
    (12, 7, 4, 3),
    (400, -200, 7, 6),
    (1000, 3000, 5, 4),
    (7, -3, 4, 3),
]


@pytest.mark.parametrize(("q", "r", "count", "expected"), VARIANT_INDEX_CASES)
def test_variant_index_matches_frontend_hash(
    q: int, r: int, count: int, expected: int
) -> None:
    assert bm.variant_index(q, r, count) == expected


def test_variant_index_is_deterministic_and_in_range() -> None:
    for q, r in [(0, 0), (3, -2), (-7, 11), (128, 512)]:
        i = bm.variant_index(q, r, 3)
        assert i == bm.variant_index(q, r, 3)
        assert 0 <= i < 3


def test_variant_index_single_variant_group_is_zero() -> None:
    assert bm.variant_index(5, 9, 1) == 0


def test_locale_sorted_matches_js_locale_compare() -> None:
    # Expected order captured from node: [...names].sort((a, b) => a.localeCompare(b)).
    expected = [
        "_x.png",
        "-x.png",
        "00.png",
        "0a.png",
        "10.png",
        "9.png",
        "a0.png",
        "A0.png",
        "x_1.png",
        "x-1.png",
        "x.png",
        "X.png",
        "x1.png",
    ]
    assert bm.locale_sorted(reversed(expected)) == expected
    # The case that matters for variant groups: ICU puts "_" before ".".
    assert bm.locale_sorted(
        ["straight_v.png", "straight_v_01.png", "straight_v_00.png"]
    ) == [
        "straight_v_00.png",
        "straight_v_01.png",
        "straight_v.png",
    ]


# --- riverTiles.test.ts: connectedEdges ----------------------------------------


def test_connected_edges_finds_river_neighbours() -> None:
    probe = terrain_probe(neighbours([2, 5], "river"))
    assert bm.connected_edges(0, 0, probe) == [2, 5]


def test_connected_edges_counts_bridges() -> None:
    probe = terrain_probe(neighbours([0, 3], "bridge"))
    assert bm.connected_edges(0, 0, probe) == [0, 3]


def test_connected_edges_ignores_land_and_off_map() -> None:
    probe = terrain_probe({**neighbours([2], "river"), **neighbours([5], "plains")})
    assert bm.connected_edges(0, 0, probe) == [2]


def test_connected_edges_ascending() -> None:
    probe = terrain_probe(neighbours([5, 2, 0], "river"))
    assert bm.connected_edges(0, 0, probe) == [0, 2, 5]


# --- riverTiles.test.ts: orientedRiverTile -------------------------------------


def tile(name: str, mirror: bool = False):
    return bm.OrientedTile(name, mirror)


def test_oriented_river_tile_ends() -> None:
    assert bm.oriented_river_tile([0]) == tile("end_se")
    assert bm.oriented_river_tile([4]) == tile("end_se", True)
    assert bm.oriented_river_tile([1]) == tile("end_ne")
    assert bm.oriented_river_tile([3]) == tile("end_ne", True)
    assert bm.oriented_river_tile([2]) == tile("end_n")
    assert bm.oriented_river_tile([5]) == tile("end_s")


def test_oriented_river_tile_straights() -> None:
    assert bm.oriented_river_tile([2, 5]) == tile("straight_v")
    assert bm.oriented_river_tile([0, 3]) == tile("straight_d")
    assert bm.oriented_river_tile([1, 4]) == tile("straight_d", True)


def test_oriented_river_tile_gentle_turns() -> None:
    assert bm.oriented_river_tile([0, 2]) == tile("turn_n_se")
    assert bm.oriented_river_tile([3, 5]) == tile("turn_s_nw")
    assert bm.oriented_river_tile([0, 4]) == tile("turn_se_sw")
    assert bm.oriented_river_tile([1, 3]) == tile("turn_ne_nw")
    assert bm.oriented_river_tile([2, 4]) == tile("turn_n_se", True)
    assert bm.oriented_river_tile([1, 5]) == tile("turn_s_nw", True)


def test_oriented_river_tile_covers_all_but_sharp_pairs() -> None:
    for a in range(6):
        for b in range(a + 1, 6):
            sharp = b - a == 1 or (a == 0 and b == 5)
            assert (bm.oriented_river_tile([a, b]) is None) == sharp


def test_oriented_river_tile_unhandled_masks_are_none() -> None:
    assert bm.oriented_river_tile([0, 1]) is None
    assert bm.oriented_river_tile([1, 2]) is None
    assert bm.oriented_river_tile([0, 1, 2, 3]) is None
    assert bm.oriented_river_tile([]) is None


MIRROR = {0: 4, 1: 3, 2: 2, 3: 1, 4: 0, 5: 5}
TRIPLES = [
    [a, b, c] for a in range(6) for b in range(a + 1, 6) for c in range(b + 1, 6)
]


def test_oriented_river_tile_resolves_all_twenty_triples() -> None:
    assert len(TRIPLES) == 20
    assert all(bm.oriented_river_tile(m) is not None for m in TRIPLES)


def test_oriented_river_tile_triples_are_mirror_consistent() -> None:
    for mask in TRIPLES:
        mirrored_mask = sorted(MIRROR[e] for e in mask)
        own = bm.oriented_river_tile(mask)
        mirrored = bm.oriented_river_tile(mirrored_mask)
        assert mirrored.name == own.name
        if mirrored_mask == mask:
            assert own.mirror is False and mirrored.mirror is False
        else:
            assert mirrored.mirror is (not own.mirror)


@pytest.mark.parametrize(
    ("mask", "name", "mirror"),
    [
        ("0,2,5", "fork_se_n_s", False),
        ("2,4,5", "fork_se_n_s", True),
        ("1,2,5", "fork_ne_n_s", False),
        ("2,3,5", "fork_ne_n_s", True),
        ("0,1,3", "fork_se_ne_nw", False),
        ("1,3,4", "fork_se_ne_nw", True),
        ("0,2,3", "fork_se_n_nw", False),
        ("1,2,4", "fork_se_n_nw", True),
        ("0,3,4", "fork_se_nw_sw", False),
        ("0,1,4", "fork_se_nw_sw", True),
        ("0,3,5", "fork_se_nw_s", False),
        ("1,4,5", "fork_se_nw_s", True),
        ("0,2,4", "fork_se_n_sw", False),
        ("1,3,5", "fork_ne_nw_s", False),
        ("1,2,3", "fork_ne_n_nw", False),
        ("0,1,2", "fork_se_ne_n", False),
        ("2,3,4", "fork_se_ne_n", True),
        ("0,1,5", "fork_se_ne_s", False),
        ("3,4,5", "fork_se_ne_s", True),
        ("0,4,5", "fork_se_sw_s", False),
    ],
)
def test_oriented_river_tile_fork_spot_checks(
    mask: str, name: str, mirror: bool
) -> None:
    edges = [int(e) for e in mask.split(",")]
    assert bm.oriented_river_tile(edges) == tile(name, mirror)


# --- riverTiles.test.ts: riverCourseEdges --------------------------------------


def padded(cells: dict[tuple[int, int], str]) -> dict[tuple[int, int], str]:
    """``cells`` with every unlisted neighbour as plains (riverTiles.test.ts
    ``world``), so no listed river hex is on the map edge and the edge rule stays
    out of tests about something else."""
    out = dict(cells)
    for q, r in cells:
        for dq, dr in DIRS:
            out.setdefault((q + dq, r + dr), "plains")
    return out


def course(cells: dict[tuple[int, int], str]) -> dict[str, list[int]]:
    world = padded(cells)
    return bm.river_course_edges(world.keys(), terrain_probe(world))


def test_river_course_leaves_triangle_free_course_untouched() -> None:
    result = course({(0, -1): "river", (0, 0): "river", (0, 1): "river"})
    assert result["0,0"] == [2, 5]
    assert result["0,-1"] == [5]
    assert result["0,1"] == [2]


def test_river_course_excludes_non_river_hexes() -> None:
    result = course({(0, 0): "river", (1, 0): "plains"})
    assert "0,0" in result
    assert "1,0" not in result


def test_river_course_resolves_tributary_junction() -> None:
    result = course(
        {
            (0, 0): "river",  # J
            (1, 0): "river",  # E
            (-1, 1): "river",  # W
            (0, 1): "river",  # T
            (2, -1): "river",  # E2
            (-2, 1): "river",  # W2
            (0, 2): "river",  # T2
        }
    )
    assert result["0,0"] == [0, 4, 5]
    assert bm.oriented_river_tile(result["0,0"]) == tile("fork_se_sw_s")
    assert result["1,0"] == [1, 3]
    assert result["-1,1"] == [1, 3]
    assert result["0,1"] == [2, 5]


def test_river_course_never_disconnects() -> None:
    result = course({(0, 0): "river", (1, 0): "river", (0, 1): "river"})
    assert all(len(edges) > 0 for edges in result.values())


# --- roadTiles.test.ts ----------------------------------------------------------


def test_road_connected_edges_road_flagged_neighbours() -> None:
    probe = hex_probe(road_neighbours([5, 2], "plains", True))
    assert bm.road_connected_edges(0, 0, probe) == [2, 5]


def test_road_connected_edges_counts_unflagged_bridges() -> None:
    probe = hex_probe(road_neighbours([0], "bridge", False))
    assert bm.road_connected_edges(0, 0, probe) == [0]


def test_road_connected_edges_ignores_non_road_and_off_map() -> None:
    probe = hex_probe(
        road_neighbours([2], "plains", True) + road_neighbours([5], "plains", False)
    )
    assert bm.road_connected_edges(0, 0, probe) == [2]


def refs_for(centre, dirs: list[int], terrain: str = "plains"):
    hexes = [centre, *road_neighbours(dirs, terrain, True)]
    return bm.road_tile_refs(hexes, bm.road_course_edges(hexes, hex_probe(hexes)))


def test_road_tile_refs_straight() -> None:
    refs = refs_for(make_hex(0, 0, "plains", True), [2, 5])
    assert refs["0,0"] == bm.RoadTileRef("road/plains_straight_v", False)


def test_road_tile_refs_mirrored_turn() -> None:
    refs = refs_for(make_hex(0, 0, "hills", True), [2, 4])
    assert refs["0,0"] == bm.RoadTileRef("road/hills_turn_n_se", True)


def test_road_tile_refs_keyed_by_own_terrain() -> None:
    refs = refs_for(make_hex(0, 0, "town", True), [0])
    assert refs["0,0"] == bm.RoadTileRef("road/town_end_se", False)


def test_road_tile_refs_omit_unhandled_masks() -> None:
    # A solid road blob (radius-2 disc, 42 chords) is past the pruning cap and
    # keeps its raw masks: the centre's six edges are unmodelled.
    hexes = [make_hex(q, r, "plains", True) for q, r in disc(2)]
    refs = bm.road_tile_refs(hexes, bm.road_course_edges(hexes, hex_probe(hexes)))
    assert "0,0" not in refs


def test_road_tile_refs_omit_river_and_bridge() -> None:
    assert "0,0" not in refs_for(make_hex(0, 0, "bridge", True), [2, 5])


def test_road_tile_refs_omit_unflagged() -> None:
    assert "0,0" not in refs_for(make_hex(0, 0, "plains", False), [2, 5])


# --- map input -----------------------------------------------------------------


def test_offset_to_axial_matches_engine_odd_q() -> None:
    assert bm.offset_to_axial(0, 0) == (0, 0)
    assert bm.offset_to_axial(1, 0) == (1, 0)
    assert bm.offset_to_axial(2, 0) == (2, -1)
    assert bm.offset_to_axial(3, 5) == (3, 4)


def test_ascii_grid_neighbours_are_axial_neighbours() -> None:
    # A straight authored row zigzags in odd-q: even col 0 reaches col 1 via SE
    # (edge 0), odd col 1 reaches col 0 via NW (3) and col 2 via NE (1), even col
    # 2 reaches col 1 via SW (4).
    m = bm.parse_ascii("~~~\n...\n", "t")
    result = bm.course_graph_edges(
        ((h.q, h.r) for h in m.hexes), lambda q, r: m.terrain_at(q, r) in bm.RIVER_LIKE
    )
    by_col = {h.col: result[f"{h.q},{h.r}"] for h in m.hexes if h.row == 0}
    assert by_col == {0: [0], 1: [1, 3], 2: [4]}


def test_parse_ascii_with_roads_section() -> None:
    m = bm.parse_ascii(
        "; comment\n[terrain]\n.~.\n.=.\n.~.\n[roads]\n...\n###\n...\n", "t"
    )
    assert (m.width, m.height) == (3, 3)
    bridge = next(h for h in m.hexes if h.terrain == "bridge")
    assert bridge.road and (bridge.col, bridge.row) == (1, 1)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("..x\n...\n", "unknown terrain char 'x'"),
        ("...\n..\n", "row 1 has 2 chars"),
        ("...\n[roads]\n..*\n", "unknown road char '*'"),
        (".~.\n[roads]\n.#.\n", "road runs over river"),
        (".=.\n.~.\n", "has no road"),
        (".=.\n...\n[roads]\n.#.\n...\n", "not adjacent to any river"),
        ("...\n[rivers]\n", "unknown section"),
    ],
)
def test_parse_ascii_rejects_bad_maps(text: str, message: str) -> None:
    with pytest.raises(bm.MapError, match=message):
        bm.parse_ascii(text, "bad")


def test_parse_scenario_twin_rivers_loads() -> None:
    path = SCRIPT.parents[1] / "api" / "app" / "scenarios" / "twin_rivers.json"
    m = bm.load_map(path)
    assert (m.width, m.height) == (40, 40)
    assert sum(h.terrain == "bridge" for h in m.hexes) == 4


def test_parse_scenario_rejects_size_mismatch() -> None:
    with pytest.raises(bm.MapError, match="declared width=5"):
        bm.parse_scenario({"width": 5, "height": 1, "terrain": ["..."]}, "s")


# --- tile sets, fallback and rendering ----------------------------------------


def write_tile(path: Path, color: tuple[int, int, int], size: int = 128) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (size, size), (*color, 255)).save(path)


def write_split_tile(path: Path) -> None:
    """Left half red, right half blue: shows whether a hex was mirrored."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.zeros((128, 128, 4), dtype=np.uint8)
    arr[:, :64] = (255, 0, 0, 255)
    arr[:, 64:] = (0, 0, 255, 255)
    Image.fromarray(arr, "RGBA").save(path)


@pytest.fixture()
def tile_sets(tmp_path: Path):
    primary = tmp_path / "primary"
    fallback = tmp_path / "fallback"
    write_tile(primary / "plains" / "00.png", (0, 200, 0))
    write_tile(primary / "river" / "straight_v_00.png", (0, 0, 200))
    write_split_tile(primary / "river" / "straight_d_00.png")
    write_tile(fallback / "plains" / "00.png", (1, 1, 1))
    write_tile(fallback / "river" / "00.png", (9, 9, 9), size=64)
    write_tile(fallback / "river" / "turn_n_se_00.png", (50, 50, 50))
    write_tile(fallback / "road" / "plains_straight_v_00.png", (90, 60, 30))
    return (
        bm.TileSet.discover(primary, "tiles"),
        bm.TileSet.discover(fallback, "fallback"),
    )


def choices_for(game_map, library):
    course = bm.river_course_edges(
        ((h.q, h.r) for h in game_map.hexes), game_map.terrain_at
    )
    road_course = bm.road_course_edges(game_map.hexes, game_map.hex_at)
    refs = bm.road_tile_refs(game_map.hexes, road_course)
    return (
        {
            (h.col, h.row): bm.choose_tile(h, library, course, refs, road_course)
            for h in game_map.hexes
        },
        course,
        refs,
    )


def test_tile_library_prefers_primary_then_fallback_then_generic(tile_sets) -> None:
    primary, fallback = tile_sets
    library = bm.TileLibrary(primary, fallback)
    # col 1: a vertical river (straight_v at row 1) whose row-2 hex turns SE
    # (turn_n_se) into col 2 row 3 (odd col 1's SE neighbour is (2, row + 1)).
    # The map is one column and one row wider than the river so (2, 3) is an
    # inland end cap (on the border the edge rule would run it off the map).
    m = bm.parse_ascii("[terrain]\n.~..\n.~..\n.~..\n..~.\n....\n", "t")
    choices, _, _ = choices_for(m, library)
    assert choices[(1, 1)].group.tile_set is primary
    assert choices[(1, 1)].path.name == "straight_v_00.png"
    assert choices[(1, 2)].group.tile_set is fallback
    assert choices[(1, 2)].path.name == "turn_n_se_00.png"
    # (2, 3) is an end_ne-mirrored cap; nobody ships it -> generic 00 from fallback.
    end = choices[(2, 3)]
    assert end.path.name == "00.png" and end.group.tile_set is fallback
    assert any("generic 00" in note for note in end.notes)
    # plains exists in primary, so the fallback's plains are never used.
    assert choices[(0, 0)].group.tile_set is primary


def test_missing_baked_road_draws_vector_road(tile_sets) -> None:
    primary, fallback = tile_sets
    m = bm.parse_ascii("...\n...\n...\n[roads]\n.#.\n.#.\n.#.\n", "t")
    only_primary = bm.TileLibrary(primary, None)
    choices, course, refs = choices_for(m, only_primary)
    middle = next(h for h in m.hexes if (h.col, h.row) == (1, 1))
    assert choices[(1, 1)].kind == "terrain"
    assert bm.draws_vector_road(middle, only_primary, course, refs)
    with_fallback = bm.TileLibrary(primary, fallback)
    choices, course, refs = choices_for(m, with_fallback)
    assert choices[(1, 1)].kind == "road"
    assert not bm.draws_vector_road(middle, with_fallback, course, refs)


def test_no_vector_roads_skips_the_vector_road_fallback(tile_sets) -> None:
    primary, _ = tile_sets
    library = bm.TileLibrary(primary, None)
    roaded = bm.parse_ascii("...\n...\n...\n[roads]\n.#.\n.#.\n.#.\n", "t")
    bare = bm.parse_ascii("...\n...\n...\n", "t")
    with_vector, report = bm.render(roaded, library, 64, False, None)
    assert report.vector_roads
    without, report = bm.render(roaded, library, 64, False, None, draw_vector_roads=False)
    assert report.vector_roads == []
    plain, _ = bm.render(bare, library, 64, False, None)
    assert list(without.getdata()) == list(plain.getdata())
    assert list(with_vector.getdata()) != list(plain.getdata())


def test_missing_base_terrain_raises(tile_sets) -> None:
    primary, _ = tile_sets
    m = bm.parse_ascii("w\n", "t")
    with pytest.raises(bm.TileSetError, match="woods"):
        bm.render(m, bm.TileLibrary(primary, None), 64, True, None)


def test_render_mirrors_mirrored_shapes(tile_sets) -> None:
    primary, _ = tile_sets
    library = bm.TileLibrary(primary, None)
    # Odd col 1 row 1 with river NW (0,1) and SE (2,2): mask {0,3} = straight_d
    # unmirrored. With river NE (2,1) and SW (0,2): {1,4} = straight_d mirrored.
    # Only (1,1) is rendered (crop), so the uncovered end caps never load.
    unmirrored = bm.parse_ascii("...\n~~.\n..~\n", "t")
    mirrored = bm.parse_ascii("...\n.~~\n~..\n", "t")
    for game_map, left_is_red in ((unmirrored, True), (mirrored, False)):
        image, report = bm.render(game_map, library, 128, False, (1, 1, 1, 1))
        hex_, choice = report.choices[0]
        assert (hex_.col, hex_.row) == (1, 1)
        assert choice.mirror is not left_is_red
        px = np.asarray(image)
        cy, cx = px.shape[0] // 2, px.shape[1] // 2
        left, right = px[cy, cx - 30], px[cy, cx + 30]
        assert bool(left[0] > left[2]) is left_is_red
        assert bool(right[2] > right[0]) is left_is_red


def test_render_canvas_matches_map_pixel_bounds(tile_sets) -> None:
    primary, _ = tile_sets
    m = bm.parse_ascii("...\n...\n", "t")
    image, _ = bm.render(m, bm.TileLibrary(primary, None), 64, True, None)
    # hex.ts mapPixelBounds at zoom 1: centres span x 0..96 (1.5*32 per col) and
    # y 0..1.5*sqrt(3)*32 (odd col 1 sits half a row down), padded by 32 a side.
    assert image.size == (160, int(np.ceil(1.5 * np.sqrt(3) * 32 + 64)))


def test_nested_tile_dirs_are_rejected(tmp_path: Path) -> None:
    write_tile(tmp_path / "plains" / "extra" / "00.png", (0, 0, 0))
    with pytest.raises(bm.TileSetError, match="nested"):
        bm.TileSet.discover(tmp_path, "tiles")


# --- opt-in site overlay ([sites] + --sites) ----------------------------------

SITES_MAP = "...\n...\nt..\n[sites]\n1,0 farm 2\n2,1 factory 1\n"


def test_sites_section_parses_kind_and_level() -> None:
    m = bm.parse_ascii(SITES_MAP, "t")
    assert m.sites == {
        bm.offset_to_axial(1, 0): ("farm", 2),
        bm.offset_to_axial(2, 1): ("factory", 1),
    }
    assert bm.parse_ascii("...\n...\n", "t").sites == {}


@pytest.mark.parametrize(
    "line,match",
    [
        ("0,2 farm 1", "plains hex"),  # on the town
        ("1,0 mine 1", "unknown site kind"),
        ("1,0 farm 4", "level"),
        ("5,0 farm 1", "off the"),
        ("1,0 farm", "expected"),
    ],
)
def test_sites_section_rejects_bad_lines(line: str, match: str) -> None:
    with pytest.raises(bm.MapError, match=match):
        bm.parse_ascii(f"...\n...\nt..\n[sites]\n{line}\n", "t")


def test_sites_section_rejects_roads() -> None:
    text = "...\n...\n[roads]\n.#.\n...\n[sites]\n1,0 farm 1\n"
    with pytest.raises(bm.MapError, match="road"):
        bm.parse_ascii(text, "t")


def test_render_draws_site_tiles_only_when_opted_in(tmp_path: Path) -> None:
    root = tmp_path / "tiles"
    write_tile(root / "plains" / "00.png", (0, 200, 0))
    write_tile(root / "town" / "00.png", (200, 200, 200))
    sites_dir = tmp_path / "sites"
    write_tile(sites_dir / "farm" / "l2_00.png", (200, 0, 0))
    write_tile(sites_dir / "farm" / "l2_01.png", (200, 0, 0))
    write_tile(sites_dir / "factory" / "l1_00.png", (0, 0, 200))
    library = bm.TileLibrary(bm.TileSet.discover(root, "tiles"), None)
    m = bm.parse_ascii(SITES_MAP, "t")
    plain_map = bm.parse_ascii(SITES_MAP.split("[sites]")[0], "t")
    without, report = bm.render(m, library, 64, False, None)
    assert report.sites == []
    baseline, _ = bm.render(plain_map, library, 64, False, None)
    assert np.array_equal(np.asarray(without), np.asarray(baseline))
    sites = bm.SiteSet.discover(sites_dir)
    image, report = bm.render(m, library, 64, False, None, sites=sites)
    drawn = {(h.col, h.row): path.parent.name for h, path in report.sites}
    assert drawn == {(1, 0): "farm", (2, 1): "factory"}
    px = np.asarray(image)
    for h, path in report.sites:
        x, y = bm.axial_to_pixel(h.q, h.r, bm.HEX_SIZE)
        sx = round(x + bm.HEX_SIZE)  # canvas origin is (-HEX_SIZE, -HEX_SIZE) at zoom 1
        sy = round(y + bm.HEX_SIZE)
        expect = (200, 0, 0) if path.parent.name == "farm" else (0, 0, 200)
        assert tuple(px[sy, sx, :3]) == expect


def test_missing_site_level_raises(tmp_path: Path) -> None:
    root = tmp_path / "tiles"
    write_tile(root / "plains" / "00.png", (0, 200, 0))
    write_tile(root / "town" / "00.png", (200, 200, 200))
    write_tile(tmp_path / "sites" / "farm" / "l1_00.png", (200, 0, 0))
    library = bm.TileLibrary(bm.TileSet.discover(root, "tiles"), None)
    m = bm.parse_ascii(SITES_MAP, "t")
    with pytest.raises(bm.TileSetError, match="farm/l2"):
        bm.render(m, library, 64, False, None, sites=bm.SiteSet.discover(tmp_path / "sites"))


def write_stage(root: Path, name: str, offsets: list[tuple[int, int]], colors) -> None:
    d = root / "base" / name
    positions = {}
    for i, (off, color) in enumerate(zip(offsets, colors)):
        write_tile(d / f"h{i}.png", color)
        positions[f"h{i}"] = list(off)
    (d / "positions.json").write_text(__import__("json").dumps({"positions": positions}))


def test_staged_base_draws_its_whole_footprint(tmp_path: Path) -> None:
    root = tmp_path / "tiles"
    write_tile(root / "plains" / "00.png", (0, 200, 0))
    sites_dir = tmp_path / "sites"
    colors = [(200, 0, 0), (0, 0, 200), (200, 200, 0)]
    write_stage(sites_dir, "stage3_a", [(0, 0), (1, 0), (0, 1)], colors)
    library = bm.TileLibrary(bm.TileSet.discover(root, "tiles"), None)
    m = bm.parse_ascii("....\n....\n....\n[sites]\n0,0 base 3\n", "t")
    image, report = bm.render(m, library, 64, False, None, sites=bm.SiteSet.discover(sites_dir))
    drawn = {(h.q, h.r): path.name for h, path in report.sites}
    assert drawn == {(0, 0): "h0.png", (1, 0): "h1.png", (0, 1): "h2.png"}
    px = np.asarray(image)
    for h, path in report.sites:
        x, y = bm.axial_to_pixel(h.q, h.r, bm.HEX_SIZE)
        expect = colors[int(path.stem[1])]
        assert tuple(px[round(y + bm.HEX_SIZE), round(x + bm.HEX_SIZE), :3]) == expect


def test_staged_base_footprint_is_validated(tmp_path: Path) -> None:
    with pytest.raises(bm.MapError, match="plains"):  # SE neighbour is a town
        bm.parse_ascii(".t..\n....\n[sites]\n0,0 base 2\n", "t")
    with pytest.raises(bm.MapError, match="overlaps"):
        bm.parse_ascii("....\n....\n....\n[sites]\n0,0 base 4\n1,1 farm 1\n", "t")
    with pytest.raises(bm.MapError, match="level"):
        bm.parse_ascii("....\n....\n[sites]\n0,0 farm 4\n", "t")
    sites_dir = tmp_path / "sites"
    write_stage(sites_dir, "stage2_a", [(0, 0), (0, 1)], [(1, 1, 1), (2, 2, 2)])
    with pytest.raises(bm.TileSetError, match="footprint"):
        bm.SiteSet.discover(sites_dir)


# --- lakes and the river-mouth rule -------------------------------------------

# mouth_direction(river_dirs, lake_dirs) -> expected lake direction or None.
MOUTH_CASES = [
    # straight beats gentle, whatever the direction order
    ([2], [0, 4, 5], 5),
    ([5], [0, 2, 3], 2),
    # gentle when no straight option; ties go to the lowest direction
    ([2], [0, 4], 0),
    ([0], [2, 4], 2),
    ([3], [1], 1),
    # sharp turns are never taken
    ([2], [1, 3], None),
    ([0], [1], None),
    ([0], [1, 3], 3),
    # no river-like neighbour: the lowest lake direction
    ([], [4, 1, 3], 1),
    # mid-course hexes and hexes with no lake neighbour get no mouth
    ([1, 4], [0], None),
    ([2], [], None),
    ([], [], None),
]


@pytest.mark.parametrize(("river_dirs", "lake_dirs", "expected"), MOUTH_CASES)
def test_mouth_direction(river_dirs: list[int], lake_dirs: list[int], expected) -> None:
    assert bm.mouth_direction(river_dirs, lake_dirs) == expected


def test_lake_is_not_river_like_for_the_course() -> None:
    # a river running past a lake (two river neighbours) does not connect to it
    cells = {(0, 0): "river", DIRS[2]: "river", DIRS[5]: "river", DIRS[0]: "lake"}
    probe = terrain_probe(cells)
    assert course(cells)["0,0"] == [2, 5]
    assert bm.lake_mouths(cells, probe) == {}
    assert bm.connected_edges(0, 0, probe) == [2, 5]


def test_lake_mouths_extend_the_course_to_the_lake() -> None:
    # river course from the north ends at (0,0); lakes S and SE of it
    cells = {(0, 0): "river", DIRS[2]: "river", DIRS[5]: "lake", DIRS[0]: "lake"}
    probe = terrain_probe(padded(cells))
    mouths = bm.lake_mouths(cells, probe)
    assert mouths == {"0,0": 5}
    full = bm.river_course_edges(padded(cells), probe)
    assert full["0,0"] == [2, 5]
    assert bm.oriented_river_tile(full["0,0"]) == bm.OrientedTile("straight_v", False)
    # the upstream hex is unchanged: its own lake-free course
    assert full["0,-1"] == [5]


def test_lake_mouth_on_a_lone_river_hex_is_an_end_cap() -> None:
    cells = {(0, 0): "river", DIRS[3]: "lake", DIRS[1]: "lake"}
    probe = terrain_probe(padded(cells))
    mouths = bm.lake_mouths(cells, probe)
    assert mouths == {"0,0": 1}
    full = bm.river_course_edges(padded(cells), probe)
    assert bm.oriented_river_tile(full["0,0"]) == bm.OrientedTile("end_ne", False)


def test_parse_ascii_reads_lake_and_rejects_roads_on_it() -> None:
    m = bm.parse_ascii("o.\n..\n", "t")
    assert m.terrain_at(0, 0) == "lake"
    with pytest.raises(bm.MapError, match="lake"):
        bm.parse_ascii("o.\n..\n[roads]\n#.\n..\n", "t")


def test_lake_without_art_renders_flat_fill(tile_sets) -> None:
    primary, _ = tile_sets
    m = bm.parse_ascii("o\n", "t")
    image, report = bm.render(m, bm.TileLibrary(primary, None), 64, False, None)
    assert report.choices == []
    assert [(h.col, h.row) for h in report.fill_only] == [(0, 0)]
    px = np.asarray(image)
    fill = bm.TERRAIN_FILL["lake"]
    assert tuple(px[px.shape[0] // 2, px.shape[1] // 2]) == (
        (fill >> 16) & 0xFF,
        (fill >> 8) & 0xFF,
        fill & 0xFF,
    )
    assert "flat fill only" in bm.format_report(report)


def test_lake_variants_use_the_cosmetic_hash(tmp_path: Path) -> None:
    root = tmp_path / "tiles"
    write_tile(root / "plains" / "00.png", (0, 200, 0))
    for i in range(3):
        write_tile(root / "lake" / f"0{i}.png", (0, 0, 100 + i))
    library = bm.TileLibrary(bm.TileSet.discover(root, "tiles"), None)
    m = bm.parse_ascii("ooo\nooo\n", "t")
    _, report = bm.render(m, library, 64, False, None)
    for h, choice in report.choices:
        assert choice.kind == "terrain"
        assert choice.path.name == f"0{bm.variant_index(h.q, h.r, 3)}.png"


def write_joins(root: Path, files: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "joins.json").write_text('{"hex_width": 128}')
    for name in files:
        write_tile(root / name, (10, 20, 30), size=64)


def test_lake_joins_and_mouth_sprite_are_chosen(tmp_path: Path) -> None:
    root = tmp_path / "tiles"
    write_tile(root / "plains" / "00.png", (0, 200, 0))
    write_tile(root / "river" / "00.png", (0, 0, 200))
    joins_dir = tmp_path / "joins"
    write_joins(joins_dir, ["lake/edge_n_00.png", "lake/mouth_s_00.png", "lake/mouth_n_00.png"])
    joins = bm.JoinSet.load(joins_dir)
    # col 0: river rows 0-1 flowing south into lake rows 2-3
    m = bm.parse_ascii("~.\n~.\no.\no.\n", "t")
    _, report = bm.render(m, bm.TileLibrary(bm.TileSet.discover(root, "tiles"), None), 64, False, None, joins=joins)
    assert report.mouths == {"0,1": 5}
    kinds = [(j.path.name, tuple((h.col, h.row) for h in j.hexes)) for j in report.joins]
    # lake-lake edge (A = lower lake, its N neighbour) first, then the mouth
    assert kinds == [("edge_n_00.png", ((0, 3), (0, 2))), ("mouth_s_00.png", ((0, 1), (0, 2)))]
    mouth = report.joins[-1]
    assert mouth.code == bm.MOUTH_CODE_BASE + 5
    r_hex, lake = mouth.hexes
    rx, ry = bm.axial_to_pixel(r_hex.q, r_hex.r, bm.HEX_SIZE)
    lx, ly = bm.axial_to_pixel(lake.q, lake.r, bm.HEX_SIZE)
    assert mouth.anchor == pytest.approx(((rx + lx) / 2, (ry + ly) / 2))


def test_join_set_rejects_unknown_kinds(tmp_path: Path) -> None:
    write_joins(tmp_path, ["lake/mouth_up_00.png"])
    with pytest.raises(bm.TileSetError, match="kind must be one of"):
        bm.JoinSet.load(tmp_path)


# --- the map-edge rule ----------------------------------------------------------

EDGE_CASES = [
    ([2], [5, 0, 4], 5),  # straight preferred
    ([2], [0, 4], 0),  # gentle fallback, lowest of the ties
    ([5], [3], 3),  # gentle
    ([2], [1, 3], None),  # sharp only: stays an end cap
    ([], [4, 2], 2),  # no river neighbour: lowest off-map direction
    ([1, 3], [2], None),  # mid-course hex on the edge: unchanged
    ([2], [], None),  # interior hex: unchanged
]


@pytest.mark.parametrize(("river_dirs", "off_map", "expected"), EDGE_CASES)
def test_edge_direction(river_dirs: list[int], off_map: list[int], expected) -> None:
    assert bm.edge_direction(river_dirs, off_map) == expected


def edge_outlets_of(text: str) -> tuple[dict[str, int], dict[str, int]]:
    m = bm.parse_ascii(text, "t")
    coords = [(h.q, h.r) for h in m.hexes]
    mouths = bm.lake_mouths(coords, m.terrain_at)
    return mouths, bm.map_edge_outlets(coords, m.terrain_at, mouths)


def test_edge_rule_runs_a_border_river_off_the_map() -> None:
    # col 1 (odd) runs top to bottom: top hex (1,0) exits N, bottom (1,2) exits S
    mouths, outlets = edge_outlets_of(".~.\n.~.\n.~.\n")
    assert mouths == {}
    assert outlets == {"1,0": 2, "1,2": 5}


def test_edge_rule_leaves_interior_caps_and_mid_course_hexes_alone() -> None:
    # interior spring at (2,2) flowing north to the border at (2,0)
    _, outlets = edge_outlets_of(".....\n..~..\n..~..\n.....\n.....\n")
    assert outlets == {}
    # a river along the top border: the middle hex has two river neighbours
    _, outlets = edge_outlets_of("~~~\n...\n")
    assert "1,0" not in outlets


def test_mouth_rule_wins_over_the_edge_rule() -> None:
    # (1,0) is on the top border and touches the lake below it
    mouths, outlets = edge_outlets_of(".~.\n.o.\n...\n")
    assert mouths == {"1,0": 5}
    assert outlets == {}


def test_render_draws_edge_exits_instead_of_end_caps(tmp_path: Path) -> None:
    root = tmp_path / "tiles"
    write_tile(root / "plains" / "00.png", (0, 200, 0))
    write_tile(root / "river" / "00.png", (0, 0, 9))
    write_tile(root / "river" / "straight_v_00.png", (0, 0, 200))
    library = bm.TileLibrary(bm.TileSet.discover(root, "tiles"), None)
    _, report = bm.render(bm.parse_ascii(".~.\n.~.\n.~.\n", "t"), library, 64, False, None)
    shapes = {(h.col, h.row): c.shape for h, c in report.choices if h.terrain == "river"}
    assert shapes == {(1, 0): "straight_v", (1, 1): "straight_v", (1, 2): "straight_v"}
    assert report.edge_outlets == {"1,0": 2, "1,2": 5}



# --- courseGraph.ts: local junction pruning (riverTiles/roadTiles tests) ---------


def disc(radius: int) -> list[tuple[int, int]]:
    """Every axial coord within ``radius`` of the origin."""
    return [
        (q, r)
        for q in range(-radius, radius + 1)
        for r in range(max(-radius, -q - radius), min(radius, -q + radius) + 1)
    ]


def ray(at: tuple[int, int], direction: int, steps: int) -> list[tuple[int, int]]:
    dq, dr = DIRS[direction]
    return [(at[0] + dq * (i + 1), at[1] + dr * (i + 1)) for i in range(steps)]


def road_map(area, roads) -> list:
    on_road = set(roads)
    return [make_hex(q, r, "plains", (q, r) in on_road) for q, r in area]


def course_triangles(course: dict[str, list[int]]) -> int:
    """Triangles whose three edges all survive, counted once per corner."""
    count = 0
    for key, edges in course.items():
        q, r = map(int, key.split(","))
        for d in range(6):
            e = (d + 1) % 6
            if d in edges and e in edges:
                bq, br = DIRS[d]
                if (d + 2) % 6 in course[f"{q + bq},{r + br}"]:
                    count += 1
    return count


def components(edges: dict[str, list[int]]) -> str:
    seen: set[str] = set()
    groups: list[str] = []
    for start in edges:
        if start in seen:
            continue
        members, stack = [], [start]
        seen.add(start)
        while stack:
            k = stack.pop()
            members.append(k)
            q, r = map(int, k.split(","))
            for d in edges[k]:
                n = f"{q + DIRS[d][0]},{r + DIRS[d][1]}"
                if n not in seen:
                    seen.add(n)
                    stack.append(n)
        groups.append(" ".join(sorted(members)))
    return " | ".join(sorted(groups))


def junction_cells(dq: int) -> dict[tuple[int, int], str]:
    """The twin_rivers tributary junction, shifted by ``dq`` columns."""
    return {
        (q + dq, r): "river"
        for q, r in ((0, 0), (1, 0), (-1, 1), (0, 1), (2, -1), (-2, 1), (0, 2))
    }


def test_far_apart_junctions_are_all_pruned() -> None:
    # Four junctions of five chords each: 20 chords map-wide, over the old
    # whole-map cap of 16, but each cluster is small.
    cells: dict[tuple[int, int], str] = {}
    for dq in (0, 20, 40, 60):
        cells.update(junction_cells(dq))
    result = course(cells)
    for dq in (0, 20, 40, 60):
        assert result[f"{dq},0"] == [0, 4, 5]
        assert result[f"{dq + 1},0"] == [1, 3]
        assert result[f"{dq - 1},1"] == [1, 3]
        assert result[f"{dq},1"] == [2, 5]


def test_road_t_junction_prunes_to_one_fork() -> None:
    # A N/S road through the origin; a branch leaves east from (1,0), which also
    # touches (0,1): the raw masks make a triangle loop at the join.
    roads = [(0, 0), *ray((0, 0), 2, 3), *ray((0, 0), 5, 3), *ray((0, 0), 0, 3)]
    hexes = road_map(disc(5), roads)
    road_course = bm.road_course_edges(hexes, hex_probe(hexes))
    assert course_triangles(road_course) == 0
    assert road_course["0,0"] == [0, 2, 5]
    assert road_course["0,1"] == [2, 5]
    assert road_course["1,0"] == [0, 3]
    refs = bm.road_tile_refs(hexes, road_course)
    assert refs["0,0"] == bm.RoadTileRef("road/plains_fork_se_n_s", False)


def test_three_road_meeting_keeps_every_arm() -> None:
    a, b, c = (0, 0), (1, 0), (0, 1)
    roads = [a, b, c, *ray(a, 2, 3), *ray(b, 0, 3), *ray(c, 4, 3)]
    hexes = road_map(disc(5), roads)
    road_course = bm.road_course_edges(hexes, hex_probe(hexes))
    assert course_triangles(road_course) == 0
    assert len(components(road_course).split(" | ")) == 1
    assert 2 in road_course["0,0"]
    assert 0 in road_course["1,0"]
    assert 4 in road_course["0,1"]


def test_road_y_meeting_without_triangles_is_untouched() -> None:
    roads = [(0, 0), *ray((0, 0), 0, 3), *ray((0, 0), 2, 3), *ray((0, 0), 4, 3)]
    hexes = road_map(disc(5), roads)
    assert bm.road_course_edges(hexes, hex_probe(hexes))["0,0"] == [0, 2, 4]


def test_road_pruning_never_changes_connectivity() -> None:
    rng = np.random.default_rng(0x5EED)
    area = disc(4)
    for _ in range(60):
        density = 0.25 + rng.random() * 0.35
        hexes = [
            make_hex(q, r, "bridge" if rng.random() < 0.1 else "plains", bool(rng.random() < density))
            for q, r in area
        ]
        probe = hex_probe(hexes)
        raw = {
            f"{h.q},{h.r}": bm.road_connected_edges(h.q, h.r, probe)
            for h in hexes
            if h.road or h.terrain == "bridge"
        }
        pruned = bm.road_course_edges(hexes, probe)
        assert sorted(pruned) == sorted(raw)
        assert components(pruned) == components(raw)
        for key, edges in pruned.items():
            assert set(edges) <= set(raw[key])
            q, r = map(int, key.split(","))
            for d in edges:
                assert (d + 3) % 6 in pruned[f"{q + DIRS[d][0]},{r + DIRS[d][1]}"]


def test_course_graph_rejects_missing_carrying_neighbours() -> None:
    cells = {(0, 0): "river", (0, 1): "river"}
    with pytest.raises(ValueError, match="not among the coords"):
        bm.course_graph_edges([(0, 0)], lambda q, r: cells.get((q, r)) == "river")


def test_render_draws_road_stubs_along_the_pruned_course(tmp_path: Path) -> None:
    root = tmp_path / "tiles"
    write_tile(root / "plains" / "00.png", (0, 200, 0))
    library = bm.TileLibrary(bm.TileSet.discover(root, "tiles"), None)
    # no baked road art: every road hex draws vector stubs
    m = bm.parse_ascii(".....\n.....\n.....\n.....\n[roads]\n..#..\n..##.\n..#..\n..#..\n", "t")
    _, report = bm.render(m, library, 64, False, None)
    road_course = bm.road_course_edges(m.hexes, m.hex_at)
    assert course_triangles(road_course) == 0
    assert {(h.col, h.row) for h in report.vector_roads} == {
        (h.col, h.row) for h in m.hexes if h.road
    }
