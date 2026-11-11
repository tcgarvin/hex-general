#!/usr/bin/env python3
"""Lake tiles painted whole per neighbour mask (paint-in round 2, 2026-11-10).

A lake hex draws the tile of its **mask**: bit d set when the neighbour in
direction d (`hexgeom.AXIAL_DIRECTIONS`, edge d in `paintin_geom`'s numbering:
0=SE 1=NE 2=N 3=NW 4=SW 5=S) is lake; off-map counts as land. 64 masks. Each
tile is painted whole by Codex on a deterministic per-mask base and then pinned,
so any tile for mask X sits next to any tile for mask Y without a seam:

- **W edge** (lake neighbour): open water runs to the hex edge. The rim there
  is pinned to **W0**, one water tile that tiles seamlessly with itself on the
  hex lattice (`synth_spectral`, `coset_blend`), so two lake tiles meet in
  continuous water.
- **L edge** (land neighbour): a shore `SHORE_INSET_PX` inside the hex with the
  plains rim outside it, pinned to the plains base like every terrain.
- **Shore field** (`ShoreGeometry`): the signed distance from the land, i.e. a
  soft minimum over the L edges' *segment* distances minus the inset. Where an
  L edge meets a W edge at corner V the shore is the arc of radius
  `SHORE_INSET_PX` round V, so it leaves the tile across the W edge at that
  distance from V, square to the edge. The lake hex across that W edge has the
  same land hex at V and so the same arc: the two shorelines meet. Seeded
  wobble and the broken bank lip fade out within `FIXED_NEAR_W_PX` of every W
  edge, so the cross-section there is fixed.
- **Pin** (`pin_lake_tile`): rim exact to the base; land interior restored
  with the paint-in difference matte; water interior is Codex's, tone-matched
  toward W0 near W edges (low frequencies only) and cross-faded into W0 with a
  variance-preserving blend, so no flat or soft ring shows inside water edges.

CLI (see `docs/ART_PIPELINE.md`, "Lake tiles by mask"):

    paintin_lake.py w0     build W0 (spectral or from painted all-water cells)
    paintin_lake.py job    per-mask bases + paint-in job + per-hexagon prompt
    paintin_lake.py run    sheet -> Codex (or --from-raw) -> cut -> lake pin -> checks
    paintin_lake.py apply  copy picked tiles into a candidate set with provenance
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from hexgeom import axial_to_pixel  # noqa: E402
from paintin_gate import luminance  # noqa: E402
from paintin_geom import (  # noqa: E402
    EDGE_NAMES,
    TILE_PX,
    apothem,
    edge_frame,
    edge_normal,
    edge_tangent,
    gaussian_blur,
    pixel_grid,
    sample_bilinear,
    smoothstep,
    tile_distance,
)
from paintin_pin import PinConfig, ground_restore, water_mask  # noqa: E402
from paintin_sheet import output_alpha, repo_rel, sha256_of  # noqa: E402

N_MASKS = 64
HEX_SIZE = TILE_PX / 2.0  # circumradius at 128 px
# Hex lattice at 128 px per hex: axial (1, 0) and (0, 1) neighbour offsets.
LATTICE = (
    np.array(axial_to_pixel(1, 0, HEX_SIZE)),
    np.array(axial_to_pixel(0, 1, HEX_SIZE)),
)
COMPASS = {
    0: "south-east",
    1: "north-east",
    2: "north",
    3: "north-west",
    4: "south-west",
    5: "south",
}

SHORE_INSET_PX = 11.0  # water starts this far from the land (edge line or corner)
SHORE_SOFT_PX = 8.0  # soft-minimum scale: rounds the shore inside L-L corners
SOFT_FIXED_PX = 1.5  # ...shrinking to this near W edges (crossings stay exact)
WOBBLE_PX = 2.4  # max shore wobble in the middle of a shore run
WOBBLE_WAVELENGTHS = (16.0, 22.0, 30.0, 41.0)
FIXED_NEAR_W_PX = (
    11.0,
    26.0,
)  # wobble / lip noise fade in over this W-segment distance


class LakeError(ValueError):
    """Bad lake input (mask out of range, missing W0, bad job)."""


def check_mask(mask: int) -> int:
    """Validate a 6-bit mask (boundary check for CLI/job input)."""
    if not isinstance(mask, int) or not 0 <= mask < N_MASKS:
        raise LakeError(f"lake mask must be an int 0..63, got {mask!r}")
    return mask


def water_edges(mask: int) -> tuple[int, ...]:
    """Edges toward lake neighbours (W edges) of a mask."""
    return tuple(d for d in range(6) if mask >> d & 1)


def land_edges(mask: int) -> tuple[int, ...]:
    """Edges toward land neighbours (L edges) of a mask."""
    return tuple(d for d in range(6) if not mask >> d & 1)


def tile_name(mask: int, variant: int) -> str:
    """Candidate file stem: ``m<mask two digits>_<variant two digits>``."""
    return f"m{check_mask(mask):02d}_{variant:02d}"


# ---------------------------------------------------------------- geometry ----


def edge_segment(edge: int) -> tuple[np.ndarray, np.ndarray]:
    """End points (tile px) of `edge` of the hex centred at (64, 64)."""
    mid = np.array([64.0, 64.0]) + apothem() * np.asarray(edge_normal(edge))
    half = HEX_SIZE / 2.0 * np.asarray(edge_tangent(edge))
    return mid - half, mid + half


def segment_distance(xs: np.ndarray, ys: np.ndarray, edge: int) -> np.ndarray:
    """Distance from points to the closed segment of `edge`."""
    a, b = edge_segment(edge)
    ab = b - a
    t = np.clip(((xs - a[0]) * ab[0] + (ys - a[1]) * ab[1]) / float(ab @ ab), 0.0, 1.0)
    return np.hypot(xs - (a[0] + t * ab[0]), ys - (a[1] + t * ab[1]))


def _noise(xs: np.ndarray, ys: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Smooth seeded noise in about [-1, 1] (a few random plane waves)."""
    out = np.zeros_like(xs)
    for wl in WOBBLE_WAVELENGTHS:
        th = rng.uniform(0.0, math.pi)
        k = 2.0 * math.pi / wl
        out += np.cos(
            k * (xs * math.cos(th) + ys * math.sin(th)) + rng.uniform(0, 2 * math.pi)
        )
    return out / math.sqrt(len(WOBBLE_WAVELENGTHS) / 2.0) / 1.6


# the noise is not clipped: its unit is about one std, and it can reach this
WOBBLE_NOISE_PEAK = (
    len(WOBBLE_WAVELENGTHS) / math.sqrt(len(WOBBLE_WAVELENGTHS) / 2.0) / 1.6
)


@dataclass(frozen=True)
class ShoreGeometry:
    """The deterministic shoreline of one lake tile (see module docstring)."""

    mask: int
    seed: int
    inset: float = SHORE_INSET_PX
    soft: float = SHORE_SOFT_PX
    wobble: float = WOBBLE_PX

    def fixed_weight(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        """0 near every W edge (the fixed cross-section), 1 in free shore runs."""
        w_edges = water_edges(self.mask)
        if not w_edges:
            return np.ones_like(xs)
        near = np.min([segment_distance(xs, ys, e) for e in w_edges], axis=0)
        return smoothstep(*FIXED_NEAR_W_PX, near)

    def phi(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        """Signed shore distance (px): > 0 water, < 0 land; 0 on the shoreline."""
        l_edges = land_edges(self.mask)
        if not l_edges:
            return np.full_like(xs, 1e3)
        d = np.stack([segment_distance(xs, ys, e) for e in l_edges])
        free = self.fixed_weight(xs, ys)
        # near W edges the soft minimum shrinks to SOFT_FIXED_PX, so far L
        # edges (which differ between the two lake hexes there) contribute
        # nothing measurable to the shared crossing
        k = SOFT_FIXED_PX + (self.soft - SOFT_FIXED_PX) * free
        lo = d.min(axis=0)
        soft_min = lo - k * np.log(np.exp(-(d - lo) / k).sum(axis=0))
        noise = _noise(xs, ys, np.random.default_rng(self.seed))
        return soft_min - self.inset + self.wobble * noise * free

    def lip_strength(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        """Broken bank lip: patchy along free runs, a fixed 0.6 near W edges."""
        rng = np.random.default_rng(self.seed + 104729)
        n = _noise(xs * 2.2, ys * 2.2, rng)
        free = 0.25 + 0.75 * smoothstep(-0.25, 0.35, n)
        fixed = self.fixed_weight(xs, ys)
        return 0.6 * (1.0 - fixed) + free * fixed

    def tile_phi(self) -> np.ndarray:
        """phi at the 128 px tile's pixel centres."""
        xs, ys = pixel_grid(TILE_PX)
        return self.phi(xs, ys)


# ------------------------------------------------------------------- bank ----

# Measured on the round-1 lake picks (mean colour by signed distance from the
# water's edge): grass ~(101, 102, 45), lip peak ~(120, 115, 60) at -4 px, dark
# undercut at the water, water darker within ~6 px of the shore.
BANK_DARK = np.array([58.0, 58.0, 46.0])
BANK_LIP = np.array([136.0, 127.0, 72.0])
LIP_CENTRE, LIP_HALF, LIP_ALPHA = -3.6, 1.2, 0.75
DARK_CENTRE, DARK_HALF, DARK_ALPHA = -0.7, 0.9, 0.9
SHADE_DEPTH, SHADE_MIN = 6.0, 0.84


def _band(phi: np.ndarray, centre: float, half: float) -> np.ndarray:
    return 1.0 - smoothstep(half - 0.6, half + 0.6, np.abs(phi - centre))


def compose_base(
    phi: np.ndarray, lip: np.ndarray, plains: np.ndarray, w0: np.ndarray
) -> np.ndarray:
    """Plains on the land, W0 (shaded near the shore) on the water, bank between."""
    water = np.clip(0.5 + phi, 0.0, 1.0)[..., None]
    shade = SHADE_MIN + (1.0 - SHADE_MIN) * smoothstep(0.0, SHADE_DEPTH, phi)
    rgb = plains * (1.0 - water) + w0 * shade[..., None] * water
    lip_a = (LIP_ALPHA * lip * _band(phi, LIP_CENTRE, LIP_HALF))[..., None]
    rgb = rgb * (1.0 - lip_a) + BANK_LIP * lip_a
    dark_a = (DARK_ALPHA * _band(phi, DARK_CENTRE, DARK_HALF))[..., None]
    return rgb * (1.0 - dark_a) + BANK_DARK * dark_a


def base_tile(geom: ShoreGeometry, plains: np.ndarray, w0: np.ndarray) -> np.ndarray:
    """The 128 px per-mask base (float RGB, opaque square)."""
    if plains.shape != (TILE_PX, TILE_PX, 3) or w0.shape != (TILE_PX, TILE_PX, 3):
        raise LakeError("plains and W0 must be 128x128 RGB")
    xs, ys = pixel_grid(TILE_PX)
    return compose_base(geom.phi(xs, ys), geom.lip_strength(xs, ys), plains, w0)


# ---------------------------------------------------------------- W0 synth ----


def reciprocal_lattice() -> tuple[np.ndarray, np.ndarray]:
    """b1, b2 with a_i . b_j = delta_ij (cycles per px)."""
    a = np.stack(LATTICE, axis=1)  # columns a1, a2
    b = np.linalg.inv(a).T
    return b[:, 0], b[:, 1]


def lattice_frequencies(k_max: float = 0.5) -> np.ndarray:
    """Every reciprocal-lattice frequency in the upper half-plane with |k| <= k_max."""
    b1, b2 = reciprocal_lattice()
    n = int(math.ceil(k_max / min(np.linalg.norm(b1), np.linalg.norm(b2)))) + 2
    out = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            k = i * b1 + j * b2
            r = float(np.hypot(*k))
            if r == 0.0 or r > k_max:
                continue
            if k[1] > 0 or (k[1] == 0 and k[0] > 0):
                out.append(k)
    return np.array(out)


def measure_spectrum(patches: list[np.ndarray]) -> np.ndarray:
    """Mean Hann-windowed periodogram (fftshifted, square) of equal scalar patches."""
    size = patches[0].shape[0]
    win = np.outer(np.hanning(size), np.hanning(size))
    acc = np.zeros((size, size))
    for p in patches:
        if p.shape != (size, size):
            raise LakeError("spectrum patches must be equal squares")
        f = np.fft.fft2((p - p.mean()) * win)
        acc += np.abs(np.fft.fftshift(f)) ** 2
    return acc / len(patches)


def spectrum_at(spec: np.ndarray, k: np.ndarray) -> np.ndarray:
    """Bilinear lookup of an fftshifted periodogram at frequencies k (cycles/px)."""
    size = spec.shape[0]
    fx = k[:, 0] * size + size / 2.0 + 0.5
    fy = k[:, 1] * size + size / 2.0 + 0.5
    return sample_bilinear(spec, fx, fy)


def isotropise(spec: np.ndarray, amount: float) -> np.ndarray:
    """Blend a periodogram toward its radial average (amount 1 = isotropic)."""
    size = spec.shape[0]
    ys, xs = np.mgrid[0:size, 0:size] - size / 2.0
    r = np.hypot(xs, ys)
    bins = np.round(r).astype(int)
    radial = np.bincount(bins.ravel(), spec.ravel()) / np.maximum(
        np.bincount(bins.ravel()), 1
    )
    return (1.0 - amount) * spec + amount * radial[bins]


@dataclass(frozen=True)
class SpectralField:
    """A sum of cosines at reciprocal-lattice frequencies: lattice-periodic."""

    ks: np.ndarray  # (N, 2) cycles per px
    amp: np.ndarray  # (N,)
    phase: np.ndarray  # (N,)

    def at(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Field values at points relative to the hex centre (raw, not normalised)."""
        xf, yf = x.ravel(), y.ravel()
        out = np.zeros(xf.shape)
        for lo in range(0, len(self.ks), 256):
            kk = self.ks[lo : lo + 256]
            arg = 2.0 * math.pi * (np.outer(xf, kk[:, 0]) + np.outer(yf, kk[:, 1]))
            out += (
                np.cos(arg + self.phase[None, lo : lo + 256])
                * self.amp[None, lo : lo + 256]
            ).sum(axis=1)
        return out.reshape(x.shape)


def spectral_field(
    spectrum: np.ndarray, seed: int, k_max: float = 0.5
) -> SpectralField:
    """Random-phase field with the measured amplitude at every lattice frequency."""
    ks = lattice_frequencies(k_max)
    amp = np.sqrt(np.maximum(spectrum_at(spectrum, ks), 0.0))
    phase = np.random.default_rng(seed).uniform(0.0, 2.0 * math.pi, len(ks))
    return SpectralField(ks, amp, phase)


def synth_spectral(
    spectrum: np.ndarray, seed: int, size: int = TILE_PX, k_max: float = 0.5
) -> np.ndarray:
    """A unit-variance scalar tile, exactly periodic on the hex lattice.

    The field (`spectral_field`) is evaluated at pixel centres relative to the
    tile's hex centre, so tile copies placed at lattice offsets agree exactly.
    """
    xs, ys = pixel_grid(size)
    out = spectral_field(spectrum, seed, k_max).at(xs - size / 2.0, ys - size / 2.0)
    return (out - out.mean()) / out.std()


def match_quantiles(values: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Pointwise monotone map of `values` onto the distribution of `reference`."""
    order = np.argsort(values.ravel())
    ranks = np.empty(order.size)
    ranks[order] = (np.arange(order.size) + 0.5) / order.size
    ref = np.sort(reference.ravel())
    q = np.interp(ranks, (np.arange(ref.size) + 0.5) / ref.size, ref)
    return q.reshape(values.shape)


@dataclass(frozen=True)
class WaterStats:
    """Colour model of painted water: mean and two principal colour axes."""

    mean: np.ndarray
    axes: np.ndarray  # (2, 3) unit vectors
    proj: list[np.ndarray]  # reference projections on each axis


def water_stats(pixels: np.ndarray) -> WaterStats:
    """PCA of water pixels (N, 3)."""
    mean = pixels.mean(axis=0)
    cov = np.cov((pixels - mean).T)
    vals, vecs = np.linalg.eigh(cov)
    axes = vecs[:, ::-1][:, :2].T
    # orient the main axis toward lighter water
    axes = np.array([a if a.sum() >= 0 else -a for a in axes])
    proj = [(pixels - mean) @ a for a in axes]
    return WaterStats(mean, axes, proj)


def colourise(
    fields: list[np.ndarray], stats: WaterStats, target_mean: np.ndarray
) -> np.ndarray:
    """Scalar fields -> RGB water, each matched to its principal axis's histogram."""
    rgb = np.broadcast_to(target_mean, fields[0].shape + (3,)).astype(np.float64).copy()
    for f, axis, ref in zip(fields, stats.axes, stats.proj):
        rgb += match_quantiles(f, ref)[..., None] * axis[None, None, :]
    return rgb


def coset_points(
    x: np.ndarray, y: np.ndarray, offset: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest point of the lattice coset `offset + Lattice` to each (x, y)."""
    a = np.stack(LATTICE, axis=1)
    inv = np.linalg.inv(a)
    px, py = x - offset[0], y - offset[1]
    u = inv[0, 0] * px + inv[0, 1] * py
    v = inv[1, 0] * px + inv[1, 1] * py
    best_d = np.full(x.shape, np.inf)
    best = (np.zeros_like(x), np.zeros_like(y))
    for du in (0, 1):
        for dv in (0, 1):
            iu = np.floor(u) + du
            iv = np.floor(v) + dv
            cx = offset[0] + a[0, 0] * iu + a[0, 1] * iv
            cy = offset[1] + a[1, 0] * iu + a[1, 1] * iv
            dd = np.hypot(x - cx, y - cy)
            better = dd < best_d
            best_d = np.where(better, dd, best_d)
            best = (np.where(better, cx, best[0]), np.where(better, cy, best[1]))
    # the 2x2 cell corners miss a nearer point in a few obtuse cases: check
    # the six lattice neighbours of the current best too
    for _ in range(2):
        for s in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)):
            cx = best[0] + a[0, 0] * s[0] + a[0, 1] * s[1]
            cy = best[1] + a[1, 0] * s[0] + a[1, 1] * s[1]
            dd = np.hypot(x - cx, y - cy)
            better = dd < best_d
            best_d = np.where(better, dd, best_d)
            best = (np.where(better, cx, best[0]), np.where(better, cy, best[1]))
    return best


COSET_FLATTEN_SIGMA = 6.0
COSET_OFFSETS = (
    np.array([0.0, 0.0]),  # hex centres
    np.array([HEX_SIZE, 0.0]),  # east corners
    np.array([HEX_SIZE / 2.0, HEX_SIZE * math.sqrt(3.0) / 2.0]),  # south-east corners
)


def coset_blend_at(
    sources: list[np.ndarray],
    x: np.ndarray,
    y: np.ndarray,
    radius: float,
    sharpness: float,
) -> np.ndarray:
    """`coset_blend` evaluated at points (x, y) relative to the hex centre."""
    if len(sources) != 3:
        raise LakeError("coset_blend needs exactly three source hexes")
    inner = tile_distance() > 3.0  # the painted hex, not the cut's fill outside it
    means = [s[inner].mean(axis=0) for s in sources]
    mean = np.mean(means, axis=0)
    terms = []
    weights = []
    for src, m, off in zip(sources, means, COSET_OFFSETS):
        # texture only: each source's slow tone drift is removed (it would
        # step at the window borders and repeat as a lattice of patches)
        low = _masked_blur(src, inner.astype(np.float64), COSET_FLATTEN_SIGMA)
        flat = src - low + m
        cx, cy = coset_points(x, y, off)
        ux, uy = x - cx, y - cy
        r = np.hypot(ux, uy)
        w = np.exp(-((r / radius) ** sharpness)) * (r < radius * 1.6)
        terms.append(sample_bilinear(flat, 64.0 + ux, 64.0 + uy) - m)
        weights.append(w)
    wsum = np.maximum(np.sum(weights, axis=0), 1e-9)
    wn = [w / wsum for w in weights]
    num = sum(w[..., None] * t for w, t in zip(wn, terms))
    norm = np.sqrt(sum(w**2 for w in wn))[..., None]
    return mean + num / norm


def coset_blend(
    sources: list[np.ndarray], radius: float, sharpness: float, size: int = TILE_PX
) -> np.ndarray:
    """A lattice-periodic water tile from three painted all-water hexes.

    The hex centres and the two kinds of hex corner are three cosets of the
    lattice; every point lies within 37 px of one of them. Source j is sampled
    around the nearest point of coset j (offset up to `radius` px, inside its
    own hex) with a soft window; windows are blended with a variance-preserving
    (histogram-preserving) blend so overlaps keep the painted contrast. Each
    term is lattice-periodic, so the result tiles exactly.
    """
    xs, ys = pixel_grid(size)
    return coset_blend_at(sources, xs - size / 2.0, ys - size / 2.0, radius, sharpness)


def lattice_sample(w0: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Sample a lattice-periodic W0 tile at any tile-space points.

    The hex is the Voronoi cell of the lattice, so every point maps to the hex
    by subtracting its nearest lattice point.
    """
    x = xs - 64.0
    y = ys - 64.0
    cx, cy = coset_points(x, y, COSET_OFFSETS[0])
    return sample_bilinear(w0, 64.0 + x - cx, 64.0 + y - cy)


def sheet_water(w0: np.ndarray, seed: int) -> np.ndarray:
    """W0 shifted by a seeded offset: the same water, a different patch of it.

    Used for sheet bases only (so Codex does not see one identical texture in
    every hexagon); pin targets always use W0 itself.
    """
    rng = np.random.default_rng(seed + 31337)
    dx, dy = rng.uniform(0.0, 96.0), rng.uniform(0.0, 110.0)
    xs, ys = pixel_grid(TILE_PX)
    return lattice_sample(w0, xs + dx, ys + dy)


def periodicity_report(w0: np.ndarray) -> dict[str, Any]:
    """Seam measure of a W0 tile against itself across each edge pair.

    For edges e = 0, 1, 2, the row just inside edge e (depth 0.5 px) of one copy
    meets the row just inside edge e + 3 of its neighbour across that edge. The
    across-edge step |A(0.5) - B(0.5)| is compared with the in-tile step
    between depths 0.5 and 1.5 (the texture's own 1 px variation): a ratio near
    1 means the seam is no stronger than any other 1 px step.
    """
    ts = np.arange(-26.0, 26.0 + 1e-9, 0.5)
    out: dict[str, Any] = {}
    for e in range(3):
        nx, ny = edge_normal(e)
        tx, ty = edge_tangent(e)
        a = apothem()

        def row(edge: int, depth: float) -> np.ndarray:
            mx, my = edge_normal(edge)
            r = a - depth
            return sample_bilinear(w0, 64.0 + r * mx + ts * tx, 64.0 + r * my + ts * ty)

        inside = row(e, 0.5)
        across = row(e + 3, 0.5)  # neighbour's row, same canonical tangent t
        inner = row(e, 1.5)
        seam = float(np.abs(inside - across).mean())
        step = float(np.abs(inside - inner).mean())
        out[EDGE_NAMES[e] + "/" + EDGE_NAMES[e + 3]] = {
            "across_edge_mean_abs": round(seam, 3),
            "in_tile_1px_mean_abs": round(step, 3),
            "ratio": round(seam / max(step, 1e-9), 3),
        }
        del nx, ny
    return out


def water_texture_stats(rgb: np.ndarray, region: np.ndarray) -> dict[str, Any]:
    """Median RGB, luminance std and mean, and 1 px gradient of water pixels."""
    lum = luminance(rgb)
    gx = np.abs(np.diff(lum, axis=1))[region[:, 1:] & region[:, :-1]]
    gy = np.abs(np.diff(lum, axis=0))[region[1:, :] & region[:-1, :]]
    return {
        "median_rgb": [round(float(v), 1) for v in np.median(rgb[region], axis=0)],
        "lum_mean": round(float(lum[region].mean()), 2),
        "lum_std": round(float(lum[region].std()), 2),
        "grad_x": round(float(gx.mean()), 2),
        "grad_y": round(float(gy.mean()), 2),
    }


# --------------------------------------------------------------------- pin ----


@dataclass(frozen=True)
class LakePinConfig:
    """Lake pin parameters (tile px)."""

    land_exact_px: float = 1.5
    land_ramp_px: float = 6.0
    water_exact_px: float = 1.5  # covers the cut's antialiased hexagon fringe
    water_ramp_px: float = 11.0
    tone_sigma: float = 5.0
    tone_reach_px: float = 26.0
    mean_sigma: float = 3.0
    # every tile's open water is pulled all the way to one mean (W0 has it
    # too), so no tile is lighter or darker than the water at its W edges
    feature_strength: float = 1.0
    feature_rgb: tuple[float, float, float] = (65.0, 80.0, 77.0)
    # ...and its ripple contrast (luminance high-pass std at `water_hp_sigma`)
    # scaled to one value (W0 has it too), so no tile, and no band of W0 at a
    # water edge, is calmer or busier than its neighbours; 0 is off
    water_hp_std: float = 6.0
    water_hp_sigma: float = 3.0
    max_correlation: float = 0.6

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> LakePinConfig:
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known
        if unknown:
            raise LakeError(f"unknown lake pin settings {sorted(unknown)}")
        cfg = dict(raw)
        if "feature_rgb" in cfg:
            cfg["feature_rgb"] = tuple(float(v) for v in cfg["feature_rgb"])
        return cls(**cfg)


def edge_weight(mask: int, cfg: LakePinConfig) -> np.ndarray:
    """Painted weight: 0 at the rim (exact), ramping in per edge type."""
    xs, ys = pixel_grid(TILE_PX)
    w = np.ones((TILE_PX, TILE_PX))
    for e in range(6):
        depth, _ = edge_frame(e, xs, ys)
        if mask >> e & 1:
            ramp = smoothstep(
                cfg.water_exact_px, cfg.water_exact_px + cfg.water_ramp_px, depth
            )
        else:
            ramp = smoothstep(
                cfg.land_exact_px, cfg.land_exact_px + cfg.land_ramp_px, depth
            )
        w = np.minimum(w, ramp)
    return w


def water_region(phi: np.ndarray) -> np.ndarray:
    """Soft water indicator from the shore field (1 = open water, 0 = bank/land)."""
    return smoothstep(-1.5, 1.0, phi)


def normalise_water(
    cut: np.ndarray, phi: np.ndarray, cfg: LakePinConfig
) -> tuple[np.ndarray, dict[str, Any]]:
    """Pull a cell's open-water mean colour toward `cfg.feature_rgb`.

    The water is where the shore field says it is (the guide Codex painted
    to), not the colour classifier: Codex's lake water sits right at the
    classifier's saturation ramp, so the classifier would shift it only
    partly. The offset fades in over the first 2 px past the shoreline.
    """
    if cfg.feature_strength == 0.0:
        return cut, {}
    core = (phi > 4.0) & (tile_distance() > 3.0)
    if int(core.sum()) < 40:
        return cut, {"skipped": f"only {int(core.sum())} open-water pixels"}
    mean = cut[core].mean(axis=0)
    offset = cfg.feature_strength * (np.asarray(cfg.feature_rgb) - mean)
    weight = smoothstep(-0.5, 2.0, phi)
    out = cut + offset[None, None, :] * weight[..., None]
    record: dict[str, Any] = {
        "mean_before": [round(float(v), 1) for v in mean],
        "offset": [round(float(v), 1) for v in offset],
    }
    if cfg.water_hp_std > 0:
        open_core = (phi > 6.0) & (tile_distance() > 3.0)
        if int(open_core.sum()) >= 200:
            hp = out - _masked_blur(out, smoothstep(1.0, 3.0, phi), cfg.water_hp_sigma)
            std = float(luminance(hp)[open_core].std())
            gain = float(np.clip(cfg.water_hp_std / max(std, 1e-6), 0.6, 1.6))
            out = out + (gain - 1.0) * hp * smoothstep(1.0, 4.0, phi)[..., None]
            record["hp_std_before"] = round(std, 2)
            record["hp_gain"] = round(gain, 3)
    return out, record


def _masked_blur(img: np.ndarray, weight: np.ndarray, sigma: float) -> np.ndarray:
    num = gaussian_blur(img * weight[..., None], sigma)
    den = gaussian_blur(weight, sigma)[..., None]
    fallback = (img * weight[..., None]).sum(axis=(0, 1)) / max(
        float(weight.sum()), 1.0
    )
    return np.where(den > 0.05, num / np.maximum(den, 1e-9), fallback)


@dataclass(frozen=True)
class LakePinResult:
    """A pinned lake tile and its intermediates."""

    rgba: np.ndarray  # uint8 (128, 128, 4)
    weight: np.ndarray
    water: np.ndarray
    correlation: float


def pin_lake_tile(
    cut: np.ndarray,
    base: np.ndarray,
    phi: np.ndarray,
    mask: int,
    w0: np.ndarray,
    cfg: LakePinConfig,
) -> LakePinResult:
    """Pin a (colour-normalised) cut lake cell to its per-mask base.

    `base` is the pin target (rim exact). Land: paint-in ground restore.
    Water: Codex's water, its low frequencies pulled to W0's near W edges, then
    a variance-preserving cross-fade from the target (W0 at the rim) into it.
    """
    for name, arr in (("cut", cut), ("base", base), ("w0", w0)):
        if arr.shape != (TILE_PX, TILE_PX, 3):
            raise LakeError(f"{name} must be 128x128x3, got {arr.shape}")
    weight = edge_weight(mask, cfg)
    water = water_region(phi)
    restored, _ = ground_restore(cut, base, PinConfig(restore=True))
    interior_water = cut
    w_edges = water_edges(mask)
    if w_edges:
        xs, ys = pixel_grid(TILE_PX)
        near = np.min([edge_frame(e, xs, ys)[0] for e in w_edges], axis=0)
        reach = (1.0 - smoothstep(0.0, cfg.tone_reach_px, near)) * water
        open_w = water * (water > 0.5)
        low_cut = _masked_blur(cut, open_w, cfg.tone_sigma)
        low_w0 = gaussian_blur(w0, cfg.tone_sigma)
        interior_water = cut + reach[..., None] * (low_w0 - low_cut)
    interior = restored * (1.0 - water[..., None]) + interior_water * water[..., None]
    # linear blend (land, bank)
    lin = base * (1.0 - weight[..., None]) + interior * weight[..., None]
    # variance-preserving blend (water): around the local mean of the target
    m_t = gaussian_blur(base, cfg.mean_sigma)
    m_i = gaussian_blur(interior, cfg.mean_sigma)
    hp_t = base - m_t
    hp_i = interior - m_i
    # open water only: the shore's shading is correlated in both by design
    ring = (phi > 6.0) & (weight > 0.2) & (weight < 0.8)
    rho = 0.0
    if int(ring.sum()) > 30:
        a = hp_t[ring].ravel()
        b = hp_i[ring].ravel()
        rho = float(np.clip(np.corrcoef(a, b)[0, 1], 0.0, cfg.max_correlation))
    wa = 1.0 - weight
    wb = weight
    norm = np.sqrt(np.maximum(wa**2 + wb**2 + 2.0 * wa * wb * rho, 1e-9))[..., None]
    mean = m_t * wa[..., None] + m_i * wb[..., None]
    vp = mean + (wa[..., None] * hp_t + wb[..., None] * hp_i) / norm
    # exact at the rim: the variance-preserving form equals the target only at
    # weight 0 up to the mean filter; force exactness where weight is 0
    vp = np.where((weight <= 0.0)[..., None], base, vp)
    rgb = lin * (1.0 - water[..., None]) + vp * water[..., None]
    rgba = np.empty((TILE_PX, TILE_PX, 4), dtype=np.uint8)
    rgba[..., :3] = np.clip(np.round(rgb), 0, 255).astype(np.uint8)
    rgba[..., 3] = output_alpha()
    return LakePinResult(rgba=rgba, weight=weight, water=water, correlation=rho)


# ------------------------------------------------------------------ checks ----


@dataclass(frozen=True)
class LakeCheck:
    """One measured check (hard checks fail the tile)."""

    name: str
    value: float
    limit: float
    passed: bool
    hard: bool
    detail: str

    def line(self) -> str:
        tag = "ok  " if self.passed else ("FAIL" if self.hard else "warn")
        return f"{tag} {self.name}: {self.detail}"


def _chk(
    name: str, value: float, limit: float, ok: bool, detail: str, hard: bool = True
) -> LakeCheck:
    return LakeCheck(name, round(float(value), 4), float(limit), bool(ok), hard, detail)


WATER_TO_EDGE_MIN = 0.85  # share of water in the open part of each W edge band (cut)
LAND_LEAK_MAX = 0.03  # share of water-classified pixels where the base has grass (cut)
RING_LUM_TOL = 3.0  # |mean L| of water bands near W edges vs deeper water
RING_STD_RANGE = (0.75, 1.3)  # texture std of those bands vs deeper water


def ring_profile(rgb: np.ndarray, phi: np.ndarray, mask: int) -> list[dict[str, float]]:
    """Mean luminance and high-pass std of open water by depth from the nearest W edge."""
    w_edges = water_edges(mask)
    xs, ys = pixel_grid(TILE_PX)
    near = np.min([edge_frame(e, xs, ys)[0] for e in w_edges], axis=0)
    lum = luminance(rgb)
    hp = lum - gaussian_blur(lum, 3.0)
    inside = tile_distance() > 0.0
    open_w = (phi > 5.0) & inside
    out = []
    for lo, hi in ((0, 2), (2, 4), (4, 7), (7, 11), (11, 16), (16, 40)):
        sel = open_w & (near >= lo) & (near < hi)
        if int(sel.sum()) < 25:
            continue
        out.append(
            {
                "depth": [lo, hi],
                "px": int(sel.sum()),
                "lum": round(float(lum[sel].mean()), 2),
                "hp_std": round(float(hp[sel].std()), 2),
            }
        )
    return out


def lake_checks(
    cut: np.ndarray, target: np.ndarray, final: np.ndarray, phi: np.ndarray, mask: int
) -> list[LakeCheck]:
    """Per-tile checks; `cut` is the colour-normalised painting before the pin."""
    checks: list[LakeCheck] = []
    d = tile_distance()
    inside = d > 0.0
    rgb = final[..., :3].astype(np.float64)
    rim = inside & (d < 1.0)
    err = float(np.abs(rgb[rim] - np.round(target[rim])).max())
    checks.append(
        _chk(
            "rim_exact",
            err,
            1.0,
            err <= 1.0,
            f"max |final - base| on the rim {err:.1f}",
        )
    )
    wm = water_mask(cut) > 0.5
    xs, ys = pixel_grid(TILE_PX)
    for e in water_edges(mask):
        depth, _ = edge_frame(e, xs, ys)
        band = inside & (depth > 2.0) & (depth < 10.0) & (phi > 4.0)
        share = float(wm[band].mean()) if band.any() else 1.0
        checks.append(
            _chk(
                f"water_to_edge_{EDGE_NAMES[e]}",
                share,
                WATER_TO_EDGE_MIN,
                share >= WATER_TO_EDGE_MIN,
                f"cut water share {share:.2f} in the {EDGE_NAMES[e]} edge band",
            )
        )
    land = inside & (d > 1.5) & (phi < -5.0)
    if land.any():
        leak = float(wm[land].mean())
        checks.append(
            _chk(
                "land_leak",
                leak,
                LAND_LEAK_MAX,
                leak <= LAND_LEAK_MAX,
                f"cut water on base grass {leak:.3f}",
            )
        )
    if water_edges(mask):
        prof = ring_profile(rgb, phi, mask)
        deep = [p for p in prof if p["depth"][0] >= 11]
        if prof and deep:
            ref_l = float(np.mean([p["lum"] for p in deep]))
            ref_s = float(np.mean([p["hp_std"] for p in deep]))
            dl = max(abs(p["lum"] - ref_l) for p in prof)
            ratios = [p["hp_std"] / max(ref_s, 1e-9) for p in prof]
            ok = (
                dl <= RING_LUM_TOL
                and RING_STD_RANGE[0] <= min(ratios)
                and max(ratios) <= RING_STD_RANGE[1]
            )
            checks.append(
                _chk(
                    "ring",
                    dl,
                    RING_LUM_TOL,
                    ok,
                    f"near-W-edge water vs deep: max |dL| {dl:.1f}, texture std ratio "
                    f"{min(ratios):.2f}-{max(ratios):.2f}",
                    hard=False,
                )
            )
    return checks


def pair_seam(
    a: np.ndarray, b: np.ndarray, edge: int, t_half: float = 26.0
) -> dict[str, float]:
    """Seam across `edge` of tile a (b is its neighbour across it, edge + 3).

    Compares the step between the rows 0.5 px either side of the shared edge
    with the in-tile step between rows 0.5 and 1.5 px inside a: ratio ~1 is
    no seam.
    """
    ts = np.arange(-t_half, t_half + 1e-9, 0.5)
    tx, ty = edge_tangent(edge)

    def row(img: np.ndarray, e: int, depth: float) -> np.ndarray:
        nx, ny = edge_normal(e)
        r = apothem() - depth
        return luminance(
            sample_bilinear(img, 64.0 + r * nx + ts * tx, 64.0 + r * ny + ts * ty)
        )

    ra = row(a, edge, 0.5)
    rb = row(b, (edge + 3) % 6, 0.5)
    inner = row(a, edge, 1.5)
    seam = float(np.abs(ra - rb).mean())
    step = float(np.abs(ra - inner).mean())
    return {
        "seam": round(seam, 3),
        "step": round(step, 3),
        "ratio": round(seam / max(step, 1e-9), 3),
    }


# ------------------------------------------------------------------- prompt ----

PROMPT_HEAD = """Use your built-in image generation tool to create ONE image. Do not write code, do not run shell commands, do not edit any files. Just generate the image and then reply with one line saying it is done.

Image 1 is a sheet of {n} separate flat-top hexagons on a flat magenta (#FF00FF) background, in {rows} rows of {cols}. Number the hexagons 1 to {n}: left to right, top row first. In every description below, north is up, east is right. Each hexagon is one piece of a lake map: dark slate water, olive meadow grass, and between them a rough guide of the shore (a dark line with a pale khaki line beside it).

Image 2 is the target painted wargame map: an all-meadow countryside map. Image 3 is a close-up of the small lake on that map. Image 4 shows three finished river hexagons from the same game, at exactly the scale of image 1: the lake water must be EXACTLY the same water as these rivers and the lake shore EXACTLY the same bank as their banks, so a river flowing into the lake reads as the same water. Ignore everything else in images 2, 3 and 4: units, icons, panels, forests, roads, the magenta, and the thin hex grid lines.

Edit image 1: repaint every hexagon as calm lake water and meadow, seen from straight above, painted exactly like the river water and river banks of image 4, keeping the water, the shore and the grass exactly where they are in image 1.
- WATER THAT REACHES A HEXAGON EDGE: wherever the water touches a hexagon's straight edge, the lake carries on into the next hexagon. Paint the open water right out to that straight edge, the same water to the very last pixel: NO bank, NO shore, NO grass, NO dark rim, NO light rim, NO outline and NO change of colour along that edge. The water is simply cut off by the hexagon's straight edge.
- Water: calm, still lake water, dark slate grey with a cool blue-teal cast, clearly darker than the grass, desaturated, NOT bright blue, NOT green, NOT pale, NOT shiny. Copy the river surface of image 4: many small, broken, slightly lighter grey-blue ripple flecks and soft pale glints scattered irregularly over the water, as visible as in the rivers of image 4. Calm: NO long regular stripes, NO evenly spaced bands, NO wind streaks, NO waves, NO foam, NO white highlights, NO smooth flat areas. Slightly darker water just along the shore.
- Shore (only where image 1 shows the dark and khaki guide lines): painted exactly like the river banks of image 4, along the guide: right at the water a thin, broken, dark brown undercut shadow, and above it a broken, uneven lip of pale khaki dry earth, wider in some places and absent in others, with small bays and points. Here and there a small grass tuft overhangs the water. Never a smooth curve, never straight, NOT a regular sawtooth, zigzag or scalloped edge, NOT a continuous outline.
- Small details on the shore only, as listed per hexagon below: a small clump of dark olive reeds standing in the shallows, or two or three TINY dull grey-brown stones half sunk in the bank, or a short stretch of pale gravel. Each detail is small (a reed clump about one-sixteenth of the hexagon's width, a stone smaller still), sits on the shore, never out in the open water, never near a hexagon edge.
- Grass: keeps exactly the same colour, texture and brightness as in image 1, right up to the bank: no darker halo, no brighter or greener patch, no shadow band, no mud ring, no path.

Per hexagon:
{cells}

EVERY HEXAGON MUST LOOK DIFFERENT, following its own description and its own water outline from image 1. Do not repeat one ripple pattern or one shore across the sheet.

Keep EXACTLY as they are: every hexagon's position, size and sharp straight outline, where the water, shore and grass lie in each hexagon, and the flat pure magenta background. Same framing and size as image 1. Do not add anything else: no trees, no bushes, no houses, no boats, no piers, no bridges, no roads, no paths, no islands, no lily pads, no animals, no outlines, no borders, no grid lines, no labels, no text, nothing on the magenta. Seen from straight above. Light from the upper left (west-north-west). Soft overcast daylight, muted natural colours, the same palette as image 2: hand-painted with a fine dry-brush grain of short strokes, NOT bright, NOT glossy, NOT airbrushed.
"""

DETAILS = (
    "one clump of reeds on the {a} shore",
    "two tiny stones on the {a} shore",
    "a short stretch of pale gravel on the {a} shore",
    "one clump of reeds on the {a} shore and two tiny stones on the {b} shore",
    "two small clumps of reeds close together on the {a} shore",
    "nothing on the shore but a small overhanging grass tuft on the {a} side",
)
OPEN_WATER = (
    # tone-neutral on purpose: a darker or lighter region inside one tile would
    # repeat across a lake as a lattice of patches
    "fine broken ripple flecks evenly scattered",
    "small broken ripples, a few of them slightly longer",
    "very calm, only faint small flecks and glints",
    "small soft pale glints scattered among fine ripples",
    "tiny broken ripples in loose irregular groups",
    "fine ripples with a few short pale glints",
    "faint irregular ripple flecks, nothing larger",
    "small scattered flecks, no two alike",
)


def _join_words(words: list[str]) -> str:
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + " and " + words[-1]


def hexagon_line(mask: int, seed: int) -> str:
    """The per-hexagon prompt line for a mask (deterministic in `seed`)."""
    rng = np.random.default_rng(seed)
    w = [COMPASS[e] for e in water_edges(mask)]
    land = [COMPASS[e] for e in land_edges(mask)]
    ripple = OPEN_WATER[seed % len(OPEN_WATER)]
    if mask == N_MASKS - 1:
        return (
            "All open water from edge to edge, no shore at all: water reaches all six "
            f"edges; {ripple}."
        )
    if not w:
        first = "A small lake with its shore all round, water nowhere touching an edge"
    elif len(land) == 1:
        first = (
            f"Open water runs right out to every edge except the {land[0]} one, where a "
            f"narrow strip of meadow lies along that edge with the shore along it"
        )
    else:
        sides = "edge" if len(w) == 1 else "edges"
        first = (
            f"Open water runs right out to the {_join_words(w)} {sides} (no shore there); "
            f"shore along the {_join_words(land)} {'side' if len(land) == 1 else 'sides'}"
        )
    a = land[int(rng.integers(len(land)))]
    others = [x for x in land if x != a] or [a]
    b = others[int(rng.integers(len(others)))]
    detail = DETAILS[int(rng.integers(len(DETAILS)))].format(a=a, b=b)
    return f"{first}; {detail}; {ripple}."


def make_prompt(cells: list[tuple[int, int]], layout: tuple[int, int]) -> str:
    """The sheet prompt for (mask, seed) cells in slot order."""
    lines = [f"{i + 1}. {hexagon_line(m, s)}" for i, (m, s) in enumerate(cells)]
    cols, rows = layout
    return PROMPT_HEAD.format(
        n=len(cells), rows=rows, cols=cols, cells="\n".join(lines)
    )


# --------------------------------------------------------------------- jobs ----

LAYOUT = "4x4"
ANCHORS = {"lum": 96.7, "hue": 62.0}


def cell_seed(mask: int, variant: int) -> int:
    """Deterministic guide seed of a (mask, variant) cell."""
    return 17 + 101 * mask + 7919 * variant


def load_rgb128(path: Path) -> np.ndarray:
    """A 128 px tile as float RGB (boundary: fails loudly)."""
    try:
        with Image.open(path) as im:
            arr = np.asarray(im.convert("RGB"), dtype=np.float64)
    except OSError as err:
        raise LakeError(f"{path}: cannot read image: {err}") from err
    if arr.shape != (TILE_PX, TILE_PX, 3):
        raise LakeError(f"{path}: must be 128x128, got {arr.shape[1]}x{arr.shape[0]}")
    return arr


def plains_for(plains: list[Path], mask: int, variant: int) -> Path:
    """The plains base under a (mask, variant) cell."""
    return plains[(mask + variant) % len(plains)]


def parse_cells(spec: str) -> list[tuple[int, int]]:
    """``"63:0,63:1,7:0"`` -> [(mask, variant), ...] (boundary validation)."""
    out = []
    for item in spec.split(","):
        try:
            m, v = (int(x) for x in item.split(":"))
        except ValueError as err:
            raise LakeError(f"bad cell {item!r} (want mask:variant)") from err
        if v < 0 or v > 99:
            raise LakeError(f"variant must be 0..99, got {v}")
        out.append((check_mask(m), v))
    if len(set(out)) != len(out):
        raise LakeError(f"duplicate cells in {spec!r}")
    return out


def run_job(args: argparse.Namespace) -> int:
    """`job`: per-mask bases, the paint-in job, lake.json and the prompt."""
    cells = parse_cells(args.cells)
    if len(cells) > 16:
        raise LakeError(f"{len(cells)} cells do not fit a 4x4 sheet")
    w0 = load_rgb128(args.w0)
    plains = sorted(args.plains_dir.glob("[0-9][0-9].png"))
    if not plains:
        raise LakeError(f"no NN.png plains bases in {args.plains_dir}")
    out = args.out_dir
    (out / "bases").mkdir(parents=True, exist_ok=True)
    job_cells = []
    lake_cells = []
    prompt_cells = []
    for slot, (mask, variant) in enumerate(cells):
        name = tile_name(mask, variant)
        seed = cell_seed(mask, variant)
        geom = ShoreGeometry(mask, seed)
        plains_path = plains_for(plains, mask, variant)
        base = base_tile(geom, load_rgb128(plains_path), sheet_water(w0, seed))
        base_path = out / "bases" / f"{name}.png"
        Image.fromarray(np.clip(np.round(base), 0, 255).astype(np.uint8)).save(
            base_path
        )
        job_cells.append({"name": name, "slot": slot, "base": f"bases/{name}.png"})
        lake_cells.append(
            {
                "name": name,
                "mask": mask,
                "variant": variant,
                "seed": seed,
                "plains": repo_rel(plains_path),
                "plains_sha256": sha256_of(plains_path),
            }
        )
        prompt_cells.append((mask, seed))
    job = {
        "name": args.name,
        "layout": LAYOUT,
        "cells": job_cells,
        "anchors": ANCHORS,
    }
    (out / "job.json").write_text(json.dumps(job, indent=1) + "\n")
    lake = {
        "tool": "scripts/tile-foundry/paintin_lake.py",
        "sheet_w0": repo_rel(args.w0),
        "sheet_w0_sha256": sha256_of(args.w0),
        "geometry": {
            "inset": SHORE_INSET_PX,
            "soft": SHORE_SOFT_PX,
            "soft_fixed": SOFT_FIXED_PX,
            "wobble": WOBBLE_PX,
            "fixed_near_w": list(FIXED_NEAR_W_PX),
        },
        "cells": lake_cells,
    }
    (out / "lake.json").write_text(json.dumps(lake, indent=1) + "\n")
    (out / "prompt.txt").write_text(make_prompt(prompt_cells, (4, 4)))
    print(f"wrote {out}/job.json, lake.json, prompt.txt and {len(cells)} bases")
    return 0


# ---------------------------------------------------------------------- run ----


def save_rgb(arr: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(np.round(arr), 0, 255).astype(np.uint8)).save(path)


def run_run(args: argparse.Namespace) -> int:
    """`run`: sheet -> Codex (or --from-raw) -> cut -> lake pin -> checks -> montage."""
    import paintin as pt  # the paint-in CLI module: sheet, Codex, cut, montage
    from paintin_gate import GateConfig
    from paintin_sheet import build_sheet, load_job, load_sidecar

    work = args.work_dir
    job = load_job(work / "job.json")
    lake = json.loads((work / "lake.json").read_text())
    by_name = {c["name"]: c for c in lake["cells"]}
    cfg = LakePinConfig.from_json(lake.get("pin", {}))
    w0 = load_rgb128(args.w0)
    sheet_path, sidecar_path = build_sheet(job, work)
    sidecar = load_sidecar(sidecar_path)
    gate_cfg = GateConfig.from_json(sidecar["gates"])
    if args.from_raw is not None:
        raw = args.from_raw
        run_json = raw.with_suffix(".run.json")
        codex = (
            json.loads(run_json.read_text())
            if run_json.exists()
            else {"not_recorded": True}
        )
    else:
        raw = work / "raw" / f"{job.name}.png"
        if raw.exists() and not args.overwrite_raw:
            raise LakeError(
                f"{raw} exists; replay it with --from-raw or pass --overwrite-raw"
            )
        refs = [sheet_path, *args.ref]
        print(f"calling Codex with {[repo_rel(r) for r in refs]} ...", flush=True)
        codex = pt.run_codex(work / "prompt.txt", refs, raw).to_json()
        print(
            f"codex thread {codex['codex_thread']}, {codex['wall_seconds']} s",
            flush=True,
        )
    reg, cuts = pt.stage_cut(sidecar, raw, work, gate_cfg)
    tiles_dir = work / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    alpha = output_alpha()
    rows = []
    report_tiles: dict[str, Any] = {}
    normalise: dict[str, Any] = {}
    for cell in sidecar["cells"]:
        name = cell["name"]
        meta = by_name[name]
        mask = meta["mask"]
        geom = ShoreGeometry(mask, meta["seed"])
        target = base_tile(geom, load_rgb128(Path(repo_path_str(meta["plains"]))), w0)
        target = np.round(target)
        phi = geom.tile_phi()
        cut, shift = normalise_water(cuts[name], phi, cfg)
        normalise[name] = shift
        save_rgb(cut, work / "norm" / f"{name}.png")
        res = pin_lake_tile(cut, target, phi, mask, w0, cfg)
        Image.fromarray(res.rgba, "RGBA").save(tiles_dir / f"{name}.png")
        checks = lake_checks(cut, target, res.rgba, phi, mask)
        fit = reg.cells[name]
        passed = all(c.passed or not c.hard for c in checks)
        open_w = (phi > 5.0) & (tile_distance() > 1.0)
        report_tiles[name] = {
            "mask": mask,
            "passed": passed,
            "registration": {
                "residual_tile_px": round(fit.residual_tile_px, 3),
                "iou": round(fit.iou, 4),
            },
            "blend_correlation": round(res.correlation, 3),
            "water": (
                water_texture_stats(res.rgba[..., :3].astype(np.float64), open_w)
                if open_w.sum() > 50
                else {}
            ),
            "ring": (
                ring_profile(res.rgba[..., :3].astype(np.float64), phi, mask)
                if water_edges(mask)
                else []
            ),
            "checks": [asdict(c) for c in checks],
        }
        rows.append(
            (
                f"{name}\n{'PASS' if passed else 'FAIL'}",
                [
                    ("base", pt.as_rgba(target, alpha)),
                    ("cut (Codex)", pt.as_rgba(cuts[name], alpha)),
                    ("final", res.rgba),
                ],
            )
        )
        print(f"{'PASS' if passed else 'FAIL'} {name}")
        for c in checks:
            if not c.passed:
                print(f"    {c.line()}")
    (work / "normalise.json").write_text(json.dumps(normalise, indent=1))
    reg_json = reg.to_json()
    report = {
        "sheet": sidecar["sheet"],
        "w0": repo_rel(args.w0),
        "w0_sha256": sha256_of(args.w0),
        "registration": {
            k: reg_json[k]
            for k in ("scale", "tx", "ty", "max_residual_tile_px", "min_iou")
        },
        "pin": asdict(cfg),
        "tiles": report_tiles,
        "passed": all(t["passed"] for t in report_tiles.values()),
    }
    (work / "report.json").write_text(json.dumps(report, indent=1, default=float))
    review = args.review_dir or work / "review"
    mont = pt.montage(
        rows, 2, review / f"{job.name}.montage.png", f"{job.name}: base | cut | final"
    )
    entry = {
        "name": job.name,
        "when": dt.datetime.now().isoformat(timespec="seconds"),
        "job": repo_rel(job.path),
        "prompt": repo_rel(work / "prompt.txt"),
        "prompt_sha256": hashlib.sha256((work / "prompt.txt").read_bytes()).hexdigest(),
        "sheet": repo_rel(sheet_path),
        "raw": repo_rel(raw),
        "raw_sha256": sha256_of(raw),
        "from_raw": args.from_raw is not None,
        "codex": codex,
        "refs": [repo_rel(sheet_path)] + [repo_rel(r) for r in args.ref],
        "w0": repo_rel(args.w0),
        "passed": [n for n, t in report_tiles.items() if t["passed"]],
        "failed": [n for n, t in report_tiles.items() if not t["passed"]],
    }
    pt.append_manifest(work / "manifest.json", entry)
    print(f"wrote {work / 'report.json'}, {mont}")
    return 0 if report["passed"] else 1


def repo_path_str(stored: str) -> str:
    """Inverse of `repo_rel` as a string path."""
    from paintin_sheet import repo_path

    return str(repo_path(stored))


# ----------------------------------------------------------------------- W0 ----


def open_water_pixels(
    tiles: list[Path], erode_px: int = 4
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Open-water pixels and the 40 px all-water windows of painted lake tiles."""
    from paintin_geom import min_filter

    pix = []
    patches = []
    size = 40
    for path in tiles:
        t = load_rgb128(path)
        core = min_filter((water_mask(t) > 0.5).astype(np.float64), erode_px) > 0.5
        core &= tile_distance() > 3.0
        pix.append(t[core])
        for y0 in range(4, TILE_PX - size - 4, 6):
            for x0 in range(4, TILE_PX - size - 4, 6):
                if core[y0 : y0 + size, x0 : x0 + size].all():
                    patches.append(t[y0 : y0 + size, x0 : x0 + size])
    if not patches:
        raise LakeError("no 40 px all-water windows in the source tiles")
    return np.concatenate(pix), patches


def build_w0_spectral(
    tiles: list[Path], seed: int, isotropy: float
) -> tuple[np.ndarray, dict[str, Any]]:
    """W0 by spectral synthesis from painted water (exactly lattice-periodic)."""
    pix, patches = open_water_pixels(tiles)
    stats = water_stats(pix)
    fields = []
    for i, axis in enumerate(stats.axes):
        proj = [(p - stats.mean) @ axis for p in patches]
        spec = isotropise(measure_spectrum(proj), isotropy)
        fields.append(synth_spectral(spec, seed + i))
    rgb = colourise(fields, stats, stats.mean)
    return rgb, {
        "method": "spectral",
        "sources": [repo_rel(p) for p in tiles],
        "windows": len(patches),
        "seed": seed,
        "isotropy": isotropy,
    }


def build_w0_coset(
    sources: list[Path], radius: float, sharpness: float
) -> tuple[np.ndarray, dict[str, Any]]:
    """W0 from three painted all-water hexes (`coset_blend`)."""
    imgs = [load_rgb128(p) for p in sources]
    return coset_blend(imgs, radius, sharpness), {
        "method": "coset",
        "sources": [repo_rel(p) for p in sources],
        "radius": radius,
        "sharpness": sharpness,
    }


def set_contrast(
    w0: np.ndarray, hp_std: float, sigma: float = 3.0
) -> tuple[np.ndarray, float]:
    """Scale W0 about its mean so its luminance high-pass std is `hp_std`.

    Pointwise, so lattice periodicity is kept; the std is measured away from
    the tile border, where the (non-periodic) blur's padding does not reach.
    """
    lum = luminance(w0)
    hp = lum - gaussian_blur(lum, sigma)
    std = float(hp[tile_distance() > 12.0].std())
    gain = hp_std / std
    mean = w0[tile_distance() > 0.0].mean(axis=0)
    return mean + gain * (w0 - mean), gain


def soften_glints(w0: np.ndarray, knee_sigma: float, ratio: float = 0.4) -> np.ndarray:
    """Compress the brightest flecks of W0 (luminance above mean + knee_sigma *
    std, by `ratio`): every W0 copy carries the same flecks at the same spot,
    and the bright ones are what makes the repeat readable. Pointwise, so the
    lattice periodicity is kept."""
    lum = luminance(w0)
    inside = tile_distance() > 0.0
    mu = float(lum[inside].mean())
    knee = mu + knee_sigma * float(lum[inside].std())
    over = np.maximum(lum - knee, 0.0)
    return w0 - ((1.0 - ratio) * over)[..., None]


def block_review(w0: np.ndarray, out_stem: Path) -> tuple[Path, Path]:
    """W0 alone as a 5x5 block of hexes at 128 and 64 px."""
    import paintin as pt

    rgba = pt.as_rgba(w0, output_alpha())
    p128 = Path(f"{out_stem}.128.png")
    p64 = Path(f"{out_stem}.64.png")
    pt.board_preview(pt.block_placements([rgba] * 25, 5), p128, p64)
    return p128, p64


def run_w0(args: argparse.Namespace) -> int:
    """`w0`: build the shared water tile and its periodicity report."""
    if args.method == "spectral":
        rgb, meta = build_w0_spectral(args.sources, args.seed, args.isotropy)
    else:
        if len(args.sources) != 3:
            raise LakeError("--method coset needs exactly three --sources")
        rgb, meta = build_w0_coset(args.sources, args.radius, args.sharpness)
    if args.glint_knee > 0:
        rgb = soften_glints(rgb, args.glint_knee)
        meta["glint_knee"] = args.glint_knee
    if args.hp_std > 0:
        rgb, gain = set_contrast(rgb, args.hp_std)
        meta["hp_std"] = args.hp_std
        meta["hp_gain"] = round(gain, 4)
    out_img = np.clip(np.round(rgb), 0, 255)
    save_rgb(out_img, args.out)
    region = tile_distance() > 0.0
    meta["periodicity"] = periodicity_report(out_img)
    meta["water"] = water_texture_stats(out_img, region)
    meta["sha256"] = sha256_of(args.out)
    args.out.with_suffix(".json").write_text(json.dumps(meta, indent=1) + "\n")
    print(json.dumps({k: meta[k] for k in ("periodicity", "water")}, indent=1))
    if args.review_stem is not None:
        p128, p64 = block_review(out_img, args.review_stem)
        print(f"wrote {p128} and {p64}")
    return 0


# -------------------------------------------------------------------- apply ----


def run_apply(args: argparse.Namespace) -> int:
    """`apply`: copy picked tiles into a candidate set, with provenance."""
    picks = json.loads(args.picks.read_text())
    out = args.out
    lake_dir = out / "lake"
    if lake_dir.exists():
        shutil.rmtree(lake_dir)
    lake_dir.mkdir(parents=True)
    records = []
    for p in picks["picks"]:
        work = repo_root() / p["work"]
        report = json.loads((work / "report.json").read_text())
        tile = report["tiles"][p["cell"]]
        mask = check_mask(int(tile["mask"]))
        name = tile_name(mask, int(p["variant"]))
        src = work / "tiles" / f"{p['cell']}.png"
        dest = lake_dir / f"{name}.png"
        if dest.exists():
            raise LakeError(f"two picks map to {dest.name}")
        shutil.copyfile(src, dest)
        manifest = json.loads((work / "manifest.json").read_text())["runs"][-1]
        records.append(
            {
                "file": f"lake/{dest.name}",
                "mask": mask,
                "source_tile": repo_rel(src),
                "sheet": manifest["name"],
                "cell": p["cell"],
                "raw": manifest["raw"],
                "codex_thread": manifest["codex"].get("codex_thread"),
                "passed": tile["passed"],
                "warnings": [c["detail"] for c in tile["checks"] if not c["passed"]],
                "note": p.get("note", ""),
                "sha256": sha256_of(dest),
            }
        )
    shutil.copyfile(args.w0, out / "w0.png")
    (out / "picks.json").write_text(
        json.dumps(
            {
                "w0": repo_rel(args.w0),
                "w0_sha256": sha256_of(args.w0),
                "picks": records,
            },
            indent=1,
        )
        + "\n"
    )
    print(f"{len(records)} lake tiles -> {lake_dir}")
    return 0


def repo_root() -> Path:
    return SCRIPT_DIR.parents[1]


# ---------------------------------------------------------------------- CLI ----


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("w0", help="build the shared lattice-periodic water tile")
    w.add_argument("--method", choices=("spectral", "coset"), required=True)
    w.add_argument(
        "--sources",
        type=Path,
        nargs="+",
        required=True,
        help="painted 128 px lake tiles",
    )
    w.add_argument("--seed", type=int, default=1)
    w.add_argument("--isotropy", type=float, default=0.0)
    w.add_argument("--radius", type=float, default=30.0)
    w.add_argument("--sharpness", type=float, default=4.0)
    w.add_argument(
        "--hp-std", type=float, default=0.0, help="scale the ripple contrast to this"
    )
    w.add_argument(
        "--glint-knee", type=float, default=0.0, help="soften flecks above mean + k std"
    )
    w.add_argument("--out", type=Path, required=True)
    w.add_argument(
        "--review-stem",
        type=Path,
        help="writes <stem>.128.png/.64.png: W0 as a 5x5 block",
    )
    j = sub.add_parser("job", help="per-mask bases, paint-in job and prompt")
    j.add_argument("--name", required=True)
    j.add_argument(
        "--cells", required=True, help='"mask:variant,..." in slot order (<= 16)'
    )
    j.add_argument("--w0", type=Path, required=True)
    j.add_argument("--plains-dir", type=Path, required=True)
    j.add_argument("--out-dir", type=Path, required=True)
    r = sub.add_parser("run", help="sheet -> Codex -> cut -> lake pin -> checks")
    r.add_argument("--work-dir", type=Path, required=True)
    r.add_argument(
        "--w0", type=Path, required=True, help="the W0 the tiles are pinned to"
    )
    r.add_argument("--ref", type=Path, action="append", default=[])
    r.add_argument("--from-raw", type=Path)
    r.add_argument("--overwrite-raw", action="store_true")
    r.add_argument("--review-dir", type=Path)
    a = sub.add_parser("apply", help="copy picks into a candidate set")
    a.add_argument("--picks", type=Path, required=True)
    a.add_argument("--w0", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    handlers = {"w0": run_w0, "job": run_job, "run": run_run, "apply": run_apply}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
