"""Seam and blotch numbers for a board-mock field render pair (grid / no-grid).

The grid overlay marks exactly where hex edges are, so pixels that differ between the
grid and no-grid renders form the seam mask (dilated 2 px). On the no-grid render:
  seam_index  mean gradient magnitude on seam pixels / off seam pixels (1.0 = seams
              indistinguishable from the texture; a visible seam pushes it up)
  blotch_K    std of luminance blurred with sigma K tile px (low-frequency blotchiness)
Interior only: the ragged outer rim of the field is excluded.

Usage: python3 field_metrics.py <grid.png> <no-grid.png>
"""

from __future__ import annotations

import json
import sys

import numpy as np
from PIL import Image

from grain import blur, luminance


def dilate(m: np.ndarray, n: int) -> np.ndarray:
    for _ in range(n):
        m = m | np.roll(m, 1, 0) | np.roll(m, -1, 0) | np.roll(m, 1, 1) | np.roll(m, -1, 1)
    return m


def field_metrics(grid_path: str, plain_path: str, hex_px: int) -> dict[str, float]:
    g = np.asarray(Image.open(grid_path).convert("RGB"), dtype=np.float64)
    p = np.asarray(Image.open(plain_path).convert("RGB"), dtype=np.float64)
    seam = dilate(np.abs(g - p).sum(-1) > 30, 1)
    h, w = seam.shape
    inner = np.zeros_like(seam)
    inner[hex_px : h - hex_px, hex_px : w - hex_px] = True
    lum = luminance(p)
    gy, gx = np.gradient(blur(lum, 0.7))
    mag = np.hypot(gx, gy)
    on, off = seam & inner, ~dilate(seam, 3) & inner
    out = {"seam_index": round(float(mag[on].mean() / mag[off].mean()), 3)}
    for k in (4, 8, 16):
        out[f"blotch_{k}"] = round(float(blur(lum, k * hex_px / 128)[inner].std()), 2)
    return out


if __name__ == "__main__":
    grid, plain = sys.argv[1], sys.argv[2]
    hex_px = int(sys.argv[3]) if len(sys.argv) > 3 else 128
    print(json.dumps(field_metrics(grid, plain, hex_px)))
