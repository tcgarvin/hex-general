"""Tests for scripts/tile-foundry/joins.py (same-terrain join sprites) and the
opt-in board-mock overlays (--joins, --drift, --grid-style concept).

Run: python3 -m pytest scripts/tests
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from PIL import Image, ImageDraw

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations via this
    spec.loader.exec_module(module)
    return module


joins = _load("joins", SCRIPTS / "tile-foundry" / "joins.py")
bm = _load("board_mock_joins", SCRIPTS / "board-mock.py")

SIZE = joins.HEX_SIZE


def hex_contains(cx: float, cy: float, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Exact flat-top hex containment (radius SIZE) of points, no rasteriser."""
    dx, dy = np.abs(x - cx), np.abs(y - cy)
    apothem = SIZE * math.sqrt(3) / 2
    inside: np.ndarray = (dy <= apothem) & (
        math.sqrt(3) * dx + dy <= math.sqrt(3) * SIZE
    )
    return inside


def pixel_points(canvas: int) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.mgrid[0:canvas, 0:canvas].astype(np.float64)
    return xs + 0.5 - canvas / 2, ys + 0.5 - canvas / 2


# --- lens geometry -------------------------------------------------------------


@pytest.mark.parametrize("orientation", ["se", "ne", "n"])
@pytest.mark.parametrize("terrain", ["woods", "city", "hills"])
def test_unclipped_lens_stays_inside_the_two_hexes(
    orientation: str, terrain: str
) -> None:
    # The lens alone (before the union clip) must already avoid any third hex:
    # every pixel it touches has its centre inside A or B.
    alpha = joins.lens_alpha(orientation, joins.DEFAULT_PARAMS[terrain])
    xs, ys = pixel_points(joins.CANVAS)
    inside = np.zeros_like(alpha, dtype=bool)
    for cx, cy in joins.pair_centres(orientation):
        inside |= hex_contains(cx, cy, xs, ys)
    assert alpha.max() > 0.99
    assert alpha[~inside].max(initial=0.0) == 0.0


@pytest.mark.parametrize("orientation", ["se", "ne", "n"])
def test_lens_is_widest_at_midpoint_and_gone_before_vertices(orientation: str) -> None:
    params = joins.DEFAULT_PARAMS["woods"]
    alpha = joins.lens_alpha(orientation, params)
    normal, along = joins.edge_frame(orientation)
    xs, ys = pixel_points(joins.CANVAS)
    t = xs * along[0] + ys * along[1]
    p = xs * normal[0] + ys * normal[1]
    # Centre pixel opaque; nothing within 3 px of either vertex (t = +-SIZE / 2).
    centre = joins.CANVAS // 2
    assert alpha[centre - 1 : centre + 1, centre - 1 : centre + 1].min() > 0.99
    near_vertex = (np.abs(np.abs(t) - SIZE / 2) < 3) & (np.abs(p) < 3)
    assert alpha[near_vertex].max() == 0.0
    # Perpendicular reach is largest on the midpoint line.
    reach_mid = np.abs(p[(np.abs(t) < 1) & (alpha > 0.5)]).max()
    reach_off = np.abs(p[(np.abs(t - 15) < 1) & (alpha > 0.5)]).max()
    assert reach_mid > reach_off
    assert reach_mid >= params.half_width - params.feather


def test_lens_rejects_half_length_reaching_the_vertices() -> None:
    with pytest.raises(joins.JoinError, match="vertices"):
        joins.lens_alpha("n", joins.LensParams(20.0, SIZE / 2, 5.0))


@pytest.mark.parametrize("vertex", ["e", "ne"])
def test_vertex_fill_stays_inside_the_three_hexes(vertex: str) -> None:
    alpha = joins.vertex_alpha(vertex, joins.VERTEX_PARAMS["woods"])
    xs, ys = pixel_points(joins.CANVAS)
    centres = joins.triangle_centres(vertex)
    inside = np.zeros_like(alpha, dtype=bool)
    for cx, cy in centres:
        inside |= hex_contains(cx, cy, xs, ys)
    assert alpha[~inside].max(initial=0.0) == 0.0
    # The anchor is the vertex the three hexes share: each centre is SIZE away.
    for cx, cy in centres:
        assert math.hypot(cx, cy) == pytest.approx(SIZE)


def test_triangle_centres_are_mutually_adjacent() -> None:
    apothem2 = 2 * SIZE * math.sqrt(3) / 2
    for vertex in joins.VERTICES:
        c = joins.triangle_centres(vertex)
        for i in range(3):
            for j in range(i + 1, 3):
                d = math.hypot(c[i][0] - c[j][0], c[i][1] - c[j][1])
                assert d == pytest.approx(apothem2)


# --- builder -------------------------------------------------------------------


def write_feature_tile(path: Path, color: tuple[int, int, int], church: bool) -> None:
    """A 128 px hex tile; optional grey 'church' blob left of centre."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.polygon(
        [
            (
                64 + 64 * math.cos(math.radians(60 * i)),
                64 + 64 * math.sin(math.radians(60 * i)),
            )
            for i in range(6)
        ],
        fill=(*color, 255),
    )
    if church:
        draw.rectangle((44, 56, 60, 72), fill=(110, 110, 112, 255))
    im.save(path)


def test_landmark_ranking_prefers_windows_away_from_grey_blobs(tmp_path: Path) -> None:
    write_feature_tile(tmp_path / "t.png", (140, 70, 40), church=True)
    with Image.open(tmp_path / "t.png") as tile:
        alpha = joins.join_alpha("n", joins.DEFAULT_PARAMS["city"])
        ranked = joins.rank_offsets(tile, alpha)
        landmarks = np.pad(joins.landmark_mask(tile), joins.CANVAS // 2)
    assert landmarks.max() == 1.0  # the grey blob is found; the flat roofs are not

    def under(offset: tuple[int, int]) -> float:
        x0, y0 = 64 + offset[0], 64 + offset[1]
        window = landmarks[y0 : y0 + joins.CANVAS, x0 : x0 + joins.CANVAS]
        return float((window * alpha).sum())

    assert under(ranked[0]) < under((0, 0))
    assert ranked[0][0] > 0  # the blob sits left of centre, so look right
    assert ranked == joins.rank_offsets(Image.open(tmp_path / "t.png"), alpha)


def test_builder_writes_kinds_manifest_and_readme(tmp_path: Path) -> None:
    tiles = tmp_path / "tiles"
    for i, color in enumerate([(30, 80, 30), (40, 90, 35)]):
        write_feature_tile(tiles / "woods" / f"{i:02d}.png", color, church=False)
    out = tmp_path / "joins"
    assert (
        joins.main(
            [
                "--tiles",
                str(tiles),
                "--out",
                str(out),
                "--terrains",
                "woods",
                "--variants",
                "2",
            ]
        )
        == 0
    )
    names = sorted(p.name for p in (out / "woods").iterdir())
    kinds = ["edge_n", "edge_ne", "edge_se", "vert_e", "vert_ne"]
    assert names == sorted(f"{k}_{v:02d}.png" for k in kinds for v in range(2))
    manifest = json.loads((out / "joins.json").read_text())
    assert manifest["hex_width"] == 128 and manifest["canvas"] == joins.CANVAS
    readme = (out / "README.md").read_text()
    assert "**Anchor:**" in readme and "Draw order" in readme
    sprite = np.asarray(Image.open(out / "woods" / "edge_n_00.png"))
    assert sprite.shape == (joins.CANVAS, joins.CANVAS, 4)
    assert sprite[..., 3].max() == 255 and sprite[0, 0, 3] == 0
    # Board-mock reads the set back.
    joinset = bm.JoinSet.load(out)
    assert set(joinset.groups) == {("woods", code) for code in range(5)}


def test_builder_rejects_missing_terrain(tmp_path: Path) -> None:
    (tmp_path / "tiles").mkdir()
    with pytest.raises(joins.JoinError, match="woods"):
        joins.build_patch_joins(tmp_path / "tiles", "woods", tmp_path / "out", 2)


# --- board-mock: join selection -------------------------------------------------


# (q, r, code, count) -> index, captured by running the README's JS under node.
JOIN_HASH_CASES = [
    (0, 0, 0, 3, 0),
    (0, 0, 1, 3, 0),
    (0, 0, 2, 3, 1),
    (0, 0, 3, 3, 0),
    (0, 0, 4, 3, 1),
    (7, -3, 0, 3, 1),
    (7, -3, 2, 5, 2),
    (-4, 9, 1, 3, 1),
    (-4, 9, 4, 7, 3),
    (120, -60, 2, 3, 2),
    (39, -19, 3, 4, 3),
    (1000, 3000, 1, 3, 2),
]


@pytest.mark.parametrize(("q", "r", "code", "count", "expected"), JOIN_HASH_CASES)
def test_join_variant_index_matches_js(
    q: int, r: int, code: int, count: int, expected: int
) -> None:
    assert bm.join_variant_index(q, r, code, count) == expected


def test_join_variant_index_rejects_bad_codes() -> None:
    with pytest.raises(ValueError):
        bm.join_variant_index(0, 0, 11, 3)  # 0-4 joins, 5-10 river mouths


def test_canonical_edge_names_each_edge_once() -> None:
    for d, (dq, dr) in enumerate(bm.AXIAL_DIRECTIONS):
        a, b = (2, 3), (2 + dq, 3 + dr)
        assert bm.canonical_edge(a, b) == bm.canonical_edge(b, a)
        owner, direction = bm.canonical_edge(a, b)
        assert direction in (0, 1, 2)
    with pytest.raises(ValueError):
        bm.canonical_edge((0, 0), (2, 0))


@pytest.fixture()
def woods_joinset(tmp_path: Path):
    tiles = tmp_path / "tiles"
    write_feature_tile(tiles / "woods" / "00.png", (30, 80, 30), church=False)
    out = tmp_path / "joins"
    joins.main(
        [
            "--tiles",
            str(tiles),
            "--out",
            str(out),
            "--terrains",
            "woods",
            "--variants",
            "1",
        ]
    )
    return bm.JoinSet.load(out)


def test_choose_joins_pairs_and_triangles(woods_joinset) -> None:
    # A triangle of woods (0,0), (1,0) [SE of col 0], (0,1) [S of col 0] plus a
    # lone woods hex (3, 3) and plains elsewhere.
    m = bm.parse_ascii("w...\nw...\n....\n...w\n", "t")
    m2 = bm.parse_ascii("ww..\nw...\n....\n...w\n", "t")
    assert all(c.code < 3 for c in bm.choose_joins(m, m.hexes, woods_joinset))
    chosen = bm.choose_joins(m2, m2.hexes, woods_joinset)
    edges = [c for c in chosen if c.code < 3]
    vertices = [c for c in chosen if c.code >= 3]
    assert len(edges) == 3 and len(vertices) == 1
    assert chosen == edges + vertices  # edges drawn before vertex fills
    for c in edges:
        a, b = c.hexes
        ax, ay = bm.axial_to_pixel(a.q, a.r, bm.HEX_SIZE)
        bx, by = bm.axial_to_pixel(b.q, b.r, bm.HEX_SIZE)
        assert c.anchor == pytest.approx(((ax + bx) / 2, (ay + by) / 2))
    vertex = vertices[0].anchor
    for h in vertices[0].hexes:
        hx, hy = bm.axial_to_pixel(h.q, h.r, bm.HEX_SIZE)
        assert math.hypot(hx - vertex[0], hy - vertex[1]) == pytest.approx(bm.HEX_SIZE)


def test_joinset_rejects_unknown_kinds(tmp_path: Path) -> None:
    (tmp_path / "woods").mkdir()
    (tmp_path / "joins.json").write_text(json.dumps({"hex_width": 128}))
    Image.new("RGBA", (64, 64)).save(tmp_path / "woods" / "edge_sw_00.png")
    with pytest.raises(bm.TileSetError, match="kind must be"):
        bm.JoinSet.load(tmp_path)


# --- board-mock: drift ------------------------------------------------------------


def test_drift_noise_is_deterministic_bounded_and_smooth() -> None:
    xs = np.linspace(0, 64 * 30, 600)
    a = bm.drift_noise(xs[None, :], xs[:, None])
    b = bm.drift_noise(xs[None, :], xs[:, None])
    assert np.array_equal(a, b)
    assert a.min() >= -1 and a.max() <= 1 and a.std() > 0.25
    # Smooth: neighbouring samples ~3 world px apart barely differ.
    assert np.abs(np.diff(a, axis=1)).max() < 0.1


def test_drift_does_not_repeat_with_the_hex_lattice() -> None:
    rng = np.random.default_rng(1)
    pts = rng.uniform(0, 64 * 40, size=(400, 2))
    base = bm.drift_noise(pts[:, 0], pts[:, 1])
    for dq, dr in [(1, 0), (0, 1), (2, -1), (3, 0)]:
        dx, dy = bm.axial_to_pixel(dq, dr, bm.HEX_SIZE)
        shifted = bm.drift_noise(pts[:, 0] + dx, pts[:, 1] + dy)
        assert np.abs(shifted - base).mean() > 0.02


# --- board-mock: defaults unchanged ---------------------------------------------


def plain_tiles(tmp_path: Path) -> Path:
    root = tmp_path / "tiles"
    write_feature_tile(root / "plains" / "00.png", (100, 110, 50), church=False)
    write_feature_tile(root / "woods" / "00.png", (30, 80, 30), church=False)
    return root


def test_default_render_is_unchanged(tmp_path: Path) -> None:
    library = bm.TileLibrary(bm.TileSet.discover(plain_tiles(tmp_path), "tiles"), None)
    m = bm.parse_ascii("w.w\n.ww\n", "t")
    image, report = bm.render(m, library, 128, True, None)
    explicit, _ = bm.render(
        m, library, 128, True, None, joins=None, drift=0.0, grid_style="dark"
    )
    assert image.tobytes() == explicit.tobytes()
    assert report.joins == []
    # Pinned when the overlays landed, after the default output was checked
    # byte-identical to pre-overlay renders of showcase, twin_rivers and
    # r1-terrain; a change here means the default board-mock output moved.
    assert hashlib.sha256(image.tobytes()).hexdigest()[:16] == PINNED_DEFAULT_RENDER


PINNED_DEFAULT_RENDER = "0fa9d7b012b51a57"


def test_cli_drift_flag_defaults(tmp_path: Path, capsys) -> None:
    tiles = plain_tiles(tmp_path)
    (tmp_path / "m.txt").write_text("w.\n..\n")
    out = tmp_path / "o.png"
    base = ["--tiles", str(tiles), "--map", str(tmp_path / "m.txt"), "--hex-px", "64"]
    bm.main([*base, "--out", str(out)])
    plain = np.asarray(Image.open(out)).copy()
    bm.main([*base, "--out", str(out), "--drift"])
    drifted = np.asarray(Image.open(out))
    bm.main([*base, "--out", str(out), "--drift", "0.05"])
    explicit = np.asarray(Image.open(out))
    assert np.array_equal(drifted, explicit)
    assert not np.array_equal(plain, drifted)
    ratio = drifted.astype(float).mean() / plain.astype(float).mean()
    assert 0.94 < ratio < 1.06


def test_concept_grid_changes_only_the_grid(tmp_path: Path) -> None:
    library = bm.TileLibrary(bm.TileSet.discover(plain_tiles(tmp_path), "tiles"), None)
    m = bm.parse_ascii("..\n..\n", "t")
    dark, _ = bm.render(m, library, 128, True, None)
    concept, _ = bm.render(m, library, 128, True, None, grid_style="concept")
    off, _ = bm.render(m, library, 128, False, None)
    d, c, o = (np.asarray(x).astype(int) for x in (dark, concept, off))
    changed = np.abs(c - o).sum(axis=2) > 0
    # The concept line touches about as many pixels as the dark one (a few px
    # each side of every edge) and nothing else.
    assert changed.any() and (np.abs(d - o).sum(axis=2) > 0).any()
    assert changed.mean() < 0.15
    # Core darker than the plains under it, highlight lighter.
    assert (c[changed].sum(axis=1) < o[changed].sum(axis=1) - 60).any()
    assert (c[changed].sum(axis=1) > o[changed].sum(axis=1) + 60).any()
    with pytest.raises(ValueError):
        bm.render(m, library, 128, True, None, grid_style="bold")


# --- lake joins and river mouths -------------------------------------------------

PROFILE = SCRIPTS.parent / "art" / "codex" / "terrain" / "river" / "profile.json"


def flat_water() -> np.ndarray:
    out = np.zeros((joins.CANVAS, joins.CANVAS, 4))
    out[..., :3] = (60, 75, 72)
    out[..., 3] = 255
    return out


@pytest.mark.parametrize("name", list(joins.MOUTHS))
def test_mouth_sprite_continues_the_river_crossing(name: str) -> None:
    """On the shared edge the sprite is the river profile; it reaches into the
    lake hex only, and its water fades out before the canvas edge."""
    direction = joins.MOUTHS[name]
    profile = joins.CrossProfile.load(PROFILE)
    sprite = joins.mouth_sprite(direction, profile, flat_water(), seed=0)
    normal, tangent = joins.mouth_frame(direction)
    xs, ys = joins._pixel_grid(joins.CANVAS)
    p = xs * normal[0] + ys * normal[1]
    t = xs * tangent[0] + ys * tangent[1]
    alpha = sprite[..., 3] / 255
    assert alpha[p < -joins.MOUTH_BACK_PX].max() == 0  # nothing inside the river hex
    on_edge = (np.abs(p) < 0.8) & (np.abs(t) < 6)
    rgb, _ = profile.sample(t[on_edge])
    assert np.abs(sprite[..., :3][on_edge] - rgb).max() < 4
    assert alpha[on_edge].min() > 0.4
    assert alpha[p > joins.MOUTH_WATER_FADE[1]].max() == 0


def test_lake_joins_build_every_kind(tmp_path: Path) -> None:
    tiles = tmp_path / "tiles" / "lake"
    tiles.mkdir(parents=True)
    arr = np.zeros((128, 128, 4), dtype=np.uint8)
    arr[...] = (60, 75, 72, 255)
    for i in range(2):
        Image.fromarray(arr, "RGBA").save(tiles / f"0{i}.png")
    out = tmp_path / "joins"
    assert (
        joins.main(
            [
                "--tiles",
                str(tmp_path / "tiles"),
                "--out",
                str(out),
                "--terrains",
                "lake",
                "--variants",
                "2",
            ]
        )
        == 0
    )
    names = sorted(p.stem for p in (out / "lake").glob("*.png"))
    kinds = sorted({n.rsplit("_", 1)[0] for n in names})
    assert kinds == sorted(
        ["edge_se", "edge_ne", "edge_n", "vert_e", "vert_ne"]
        + [f"mouth_{m}" for m in joins.MOUTHS]
    )
    loaded = bm.JoinSet.load(out)
    assert ("lake", bm.MOUTH_CODE_BASE + 5) in loaded.groups
    assert "Mouth kinds" in (out / "README.md").read_text()
