#!/usr/bin/env python3
"""Cut a Codex column sheet (default 4x3) into hi-res RGBA source crops for unit.py.

Keying reuses the magenta keyer from the sites round (alpha from
``min(r,b) - g``, colour un-mixed from the sampled background, then
despilled). The crops are not fitted here: unit.py scales them. Instead each
crop stores the CLEAN (despilled) colour re-composited over the exact sampled
background, with fully keyed pixels set to that background. That matches the
input contract of ``scripts/tile-foundry/unit.py`` ``load_crop`` (it measures
the extraction background as the mean of the alpha==0 pixels and un-mattes the
edge against it), so unit.py recovers the despilled colour exactly and its
split/scale/rim pipeline runs unmodified. Storing the raw sheet RGB instead
left magenta spill on opaque edge pixels (the keyer's opaque threshold), and
LANCZOS mixed that into a few cobalt-residue pixels in the base gate.

Cells are split at the widest gaps (not a fixed grid); alpha below
``SPECK_ALPHA`` is zeroed, and connected blobs smaller than ``SPECK_FRAC`` of
the cell's largest blob are dropped so stray keying specks can't inflate the
bbox unit.py scales from.

Usage: cut_sheet.py raw/<sheet>.png --names a-1,a-2,... --out-dir <sheet>/src [--grid 1x1]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import deque

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sites"))
from cut_sheet import MIN_HITS, key_magenta, split_bands  # noqa: E402

PAD = 6  # transparent margin round each crop, px at sheet resolution
SPECK_ALPHA = 10.0 / 255.0
SPECK_FRAC = 0.01


def components(mask: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """4-connected labelling (BFS; cells are small, no scipy in this env)."""
    labels = np.zeros(mask.shape, dtype=np.int32)
    sizes: list[int] = []
    h, w = mask.shape
    for y, x in zip(*np.nonzero(mask)):
        if labels[y, x]:
            continue
        lab = len(sizes) + 1
        labels[y, x] = lab
        q = deque([(y, x)])
        n = 0
        while q:
            cy, cx = q.popleft()
            n += 1
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = lab
                    q.append((ny, nx))
        sizes.append(n)
    return labels, sizes


def clean_alpha(alpha: np.ndarray) -> np.ndarray:
    """Zero sub-threshold alpha and drop speck blobs."""
    alpha = np.where(alpha < SPECK_ALPHA, 0.0, alpha)
    labels, sizes = components(alpha > 0)
    biggest = max(sizes)
    keep = np.zeros(len(sizes) + 1, dtype=bool)
    keep[1:] = np.array(sizes) >= SPECK_FRAC * biggest
    return np.where(keep[labels], alpha, 0.0)


def sheet_background(rgb: np.ndarray) -> np.ndarray:
    """The sampled background colour, exactly as key_magenta measures it."""
    corners = np.concatenate(
        [rgb[:8, :8].reshape(-1, 3), rgb[:8, -8:].reshape(-1, 3), rgb[-8:, :8].reshape(-1, 3)]
    )
    return corners.mean(axis=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet")
    ap.add_argument("--names", required=True, help="12 comma-separated cell names, row-major")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--grid", default="4x3", help="COLSxROWS (1x1 for a single-subject edit)")
    args = ap.parse_args()
    COLS, ROWS = (int(v) for v in args.grid.split("x"))
    names = args.names.split(",")
    if len(names) != COLS * ROWS:
        raise SystemExit(f"--names needs {COLS * ROWS} names, got {len(names)}")

    raw = np.asarray(Image.open(args.sheet).convert("RGB"), dtype=np.float64)
    keyed = key_magenta(raw)
    clean, alpha_all = keyed[..., :3], keyed[..., 3] / 255.0
    bg = sheet_background(raw)
    occupied = alpha_all > 24 / 255.0
    os.makedirs(args.out_dir, exist_ok=True)

    report = []
    idx = 0
    for y0, y1 in split_bands(occupied.sum(axis=1) >= MIN_HITS, ROWS):
        for x0, x1 in split_bands(occupied[y0:y1].sum(axis=0) >= MIN_HITS, COLS):
            alpha = clean_alpha(alpha_all[y0:y1, x0:x1])
            ys, xs = np.nonzero(alpha)
            cy0, cy1 = max(0, ys.min() - PAD), min(alpha.shape[0], ys.max() + 1 + PAD)
            cx0, cx1 = max(0, xs.min() - PAD), min(alpha.shape[1], xs.max() + 1 + PAD)
            a = alpha[cy0:cy1, cx0:cx1]
            col = clean[y0:y1, x0:x1][cy0:cy1, cx0:cx1]
            rgb = a[..., None] * col + (1.0 - a[..., None]) * bg
            out = np.dstack([rgb, a * 255.0]).round().clip(0, 255).astype(np.uint8)
            Image.fromarray(out, "RGBA").save(os.path.join(args.out_dir, f"{names[idx]}.png"))
            report.append(
                {
                    "cell": idx,
                    "file": f"{names[idx]}.png",
                    "crop_xyxy": [int(v) for v in (x0 + cx0, y0 + cy0, x0 + cx1, y0 + cy1)],
                }
            )
            idx += 1
    print(json.dumps({"sheet": args.sheet, "cells": report}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
