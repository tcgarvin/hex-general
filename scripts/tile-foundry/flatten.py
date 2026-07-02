#!/usr/bin/env python3
"""Flatten large-scale illumination (light/dark patches) out of a terrain swatch.

Use this when `gate.py`'s luminance spread across cuts from one swatch is too
wide to fit inside a single `--lum-min`/`--lum-max` band — usually a sign the
source render has a broad lighting gradient baked in (a sun-facing brighter
corner, a shadowed edge), rather than the tiles themselves being genuinely
different terrain. This divides out a heavily-blurred version of the
luminance channel (the "illumination field") and multiplies the RGB back
up/down to compensate, while leaving high-frequency detail (crown texture,
blade-level grass contrast) untouched — a sigma=24 blur removes only slow,
large-scale variation.

Example:
    python3 flatten.py swatch.png --out swatch-flat.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

DEFAULT_SIGMA = 24.0
DEFAULT_CLIP_LO = 0.6
DEFAULT_CLIP_HI = 1.8
FIELD_DOWNSCALE = 256


def luminance_of(rgb: np.ndarray) -> np.ndarray:
    """Standard-weights luminance of an `...x3` float RGB array."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return 0.299 * r + 0.587 * g + 0.114 * b


def illumination_field(luminance: np.ndarray, sigma: float, downscale: int = FIELD_DOWNSCALE) -> np.ndarray:
    """Blurred, low-resolution illumination estimate, upsampled back to the source size.

    Downscaling first (rather than blurring at full resolution) keeps a
    large `sigma` cheap; since the field is meant to capture only slow
    lighting variation, this loses nothing perceptually relevant.
    """
    h, w = luminance.shape
    scale = downscale / max(h, w)
    small_w, small_h = max(1, round(w * scale)), max(1, round(h * scale))
    clamped = np.clip(luminance, 0, 255).astype(np.uint8)
    small_img = Image.fromarray(clamped, mode="L").resize((small_w, small_h), Image.BILINEAR)
    blurred = small_img.filter(ImageFilter.GaussianBlur(radius=sigma))
    field_img = blurred.resize((w, h), Image.BICUBIC)
    return np.asarray(field_img, dtype=np.float64)


def flatten(rgb: np.ndarray, sigma: float, clip_lo: float, clip_hi: float) -> np.ndarray:
    """Divide out the illumination field and return the flattened uint8 RGB array."""
    luminance = luminance_of(rgb.astype(np.float64))
    field = illumination_field(luminance, sigma)
    median_field = float(np.median(field))
    safe_median = median_field if median_field > 0 else 1.0
    safe_field = np.where(field <= 0, safe_median, field)
    gain = np.clip(safe_median / safe_field, clip_lo, clip_hi)
    flattened = rgb.astype(np.float64) * gain[..., np.newaxis]
    return np.clip(flattened, 0, 255).astype(np.uint8)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Flatten large-scale illumination out of a terrain swatch.")
    ap.add_argument("swatch", type=Path, help="input swatch image")
    ap.add_argument(
        "--sigma",
        type=float,
        default=DEFAULT_SIGMA,
        help=f"illumination-field Gaussian blur radius in the {FIELD_DOWNSCALE}px downscaled space (default {DEFAULT_SIGMA})",
    )
    ap.add_argument("--clip-lo", type=float, default=DEFAULT_CLIP_LO, help=f"minimum per-pixel gain (default {DEFAULT_CLIP_LO})")
    ap.add_argument("--clip-hi", type=float, default=DEFAULT_CLIP_HI, help=f"maximum per-pixel gain (default {DEFAULT_CLIP_HI})")
    ap.add_argument("--out", required=True, type=Path, help="output PNG path")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.swatch.is_file():
        print(f"swatch not found: {args.swatch}", file=sys.stderr)
        return 1

    img = Image.open(args.swatch).convert("RGB")
    rgb = np.array(img)
    flattened = flatten(rgb, args.sigma, args.clip_lo, args.clip_hi)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(flattened, mode="RGB").save(args.out)

    before_std = float(luminance_of(rgb.astype(np.float64)).std())
    after_std = float(luminance_of(flattened.astype(np.float64)).std())
    print(f"saved {args.out} ({img.width}x{img.height})")
    print(f"luminance std: {before_std:.2f} -> {after_std:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
