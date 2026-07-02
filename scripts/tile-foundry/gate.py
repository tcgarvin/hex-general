#!/usr/bin/env python3
"""Automated quality gates for cut hex tiles: seam continuity, truncated
features, and (optionally) a luminance band. Also provides a `clique`
subcommand that turns pairwise seam results into board-ready tile subsets.

Example:
    python3 gate.py candidates/cand_*.png --hex-width 128 --json
    python3 gate.py candidates/cand_*.png --seam-mode texture --summary
    python3 gate.py clique candidates/cand_*.png --seam-mode texture --pick 4 --out-dir picked/
"""

from __future__ import annotations

import argparse
import glob
import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from hexgeom import AXIAL_DIRECTIONS, neighbor_pixel_offset, shared_edge

DIRECTION_NAMES = ["E", "NE", "NW", "W", "SW", "SE"]  # cosmetic labels for AXIAL_DIRECTIONS order
SEAM_SAMPLES = 64
SEAM_EDGE_MARGIN_PX = 3.0
SEAM_BASELINE_FLOOR = 4.0
TRUNC_BAND_PX = 4
TRUNC_INTERIOR_FLOOR = 2.0

# -- seam metric v2 (structured texture) --------------------------------
# v1's baseline (|A_near - A_far| at 3px separation, `seam_ratio` below)
# measures *local* smoothness. That's right for plains-like textures, but
# wrong for anything with texture structure bigger than a few px (forest
# canopy crowns run ~30px): two samples 3px apart sit on the same crown, so
# the baseline is tiny, while any two decorrelated cuts of the same canopy
# differ by the texture's full contrast — a good-looking join then scores a
# seam ratio of 8-12 and fails. The eye doesn't see decorrelation in matching
# texture; it sees brightness steps. `seam_ratio_texture` + `dc_step` below
# replace the local baseline with a decorrelated one and add an explicit
# DC-level check.
DC_SAMPLE_N = 48
DC_DEPTHS_PX = (1.5, 3.0, 4.5, 6.0)
DC_EDGE_MARGIN_FRAC = 0.10
DEFAULT_DC_K = 0.75
DECORR_BASELINE_N = 24
DECORR_BASELINE_SEED = 20260701  # fixed seed: gate results must be reproducible
# Picked from the pilot data: clean/plains-like swatches show a per-tile
# interior luminance std in the single digits (roughly 4-9), while
# structured canopy renders show 15-30+. 10 sits comfortably in the gap
# between the two populations, so `--seam-mode auto` uses it to decide which
# metric a given pair should be judged by.
SEAM_MODE_AUTO_SIGMA_THRESHOLD = 10.0


def expand_tile_args(patterns: list[str]) -> list[Path]:
    """Expand CLI TILE args (literal paths or globs) into a deduped path list."""
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            paths.extend(Path(m) for m in matches)
        else:
            paths.append(Path(pattern))
    seen: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.append(p)
    return seen


def load_tile(path: Path, hex_width: int) -> np.ndarray:
    """Load a tile as an `HxWx4` uint8 RGBA array; enforce square, `hex_width`-sized."""
    img = Image.open(path)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    arr = np.array(img)
    h, w = arr.shape[:2]
    if h != w:
        raise ValueError(f"{path}: tile must be square, got {w}x{h}")
    if w != hex_width:
        raise ValueError(
            f"{path}: tile is {w}x{h}, expected {hex_width}x{hex_width} (pass --hex-width to match)"
        )
    return arr


def bilinear_sample(img: np.ndarray, x: float, y: float) -> np.ndarray:
    """Bilinearly sample an `HxWxC` float image at `(x, y)`, clamping to the image bounds.

    Sample points near the hex's point-corners (where the hex touches the
    canvas edge) can land a few px outside the canvas after the seam-gate's
    inward/outward offsets; clamping is the standard edge-of-image convention
    here, not a guard against a "can't happen" state.
    """
    h, w = img.shape[:2]
    x = min(max(x, 0.0), w - 1.0)
    y = min(max(y, 0.0), h - 1.0)
    x0, y0 = int(math.floor(x)), int(math.floor(y))
    x1, y1 = min(x0 + 1, w - 1), min(y0 + 1, h - 1)
    fx, fy = x - x0, y - y0
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bot = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return top * (1 - fy) + bot * fy


def edge_sample_points(
    direction_index: int, hex_width: float, n: int = SEAM_SAMPLES, margin_px: float = SEAM_EDGE_MARGIN_PX
) -> tuple[list[tuple[float, float]], tuple[float, float]]:
    """`n` evenly spaced points along the shared edge (excluding `margin_px` near each end),
    plus the edge's outward unit normal (pointing from the centre tile toward the neighbour).
    """
    (x1, y1), (x2, y2) = shared_edge(direction_index, hex_width)
    ex, ey = x2 - x1, y2 - y1
    length = math.hypot(ex, ey)
    t_margin = margin_px / length
    ts = np.linspace(t_margin, 1 - t_margin, n)
    points = [(x1 + ex * t, y1 + ey * t) for t in ts]

    nx, ny = -ey / length, ex / length
    ox, oy = neighbor_pixel_offset(direction_index, hex_width)
    if nx * ox + ny * oy < 0:
        nx, ny = -nx, -ny
    return points, (nx, ny)


def _seam_delta(a_rgb: np.ndarray, b_rgb: np.ndarray, direction_index: int, hex_width: int) -> float:
    """Mean |A-B| straddling the shared edge at 1.5px depth each side — the seam-visible signal.

    Shared by both `seam_ratio` (v1, smooth) and `seam_ratio_texture` (v2,
    structured texture); they differ only in what they divide this by.
    """
    points, (nx, ny) = edge_sample_points(direction_index, hex_width)
    ox, oy = neighbor_pixel_offset(direction_index, hex_width)
    centre = hex_width / 2.0

    seam_diffs = []
    for px, py in points:
        a_near = bilinear_sample(a_rgb, centre + px - 1.5 * nx, centre + py - 1.5 * ny)
        b_near = bilinear_sample(b_rgb, centre + px - ox + 1.5 * nx, centre + py - oy + 1.5 * ny)
        seam_diffs.append(np.abs(a_near - b_near).mean())
    return float(np.mean(seam_diffs))


def seam_ratio(a_rgb: np.ndarray, b_rgb: np.ndarray, direction_index: int, hex_width: int) -> float:
    """Seam-visibility ratio (v1, "smooth" mode) for tile A with tile B as its neighbour.

    ratio = (mean |A - B| straddling the seam) / (A's own mean |contrast| at
    the same 3px separation, floored). ratio near 1 means the seam looks like
    the tile's own internal texture noise; much higher means a visible edge.
    Right for smooth textures (plains); see the module docstring's seam
    metric v2 note for why this under-serves structured texture.
    """
    points, (nx, ny) = edge_sample_points(direction_index, hex_width)
    centre = hex_width / 2.0

    seam_delta = _seam_delta(a_rgb, b_rgb, direction_index, hex_width)

    baseline_diffs = []
    for px, py in points:
        a_near = bilinear_sample(a_rgb, centre + px - 1.5 * nx, centre + py - 1.5 * ny)
        a_far = bilinear_sample(a_rgb, centre + px - 4.5 * nx, centre + py - 4.5 * ny)
        baseline_diffs.append(np.abs(a_near - a_far).mean())

    baseline = max(float(np.mean(baseline_diffs)), SEAM_BASELINE_FLOOR)
    return seam_delta / baseline


def decorrelated_baseline(
    a_rgb: np.ndarray, hex_width: int, n_samples: int = DECORR_BASELINE_N, seed: int = DECORR_BASELINE_SEED
) -> float:
    """Estimate "how different do two same-texture patches look at seam-scale separation?"

    Reuses the six real hex directions' `neighbor_pixel_offset` as the sample
    displacement (the same physical jump a real seam makes) but anchors it at
    `n_samples` random interior positions within this SAME tile, instead of
    at the tile's true boundary: "would a randomly placed internal boundary,
    cutting through this tile's own texture, look this different?" A fixed
    RNG seed keeps gate results reproducible across runs.
    """
    rng = np.random.default_rng(seed)
    h, w = a_rgb.shape[:2]
    margin = hex_width * 0.15
    diffs = []
    for _ in range(n_samples):
        direction_index = int(rng.integers(0, len(AXIAL_DIRECTIONS)))
        ox, oy = neighbor_pixel_offset(direction_index, hex_width)
        lo_x, hi_x = margin + max(0.0, -ox), w - margin - max(0.0, ox)
        lo_y, hi_y = margin + max(0.0, -oy), h - margin - max(0.0, oy)
        cx = rng.uniform(lo_x, hi_x) if hi_x > lo_x else w / 2.0
        cy = rng.uniform(lo_y, hi_y) if hi_y > lo_y else h / 2.0
        near = bilinear_sample(a_rgb, cx, cy)
        far = bilinear_sample(a_rgb, cx + ox, cy + oy)
        diffs.append(float(np.abs(near - far).mean()))
    return float(np.mean(diffs))


def seam_ratio_texture(
    a_rgb: np.ndarray, b_rgb: np.ndarray, direction_index: int, hex_width: int, baseline: float
) -> float:
    """Seam-visibility ratio (v2, "texture" mode): same seam delta, decorrelated baseline.

    `baseline` is `decorrelated_baseline(a_rgb, ...)`, floored, computed once
    per tile A by the caller (it doesn't depend on direction or on B).
    """
    seam_delta = _seam_delta(a_rgb, b_rgb, direction_index, hex_width)
    return seam_delta / baseline


def _pixel_luminance(pixel: np.ndarray) -> float:
    """Luminance of a single (bilinear-sampled) RGB pixel, same weights used elsewhere."""
    return float(0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2])


def dc_step(
    a_rgb: np.ndarray,
    b_rgb: np.ndarray,
    direction_index: int,
    hex_width: int,
    n: int = DC_SAMPLE_N,
    margin_frac: float = DC_EDGE_MARGIN_FRAC,
    depths: tuple[float, ...] = DC_DEPTHS_PX,
) -> float:
    """Mean luminance step across the seam: A's edge band minus B's, as neighbour.

    Unlike `_seam_delta`'s single-depth near samples, this averages luminance
    over `n` points along the shared edge (trimming `margin_frac` off each
    end) and over several `depths` a few px inside each tile — robust to
    per-pixel texture noise (canopy speckle) while still catching a
    systematic brightness-level mismatch between the two tiles, which is
    what the eye actually notices on a textured join.
    """
    (x1, y1), (x2, y2) = shared_edge(direction_index, hex_width)
    ex, ey = x2 - x1, y2 - y1
    length = math.hypot(ex, ey)
    ts = np.linspace(margin_frac, 1 - margin_frac, n)
    edge_points = [(x1 + ex * t, y1 + ey * t) for t in ts]

    nx, ny = -ey / length, ex / length
    ox, oy = neighbor_pixel_offset(direction_index, hex_width)
    if nx * ox + ny * oy < 0:
        nx, ny = -nx, -ny

    centre = hex_width / 2.0
    a_vals = []
    b_vals = []
    for px, py in edge_points:
        for depth in depths:
            a_vals.append(
                _pixel_luminance(bilinear_sample(a_rgb, centre + px - depth * nx, centre + py - depth * ny))
            )
            b_vals.append(
                _pixel_luminance(
                    bilinear_sample(b_rgb, centre + px - ox + depth * nx, centre + py - oy + depth * ny)
                )
            )
    return float(np.mean(a_vals) - np.mean(b_vals))


def erode_mask(mask: np.ndarray, iterations: int) -> np.ndarray:
    """Binary-erode a boolean mask by `iterations` px, 4-neighbour roll-AND."""
    for _ in range(iterations):
        mask = (
            mask
            & np.roll(mask, 1, axis=0)
            & np.roll(mask, -1, axis=0)
            & np.roll(mask, 1, axis=1)
            & np.roll(mask, -1, axis=1)
        )
    return mask


def gradient_magnitude(luminance: np.ndarray) -> np.ndarray:
    """Central-difference gradient magnitude of a 2D luminance array (numpy only, no scipy)."""
    gy, gx = np.gradient(luminance.astype(np.float64))
    return np.hypot(gx, gy)


def truncation_score(tile: np.ndarray, hex_width: int) -> float:
    """(mean gradient near the hex boundary) / (mean gradient in the deep interior, floored).

    A high score means a salient feature (edge/blob) is sliced by the hex
    boundary rather than sitting inside it.
    """
    rgb = tile[..., :3].astype(np.float64)
    alpha = tile[..., 3]
    luminance = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    grad = gradient_magnitude(luminance)

    hex_bool = alpha > 127
    interior = erode_mask(hex_bool, TRUNC_BAND_PX)
    band = hex_bool & ~interior

    if not band.any():
        raise ValueError(f"hex boundary band is empty for hex_width {hex_width} (too small for a {TRUNC_BAND_PX}px band)")
    if not interior.any():
        raise ValueError(f"hex interior is empty after eroding {TRUNC_BAND_PX}px for hex_width {hex_width}")

    band_mean = float(grad[band].mean())
    interior_mean = max(float(grad[interior].mean()), TRUNC_INTERIOR_FLOOR)
    return band_mean / interior_mean


def mean_hex_luminance(tile: np.ndarray) -> float:
    """Alpha-weighted mean luminance over a tile's hex pixels."""
    rgb = tile[..., :3].astype(np.float64)
    alpha = tile[..., 3].astype(np.float64) / 255.0
    luminance = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    total_weight = alpha.sum()
    if total_weight <= 0:
        raise ValueError("tile has no nonzero-alpha pixels")
    return float((luminance * alpha).sum() / total_weight)


def interior_luminance_std(tile: np.ndarray, hex_width: int, erode_px: int = TRUNC_BAND_PX) -> float:
    """Std of luminance over a tile's eroded interior — the texture-noise scale sigma_tex.

    Used to (a) pick smooth vs texture seam-metric mode in `--seam-mode
    auto`, and (b) size the DC-step check's tolerance: a busier texture can
    hide a bigger brightness step before it reads as a visible seam.
    """
    rgb = tile[..., :3].astype(np.float64)
    alpha = tile[..., 3]
    luminance = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    hex_bool = alpha > 127
    interior = erode_mask(hex_bool, erode_px)
    if not interior.any():
        raise ValueError(f"hex interior is empty after eroding {erode_px}px for hex_width {hex_width}")
    return float(luminance[interior].std())


def run_seam_gate(
    tiles: list[tuple[Path, np.ndarray]],
    hex_width: int,
    seam_max: float,
    seam_mode: str = "auto",
    dc_k: float = DEFAULT_DC_K,
) -> tuple[bool, dict]:
    """Seam gate over every ordered pair (including self-pairs) and all 6 directions.

    `seam_mode` is `"smooth"` (v1 ratio only, unchanged behaviour), `"texture"`
    (v2 decorrelated-baseline ratio + the DC-step brightness check), or
    `"auto"` (per-pair: texture mode if the pair's mean sigma_tex exceeds
    `SEAM_MODE_AUTO_SIGMA_THRESHOLD`, else smooth).
    """
    sigmas = {p.name: interior_luminance_std(arr, hex_width) for p, arr in tiles}
    results: dict[tuple[str, str], dict] = {}
    overall_pass = True
    for pi, ai in tiles:
        a_rgb = ai[..., :3].astype(np.float64)
        texture_baseline_a = max(decorrelated_baseline(a_rgb, hex_width), SEAM_BASELINE_FLOOR)
        for pj, aj in tiles:
            b_rgb = aj[..., :3].astype(np.float64)
            sigma_tex = (sigmas[pi.name] + sigmas[pj.name]) / 2.0
            mode = seam_mode
            if mode == "auto":
                mode = "texture" if sigma_tex > SEAM_MODE_AUTO_SIGMA_THRESHOLD else "smooth"

            if mode == "texture":
                per_direction_ratio = [
                    seam_ratio_texture(a_rgb, b_rgb, d, hex_width, texture_baseline_a)
                    for d in range(len(AXIAL_DIRECTIONS))
                ]
            else:
                per_direction_ratio = [
                    seam_ratio(a_rgb, b_rgb, d, hex_width) for d in range(len(AXIAL_DIRECTIONS))
                ]

            worst_dir = int(np.argmax(per_direction_ratio))
            worst_ratio = per_direction_ratio[worst_dir]
            ratio_ok = worst_ratio <= seam_max

            entry = {
                "mode": mode,
                "sigma_tex": sigma_tex,
                "worst_ratio": worst_ratio,
                "worst_direction": DIRECTION_NAMES[worst_dir],
                "per_direction": dict(zip(DIRECTION_NAMES, per_direction_ratio)),
                "ratio_pass": ratio_ok,
            }

            ok = ratio_ok
            if mode == "texture":
                per_direction_dc = [dc_step(a_rgb, b_rgb, d, hex_width) for d in range(len(AXIAL_DIRECTIONS))]
                worst_dc_dir = int(np.argmax([abs(v) for v in per_direction_dc]))
                worst_dc = per_direction_dc[worst_dc_dir]
                dc_ok = abs(worst_dc) <= dc_k * sigma_tex
                entry["dc_step"] = worst_dc
                entry["dc_direction"] = DIRECTION_NAMES[worst_dc_dir]
                entry["dc_pass"] = dc_ok
                ok = ok and dc_ok

            entry["pass"] = ok
            overall_pass = overall_pass and ok
            results[(pi.name, pj.name)] = entry
    return overall_pass, results


def run_truncation_gate(tiles: list[tuple[Path, np.ndarray]], hex_width: int, trunc_max: float) -> tuple[bool, dict]:
    results: dict[str, dict] = {}
    overall_pass = True
    for path, arr in tiles:
        score = truncation_score(arr, hex_width)
        ok = score <= trunc_max
        overall_pass = overall_pass and ok
        results[path.name] = {"score": score, "pass": ok}
    return overall_pass, results


def run_luminance_gate(
    tiles: list[tuple[Path, np.ndarray]], lum_min: float | None, lum_max: float | None
) -> tuple[bool, dict]:
    lo = lum_min if lum_min is not None else -math.inf
    hi = lum_max if lum_max is not None else math.inf
    results: dict[str, dict] = {}
    overall_pass = True
    for path, arr in tiles:
        mean_lum = mean_hex_luminance(arr)
        ok = lo <= mean_lum <= hi
        overall_pass = overall_pass and ok
        results[path.name] = {"mean_luminance": mean_lum, "pass": ok}
    return overall_pass, results


def print_report(
    seam_results: dict, trunc_results: dict, lum_results: dict, seam_max: float, trunc_max: float
) -> None:
    print("== seam gate ==")
    print(f"{'pair':<42}{'mode':>8}{'ratio':>9}{'dir':>5}{'dc':>9}{'status':>8}")
    overall_worst = -1.0
    overall_worst_key = None
    for (a, b), v in seam_results.items():
        status = "PASS" if v["pass"] else "FAIL"
        pair = f"{a}->{b}"
        dc_str = f"{v['dc_step']:.2f}" if "dc_step" in v else "-"
        print(f"{pair:<42}{v['mode']:>8}{v['worst_ratio']:>9.3f}{v['worst_direction']:>5}{dc_str:>9}{status:>8}")
        if v["worst_ratio"] > overall_worst:
            overall_worst = v["worst_ratio"]
            overall_worst_key = (a, b, v["worst_direction"])
    if overall_worst_key:
        print(
            f"worst seam ratio: {overall_worst:.3f} "
            f"({overall_worst_key[0]}->{overall_worst_key[1]}, {overall_worst_key[2]}) [max {seam_max}]"
        )

    print("\n== truncation gate ==")
    print(f"{'tile':<32}{'score':>10}{'status':>8}")
    for name, v in trunc_results.items():
        status = "PASS" if v["pass"] else "FAIL"
        print(f"{name:<32}{v['score']:>10.3f}{status:>8} [max {trunc_max}]")

    if lum_results:
        print("\n== luminance gate ==")
        print(f"{'tile':<32}{'mean_lum':>10}{'status':>8}")
        for name, v in lum_results.items():
            status = "PASS" if v["pass"] else "FAIL"
            print(f"{name:<32}{v['mean_luminance']:>10.2f}{status:>8}")
    print()


def print_summary(seam_results: dict, trunc_results: dict, lum_results: dict, max_lines: int = 20) -> bool:
    """Compact report: per-gate pass/fail counts, failing entries only (capped), final verdict.

    Returns the overall pass/fail so callers don't need to recompute it.
    """
    seam_fail = [k for k, v in seam_results.items() if not v["pass"]]
    trunc_fail = [name for name, v in trunc_results.items() if not v["pass"]]
    lum_fail = [name for name, v in lum_results.items() if not v["pass"]]

    print(f"seam: {len(seam_results) - len(seam_fail)}/{len(seam_results)} pass")
    print(f"truncation: {len(trunc_results) - len(trunc_fail)}/{len(trunc_results)} pass")
    if lum_results:
        print(f"luminance: {len(lum_results) - len(lum_fail)}/{len(lum_results)} pass")

    lines_shown = 0
    if seam_fail:
        print("\nfailing seam pairs:")
        for a, b in seam_fail:
            if lines_shown >= max_lines:
                print(f"  ... and {len(seam_fail) - lines_shown} more")
                break
            v = seam_results[(a, b)]
            dc_str = f" dc={v['dc_step']:.2f}" if "dc_step" in v else ""
            print(f"  {a}->{b}: mode={v['mode']} ratio={v['worst_ratio']:.3f} dir={v['worst_direction']}{dc_str}")
            lines_shown += 1

    if trunc_fail:
        print("\nfailing truncation tiles:")
        for name in trunc_fail:
            if lines_shown >= max_lines:
                print(f"  ... and {len(trunc_fail) - lines_shown} more")
                break
            print(f"  {name}: score={trunc_results[name]['score']:.3f}")
            lines_shown += 1

    if lum_fail:
        print("\nfailing luminance tiles:")
        for name in lum_fail:
            if lines_shown >= max_lines:
                print(f"  ... and {len(lum_fail) - lines_shown} more")
                break
            print(f"  {name}: mean_lum={lum_results[name]['mean_luminance']:.2f}")
            lines_shown += 1

    overall_pass = not seam_fail and not trunc_fail and not lum_fail
    print("\nPASS" if overall_pass else "\nFAIL")
    return overall_pass


def parse_args_check(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Automated quality gates for cut hex tiles.")
    ap.add_argument("tiles", nargs="+", help="tile PNG files or globs")
    ap.add_argument("--hex-width", type=int, default=128, help="expected tile canvas size (default 128)")
    ap.add_argument("--seam-max", type=float, default=2.0, help="max seam ratio before FAIL (default 2.0)")
    ap.add_argument("--trunc-max", type=float, default=1.5, help="max truncation score before FAIL (default 1.5)")
    ap.add_argument("--lum-min", type=float, default=None, help="min mean hex luminance (enables the gate)")
    ap.add_argument("--lum-max", type=float, default=None, help="max mean hex luminance (enables the gate)")
    ap.add_argument(
        "--seam-mode",
        choices=["smooth", "texture", "auto"],
        default="auto",
        help="seam metric: smooth (v1, local baseline), texture (v2, decorrelated baseline + DC-step), "
        "auto (per-pair, by sigma_tex) (default auto)",
    )
    ap.add_argument(
        "--dc-k", type=float, default=DEFAULT_DC_K, help=f"DC-step tolerance in units of sigma_tex (default {DEFAULT_DC_K})"
    )
    output_group = ap.add_mutually_exclusive_group()
    output_group.add_argument("--json", action="store_true", help="emit a machine-readable JSON summary")
    output_group.add_argument(
        "--summary", action="store_true", help="compact output: pass/fail counts + failing entries only (max 20 lines)"
    )
    return ap.parse_args(argv)


def main_check(argv: list[str] | None = None) -> int:
    args = parse_args_check(argv)

    paths = expand_tile_args(args.tiles)
    if not paths:
        print("no tiles matched", file=sys.stderr)
        return 1

    tiles = [(p, load_tile(p, args.hex_width)) for p in paths]

    seam_pass, seam_results = run_seam_gate(tiles, args.hex_width, args.seam_max, args.seam_mode, args.dc_k)
    trunc_pass, trunc_results = run_truncation_gate(tiles, args.hex_width, args.trunc_max)
    lum_results: dict = {}
    lum_pass = True
    if args.lum_min is not None or args.lum_max is not None:
        lum_pass, lum_results = run_luminance_gate(tiles, args.lum_min, args.lum_max)

    overall_pass = seam_pass and trunc_pass and lum_pass

    if args.json:
        print(
            json.dumps(
                {
                    "seam": {f"{a}->{b}": v for (a, b), v in seam_results.items()},
                    "truncation": trunc_results,
                    "luminance": lum_results,
                    "pass": overall_pass,
                },
                indent=2,
            )
        )
    elif args.summary:
        overall_pass = print_summary(seam_results, trunc_results, lum_results)
    else:
        print_report(seam_results, trunc_results, lum_results, args.seam_max, args.trunc_max)
        print("PASS" if overall_pass else "FAIL")

    return 0 if overall_pass else 1


def build_compatibility_graph(names: list[str], seam_results: dict) -> dict[str, set[str]]:
    """node -> set of compatible neighbour names (edge iff both pairwise directions pass)."""
    compat: dict[str, set[str]] = {n: set() for n in names}
    for a in names:
        for b in names:
            if a == b:
                continue
            if seam_results[(a, b)]["pass"] and seam_results[(b, a)]["pass"]:
                compat[a].add(b)
    return compat


def greedy_clique(compat: dict[str, set[str]], seed: str) -> list[str]:
    """Greedily grow a mutually-compatible subset starting from `seed`."""
    clique = [seed]
    candidates = set(compat[seed])
    for node in sorted(compat.keys()):
        if node == seed or node not in candidates:
            continue
        if all(node in compat[member] for member in clique):
            clique.append(node)
            candidates &= compat[node]
    return clique


def largest_clique_all_seeds(compat: dict[str, set[str]]) -> list[str]:
    """The largest greedy clique found, trying every node as the starting seed."""
    best: list[str] = []
    for seed in sorted(compat.keys()):
        clique = greedy_clique(compat, seed)
        if len(clique) > len(best):
            best = clique
    return best


def seam_badness(entry: dict, seam_max: float, dc_k: float) -> float:
    """Normalize a pairwise seam-gate entry to gate-threshold units (1.0 == right at the FAIL line)."""
    ratio_badness = entry["worst_ratio"] / seam_max
    if "dc_step" in entry and entry["sigma_tex"] > 0:
        dc_badness = abs(entry["dc_step"]) / (dc_k * entry["sigma_tex"])
        return max(ratio_badness, dc_badness)
    return ratio_badness


def parse_args_clique(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="gate.py clique", description="Find mutually-compatible tile subsets from pairwise seam results."
    )
    ap.add_argument("tiles", nargs="+", help="tile PNG files or globs")
    ap.add_argument("--hex-width", type=int, default=128, help="expected tile canvas size (default 128)")
    ap.add_argument("--seam-max", type=float, default=2.0, help="max seam ratio before FAIL (default 2.0)")
    ap.add_argument(
        "--seam-mode", choices=["smooth", "texture", "auto"], default="auto", help="seam metric mode (default auto)"
    )
    ap.add_argument(
        "--dc-k", type=float, default=DEFAULT_DC_K, help=f"DC-step tolerance in units of sigma_tex (default {DEFAULT_DC_K})"
    )
    ap.add_argument("--lum-min", type=float, default=None, help="luminance-band low bound (reused for pool centrality)")
    ap.add_argument("--lum-max", type=float, default=None, help="luminance-band high bound (reused for pool centrality)")
    ap.add_argument("--pick", type=int, default=None, help="exhaustively pick the best N-subset (requires --out-dir)")
    ap.add_argument("--out-dir", type=Path, default=None, help="directory to copy the --pick winners into")
    ap.add_argument(
        "--pool-size",
        type=int,
        default=20,
        help="candidate pool size (by luminance-band centrality) for the exhaustive --pick search (default 20)",
    )
    return ap.parse_args(argv)


def main_clique(argv: list[str] | None = None) -> int:
    args = parse_args_clique(argv)

    paths = expand_tile_args(args.tiles)
    if not paths:
        print("no tiles matched", file=sys.stderr)
        return 1

    tiles = [(p, load_tile(p, args.hex_width)) for p in paths]
    names = [p.name for p in paths]
    _, seam_results = run_seam_gate(tiles, args.hex_width, args.seam_max, args.seam_mode, args.dc_k)

    compat = build_compatibility_graph(names, seam_results)
    best_clique = largest_clique_all_seeds(compat)
    print(f"largest mutually-compatible subset: {len(best_clique)}/{len(names)} tiles")
    for name in best_clique:
        print(f"  {name}")

    if args.pick is None and args.out_dir is None:
        return 0

    if args.pick is None or args.out_dir is None:
        print("--pick and --out-dir must be given together", file=sys.stderr)
        return 1

    lums = {p.name: mean_hex_luminance(arr) for p, arr in tiles}
    if args.lum_min is not None or args.lum_max is not None:
        lo = args.lum_min if args.lum_min is not None else args.lum_max
        hi = args.lum_max if args.lum_max is not None else args.lum_min
        lum_center = (lo + hi) / 2.0
    else:
        lum_center = float(np.mean(list(lums.values())))

    pool = sorted(names, key=lambda n: abs(lums[n] - lum_center))[: args.pool_size]
    if len(pool) < args.pick:
        print(f"pool of {len(pool)} candidates (by luminance centrality) is smaller than --pick {args.pick}", file=sys.stderr)
        return 1

    best_subset: tuple[str, ...] | None = None
    best_score = math.inf
    for combo in itertools.combinations(pool, args.pick):
        score = max(seam_badness(seam_results[(a, b)], args.seam_max, args.dc_k) for a in combo for b in combo)
        if score < best_score:
            best_score = score
            best_subset = combo

    if best_subset is None:
        raise RuntimeError("no combination evaluated — this should be unreachable given the pool-size check above")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    by_name = {p.name: p for p in paths}
    manifest_tiles = []
    for i, name in enumerate(best_subset):
        dst = args.out_dir / f"{i:02d}.png"
        dst.write_bytes(by_name[name].read_bytes())
        manifest_tiles.append({"index": i, "source": name, "mean_luminance": lums[name]})

    manifest = {
        "pick": args.pick,
        "seam_mode": args.seam_mode,
        "seam_max": args.seam_max,
        "dc_k": args.dc_k,
        "worst_pairwise_metric": best_score,
        "tiles": manifest_tiles,
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"picked {args.pick} tiles (worst pairwise metric {best_score:.3f}) -> {args.out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "clique":
        return main_clique(argv[1:])
    return main_check(argv)


if __name__ == "__main__":
    raise SystemExit(main())
