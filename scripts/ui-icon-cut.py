#!/usr/bin/env python3
"""Cut the Field-HQ icon glyph sheet into individual assets.

Source: art/foundry/ui/kit-icons.png — a 4-row grid of dark warm-ink glyphs on
flat neutral gray, each with a small caption below it (gen provenance in
art/foundry/ui/kit-icons.prompt.txt). Ratified 2026-07-09: the ORDER glyphs
(move/barrage/rest/hold/clear) were rejected in favour of the existing in-code
vectors; transport/unit/status/economy glyphs were accepted.

Two outputs, both deterministic (re-run after a re-gen):

  * Provenance cuts  → art/foundry/ui/cut/icon-<name>.png
        Every glyph, faithful warm-ink RGB + darkness alpha. Feeds the montage
        and keeps the ratified source addressable.

  * Frontend masks   → frontend/src/board/panels/chrome/icon-<name>.png
        Only the WIRED set (transport nav). White RGB + darkness alpha so Pixi
        `tint` recolours them (ink on brass, ivory on the flat fallback), and
        normalized into a uniform square cell so a single render size keeps the
        sheet's relative glyph sizing. Unlike the 9-slice chrome this skips
        ui-kit-prep.py: a tint-mask glyph atlas needs whitening + uniform
        cells, not per-consumer 9-slice prep.

Add a glyph name to EMIT to wire it once a consumer exists (unit-type markers,
status badges, economy readouts are cut to provenance but not yet emitted).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SHEET = REPO / "art" / "foundry" / "ui" / "kit-icons.png"
CUT_DIR = REPO / "art" / "foundry" / "ui" / "cut"
CHROME_DIR = REPO / "frontend" / "src" / "board" / "panels" / "chrome"

# Glyph grid, left-to-right per row (matches the gen prompt's row order).
ROWS: list[list[str]] = [
    ["move", "barrage", "rest", "hold", "clear"],
    ["rewind", "back", "play", "pause", "forward", "fast"],
    ["infantry", "armor", "artillery"],
    ["valid", "warning", "blocked", "credits", "recruit"],
]

# Glyphs wired into the frontend now (ReplayControlPanel transport nav). The
# speed keycap stays a "1×/2×" text glyph, so "fast" is intentionally unwired.
EMIT = ["rewind", "back", "play", "pause", "forward"]

# Emitted tile: target square size (px) and padding fraction around the
# normalized glyph. ~2x the ~30px on-screen keycap glyph, crisp on hidpi.
TILE_PX = 64
TILE_PAD = 0.12


def _runs(mask: np.ndarray, min_gap: int) -> list[tuple[int, int]]:
    """Contiguous True-runs in a 1-D bool array, merging gaps < ``min_gap``."""
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return []
    segs: list[tuple[int, int]] = []
    start = prev = int(idx[0])
    for i in idx[1:]:
        if i - prev > min_gap:
            segs.append((start, prev))
            start = int(i)
        prev = int(i)
    segs.append((start, prev))
    return segs


def _locate(lum: np.ndarray, bg_lum: float) -> dict[str, tuple[int, int, int, int]]:
    """Bounding boxes {name: (x0, y0, x1, y1)} for every glyph on the sheet."""
    dark = lum < (bg_lum - 30)
    row_segs = _runs(dark.sum(axis=1) > 3, min_gap=18)
    glyph_rows = [s for s in row_segs if (s[1] - s[0]) > 70]  # captions are shorter
    if len(glyph_rows) != len(ROWS):
        raise RuntimeError(f"expected {len(ROWS)} glyph rows, found {len(glyph_rows)}")

    boxes: dict[str, tuple[int, int, int, int]] = {}
    for (y0, y1), names in zip(glyph_rows, ROWS):
        col_segs = _runs(dark[y0 : y1 + 1, :].sum(axis=0) > 2, min_gap=45)
        if len(col_segs) != len(names):
            raise RuntimeError(f"row {names}: expected {len(names)} glyphs, found {len(col_segs)}")
        for (x0, x1), name in zip(col_segs, names):
            boxes[name] = (x0, y0, x1, y1)
    return boxes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sheet", type=Path, default=SHEET)
    args = ap.parse_args(argv)

    img = Image.open(args.sheet).convert("RGB")
    arr = np.asarray(img).astype(np.int16)
    lum = arr.mean(axis=2)
    bg_lum = float(np.median(lum))
    ink_min = float(lum.min())
    boxes = _locate(lum, bg_lum)

    CUT_DIR.mkdir(parents=True, exist_ok=True)
    CHROME_DIR.mkdir(parents=True, exist_ok=True)

    # Provenance cuts: tight box + small pad, faithful warm ink + darkness alpha.
    tight: dict[str, np.ndarray] = {}
    for name, (x0, y0, x1, y1) in boxes.items():
        pad = 8
        cy0, cy1 = max(0, y0 - pad), y1 + pad
        cx0, cx1 = max(0, x0 - pad), x1 + pad
        cell = lum[cy0:cy1, cx0:cx1]
        alpha = np.clip((bg_lum - cell) / (bg_lum - ink_min), 0, 1)
        rgb = arr[cy0:cy1, cx0:cx1].astype(np.uint8)
        rgba = np.dstack([rgb, (alpha * 255).astype(np.uint8)])
        Image.fromarray(rgba, "RGBA").save(CUT_DIR / f"icon-{name}.png")
        # Re-crop to the glyph's true alpha bbox for uniform-cell normalization.
        ys, xs = np.where(alpha > 0.25)
        tight[name] = alpha[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]

    # Frontend tint-masks: uniform square cell (preserve relative sizing across
    # the emit set), white RGB, darkness alpha, downscaled to TILE_PX.
    span = max(max(a.shape) for n, a in tight.items() if n in EMIT)
    cell_px = int(round(span * (1 + 2 * TILE_PAD)))
    for name in EMIT:
        a = tight[name]
        canvas = np.zeros((cell_px, cell_px), dtype=np.float32)
        oy = (cell_px - a.shape[0]) // 2
        ox = (cell_px - a.shape[1]) // 2
        canvas[oy : oy + a.shape[0], ox : ox + a.shape[1]] = a
        white = np.full((cell_px, cell_px, 3), 255, dtype=np.uint8)
        rgba = np.dstack([white, (canvas * 255).astype(np.uint8)])
        out = Image.fromarray(rgba, "RGBA").resize((TILE_PX, TILE_PX), Image.LANCZOS)
        out.save(CHROME_DIR / f"icon-{name}.png")

    print(f"cut {len(boxes)} glyphs → {CUT_DIR}")
    print(f"emitted {len(EMIT)} masks ({cell_px}px cell → {TILE_PX}px) → {CHROME_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
