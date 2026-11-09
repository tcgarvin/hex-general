#!/usr/bin/env python3
"""Cut a 4x3 Codex icon sheet into 12 square 128px RGBA icons.

Keying:
  magenta  - painted sheets on #FF00FF: alpha from magenta-ness (min(r,b)-g),
             unmix against the sampled bg colour, then despill.
  white    - ink sheets on white: alpha = 1 - min(rgb)/255 (with paper-noise
             floor), colour unmixed from white.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

KINDS = ["base", "city", "farm", "oil_field", "steel_mill", "factory"]
COLS, ROWS = 4, 3
OUT = 128
CONTENT = 120
MIN_HITS = 4  # pixels per row/column before it counts as occupied (ignores keying specks)  # longest side of the subject inside the 128 canvas


def key_magenta(rgb: np.ndarray) -> np.ndarray:
    h, w, _ = rgb.shape
    corners = np.concatenate(
        [rgb[:8, :8].reshape(-1, 3), rgb[:8, -8:].reshape(-1, 3), rgb[-8:, :8].reshape(-1, 3)]
    )
    bg = corners.mean(axis=0)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    m = np.minimum(r, b) - g
    lo, hi = 60.0, 225.0
    alpha = 1.0 - np.clip((m - lo) / (hi - lo), 0.0, 1.0)
    a3 = np.maximum(alpha, 1e-3)[..., None]
    col = (rgb - (1.0 - alpha)[..., None] * bg) / a3
    col = np.clip(col, 0, 255)
    # despill: remove any remaining magenta cast (r,b both above g)
    excess = np.clip(np.minimum(col[..., 0], col[..., 2]) - col[..., 1], 0, None)
    col[..., 0] -= excess
    col[..., 2] -= excess
    return np.dstack([np.clip(col, 0, 255), alpha * 255.0])


def key_white(rgb: np.ndarray) -> np.ndarray:
    mn = rgb.min(axis=2)
    lo, hi = 20.0, 240.0  # >=240 is paper, <=20 is solid ink
    alpha = 1.0 - np.clip((mn - lo) / (hi - lo), 0.0, 1.0)
    a3 = np.maximum(alpha, 1e-3)[..., None]
    col = (rgb - (1.0 - alpha)[..., None] * 255.0) / a3
    return np.dstack([np.clip(col, 0, 255), alpha * 255.0])


def fit(rgba: np.ndarray) -> Image.Image:
    a = rgba[..., 3]
    ys, xs = np.where(a > 8)
    crop = rgba[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    im = Image.fromarray(crop.astype(np.uint8), "RGBA")
    scale = CONTENT / max(im.size)
    im = im.resize(
        (max(1, round(im.width * scale)), max(1, round(im.height * scale))),
        Image.LANCZOS,
    )
    canvas = Image.new("RGBA", (OUT, OUT), (0, 0, 0, 0))
    canvas.alpha_composite(im, ((OUT - im.width) // 2, (OUT - im.height) // 2))
    return canvas


def split_bands(occupied: np.ndarray, n: int) -> list[tuple[int, int]]:
    """Split a 1-D occupancy profile into n bands at the n-1 widest interior gaps."""
    idx = np.where(occupied)[0]
    first, last = int(idx.min()), int(idx.max())
    gaps = []
    run_start = None
    for i in range(first, last + 1):
        if not occupied[i] and run_start is None:
            run_start = i
        elif occupied[i] and run_start is not None:
            gaps.append((i - run_start, run_start, i))
            run_start = None
    if len(gaps) < n - 1:
        raise RuntimeError(f"expected {n} bands, found only {len(gaps) + 1}")
    cuts = sorted(gaps, reverse=True)[: n - 1]
    bounds = [first] + sorted((s + e) // 2 for _, s, e in cuts) + [last + 1]
    return [(bounds[k], bounds[k + 1]) for k in range(n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet")
    ap.add_argument("--key", choices=["magenta", "white"], required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    rgb = np.asarray(Image.open(args.sheet).convert("RGB"), dtype=np.float64)
    rgba = key_magenta(rgb) if args.key == "magenta" else key_white(rgb)
    h, w = rgba.shape[:2]
    os.makedirs(args.out_dir, exist_ok=True)
    report = []
    alpha = rgba[..., 3] > 24
    cells = []
    for y0, y1 in split_bands(alpha.sum(axis=1) >= MIN_HITS, ROWS):
        for x0, x1 in split_bands(alpha[y0:y1].sum(axis=0) >= MIN_HITS, COLS):
            cells.append((y0, y1, x0, x1))
    for idx, (y0, y1, x0, x1) in enumerate(cells):
        r, c = divmod(idx, COLS)
        cell = rgba[y0:y1, x0:x1].copy()
        name = f"{KINDS[idx // 2]}-{idx % 2 + 1}.png"
        fit(cell).save(os.path.join(args.out_dir, name))
        report.append({"cell": idx, "row": r, "col": c, "file": name, "crop_xyxy": [x0, y0, x1, y1]})
    print(json.dumps({"sheet": args.sheet, "key": args.key, "cells": report}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
