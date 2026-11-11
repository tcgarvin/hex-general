#!/usr/bin/env python3
"""Cut a Codex emblem sheet (painted crests on flat magenta) into transparent PNGs.

Keying is the sites round's magenta keyer (`../../sites/cut_sheet.py`
``key_magenta``: alpha from ``min(r,b) - g``, colour un-mixed from the sampled
background, then despilled). Cells are split at the widest gaps. Each crest is
cropped to its alpha bbox and centred on a transparent square canvas at the
sheet's own resolution (no resampling), with a small margin, so the PNGs are
the masters; review sizes are made from them.

Usage: cut_emblems.py raw/<sheet>.png --grid 3x2 --names a,b,... --out-dir cut
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "sites"))
from cut_sheet import MIN_HITS, key_magenta, split_bands  # noqa: E402

PAD = 8
SPECK_ALPHA = 12


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet")
    ap.add_argument("--grid", default="3x2", help="COLSxROWS")
    ap.add_argument("--names", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    cols, rows = (int(v) for v in args.grid.split("x"))
    names = args.names.split(",")
    if len(names) != cols * rows:
        raise SystemExit(f"--names needs {cols * rows} names, got {len(names)}")

    rgb = np.asarray(Image.open(args.sheet).convert("RGB"), dtype=np.float64)
    rgba = key_magenta(rgb)
    rgba[..., 3] = np.where(rgba[..., 3] < SPECK_ALPHA, 0.0, rgba[..., 3])
    occupied = rgba[..., 3] > 24
    os.makedirs(args.out_dir, exist_ok=True)
    report = []
    idx = 0
    for y0, y1 in split_bands(occupied.sum(axis=1) >= MIN_HITS, rows):
        for x0, x1 in split_bands(occupied[y0:y1].sum(axis=0) >= MIN_HITS, cols):
            cell = rgba[y0:y1, x0:x1]
            ys, xs = np.nonzero(cell[..., 3] > 0)
            crop = cell[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
            h, w = crop.shape[:2]
            side = max(h, w) + 2 * PAD
            canvas = np.zeros((side, side, 4))
            oy, ox = (side - h) // 2, (side - w) // 2
            canvas[oy : oy + h, ox : ox + w] = crop
            out = Image.fromarray(canvas.round().clip(0, 255).astype(np.uint8), "RGBA")
            fname = f"{names[idx]}.png"
            out.save(os.path.join(args.out_dir, fname))
            report.append(
                {
                    "file": fname,
                    "crop_xyxy": [int(x0 + xs.min()), int(y0 + ys.min()), int(x0 + xs.max() + 1), int(y0 + ys.max() + 1)],
                    "size": side,
                }
            )
            idx += 1
    print(json.dumps({"sheet": args.sheet, "cells": report}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
