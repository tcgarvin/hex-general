#!/usr/bin/env python3
"""Find clean style-reference crops in a piece of concept art.

Slides a window over the image, scores each window by how much of it falls in
a target HSV range (tuned by default for clean grass) minus contamination
(near-black pixels, or strongly-saturated red/blue outliers), and greedily
picks the top-k non-overlapping windows.

Example:
    python3 pick-refs.py concept.png --out-dir refs/ --k 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image


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


def outlier_fraction(rgb: np.ndarray, sat: np.ndarray, val: np.ndarray) -> float:
    """Fraction of pixels that look like a targeting reticle / unit sprite artifact.

    Two independent tells, either sufficient on its own: a strongly red- or
    blue-shifted, saturated pixel (a targeting reticle or team-color unit
    marker), or a near-black pixel (sprite outline / drop shadow) — neither
    of which belongs in a clean terrain style reference.
    """
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    safe_g = np.where(g <= 0, 1e-6, g)
    color_cast = (r > 1.25 * safe_g) | (b > 1.25 * safe_g)
    saturated_cast = color_cast & (sat > 0.35)
    near_black = val < 0.18
    return float((saturated_cast | near_black).mean())


def window_score(
    rgb: np.ndarray,
    hue: np.ndarray,
    sat: np.ndarray,
    val: np.ndarray,
    hue_min: float,
    hue_max: float,
    sat_min: float,
    val_min: float,
    val_max: float,
    outlier_weight: float,
) -> float:
    """Fraction of target-range pixels, minus contaminant fraction, minus weighted outlier fraction."""
    in_range = (hue >= hue_min) & (hue <= hue_max) & (sat >= sat_min) & (val >= val_min) & (val <= val_max)
    contaminant = (val < 0.15) | ((sat > 0.5) & ((hue < 30) | (hue > 200)))
    n = hue.size
    base = float(in_range.sum() - contaminant.sum()) / n
    return base - outlier_weight * outlier_fraction(rgb, sat, val)


def overlap_fraction(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """Fraction of window `a`'s area covered by window `b` (each `(x, y, window_size)`)."""
    ax, ay, aw = a
    bx, by, bw = b
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + aw, by + bw)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    return (iw * ih) / (aw * aw)


def pick_top_k(
    scores: list[tuple[int, int, float]], window: int, k: int, max_overlap: float = 0.25
) -> list[tuple[int, int, float]]:
    """Greedily pick the top-k windows by score, suppressing ones overlapping a pick > `max_overlap`."""
    ordered = sorted(scores, key=lambda t: t[2], reverse=True)
    picked: list[tuple[int, int, float]] = []
    for x, y, score in ordered:
        if len(picked) >= k:
            break
        if any(overlap_fraction((x, y, window), (px, py, window)) > max_overlap for px, py, _ in picked):
            continue
        picked.append((x, y, score))
    return picked


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Find clean style-reference crops in concept art.")
    ap.add_argument("image", type=Path, help="concept art image")
    ap.add_argument("--out-dir", required=True, type=Path, help="directory for the picked crops")
    ap.add_argument("--window", type=int, default=256, help="window size in px (default 256)")
    ap.add_argument("--stride", type=int, default=128, help="window stride in px (default 128)")
    ap.add_argument("--k", type=int, default=3, help="number of crops to pick (default 3)")
    ap.add_argument("--hue-min", type=float, default=60.0, help="target hue range low bound (default 60, grass)")
    ap.add_argument("--hue-max", type=float, default=150.0, help="target hue range high bound (default 150, grass)")
    ap.add_argument("--sat-min", type=float, default=0.2, help="target saturation low bound (default 0.2)")
    ap.add_argument("--val-min", type=float, default=0.25, help="target value low bound (default 0.25)")
    ap.add_argument("--val-max", type=float, default=0.85, help="target value high bound (default 0.85)")
    ap.add_argument(
        "--outlier-weight",
        type=float,
        default=8.0,
        help="penalty weight for reticle/sprite-like outlier pixels, subtracted from the score (default 8.0)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.image.is_file():
        print(f"image not found: {args.image}", file=sys.stderr)
        return 1

    img = Image.open(args.image).convert("RGB")
    arr = np.asarray(img, dtype=np.float64) / 255.0
    h_img, w_img = arr.shape[:2]
    hue, sat, val = rgb_to_hsv(arr)

    if args.window > w_img or args.window > h_img:
        print(f"window {args.window} exceeds image size {w_img}x{h_img}", file=sys.stderr)
        return 1

    scores = []
    for y in range(0, h_img - args.window + 1, args.stride):
        for x in range(0, w_img - args.window + 1, args.stride):
            sl = (slice(y, y + args.window), slice(x, x + args.window))
            score = window_score(
                arr[sl],
                hue[sl],
                sat[sl],
                val[sl],
                args.hue_min,
                args.hue_max,
                args.sat_min,
                args.val_min,
                args.val_max,
                args.outlier_weight,
            )
            scores.append((x, y, score))

    if not scores:
        print("no windows produced", file=sys.stderr)
        return 1

    picks = pick_top_k(scores, args.window, args.k)
    if not picks:
        print("no non-overlapping windows found", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for i, (x, y, score) in enumerate(picks):
        crop = img.crop((x, y, x + args.window, y + args.window))
        out_path = args.out_dir / f"ref_{i}_x{x}_y{y}.png"
        crop.save(out_path)
        print(f"#{i}  score={score:.3f}  x={x} y={y}  -> {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
