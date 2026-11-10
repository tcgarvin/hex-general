"""Tests for scripts/tile-foundry/paintin*.py: sheet/cut registration round
trip, rim-pin exactness, edge-crossing symmetry across neighbours (including a
left-right mirrored one), and the gates firing on constructed bad inputs.

Run: python3 -m pytest scripts/tests/test_paintin.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

FOUNDRY = Path(__file__).resolve().parents[1] / "tile-foundry"
sys.path.insert(0, str(FOUNDRY))

import paintin_geom as geom  # noqa: E402
from hexgeom import axial_to_pixel  # noqa: E402
from paintin_gate import GateConfig, gate_cell  # noqa: E402
from paintin_pin import (  # noqa: E402
    PROFILE_STEP,
    CrossProfile,
    PinConfig,
    extract_profile,
    measure_crossing,
    pin_target,
    pin_tile,
    water_mask,
)
from paintin_sheet import (  # noqa: E402
    MAGENTA,
    CellFit,
    RegistrationError,
    build_sheet,
    cut_cell,
    load_base,
    load_job,
    load_sidecar,
    output_alpha,
    register,
)

GRASS = np.array([100.0, 104.0, 48.0])
WATER = np.array([88.0, 104.0, 110.0])
BANK = np.array([52.0, 50.0, 34.0])


def noise_ground(seed: int, size: int = 128) -> np.ndarray:
    """Olive ground with smooth mottling (no salient features)."""
    rng = np.random.default_rng(seed)
    n = geom.gaussian_blur(rng.normal(0.0, 1.0, (size, size, 1)), 2.0)
    n = n / n.std()
    return np.clip(GRASS + n * 6.0, 0, 255)


def save_png(arr: np.ndarray, path: Path) -> Path:
    Image.fromarray(np.clip(np.round(arr), 0, 255).astype(np.uint8)).save(path)
    return path


def write_job(tmp: Path, layout: str, cells: list[dict], **extra) -> Path:
    job = {"name": "t", "layout": layout, "cells": cells, **extra}
    path = tmp / "job.json"
    path.write_text(json.dumps(job))
    return path


def synthetic_job(tmp: Path, layout: str = "3x3", n: int = 9) -> Path:
    cells = []
    for i in range(n):
        base = save_png(noise_ground(i, 256), tmp / f"b{i}.png")
        cells.append({"name": f"c{i}", "base": base.name})
    return write_job(tmp, layout, cells)


def fake_codex(
    sheet: Image.Image, scale: float, shift: tuple[float, float]
) -> np.ndarray:
    """The sheet rescaled and shifted on magenta, like a Codex output that drifted."""
    w = round(sheet.width * scale)
    # output pixel (u, v) <- sheet ((u - tx) / s, (v - ty) / s)
    out = sheet.transform(
        (w, w),
        Image.Transform.AFFINE,
        (1 / scale, 0, -shift[0] / scale, 0, 1 / scale, -shift[1] / scale),
        resample=Image.Resampling.BICUBIC,
        fillcolor=MAGENTA,
    )
    return np.asarray(out, dtype=np.float64)


# ------------------------------------------------------- sheet / cut ----


@pytest.mark.parametrize(
    "layout,scale,shift",
    [
        ("3x3", 1.07, (3.3, -2.1)),
        ("4x4", 0.93, (-4.6, 5.2)),
        ("5x5", 1.03, (2.2, 1.6)),
        ("6x6", 0.97, (-1.8, 2.5)),
    ],
)
def test_register_recovers_rescaled_shifted_output(tmp_path, layout, scale, shift):
    n = int(layout[0]) ** 2
    sheet_path, side_path = build_sheet(
        load_job(synthetic_job(tmp_path, layout, n)), tmp_path
    )
    sidecar = load_sidecar(side_path)
    out = fake_codex(Image.open(sheet_path), scale, shift)
    reg = register(out, sidecar)
    assert reg.scale == pytest.approx(scale, abs=2e-3)
    assert reg.tx == pytest.approx(shift[0], abs=1.0)
    assert reg.ty == pytest.approx(shift[1], abs=1.0)
    assert max(c.residual_tile_px for c in reg.cells.values()) < 0.25
    assert min(c.iou for c in reg.cells.values()) > 0.98


def test_cut_round_trip_returns_the_base(tmp_path):
    sheet_path, side_path = build_sheet(load_job(synthetic_job(tmp_path)), tmp_path)
    sidecar = load_sidecar(side_path)
    out = fake_codex(Image.open(sheet_path), 1.05, (2.4, -1.7))
    reg = register(out, sidecar)
    inside = geom.tile_distance() > 2.0
    for cell in sidecar["cells"]:
        base = load_base(tmp_path / Path(cell["base"]).name)
        cut = cut_cell(out, reg, cell, base)
        err = np.abs(cut - base.tile)[inside].mean()
        assert err < 1.5, f"{cell['name']}: mean |cut - base| {err:.2f}"


def test_register_fails_loudly_on_a_moved_cell(tmp_path):
    sheet_path, side_path = build_sheet(load_job(synthetic_job(tmp_path)), tmp_path)
    sidecar = load_sidecar(side_path)
    out = fake_codex(Image.open(sheet_path), 1.0, (0.0, 0.0))
    c = sidecar["cells"][4]
    x0, y0 = int(c["cx"] - 180), int(c["cy"] - 160)
    patch = out[y0 : y0 + 320, x0 : x0 + 360].copy()
    out[y0 : y0 + 320, x0 : x0 + 360] = MAGENTA
    out[y0 + 12 : y0 + 332, x0 : x0 + 360] = patch  # cell 4 dropped 12 px
    with pytest.raises(RegistrationError, match="c4"):
        register(out, sidecar)


def test_register_fails_loudly_on_a_missing_cell(tmp_path):
    sheet_path, side_path = build_sheet(load_job(synthetic_job(tmp_path)), tmp_path)
    sidecar = load_sidecar(side_path)
    out = fake_codex(Image.open(sheet_path), 1.0, (0.0, 0.0))
    c = sidecar["cells"][0]
    out[: int(c["cy"] + 200), : int(c["cx"] + 200)] = MAGENTA
    with pytest.raises(RegistrationError, match="not found"):
        register(out, sidecar)


def test_job_validation_rejects_bad_input(tmp_path):
    save_png(noise_ground(0), tmp_path / "b.png")
    bad = write_job(tmp_path, "3x3", [{"name": "x", "base": "missing.png"}])
    with pytest.raises(ValueError, match="does not exist"):
        load_job(bad)
    sharp = write_job(
        tmp_path,
        "3x3",
        [
            {
                "name": "x",
                "base": "b.png",
                "guide": {"kind": "river", "edges": [0, 1], "width": 16},
            }
        ],
        profile={"extract": {"cell": "x"}},
    )
    with pytest.raises(ValueError, match="sharp turn"):
        load_job(sharp)
    no_profile = write_job(
        tmp_path,
        "3x3",
        [
            {
                "name": "x",
                "base": "b.png",
                "guide": {"kind": "river", "edges": [2, 5], "width": 16},
            }
        ],
    )
    with pytest.raises(ValueError, match="profile"):
        load_job(no_profile)


# ----------------------------------------------------------------- pin ----


def test_rim_pin_is_exact_at_the_boundary_and_painted_inside():
    rng = np.random.default_rng(1)
    base = noise_ground(3)
    cut = np.clip(base + rng.normal(0, 30, base.shape), 0, 255)
    cfg = PinConfig()
    res = pin_tile(cut, base, output_alpha(), None, None, cfg)
    d = geom.tile_distance()
    rgb = res.rgba[..., :3].astype(np.float64)
    visible = res.rgba[..., 3] > 0
    rim = visible & (d < cfg.rim_exact_px)
    assert rim.sum() > 300
    assert np.array_equal(rgb[rim], np.round(base[rim]))
    deep = d >= cfg.rim_exact_px + cfg.rim_ramp_px
    assert np.array_equal(rgb[deep], np.round(cut[deep]))
    assert np.array_equal(res.rgba[..., 3], output_alpha())


def make_profile(asymmetric: bool = False) -> CrossProfile:
    """A river cross-section: water core, dark banks, grass shoulders."""
    t = np.arange(-20.0, 20.0 + 1e-9, PROFILE_STEP)
    a = np.abs(t)
    rgb = np.where((a <= 9)[:, None], WATER, np.where((a <= 12)[:, None], BANK, GRASS))
    if asymmetric:
        rgb = rgb + (t[:, None] > 0) * 25.0
    # painted banks are soft; blur the steps over ~1 px like a real profile
    rgb = geom.gaussian_blur(rgb[:, None, :], 1.0 / PROFILE_STEP)[:, 0, :]
    alpha = 1.0 - geom.smoothstep(12.0, 16.0, a)
    # the feature width as the water classifier sees it (blurred banks count a bit)
    width = float((water_mask(rgb[None])[0] > 0.5).sum() * PROFILE_STEP)
    return CrossProfile("river", t, rgb, alpha, width, 12.0, 4.0, {"test": True})


def river_tile(edges: tuple[int, ...], seed: int, profile: CrossProfile) -> np.ndarray:
    guide = geom.Guide("river", edges, 18.0, (88, 104, 108), None)
    base = noise_ground(seed)
    band = geom.render_guide_mask(guide, 128, 128.0)[..., None]
    cut = base * (1 - band) + WATER * band
    return pin_tile(cut, base, output_alpha(), guide, profile, PinConfig()).rgba


def board_samples(
    rgba: np.ndarray, centre, mirror: bool, pts: np.ndarray
) -> np.ndarray:
    """Sample a tile placed with its centre at board `centre` at board points."""
    arr = rgba[..., :3].astype(np.float64)
    if mirror:
        arr = arr[:, ::-1]
    xs = pts[:, 0] - centre[0] + 64.0
    ys = pts[:, 1] - centre[1] + 64.0
    return geom.sample_bilinear(arr, xs, ys)


def shared_edge_points(direction: int, profile: CrossProfile) -> np.ndarray:
    """Board points along tile A's edge `direction`, inside the profile core."""
    mx, my = geom.edge_midpoint(direction, (0.0, 0.0))
    tx, ty = geom.edge_tangent(direction)
    t = np.linspace(-profile.core_half + 0.5, profile.core_half - 0.5, 41)
    return np.stack([mx + t * tx, my + t * ty], axis=-1)


@pytest.mark.parametrize(
    "a_edges,direction,b_edges,b_mirror",
    [
        ((2, 5), 2, (2, 5), False),  # vertical straight, N neighbour
        ((2, 5), 5, (0, 2), False),  # S neighbour is a N+SE turn
        ((0, 3), 0, (0, 3), False),  # diagonal straight, SE neighbour
        ((0, 3), 3, (0, 2), False),  # NW neighbour is a turn using its SE edge
        ((0, 3), 0, (1, 4), True),  # SE neighbour is a mirrored straight_d art
        ((2, 5), 2, (3, 5), True),  # N neighbour: turn_s_nw art mirrored -> S + NE
        ((0, 2), 0, (1, 3), True),  # turn's SE neighbour: (1,3) art mirrored -> 3 + 1
    ],
)
def test_edge_crossing_matches_across_neighbours(a_edges, direction, b_edges, b_mirror):
    profile = make_profile()
    tile_a = river_tile(a_edges, 11, profile)
    tile_b = river_tile(b_edges, 23, profile)
    dq, dr = geom_dirs()[direction]
    b_centre = axial_to_pixel(dq, dr, 64.0)
    # the board edge B shares with A, as B's art edge after its mirror
    board_edge = (direction + 3) % 6
    art_edge = geom.MIRROR_EDGE[board_edge] if b_mirror else board_edge
    assert art_edge in b_edges
    pts = shared_edge_points(direction, profile)
    from_a = board_samples(tile_a, (0.0, 0.0), False, pts)
    from_b = board_samples(tile_b, b_centre, b_mirror, pts)
    assert np.abs(from_a - from_b).max() <= 3.0
    assert np.abs(from_a - from_b).mean() <= 1.0


def test_unsymmetrised_profile_breaks_a_mirrored_neighbour_on_vertical_edges():
    """Why profiles are symmetrised: the L-R mirror reverses the canonical tangent
    on the N/S edges (it preserves it on the diagonal ones), so an asymmetric
    profile disagrees across a N/S edge whose neighbour is mirrored."""
    profile = make_profile(asymmetric=True)
    tile_a = river_tile((2, 5), 11, profile)
    tile_b = river_tile((3, 5), 23, profile)  # turn_s_nw art, mirrored -> S + NE
    b_centre = axial_to_pixel(*geom_dirs()[2], 64.0)
    pts = shared_edge_points(2, profile)
    diff = np.abs(
        board_samples(tile_a, (0, 0), False, pts)
        - board_samples(tile_b, b_centre, True, pts)
    )
    assert diff.max() > 20.0
    # ...while across a diagonal edge the same asymmetric profile still agrees
    tile_c = river_tile((1, 4), 23, profile)
    c_centre = axial_to_pixel(*geom_dirs()[0], 64.0)
    tile_d = river_tile((0, 3), 11, profile)
    pts = shared_edge_points(0, profile)
    diag = np.abs(
        board_samples(tile_d, (0, 0), False, pts)
        - board_samples(tile_c, c_centre, True, pts)
    )
    assert diag.max() <= 3.0


def test_extracted_profile_is_symmetric_and_centred():
    base = noise_ground(5)
    guide = geom.Guide("river", (2, 5), 18.0, (88, 104, 108), None)
    band = geom.render_guide_mask(guide, 128, 128.0)
    shifted = np.roll(band, 2, axis=1)[..., None]  # painted 2 px east of the guide
    cut = base * (1 - shifted) + WATER * shifted
    prof = extract_profile(cut, base, "river", (2, 5), {"test": True})
    assert np.allclose(prof.rgb, prof.rgb[::-1])
    assert prof.feature_width == pytest.approx(18.0, abs=1.0)
    rgb, _ = prof.sample(np.array([0.0]))
    assert np.abs(rgb[0] - WATER).max() < 3.0


def geom_dirs():
    from hexgeom import AXIAL_DIRECTIONS

    return AXIAL_DIRECTIONS


# --------------------------------------------------------------- gates ----


def good_fit() -> CellFit:
    return CellFit("x", (0, 0), (0, 0), 1.0, 0.0, 0.05, 0.995)


def run_gates(cut, base, guide, profile, fit=None):
    cfg = PinConfig()
    res = pin_tile(cut, base, output_alpha(), guide, profile, cfg)
    target, _ = pin_target(base, guide, profile, cfg)
    checks = gate_cell(
        fit or good_fit(),
        cut,
        base,
        res.rgba,
        target,
        guide,
        profile,
        cfg,
        GateConfig(),
        {},
    )
    return {c.name: c for c in checks}


def disc(cx: float, cy: float, r: float) -> np.ndarray:
    xs, ys = geom.pixel_grid(128)
    return (np.hypot(xs - cx, ys - cy) <= r)[..., None].astype(np.float64)


def test_gates_pass_a_clean_non_oriented_tile():
    base = noise_ground(7)
    forest = disc(64, 64, 34)
    cut = base * (1 - forest) + np.array([30.0, 50.0, 30.0]) * forest
    checks = run_gates(cut, base, None, None)
    assert all(c.passed for c in checks.values() if c.hard), checks


def test_rim_clear_fires_on_a_sliced_feature():
    base = noise_ground(7)
    tree = disc(64, 12, 14)  # a tree clump crossing the N edge
    cut = base * (1 - tree) + np.array([30.0, 50.0, 30.0]) * tree
    checks = run_gates(cut, base, None, None)
    assert not checks["rim_clear"].passed
    assert checks["rim_clear"].value > 0.03


def test_rim_touch_fires_on_a_single_sliced_tree():
    base = noise_ground(7)
    tree = disc(64, 9, 3.5)  # one crown sitting on the N edge
    cut = base * (1 - tree) + np.array([30.0, 50.0, 30.0]) * tree
    checks = run_gates(cut, base, None, None)
    assert checks["rim_clear"].passed  # too small to move the band share...
    assert not checks["rim_touch"].passed  # ...but it is sliced by the edge


def test_registration_gate_fires():
    base = noise_ground(7)
    fit = CellFit("x", (0, 0), (3, 0), 1.0, 3.0, 1.4, 0.95)
    checks = run_gates(base.copy(), base, None, None, fit)
    assert not checks["registration_residual"].passed
    assert not checks["registration_iou"].passed


def oriented_cut(edges, offset: float, extra: np.ndarray | None = None):
    base = noise_ground(9)
    guide = geom.Guide("river", edges, 18.0, (88, 104, 108), None)
    band = geom.render_guide_mask(guide, 128, 128.0)
    band = np.roll(band, int(offset), axis=1)
    if extra is not None:
        band = np.maximum(band, extra[..., 0])
    cut = base * (1 - band[..., None]) + WATER * band[..., None]
    return cut, base, guide


def test_oriented_gates_pass_a_followed_band():
    cut, base, guide = oriented_cut((2, 5), 0)
    checks = run_gates(cut, base, guide, make_profile())
    assert all(c.passed for c in checks.values() if c.hard), {
        k: c.detail for k, c in checks.items() if not c.passed
    }


def test_crossing_gate_fires_when_codex_drifts():
    cut, base, guide = oriented_cut((2, 5), 7)
    checks = run_gates(cut, base, guide, make_profile())
    assert not checks["cross_cut_e2"].passed
    assert abs(checks["cross_cut_e2"].value) > 5.0
    # the pin still forces the edge itself onto the profile
    assert checks["cross_final_e2"].passed


def test_leak_gate_fires_on_water_at_a_non_connected_edge():
    xs, ys = geom.pixel_grid(128)
    mx, my = geom.edge_midpoint(0)
    pool = (np.hypot(xs - mx, ys - my) <= 8)[..., None].astype(np.float64)
    cut, base, guide = oriented_cut((2, 5), 0, extra=pool)
    checks = run_gates(cut, base, guide, make_profile())
    assert not checks["leak_cut"].passed
    assert checks["leak_cut"].value > 4


def test_measure_crossing_reports_offset_and_width():
    guide = geom.Guide("river", (0, 3), 20.0, (0, 0, 0), None)
    band = geom.render_guide_mask(guide, 128, 128.0)
    c = measure_crossing(band, 0, 2.0, 6.0)
    assert c is not None
    assert c.centre == pytest.approx(0.0, abs=0.5)
    assert c.width == pytest.approx(20.0, abs=1.0)
    assert measure_crossing(band, 2, 2.0, 6.0) is None


def test_guide_crosses_each_edge_straight_at_the_midpoint():
    for edges in ((2, 5), (0, 3), (0, 2), (1, 3), (0, 4, 5), (1,)):
        guide = geom.Guide("river", edges, 16.0, (0, 0, 0), None)
        band = geom.render_guide_mask(guide, 512, 512.0)
        big = np.asarray(
            Image.fromarray((band * 255).astype(np.uint8)).resize(
                (128, 128), Image.Resampling.BOX
            )
        )
        for e in edges:
            c = measure_crossing(big / 255.0, e, 0.5, 10.0)
            assert c is not None, (edges, e)
            assert abs(c.centre) < 0.5 and c.width == pytest.approx(16.0, abs=1.0), (
                edges,
                e,
                c,
            )
    assert math.isclose(geom.apothem(), 55.4256, rel_tol=1e-4)


# ------------------------------------------- natural guides (r1 oriented) ----

ALL_SHAPES = [
    (2, 5), (0, 3), (0, 2), (3, 5), (0, 4), (1, 3), (2,), (5,), (0,), (1,),
    (0, 2, 5), (1, 2, 5), (0, 1, 3), (0, 2, 3), (0, 3, 4), (0, 3, 5),
    (0, 2, 4), (1, 3, 5), (1, 2, 3), (0, 1, 2), (0, 1, 5), (0, 4, 5),
]  # fmt: skip


def natural(edges, kind="river", seed=3, **kw):
    style = (
        dict(width=22.0, meander=9.0, breathe=0.12, ragged=0.9, round_px=7.0)
        if kind == "river"
        else dict(width=9.5, meander=3.0, ragged=0.4, round_px=3.0)
    )
    style.update(kw)
    width = style.pop("width")
    return geom.Guide(kind, tuple(edges), width, (67, 81, 77), None, seed=seed, **style)


@pytest.mark.parametrize("edges", ALL_SHAPES)
@pytest.mark.parametrize("kind", ["river", "road"])
def test_natural_guide_crosses_connected_edges_at_full_width(edges, kind):
    guide = natural(edges, kind)
    band = geom.render_guide_mask(guide, 128, 128.0)
    for e in edges:
        c = measure_crossing(band, e, 0.5, 8.0)
        assert c is not None, (edges, e)
        assert abs(c.centre) < 0.6, (edges, e, c)
        assert c.width == pytest.approx(guide.width, abs=1.0), (edges, e, c)


@pytest.mark.parametrize("edges", ALL_SHAPES)
@pytest.mark.parametrize("seed", [1, 2])
def test_natural_guide_stays_off_unconnected_edges(edges, seed):
    guide = natural(edges, seed=seed)
    band = geom.render_guide_mask(guide, 128, 128.0)
    xs, ys = geom.pixel_grid(128)
    d = geom.tile_distance()
    near_rim = (d >= 0) & (d < 8.0)
    allowed = np.zeros_like(near_rim)
    for e in edges:
        depth, t = geom.edge_frame(e, xs, ys)
        allowed |= (depth < 12.0) & (np.abs(t) < guide.width / 2.0 + 3.0)
    assert band[near_rim & ~allowed].max(initial=0.0) < 0.5


def test_natural_guide_meanders_inside_the_hex():
    plain = geom.render_guide_mask(natural((2, 5), meander=0.0, ragged=0.0), 128, 128.0)
    wavy = geom.render_guide_mask(natural((2, 5), ragged=0.0), 128, 128.0)
    cols = np.arange(128) + 0.5
    centre_x = [(wavy[y] * cols).sum() / wavy[y].sum() for y in range(30, 98)]
    assert max(abs(x - 64.0) for x in centre_x) > 4.0  # swings away from the axis
    assert np.abs(plain[30:98] - plain[30:98, ::-1]).max() < 0.05  # straight is symmetric


def test_fork_plan_takes_widest_pair_and_mirror_axis_tributary():
    assert geom.fork_plan((0, 2, 5)) == ((2, 5), 0)  # straight course + branch
    assert geom.fork_plan((0, 2, 4)) == ((0, 4), 2)  # Y: tributary on the N axis
    assert geom.fork_plan((1, 3, 5)) == ((1, 3), 5)
    assert geom.fork_plan((1, 2, 3)) == ((1, 3), 2)  # fan: middle edge joins


def test_confluence_rounding_fills_only_near_the_junction():
    sharp = geom.render_guide_mask(natural((0, 2, 5), ragged=0.0, round_px=0.0), 128, 128.0)
    round_ = geom.render_guide_mask(natural((0, 2, 5), ragged=0.0), 128, 128.0)
    added = np.clip(round_ - sharp, 0, None)
    assert added.sum() > 5.0  # some fill in the acute wedge
    assert added.sum() < 0.08 * sharp.sum()  # but no pool
    d = geom.tile_distance()
    assert added[(d >= 0) & (d < 12.0)].sum() < 1.0  # nothing near the rim inside the hex


def test_end_cap_tapers_to_a_thread_and_road_end_stays_round():
    river = geom.natural_strokes(natural((2,)))[0]
    assert river.half[0] == pytest.approx(11.0) and river.half[-1] < 1.0
    road = geom.natural_strokes(natural((2,), kind="road", end_taper=0.8))[0]
    assert road.half[-1] == pytest.approx(0.8 * 9.5 / 2.0, abs=0.05)


def test_pond_is_inside_the_hex_and_touches_no_edge():
    band = geom.render_guide_mask(natural((), pond=48.0), 128, 128.0)
    d = geom.tile_distance()
    assert band[64, 64] > 0.99
    assert band[d < 20.0].max() < 0.5
    assert 0.08 < band.mean() < 0.2


def test_marker_centre_and_rotation():
    m = geom.Marker("bridge", (2, 5), 40.0, 10.0, (0, 0, 0), (64.0, 80.0), 90.0)
    mask = geom.render_marker_mask(m, 128, 128.0) > 0.5
    ys, xs = np.nonzero(mask)
    assert ys.mean() + 0.5 == pytest.approx(80.0, abs=0.6)
    assert (ys.max() - ys.min()) > 3 * (xs.max() - xs.min())  # turned north-south


def test_guide_style_round_trips_through_a_sheet(tmp_path):
    base = save_png(noise_ground(1, 256), tmp_path / "b.png")
    style = {"meander": 9, "ragged": 0.9, "round_px": 7, "seed": 4, "hold_edges": [2, 5],
             "hold_half": 16}  # fmt: skip
    cells = [{"name": "f", "base": base.name,
              "guide": {"kind": "river", "edges": [0, 2, 5], "width": 22, **style}}]  # fmt: skip
    job = load_job(write_job(tmp_path, "4x4", cells, profile={"extract": {"cell": "f"}}))
    assert job.cells[0].guide.hold_edges == (2, 5) and job.cells[0].guide.seed == 4
    _, side = build_sheet(job, tmp_path / "out")
    from paintin_sheet import cell_guide

    assert cell_guide(load_sidecar(side)["cells"][0]) == job.cells[0].guide
    bad = [{**cells[0], "guide": {**cells[0]["guide"], "meander": 99}}]
    with pytest.raises(Exception, match="meander"):
        load_job(write_job(tmp_path, "4x4", bad, profile={"extract": {"cell": "f"}}))


def test_water_mask_rejects_warm_banks_and_stone_but_keeps_slate_water():
    px = np.array(
        [[[67.0, 81.0, 77.0], [138.0, 134.0, 91.0], [120.0, 112.0, 100.0], [125.0, 111.0, 79.0]]]
    )
    w = water_mask(px)[0]
    assert w[0] > 0.9  # concept slate water
    assert w[1] < 0.05 and w[2] < 0.05 and w[3] < 0.05  # khaki lip, warm grey stone, deck


def test_normalise_feature_moves_the_water_mean_and_leaves_ground():
    from paintin_pin import normalise_feature

    cut, base, guide = oriented_cut((2, 5), 0)
    cfg = PinConfig(feature_strength=1.0, feature_rgb=(67.0, 81.0, 77.0))
    out, rec = normalise_feature(cut, base, guide, cfg)
    core = water_mask(cut) > 0.9
    assert np.abs(out[core].mean(axis=0) - np.array([67.0, 81.0, 77.0])).max() < 3.0
    ground = water_mask(cut) < 0.01
    assert np.array_equal(out[ground], cut[ground])
    assert rec["offset"]


def test_hold_edges_keep_the_base_crossing_and_paint_region_keeps_the_base():
    base = noise_ground(4)
    cut = noise_ground(9) + 30.0  # Codex repainted everything, brighter
    guide = geom.Guide(
        "road", (0, 4), 9.5, (161, 140, 96), None, ragged=0.1, hold_edges=(2, 5), hold_half=16.0
    )
    prof = make_profile()
    road_prof = CrossProfile("road", prof.t, prof.rgb, prof.alpha, 9.0, 8.0, 3.0, {"t": 1})
    cfg = PinConfig(paint_region_px=4.0)
    res = pin_tile(cut, base, output_alpha(), guide, road_prof, cfg)
    xs, ys = geom.pixel_grid(128)
    depth, t = geom.edge_frame(2, xs, ys)
    held = (depth > 0) & (depth < 3.0) & (np.abs(t) < 15.0)
    assert np.abs(res.rgba[..., :3][held] - np.round(base[held])).max() <= 1
    region = geom.render_guide_mask(guide, 128, 128.0)
    far = (geom.max_filter(region, 12) < 0.01) & (geom.tile_distance() > 0)
    assert np.abs(res.rgba[..., :3][far] - np.round(base[far])).max() <= 1


def test_tone_match_removes_a_slow_ground_offset():
    from paintin_pin import tone_match

    base = noise_ground(6)
    ramp = np.linspace(-8.0, 8.0, 128)[None, :, None]
    cut = base + ramp + 0.0
    out, _ = tone_match(cut, base, None, PinConfig(tone_sigma=6.0))
    assert np.abs(out - base).mean() < 1.0 < np.abs(cut - base).mean()
