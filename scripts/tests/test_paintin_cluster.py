"""Tests for scripts/tile-foundry/paintin_cluster.py (multi-hex cluster paint-in).

Run: python3 -m pytest scripts/tests
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tile-foundry"))

import paintin_cluster as pc  # noqa: E402
from paintin_geom import hex_inset_distance, pixel_grid  # noqa: E402
from paintin_sheet import JobError, RegistrationError  # noqa: E402

RHOMBUS = [[0, 0], [1, 0], [0, 1], [1, 1]]
TRIANGLE = [[0, 0], [1, 0], [0, 1]]


def ground_source(path: Path) -> Path:
    """A smooth-but-textured ground image at tile scale (1:1)."""
    rng = np.random.default_rng(3)
    ys, xs = np.mgrid[0:600, 0:800].astype(np.float64)
    base = np.stack(
        [100 + 10 * np.sin(xs / 23), 104 + 10 * np.cos(ys / 17), 46 + 0 * xs], axis=-1
    )
    base += rng.normal(0, 3, base.shape)
    Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).save(path)
    return path


def write_job(tmp: Path, clusters: list[dict], **extra) -> Path:
    ground_source(tmp / "ground.png")
    job = {
        "name": "t",
        "canvas": [1536, 1024],
        "hex_width": 340,
        "ground": "ground.png",
        "clusters": clusters,
        "pin": {"restore": False},
        "gates": {},
        **extra,
    }
    path = tmp / "job.json"
    path.write_text(json.dumps(job))
    return path


def two_clusters(tmp: Path) -> Path:
    return write_job(
        tmp,
        [
            {"name": "four", "centre": [400, 512], "members": RHOMBUS, "ground_origin": [150, 150]},
            {"name": "three", "centre": [1130, 512], "members": TRIANGLE, "ground_origin": [500, 150]},
        ],
    )


def build(tmp: Path) -> tuple[dict, Image.Image]:
    _, side = pc.build_cluster_sheet(pc.load_cluster_job(two_clusters(tmp)), tmp / "work")
    sidecar = json.loads(side.read_text())
    return sidecar, Image.open(tmp / "work" / "t.sheet.png").convert("RGB")


def paint_across(sheet: Image.Image, sidecar: dict) -> Image.Image:
    """A smooth dark feature crossing every internal edge of each cluster."""
    out = sheet.copy()
    d = ImageDraw.Draw(out)
    for c in sidecar["clusters"]:
        x, y = c["centre"]
        d.ellipse([x - 110, y - 150, x + 110, y + 150], fill=(70, 60, 35))
    return out.filter(__import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(3))


def test_outer_distance_ignores_internal_edges() -> None:
    xs = np.array([[64.0 + 96 / 2]])  # midpoint between (0,0) and (1,0) centres
    ys = np.array([[64.0 + 55.43 / 2]])
    members = [(0, 0), (1, 0)]
    d = pc.outer_distance(xs, ys, members, (0, 0), (64.0, 64.0), 64.0)
    # the shared edge is not boundary: the point is well inside the union
    assert d[0, 0] > 20
    alone = hex_inset_distance(xs, ys)
    assert alone[0, 0] < 1.0  # ...but it sits on hex (0,0)'s own boundary


def test_members_must_be_connected(tmp_path: Path) -> None:
    job = write_job(
        tmp_path,
        [{"name": "x", "centre": [400, 512], "members": [[0, 0], [2, 0]], "ground_origin": [150, 150]}],
    )
    with pytest.raises(JobError, match="connected"):
        pc.load_cluster_job(job)


def test_clusters_must_fit_and_not_touch(tmp_path: Path) -> None:
    job = write_job(
        tmp_path,
        [
            {"name": "a", "centre": [400, 512], "members": RHOMBUS, "ground_origin": [150, 150]},
            {"name": "b", "centre": [700, 512], "members": TRIANGLE, "ground_origin": [500, 150]},
        ],
    )
    with pytest.raises(JobError, match="closer"):
        pc.load_cluster_job(job)


def test_cluster_cut_round_trip_returns_the_base(tmp_path: Path) -> None:
    sidecar, sheet = build(tmp_path)
    raw = tmp_path / "raw.png"
    # a drifted output: rescaled and shifted on magenta
    s, sx, sy = 1.01, 3.0, -2.0
    w = round(sheet.width * s)
    h = round(sheet.height * s)
    sheet.transform(
        (w, h), Image.Transform.AFFINE, (1 / s, 0, -sx / s, 0, 1 / s, -sy / s),
        resample=Image.Resampling.BICUBIC, fillcolor=(255, 0, 255),
    ).save(raw)
    report = pc.process(sidecar, raw, tmp_path / "work")
    assert report["registration"]["max_residual_tile_px"] < 0.3
    assert report["registration"]["min_iou"] > 0.98
    inside = hex_inset_distance(*pixel_grid(128)) > 2.0
    for c in sidecar["clusters"]:
        for m in c["members"]:
            cut = np.asarray(Image.open(tmp_path / "work" / "cut" / m["output"]), dtype=float)
            base = np.asarray(Image.open(pc.repo_path(m["base"])), dtype=float)
            assert np.abs(cut - base)[inside].mean() < 3.0


def test_outer_rim_equals_base_and_internal_edges_match(tmp_path: Path) -> None:
    sidecar, sheet = build(tmp_path)
    raw = tmp_path / "raw.png"
    paint_across(sheet, sidecar).save(raw)
    report = pc.process(sidecar, raw, tmp_path / "work")
    for c in sidecar["clusters"]:
        rep = report["clusters"][c["name"]]
        tiles = {}
        for i, m in enumerate(c["members"]):
            final = np.asarray(Image.open(tmp_path / "work" / "tiles" / c["name"] / f"h{i}.png"))
            base = np.asarray(Image.open(pc.repo_path(m["base"])))
            dist = pc.member_distance(c, m)
            rim = (final[..., 3] > 0) & (dist < 1.5)
            assert rim.any() and np.array_equal(final[..., :3][rim], base[rim])
            assert rep["members"][f"h{i}"]["checks"][0]["value"] == 0.0
            tiles[tuple(m["offset"])] = final
        assert rep["seams"], "a multi-hex cluster has internal edges"
        for seam in rep["seams"]:
            # the dark feature crosses every internal edge; both tiles agree there
            assert seam["px"] > 0 and seam["mean"] < 2.0, seam
        # and the painted feature really is on both sides (not pinned away)
        a, b = tiles[(0, 0)], tiles[(1, 0)]
        assert a[..., :3].astype(int).sum(-1).min() < 250 and b[..., :3].astype(int).sum(-1).min() < 250


def test_register_fails_loudly_on_a_moved_cluster(tmp_path: Path) -> None:
    sidecar, sheet = build(tmp_path)
    arr = np.asarray(sheet).copy()
    c = sidecar["clusters"][1]
    x, y = (int(v) for v in c["centre"])
    patch = arr[y - 320 : y + 320, x - 320 : x + 320].copy()
    arr[y - 320 : y + 320, x - 320 : x + 320] = (255, 0, 255)
    arr[y - 320 + 14 : y + 320 + 14, x - 320 : x + 320] = patch
    with pytest.raises(RegistrationError, match="three"):
        pc.register_clusters(arr.astype(float), sidecar)


@pytest.mark.parametrize("members", [TRIANGLE, RHOMBUS, [[0, 0], [1, 0]]])
def test_inscribed_guide_keeps_its_margin_and_varies_its_inset(members: list[list[int]]) -> None:
    mem = [tuple(m) for m in members]
    poly = pc.inscribed_guide(mem, 14.0)  # type: ignore[arg-type]
    pts = pc._polygon_samples(poly)
    d = pc.outer_distance(pts[:, 0], pts[:, 1], mem, (0, 0), (0.0, 0.0), pc.R_TILE)  # type: ignore[arg-type]
    assert d.min() >= 14.0 - 1e-6
    # a straight-sided polygon on a lobed outline: the inset is not constant
    assert d.max() - d.min() > 8.0


def test_guide_is_drawn_on_the_sheet_but_not_the_bases(tmp_path: Path) -> None:
    job = write_job(
        tmp_path,
        [{"name": "four", "centre": [400, 512], "members": RHOMBUS, "ground_origin": [150, 150], "guide": {"margin": 16}}],
    )
    (tmp_path / "p").mkdir()
    plain = write_job(
        tmp_path / "p",
        [{"name": "four", "centre": [400, 512], "members": RHOMBUS, "ground_origin": [150, 150]}],
    )
    a_sheet, a_side = pc.build_cluster_sheet(pc.load_cluster_job(job), tmp_path / "a")
    b_sheet, _ = pc.build_cluster_sheet(pc.load_cluster_job(plain), tmp_path / "b")
    a = np.asarray(Image.open(a_sheet), dtype=int)
    b = np.asarray(Image.open(b_sheet), dtype=int)
    assert (np.abs(a - b).sum(-1) > 20).sum() > 500  # the line is there
    assert json.loads(a_side.read_text())["clusters"][0]["guide_tile_px"]
    for base in (tmp_path / "a" / "bases" / "four").glob("*.png"):
        assert np.array_equal(
            np.asarray(Image.open(base)), np.asarray(Image.open(tmp_path / "b" / "bases" / "four" / base.name))
        )


@pytest.mark.parametrize("members", [TRIANGLE, RHOMBUS])
def test_blob_guide_keeps_its_margin_is_curved_and_seeded(members: list[list[int]]) -> None:
    mem = [tuple(m) for m in members]
    a = pc.blob_guide(mem, 12.0, seed=1, jitter=0.3)  # type: ignore[arg-type]
    b = pc.blob_guide(mem, 12.0, seed=2, jitter=0.3)  # type: ignore[arg-type]
    pts = pc._polygon_samples(a)
    d = pc.outer_distance(pts[:, 0], pts[:, 1], mem, (0, 0), (0.0, 0.0), pc.R_TILE)  # type: ignore[arg-type]
    assert d.min() >= 12.0 - 0.75  # polyline chords cut slightly inside the sampled cap
    assert d.max() - d.min() > 10.0  # close to the edge in places, well inside in others
    # no long straight runs: turning angle between consecutive chords stays small but non-zero
    v = np.diff(np.array(a + a[:1]), axis=0)
    ang = np.abs(np.diff(np.unwrap(np.arctan2(v[:, 1], v[:, 0]))))
    assert ang.max() < 0.5 and (ang > 1e-3).mean() > 0.8
    assert not np.allclose(np.array(a), np.array(b))


def _angle(p: tuple[float, float], pole: tuple[float, float]) -> float:
    return float(np.degrees(np.arctan2(p[1] - pole[1], p[0] - pole[0])) % 360)


@pytest.mark.parametrize(("members", "arc", "start"), [([[0, 0]], 0.5, 150.0), ([[0, 0], [1, 0]], 0.75, 75.0)])
def test_split_guide_arc_keeps_the_requested_share_and_direction(
    members: list[list[int]], arc: float, start: float
) -> None:
    mem = [tuple(m) for m in members]
    loop = pc.blob_guide(mem, 11.0, seed=4, jitter=0.25)  # type: ignore[arg-type]
    pole = pc.union_centroid(mem)  # type: ignore[arg-type]
    solid, rest = pc.split_guide_arc(loop, pole, arc, start)
    # every solid point lies in the requested angular window, every other one outside it
    assert all((_angle(p, pole) - start) % 360 < 360 * arc + 1e-6 for p in solid)
    assert all((_angle(p, pole) - start) % 360 >= 360 * arc - 1e-6 for p in rest[1:-1])
    assert len(solid) + len(rest) - 2 == len(loop)
    assert abs(len(solid) / len(loop) - arc) < 0.02  # blob points are evenly spaced in angle
    # the remainder closes the loop: it starts where the solid arc ends and ends where it starts
    assert rest[0] == solid[-1] and rest[-1] == solid[0]
    # the solid arc is one contiguous run: consecutive points are neighbours on the loop
    idx = [loop.index(p) for p in solid]
    assert all((b - a) % len(loop) == 1 for a, b in zip(idx, idx[1:]))


def test_split_guide_arc_full_loop_and_bad_arc() -> None:
    loop = pc.blob_guide([(0, 0)], 11.0, seed=1, jitter=0.2)
    solid, rest = pc.split_guide_arc(loop, (0.0, 0.0), 1.0, 0.0)
    assert solid == loop and rest == []
    with pytest.raises(ValueError, match="arc"):
        pc.split_guide_arc(loop, (0.0, 0.0), 0.0, 0.0)


def test_dash_polyline_alternates_dash_and_gap() -> None:
    line = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]  # 20 long, with a corner
    dashes = pc.dash_polyline(line, 3.0, 2.0)
    lengths = [sum(np.hypot(*np.diff(np.array(d), axis=0).T)) for d in dashes]
    assert len(dashes) == 4  # 0-3, 5-8, 10-13 (round the corner), 15-18
    assert np.allclose(lengths, 3.0)
    assert dashes[2][0] == (10.0, 0.0) and dashes[2][-1] == (10.0, 3.0)
    with pytest.raises(ValueError):
        pc.dash_polyline(line, 0.0, 2.0)


def _guide_ink(tmp: Path, guide: dict | None, sub: str) -> np.ndarray:
    """|sheet with guide - plain sheet| summed over RGB, for one single-hex cluster."""
    (tmp / sub).mkdir()
    cl: dict = {"name": "one", "centre": [400, 512], "members": [[0, 0]], "ground_origin": [150, 150]}
    if guide is not None:
        cl["guide"] = guide
    sheet, _ = pc.build_cluster_sheet(pc.load_cluster_job(write_job(tmp / sub, [cl])), tmp / sub / "w")
    return np.asarray(Image.open(sheet), dtype=int)


def test_partial_guide_draws_only_its_arc_and_a_fainter_dashed_rest(tmp_path: Path) -> None:
    plain = _guide_ink(tmp_path, None, "plain")
    full = np.abs(_guide_ink(tmp_path, {"margin": 11, "shape": "blob", "seed": 2}, "full") - plain).sum(-1)
    half = {"margin": 11, "shape": "blob", "seed": 2, "arc": 0.5, "start_deg": 180}
    solid = np.abs(_guide_ink(tmp_path, {**half, "rest": "none"}, "solid") - plain).sum(-1)
    dashed = np.abs(_guide_ink(tmp_path, {**half, "rest": "dashed"}, "dashed") - plain).sum(-1)
    # arc 180..360 deg = the northern half (y grows down): ink above the centre only
    cy = 512
    north, south = solid[: cy - 4], solid[cy + 4 :]
    assert (north > 20).sum() > 300 and (south > 20).sum() == 0
    # the solid half is the same line as the full loop's northern half
    a, b = solid[: cy - 4] > 20, full[: cy - 4] > 20
    assert (a & b).sum() / (a | b).sum() > 0.95
    # dashed: the southern half now carries a fainter, broken trace
    s_dash = dashed[cy + 4 :]
    assert (s_dash > 10).sum() > 50
    assert s_dash.max() < 0.9 * full[cy + 4 :].max()
    assert (s_dash > 10).sum() < 0.8 * (full[cy + 4 :] > 10).sum()


def test_partial_guide_is_recorded_in_the_sidecar(tmp_path: Path) -> None:
    job = write_job(
        tmp_path,
        [{"name": "one", "centre": [400, 512], "members": [[0, 0]], "ground_origin": [150, 150],
          "guide": {"margin": 11, "shape": "blob", "arc": 0.5, "start_deg": 150, "rest": "dashed"}}],
    )
    _, side = pc.build_cluster_sheet(pc.load_cluster_job(job), tmp_path / "w")
    c = json.loads(side.read_text())["clusters"][0]
    assert c["guide_tile_px"] and c["guide_arc"]["solid_tile_px"] and c["guide_arc"]["rest_tile_px"]
    assert c["guide_arc"]["arc"] == 0.5 and c["guide_arc"]["rest"] == "dashed"


@pytest.mark.parametrize(
    ("guide", "match"),
    [
        ({"margin": 11, "shape": "blob", "arc": 0}, "arc"),
        ({"margin": 11, "shape": "blob", "arc": 1.5}, "arc"),
        ({"margin": 11, "shape": "blob", "arc": True}, "arc"),
        ({"margin": 11, "shape": "hull", "arc": 0.5}, "partial"),
        ({"margin": 11, "shape": "blob", "arc": 0.5, "rest": "dotted"}, "rest"),
        ({"margin": 11, "shape": "blob", "arc": 0.5, "start_deg": "north"}, "start_deg"),
        ({"margin": 11, "shape": "blob", "start_deg": 90}, "only apply"),
        ({"margin": 11, "shape": "blob", "arcs": 0.5}, "guide keys"),
        ({"margin": 11, "shape": "blob", "arc": 0.5, "rest_rgb": [1, 2, 3]}, "rest dashed"),
        ({"margin": 11, "shape": "blob", "arc": 0.5, "rest": "dashed", "rest_rgb": [1, 2, 300]}, "rest_rgb"),
        ({"margin": 11, "shape": "blob", "arc": 0.5, "rest": "dashed", "rest_rgb": [1, 2]}, "rest_rgb"),
        ({"margin": 11, "shape": "blob", "arc": 0.5, "rest": "dashed", "rest_alpha": 0}, "rest_alpha"),
    ],
)
def test_partial_guide_job_validation(tmp_path: Path, guide: dict, match: str) -> None:
    job = write_job(
        tmp_path,
        [{"name": "one", "centre": [400, 512], "members": [[0, 0]], "ground_origin": [150, 150], "guide": guide}],
    )
    with pytest.raises(JobError, match=match):
        pc.load_cluster_job(job)


def test_dashed_rest_takes_its_own_colour(tmp_path: Path) -> None:
    half = {"margin": 11, "shape": "blob", "seed": 2, "arc": 0.5, "start_deg": 180, "rest": "dashed"}
    dark = _guide_ink(tmp_path, half, "dark")
    pale = _guide_ink(tmp_path, {**half, "rest_rgb": [200, 170, 110], "rest_alpha": 0.8}, "pale")
    plain = _guide_ink(tmp_path, None, "plain")
    cy = 512
    # north (solid arc) identical; south (dashed rest) darker than grass in one, lighter in the other
    assert np.array_equal(dark[: cy - 16], pale[: cy - 16])  # clear of the arc junctions
    lum = lambda a: a[cy + 4 :].astype(float).sum(-1)  # noqa: E731
    changed = np.abs(lum(pale) - lum(plain)) > 30
    assert changed.sum() > 50
    assert (lum(pale) - lum(plain))[changed].mean() > 0 > (lum(dark) - lum(plain))[changed].mean()
