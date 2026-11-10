"""Cut-scale study: grain of 1:1 (128 px window) vs 2:1 (256 px window, LANCZOS to 128)
cuts from a ground source, compared like-for-like with concept open grass.

Every window is measured on its central 64x64 box (the concept's clean grass boxes are
64x64, between hex grid lines), so lum_std / hp1 / bp2 / lf8 compare at the same pixel
scale and region size. Prints the median over all windows on a stride grid.

Usage: python3 scale_study.py <source.png> [...] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from grain import metrics

CONCEPT = Path(__file__).resolve().parents[4] / "docs" / "concept-art" / "concept1.png"
# Clean 64x64 open-grass boxes in concept1.png (no grid line, road, unit or feature),
# picked by a feature-deviation scan and checked by eye (2026-11-09).
CONCEPT_BOXES = [
    (848, 456, 912, 520),
    (312, 168, 376, 232),
    (1368, 512, 1432, 576),
    (16, 448, 80, 512),
    (1368, 248, 1432, 312),
    (440, 440, 504, 504),
    (1368, 712, 1432, 776),
]
KEYS = ["lum_mean", "lum_std", "H", "S", "V", "hp1", "bp2", "lf8", "coh"]


def box_metrics(rgb: np.ndarray) -> dict[str, float]:
    full = np.ones(rgb.shape[:2], bool)
    return metrics(rgb.astype(np.float64), full, full)


def median(rows: list[dict[str, float]]) -> dict[str, float]:
    return {k: round(float(np.median([r[k] for r in rows])), 3) for k in KEYS}


def concept_reference() -> dict[str, float]:
    im = np.asarray(Image.open(CONCEPT).convert("RGB"))
    return median([box_metrics(im[t:b, l:r]) for l, t, r, b in CONCEPT_BOXES])


def windows(img: Image.Image, scale: int) -> list[np.ndarray]:
    """128 px tiles from `scale`*128 windows on a non-overlapping grid; centre 64x64 of each."""
    win = 128 * scale
    out = []
    for y in range(0, img.height - win + 1, win):
        for x in range(0, img.width - win + 1, win):
            tile = img.crop((x, y, x + win, y + win))
            if scale != 1:
                tile = tile.resize((128, 128), Image.LANCZOS)
            out.append(np.asarray(tile)[32:96, 32:96])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()
    result: dict[str, dict] = {"concept_grass": concept_reference()}
    print(f"{'source':<40}{'scale':>6}" + "".join(f"{k:>9}" for k in KEYS))
    print(f"{'concept grass (7 boxes)':<40}{'-':>6}" + "".join(f"{result['concept_grass'][k]:>9}" for k in KEYS))
    for src in args.sources:
        img = Image.open(src).convert("RGB")
        for scale in (1, 2):
            m = median([box_metrics(w) for w in windows(img, scale)])
            result[f"{Path(src).stem}@{scale}:1"] = m
            print(f"{Path(src).stem[:40]:<40}{f'{scale}:1':>6}" + "".join(f"{m[k]:>9}" for k in KEYS))
    if args.json:
        args.json.write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
