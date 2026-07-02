#!/usr/bin/env python3
"""Cut hex-masked tile candidates from a continuous terrain swatch.

Slides a `hex_width`-square window over an AI-generated swatch on a stride
grid, applies the hex alpha mask (`hexgeom.hex_mask`) to each window, and
writes one RGBA PNG candidate per window. Prints per-candidate colour metrics
so a human — or `gate.py` — can triage without opening every image.

Example:
    python3 cut.py swatch.png --out-dir candidates/ --hex-width 128 --bleed 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from hexgeom import hex_mask


def rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized RGB (float, 0..1, `...x3`) -> `(H in [0,360), S in [0,1], V in [0,1])`.

    Follows the same algorithm as `colorsys.rgb_to_hsv`, vectorized over numpy
    arrays (there's no scipy in this environment, and colorsys is scalar-only).
    """
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    v = maxc
    delta = maxc - minc
    safe_max = np.where(maxc == 0, 1.0, maxc)
    s = np.where(maxc == 0, 0.0, delta / safe_max)

    safe_delta = np.where(delta == 0, 1.0, delta)
    rc = (maxc - r) / safe_delta
    gc = (maxc - g) / safe_delta
    bc = (maxc - b) / safe_delta

    h = np.zeros_like(r)
    h = np.where(r == maxc, bc - gc, h)
    h = np.where(g == maxc, 2.0 + rc - bc, h)
    h = np.where(b == maxc, 4.0 + gc - rc, h)
    h = (h / 6.0) % 1.0
    h = np.where(delta == 0, 0.0, h)
    return h * 360.0, s, v


def hex_pixel_stats(rgb: np.ndarray, mask: np.ndarray) -> tuple[float, float, float]:
    """Alpha-weighted mean luminance, and median hue/saturation, over the hex pixels.

    `rgb` is `HxWx3` uint8, `mask` is `HxW` uint8 alpha (0..255). Luminance is
    weighted by alpha (soft edge pixels count proportionally less); hue and
    saturation use the plain median over pixels with any nonzero alpha.
    """
    weights = mask.astype(np.float64) / 255.0
    total_weight = weights.sum()
    if total_weight <= 0:
        raise ValueError("mask has no nonzero-alpha pixels")

    r = rgb[..., 0].astype(np.float64)
    g = rgb[..., 1].astype(np.float64)
    b = rgb[..., 2].astype(np.float64)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    mean_luminance = float((luminance * weights).sum() / total_weight)

    hue, sat, _val = rgb_to_hsv(rgb.astype(np.float64) / 255.0)
    inside = mask > 0
    median_hue = float(np.median(hue[inside]))
    median_sat = float(np.median(sat[inside]))
    return mean_luminance, median_hue, median_sat


def compute_windows(swatch_w: int, swatch_h: int, hex_width: int, stride: int) -> list[tuple[int, int]]:
    """Top-left `(x, y)` origins of `hex_width`-square windows on the stride grid, in-bounds."""
    xs = range(0, swatch_w - hex_width + 1, stride)
    ys = range(0, swatch_h - hex_width + 1, stride)
    return [(x, y) for y in ys for x in xs]


def dilate_mask(mask: np.ndarray, iterations: int) -> np.ndarray:
    """Binary-dilate a boolean mask by `iterations` px, 4-neighbour roll-OR.

    Mirrors `gate.erode_mask` but grows the True region instead of shrinking
    it (OR instead of AND across the four rolled copies).
    """
    for _ in range(iterations):
        mask = (
            mask
            | np.roll(mask, 1, axis=0)
            | np.roll(mask, -1, axis=0)
            | np.roll(mask, 1, axis=1)
            | np.roll(mask, -1, axis=1)
        )
    return mask


def bleed_mask(mask: np.ndarray, bleed: int) -> np.ndarray:
    """Grow a hex alpha mask's opaque region outward by `bleed` px.

    The antialiased hex edge from `hexgeom.hex_mask` fades from opaque to
    transparent over roughly a pixel; two abutting tiles' fades don't sum
    back to a solid seam on the board, leaving a faint hairline. Bleeding
    the mask makes the rim pixels fully opaque (letting real swatch content
    that was previously masked out show through instead), so adjoining tiles
    overlap by `bleed` px of real, opaque content and hide the seam.
    """
    if bleed <= 0:
        return mask
    core = mask > 127
    grown = dilate_mask(core, bleed)
    return np.where(grown, np.uint8(255), mask)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Cut hex-masked tile candidates from a swatch.")
    ap.add_argument("swatch", type=Path, help="continuous terrain swatch image")
    ap.add_argument("--out-dir", required=True, type=Path, help="directory for candidate PNGs")
    ap.add_argument("--hex-width", type=int, default=128, help="point-to-point hex width in px (default 128)")
    ap.add_argument("--stride", type=int, default=None, help="window stride in px (default hex_width // 2)")
    ap.add_argument("--limit", type=int, default=None, help="cap the number of candidates written")
    ap.add_argument(
        "--bleed",
        type=int,
        default=0,
        help="dilate the hex mask this many px so tiles carry an opaque overlap rim (default 0)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.hex_width <= 0:
        print("--hex-width must be positive", file=sys.stderr)
        return 1
    if args.bleed < 0:
        print("--bleed must not be negative", file=sys.stderr)
        return 1
    stride = args.stride if args.stride is not None else args.hex_width // 2
    if stride <= 0:
        print("--stride must be positive", file=sys.stderr)
        return 1
    if not args.swatch.is_file():
        print(f"swatch not found: {args.swatch}", file=sys.stderr)
        return 1

    swatch = Image.open(args.swatch).convert("RGB")
    sw, sh = swatch.size
    swatch_arr = np.array(swatch)

    windows = compute_windows(sw, sh, args.hex_width, stride)
    if not windows:
        print(f"swatch {sw}x{sh} is too small for hex_width {args.hex_width}", file=sys.stderr)
        return 1
    if args.limit is not None:
        windows = windows[: args.limit]

    mask = bleed_mask(hex_mask(args.hex_width, args.hex_width), args.bleed)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for x, y in windows:
        crop = swatch_arr[y : y + args.hex_width, x : x + args.hex_width]
        rgba = np.dstack([crop, mask])
        out_path = args.out_dir / f"cand_x{x:04d}_y{y:04d}.png"
        Image.fromarray(rgba, mode="RGBA").save(out_path)
        lum, hue, sat = hex_pixel_stats(crop, mask)
        rows.append((out_path.name, x, y, lum, hue, sat))

    print(f"{'file':<28}{'x':>6}{'y':>6}{'lum':>10}{'hue':>10}{'sat':>8}")
    for name, x, y, lum, hue, sat in rows:
        print(f"{name:<28}{x:>6}{y:>6}{lum:>10.2f}{hue:>10.1f}{sat:>8.3f}")
    print(f"wrote {len(rows)} candidates to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
