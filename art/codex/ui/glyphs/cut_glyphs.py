#!/usr/bin/env python3
"""Cut a Codex glyph sheet (black ink on white) into square alpha-mask PNGs.

Each glyph becomes a 256x256 RGBA PNG whose RGB is pure white and whose alpha
is the ink coverage, so the UI can tint it with any colour (ink on parchment,
ivory on wood). Alpha comes from ``1 - min(rgb)/255`` with a paper floor and a
solid-ink ceiling, so the edge anti-aliasing survives as partial alpha.

Optical size: every glyph is first fitted so its longest side is ``CONTENT``
px. Glyphs that are much heavier than the set's median ink coverage (a solid
jerrycan next to an open lattice derrick) are then shrunk a little, by the
square root of the coverage ratio, never below ``MIN_SHRINK`` of the box fit
and never enlarged past it. That keeps solid blocks from looking bigger than
open line glyphs at the same box size.

Cells are split at the widest gaps (``../../sites/cut_sheet.py split_bands``),
so uneven spacing is fine as long as the grid has the stated shape.

Usage:
  cut_glyphs.py raw/<sheet>.png --grid 4x3 --names a,b,c,... --take A --out-dir cut
  (writes cut/<name>-<take>.png and prints a JSON report; without --take the
  names are used as file stems as given, for mixed re-roll sheets)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "sites"))
from cut_sheet import MIN_HITS, split_bands  # noqa: E402

CANVAS = 256
CONTENT = 224  # longest side of the glyph inside the canvas (16 px margin each side)
MIN_SHRINK = 0.82
PAPER_LO, PAPER_HI = 30.0, 225.0  # min(rgb) <= LO is solid ink, >= HI is paper
SPECK_FRAC = 0.002  # blobs smaller than this fraction of the cell's ink are dropped


def ink_alpha(rgb: np.ndarray) -> np.ndarray:
    """Ink coverage in [0, 1] from a black-on-white sheet."""
    mn = rgb.min(axis=2)
    return 1.0 - np.clip((mn - PAPER_LO) / (PAPER_HI - PAPER_LO), 0.0, 1.0)


def drop_specks(alpha: np.ndarray) -> np.ndarray:
    """Zero isolated keying specks (tiny blobs far below the glyph's ink mass)."""
    from collections import deque

    mask = alpha > 0.05
    labels = np.zeros(mask.shape, dtype=np.int32)
    sizes: list[int] = []
    h, w = mask.shape
    for y, x in zip(*np.nonzero(mask)):
        if labels[y, x]:
            continue
        lab = len(sizes) + 1
        labels[y, x] = lab
        queue = deque([(y, x)])
        n = 0
        while queue:
            cy, cx = queue.popleft()
            n += 1
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = lab
                    queue.append((ny, nx))
        sizes.append(n)
    total = sum(sizes)
    keep = np.zeros(len(sizes) + 1, dtype=bool)
    keep[1:] = np.array(sizes) >= SPECK_FRAC * total
    # Pixels at or below 5 % coverage (label 0) go too: paper noise, not edge.
    return np.where(keep[labels], alpha, 0.0)


def box_fit(alpha: np.ndarray) -> tuple[Image.Image, float]:
    """Crop to the ink bbox and scale so the longest side is CONTENT px."""
    ys, xs = np.nonzero(alpha > 0.02)
    crop = alpha[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    im = Image.fromarray((crop * 255).round().astype(np.uint8), "L")
    scale = CONTENT / max(im.size)
    return im, scale


def place(im: Image.Image, scale: float) -> Image.Image:
    size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
    small = im.resize(size, Image.LANCZOS)
    mask = Image.new("L", (CANVAS, CANVAS), 0)
    mask.paste(small, ((CANVAS - small.width) // 2, (CANVAS - small.height) // 2))
    out = Image.new("RGBA", (CANVAS, CANVAS), (255, 255, 255, 0))
    out.putalpha(mask)
    return out


def coverage(img: Image.Image) -> float:
    a = np.asarray(img.getchannel("A"), dtype=np.float64) / 255.0
    return float(a.sum() / (CANVAS * CANVAS))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet")
    ap.add_argument("--grid", required=True, help="COLSxROWS")
    ap.add_argument("--names", required=True, help="comma-separated glyph names, row-major")
    ap.add_argument(
        "--take",
        default="",
        help="variant letter appended to each name (omit when the names already carry it)",
    )
    ap.add_argument("--out-dir", required=True)
    ap.add_argument(
        "--target-coverage",
        type=float,
        default=0.30,
        help="ink coverage (fraction of the canvas) above which a glyph is shrunk",
    )
    args = ap.parse_args()
    cols, rows = (int(v) for v in args.grid.split("x"))
    names = args.names.split(",")
    if len(names) != cols * rows:
        raise SystemExit(f"--names needs {cols * rows} names, got {len(names)}")

    rgb = np.asarray(Image.open(args.sheet).convert("RGB"), dtype=np.float64)
    alpha_all = ink_alpha(rgb)
    occupied = alpha_all > 0.1
    os.makedirs(args.out_dir, exist_ok=True)
    report = []
    idx = 0
    for y0, y1 in split_bands(occupied.sum(axis=1) >= MIN_HITS, rows):
        for x0, x1 in split_bands(occupied[y0:y1].sum(axis=0) >= MIN_HITS, cols):
            alpha = drop_specks(alpha_all[y0:y1, x0:x1])
            im, box_scale = box_fit(alpha)
            boxed = place(im, box_scale)
            cov = coverage(boxed)
            shrink = 1.0
            if cov > args.target_coverage:
                shrink = max(MIN_SHRINK, (args.target_coverage / cov) ** 0.5)
            out = place(im, box_scale * shrink) if shrink < 1.0 else boxed
            fname = f"{names[idx]}-{args.take}.png" if args.take else f"{names[idx]}.png"
            out.save(os.path.join(args.out_dir, fname))
            report.append(
                {
                    "file": fname,
                    "cell_xyxy": [int(x0), int(y0), int(x1), int(y1)],
                    "box_coverage": round(cov, 3),
                    "shrink": round(shrink, 3),
                }
            )
            idx += 1
    print(json.dumps({"sheet": args.sheet, "glyphs": report}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
