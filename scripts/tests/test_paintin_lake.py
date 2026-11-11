"""Tests for scripts/tile-foundry/paintin_lake.py: mask helpers, the shore field
(water clear of land edges, crossings that meet across every W edge), W0's exact
lattice periodicity, the base and pin (rim exact, water edges pinned to W0,
variance-preserving cross-fade), and the per-hexagon prompt.

Run: python3 -m pytest scripts/tests/test_paintin_lake.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

FOUNDRY = Path(__file__).resolve().parents[1] / "tile-foundry"
sys.path.insert(0, str(FOUNDRY))

import paintin_lake as lake  # noqa: E402
from hexgeom import AXIAL_DIRECTIONS, axial_to_pixel  # noqa: E402
from paintin_geom import (  # noqa: E402
    apothem,
    edge_frame,
    gaussian_blur,
    pixel_grid,
    tile_distance,
)

GRASS = np.array([100.0, 104.0, 48.0])


def smooth_noise(seed: int, sigma: float = 1.5, amp: float = 8.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = gaussian_blur(rng.normal(0.0, 1.0, (128, 128, 1)), sigma)
    return n / n.std() * amp


def periodic_water(seed: int = 3) -> np.ndarray:
    """A small exactly periodic W0 (few frequencies: fast)."""
    size = 64
    spec = np.zeros((size, size))
    spec[size // 2 - 6 : size // 2 + 7, size // 2 - 6 : size // 2 + 7] = 1.0
    field = lake.synth_spectral(spec, seed, k_max=0.12)
    return np.array([65.0, 80.0, 77.0]) + field[..., None] * np.array([4.0, 4.5, 4.4])


# --- masks ----------------------------------------------------------------------


def test_mask_edges_partition_the_six_edges() -> None:
    for mask in range(64):
        w = lake.water_edges(mask)
        l_ = lake.land_edges(mask)
        assert sorted(w + l_) == list(range(6))
        assert sum(1 << e for e in w) == mask


def test_tile_name_and_mask_validation() -> None:
    assert lake.tile_name(7, 1) == "m07_01"
    with pytest.raises(lake.LakeError):
        lake.check_mask(64)
    with pytest.raises(lake.LakeError):
        lake.parse_cells("7:0,7:0")
    assert lake.parse_cells("63:0,1:2") == [(63, 0), (1, 2)]


# --- the shore field ------------------------------------------------------------


def test_all_water_mask_is_water_everywhere() -> None:
    phi = lake.ShoreGeometry(63, seed=1).tile_phi()
    assert (phi > 0).all()


@pytest.mark.parametrize("mask", [0, 1, 7, 21, 42, 62])
def test_water_keeps_clear_of_every_land_edge(mask: int) -> None:
    geom = lake.ShoreGeometry(mask, seed=5)
    phi = geom.tile_phi()
    xs, ys = pixel_grid(128)
    water = phi > 0
    for e in lake.land_edges(mask):
        dist = lake.segment_distance(xs, ys, e)
        assert (
            dist[water].min()
            >= lake.SHORE_INSET_PX - lake.WOBBLE_PX * lake.WOBBLE_NOISE_PEAK - 0.6
        )


def test_lone_pond_has_no_water_near_any_edge() -> None:
    phi = lake.ShoreGeometry(0, seed=2).tile_phi()
    clear = lake.SHORE_INSET_PX - lake.WOBBLE_PX * lake.WOBBLE_NOISE_PEAK
    d = tile_distance()
    assert (phi[(d > -1.0) & (d < clear)] < 0).all()  # the hex and its bleed ring


def test_shore_leaves_a_w_edge_square_at_the_inset_from_the_corner() -> None:
    # mask 1: edge 0 (SE) water, edge 1 (NE) land; they meet at corner 0 (east)
    geom = lake.ShoreGeometry(1, seed=9)
    corner = np.array([128.0, 64.0])
    a, b = lake.edge_segment(0)
    other = b if np.allclose(a, corner) else a
    along = (other - corner) / np.linalg.norm(other - corner)
    s = np.linspace(2.0, 20.0, 19)
    pts = corner[None, :] + s[:, None] * along[None, :]
    phi = geom.phi(pts[:, 0], pts[:, 1])
    np.testing.assert_allclose(phi, s - lake.SHORE_INSET_PX, atol=1e-6)


def _neighbourhood_masks(rng: np.random.Generator, d: int) -> tuple[int, int]:
    """Random lake/land around two lake hexes A=(0,0) and B=A+dir d."""
    a = (0, 0)
    b = AXIAL_DIRECTIONS[d]
    cells = {a: True, b: True}
    for hx in (a, b):
        for dq, dr in AXIAL_DIRECTIONS:
            n = (hx[0] + dq, hx[1] + dr)
            if n not in cells:
                cells[n] = bool(rng.integers(2))

    def mask_of(h: tuple[int, int]) -> int:
        return sum(
            1 << e
            for e, (dq, dr) in enumerate(AXIAL_DIRECTIONS)
            if cells.get((h[0] + dq, h[1] + dr), False)
        )

    return mask_of(a), mask_of(b)


@pytest.mark.parametrize("d", range(6))
def test_shorelines_meet_across_every_water_edge(d: int) -> None:
    """Whatever surrounds two neighbouring lake hexes, the shore field and the
    lip agree exactly on their shared edge. phi is compared clipped at 12 px:
    beyond the bank and the shore shading its value draws nothing. Each tile is
    exact inside its own hex; in the 1 px bleed ring outside it the two may
    differ by about half the depth (A measures to its land edge's line, B to
    the corner)."""
    rng = np.random.default_rng(100 + d)
    ox, oy = axial_to_pixel(*AXIAL_DIRECTIONS[d], 64.0)
    nx, ny = math.cos(math.radians(30 - 60 * d)), math.sin(math.radians(30 - 60 * d))
    tx, ty = -ny, nx
    t = np.linspace(-32.0, 32.0, 257)
    for _ in range(20):
        ma, mb = _neighbourhood_masks(rng, d)
        ga = lake.ShoreGeometry(ma, seed=int(rng.integers(1000)))
        gb = lake.ShoreGeometry(mb, seed=int(rng.integers(1000)))
        for depth, tol in ((0.0, 1e-6), (0.5, 0.3), (-0.5, 0.3)):
            r = apothem() - depth
            xa = 64.0 + r * nx + t * tx
            ya = 64.0 + r * ny + t * ty
            xb, yb = xa - ox, ya - oy  # same board points in B's tile frame
            np.testing.assert_allclose(
                np.minimum(ga.phi(xa, ya), 12.0),
                np.minimum(gb.phi(xb, yb), 12.0),
                atol=tol,
            )
            np.testing.assert_allclose(
                ga.lip_strength(xa, ya), gb.lip_strength(xb, yb), atol=1e-6
            )


# --- W0 -------------------------------------------------------------------------


def test_reciprocal_lattice_is_dual() -> None:
    b1, b2 = lake.reciprocal_lattice()
    a1, a2 = lake.LATTICE
    assert a1 @ b1 == pytest.approx(1.0)
    assert a2 @ b2 == pytest.approx(1.0)
    assert a1 @ b2 == pytest.approx(0.0, abs=1e-12)
    assert a2 @ b1 == pytest.approx(0.0, abs=1e-12)


def test_spectral_field_is_exactly_lattice_periodic() -> None:
    spec = np.ones((32, 32))
    field = lake.spectral_field(spec, seed=4, k_max=0.2)
    rng = np.random.default_rng(0)
    x = rng.uniform(-64, 64, 200)
    y = rng.uniform(-64, 64, 200)
    base = field.at(x, y)
    for a in (*lake.LATTICE, lake.LATTICE[0] - lake.LATTICE[1]):
        np.testing.assert_allclose(field.at(x + a[0], y + a[1]), base, atol=1e-8)


def test_coset_blend_is_lattice_periodic() -> None:
    sources = [
        np.clip(GRASS * 0.7 + smooth_noise(s)[..., :1], 0, 255).repeat(1, -1)
        for s in (1, 2, 3)
    ]
    sources = [np.broadcast_to(s, (128, 128, 3)).copy() for s in sources]
    rng = np.random.default_rng(1)
    x = rng.uniform(-60, 60, (20, 20))
    y = rng.uniform(-60, 60, (20, 20))
    base = lake.coset_blend_at(sources, x, y, 30.0, 3.0)
    for a in lake.LATTICE:
        shifted = lake.coset_blend_at(sources, x + a[0], y + a[1], 30.0, 3.0)
        np.testing.assert_allclose(shifted, base, atol=1e-6)


def test_coset_points_are_nearest() -> None:
    rng = np.random.default_rng(2)
    x = rng.uniform(-200, 200, 500)
    y = rng.uniform(-200, 200, 500)
    for off in lake.COSET_OFFSETS:
        cx, cy = lake.coset_points(x, y, off)
        d = np.hypot(x - cx, y - cy)
        brute = np.full(x.shape, np.inf)
        for i in range(-6, 7):
            for j in range(-6, 7):
                p = off + i * lake.LATTICE[0] + j * lake.LATTICE[1]
                brute = np.minimum(brute, np.hypot(x - p[0], y - p[1]))
        np.testing.assert_allclose(d, brute, atol=1e-9)


def test_periodicity_report_separates_periodic_from_not() -> None:
    w0 = periodic_water()
    for row in lake.periodicity_report(w0).values():
        assert row["ratio"] < 1.6
    xs, ys = pixel_grid(128)
    ramp = np.stack([xs * 0.8 + ys * 0.5] * 3, axis=-1)  # not periodic at all
    assert max(r["ratio"] for r in lake.periodicity_report(ramp).values()) > 10


def test_lattice_sample_matches_w0_inside_the_hex() -> None:
    w0 = periodic_water()
    xs, ys = pixel_grid(128)
    inside = tile_distance() > 1.0
    got = lake.lattice_sample(w0, xs, ys)
    np.testing.assert_allclose(got[inside], w0[inside], atol=1e-9)


# --- base and pin ---------------------------------------------------------------


def test_base_is_w0_out_to_every_water_edge() -> None:
    w0 = periodic_water()
    plains = np.broadcast_to(GRASS, (128, 128, 3)).copy()
    geom = lake.ShoreGeometry(0b001011, seed=7)
    base = lake.base_tile(geom, plains, w0)
    phi = geom.tile_phi()
    open_w = phi > lake.SHADE_DEPTH + 0.5
    np.testing.assert_allclose(base[open_w], w0[open_w], atol=1e-9)
    assert (base[phi < -6.0] == GRASS).all()


def pinned(mask: int, seed: int = 11):
    w0 = periodic_water()
    plains = np.broadcast_to(GRASS, (128, 128, 3)).copy() + smooth_noise(
        seed + 1, 1.0, 3.0
    )
    geom = lake.ShoreGeometry(mask, seed=seed)
    target = np.round(lake.base_tile(geom, plains, w0))
    phi = geom.tile_phi()
    # a painting: same layout, its own water texture, slightly off colour
    water = phi[..., None] > 0
    cut = np.where(
        water, w0.mean(axis=(0, 1)) + 3.0 + smooth_noise(seed, 1.2, 4.5), plains + 2.0
    )
    res = lake.pin_lake_tile(cut, target, phi, mask, w0, lake.LakePinConfig())
    return res, target, w0, phi, cut


@pytest.mark.parametrize("mask", [0, 5, 46, 63])
def test_pin_rim_is_exactly_the_base(mask: int) -> None:
    res, target, *_ = pinned(mask)
    rim = (tile_distance() > 0) & (tile_distance() < 1.5)
    assert np.abs(res.rgba[..., :3][rim].astype(float) - target[rim]).max() <= 0.5
    assert lake.lake_checks(
        res.rgba[..., :3].astype(float) * 0 + target,
        target,
        res.rgba,
        *pinned(mask)[3:4],
        mask,
    )[0].passed


def test_pinned_water_edges_join_seamlessly() -> None:
    """Two pinned tiles across a W edge show a step no bigger than 1 px of W0."""
    a, *_ = pinned(0b000001, seed=3)  # SE edge water
    b, *_ = pinned(0b001000, seed=4)  # its neighbour across: NW edge water
    seam = lake.pair_seam(
        a.rgba[..., :3].astype(float), b.rgba[..., :3].astype(float), 0, t_half=18.0
    )
    assert seam["ratio"] < 1.6


def test_variance_preserving_cross_fade_keeps_the_texture() -> None:
    res, target, w0, phi, cut = pinned(63, seed=8)
    xs, ys = pixel_grid(128)
    mid = (res.weight > 0.4) & (res.weight < 0.6) & (tile_distance() > 0)
    out = res.rgba[..., :3].astype(float)
    hp = lambda img: img - gaussian_blur(img, 3.0)  # noqa: E731
    linear = target * 0.5 + cut * 0.5
    s_out = hp(out)[mid].std()
    s_lin = hp(linear)[mid].std()
    s_ref = hp(cut)[(tile_distance() > 20)].std()
    assert s_out > s_lin * 1.15  # the linear fade would wash the texture out
    assert s_out == pytest.approx(s_ref, rel=0.35)


def test_ring_profile_reports_depth_bands() -> None:
    res, target, w0, phi, cut = pinned(63)
    prof = lake.ring_profile(res.rgba[..., :3].astype(float), phi, 63)
    assert [p["depth"][0] for p in prof][:3] == [0, 2, 4]


def test_water_normalisation_hits_the_target_mean() -> None:
    _, target, w0, phi, cut = pinned(7)
    cfg = lake.LakePinConfig()
    out, rec = lake.normalise_water(cut, phi, cfg)
    core = (phi > 4.0) & (tile_distance() > 3.0)
    np.testing.assert_allclose(out[core].mean(axis=0), cfg.feature_rgb, atol=0.5)
    land = phi < -1.0
    np.testing.assert_allclose(out[land], cut[land])
    assert "offset" in rec


def test_edge_weight_ramps_wider_on_water_edges() -> None:
    cfg = lake.LakePinConfig()
    w = lake.edge_weight(0b000001, cfg)
    xs, ys = pixel_grid(128)
    d_water, t_w = edge_frame(0, xs, ys)
    d_land, t_l = edge_frame(3, xs, ys)
    sel_w = (np.abs(t_w) < 10) & (np.abs(d_water - 8.0) < 0.5)
    sel_l = (np.abs(t_l) < 10) & (np.abs(d_land - 8.0) < 0.5)
    assert w[sel_l].mean() > 0.99
    assert w[sel_w].mean() < 0.8


# --- prompt ---------------------------------------------------------------------


def test_hexagon_lines_name_the_open_edges() -> None:
    assert "edge to edge" in lake.hexagon_line(63, 1)
    assert "shore all round" in lake.hexagon_line(0, 1)
    line = lake.hexagon_line(0b000011, 5)
    assert "south-east and north-east edges" in line
    assert "north-west" in line  # a shore side
    strip = lake.hexagon_line(0b111110, 5)
    assert "except the south-east one" in strip


def test_make_prompt_numbers_every_cell() -> None:
    text = lake.make_prompt([(1, 1), (63, 2), (0, 3)], (4, 4))
    assert (
        "1. Open water" in text
        and "2. All open water" in text
        and "3. A small lake" in text
    )
    assert "3 separate flat-top hexagons" in text
