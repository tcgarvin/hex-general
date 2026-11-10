#!/usr/bin/env python3
"""Reference images for the lake paint-in: shipped river tiles at a sheet's cell
scale (340 px hexagons for 3x3 sheets, 256 px for 4x4) on flat magenta, so Codex
sees the exact water and bank treatment the lake must match. Writes
refs/river-tiles-340.png and refs/river-tiles-256.png.

Run from the repo root: python3 art/codex/terrain/lake/make_refs.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[4]
TILES = REPO / "frontend" / "src" / "board" / "tiles" / "river"
SOURCES = ("straight_v_00.png", "turn_s_nw_00.png", "straight_d_01.png")
CELLS = (340, 256)
GAP = 40


def build(cell: int) -> Path:
    """One reference strip with hexagons `cell` px point to point."""
    width = len(SOURCES) * cell + (len(SOURCES) + 1) * GAP
    sheet = Image.new("RGB", (width, cell + 2 * GAP), (255, 0, 255))
    for i, name in enumerate(SOURCES):
        tile = (
            Image.open(TILES / name)
            .convert("RGBA")
            .resize((cell, cell), Image.Resampling.LANCZOS)
        )
        sheet.paste(tile, (GAP + i * (cell + GAP), GAP), tile)
    out = Path(__file__).resolve().parent / "refs" / f"river-tiles-{cell}.png"
    sheet.save(out)
    return out


def main() -> None:
    for cell in CELLS:
        print(build(cell))


if __name__ == "__main__":
    main()
