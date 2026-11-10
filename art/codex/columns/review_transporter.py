#!/usr/bin/env python3
"""Review images for the tank-transporter edits (armor-transporter-1).

Reads <dir>/match.json (match_tank.py) and the built pairs (<name>-w90 / -w120),
writes under review/:
  <dir>-side.png    per variant at 128 px: deployed | w90 | w120 (viewer blue) | w120 base | mask
  <dir>-board.png   per variant, on plains and on road, as WorldRenderer draws units:
                    deployed @48 px canvas | w90 @48 | w120 @48 | w90 @ 1.35x canvas (65 px);
                    tiles at 64 px; 1x render shown 2x nearest; labels give the
                    on-board width of the tank itself (deployed tank = 34 px)
  <dir>-tint.png    w120 in the viewer blue + three other owner colours
  <dir>-match.png   the shipped source silhouette scaled/placed at the registered fit
                    over the edit (grey both, red source only, cyan edit only; rows
                    above the track bottom), with k and IoUs

Usage: review_transporter.py <dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from review_renders import (
    BG,
    DEPLOYED,
    FONT,
    FONT_S,
    NEUTRAL,
    OUT,
    TILE_PX,
    TINTS,
    UNIT_PX,
    board_tiles,
    centred,
    label,
    mask_of,
    on,
    resize_rgba,
    tinted,
)
from match_tank import FOUNDRY, TRACK_BAND, load, resize_mask

CELL = 128
WIDE_CANVAS = 1.35
BLUE = TINTS["viewer blue 3d7eff"]
DEPLOYED_TANK_W = 90  # solid bbox of the shipped armor on its 128 canvas


def render_side(d: Path, stems: list[str]) -> Path:
    pad, lab = 10, 18
    cols = ["deployed", "rig @90 footprint", "rig @120 footprint", "@120 base", "@120 mask"]
    sheet = Image.new("RGBA", (pad + 5 * (CELL + pad), pad + lab + len(stems) * (lab + CELL + pad)), BG)
    neutral = Image.new("RGBA", (CELL, CELL), NEUTRAL)
    for k, c in enumerate(cols):
        label(sheet, (pad + k * (CELL + pad), pad), c, FONT_S)
    for r, stem in enumerate(stems):
        y = pad + lab + r * (lab + CELL + pad)
        label(sheet, (pad, y), f"{d.name}/{stem}")
        w120 = d / f"{stem}-w120_base.png"
        panels = [
            tinted(DEPLOYED / "armor_base.png", BLUE),
            tinted(d / f"{stem}-w90_base.png", BLUE),
            tinted(w120, BLUE),
            Image.open(w120).convert("RGBA"),
            Image.open(mask_of(w120)).convert("RGBA"),
        ]
        for k, p in enumerate(panels):
            sheet.paste(on(neutral, p), (pad + k * (CELL + pad), y + lab))
    path = OUT / f"{d.name}-side.png"
    sheet.convert("RGB").save(path)
    return path


def render_board(d: Path, stems: list[str], match: dict) -> Path:
    plains, road = board_tiles()
    wide_px = round(UNIT_PX * WIDE_CANVAS)
    pad, lab, zoom = 4, 24, 2
    w = pad + 4 * (TILE_PX + pad)
    h = pad + len(stems) * (lab + 2 * (TILE_PX + pad))
    sheet = Image.new("RGBA", (w, h), BG)
    notes = []
    for r, stem in enumerate(stems):
        m = match[stem]["tank_w_in_canvas"]
        sprites = [
            (resize_rgba(tinted(DEPLOYED / "armor_base.png", BLUE), UNIT_PX), DEPLOYED_TANK_W * UNIT_PX / CELL),
            (resize_rgba(tinted(d / f"{stem}-w90_base.png", BLUE), UNIT_PX), m["w90"] * UNIT_PX / CELL),
            (resize_rgba(tinted(d / f"{stem}-w120_base.png", BLUE), UNIT_PX), m["w120"] * UNIT_PX / CELL),
            (resize_rgba(tinted(d / f"{stem}-w90_base.png", BLUE), wide_px), m["w90"] * wide_px / CELL),
        ]
        y0 = pad + r * (lab + 2 * (TILE_PX + pad)) + lab
        for t, tile in enumerate((plains, road)):
            for k, (sp, _) in enumerate(sprites):
                sheet.alpha_composite(centred(tile, [(sp, 0, 0)]), (pad + k * (TILE_PX + pad), y0 + t * (TILE_PX + pad)))
        notes.append((y0 - lab, [tw for _, tw in sprites]))
    big = sheet.resize((w * zoom, h * zoom), Image.NEAREST)
    heads = ["deployed, 48px", "@90, 48px canvas", "@120, 48px canvas", "@90, 1.35x canvas"]
    for (y, tws), stem in zip(notes, stems):
        label(big, (pad * zoom, y * zoom), stem, FONT)
        for k, (hd, tw) in enumerate(zip(heads, tws)):
            x = (pad + k * (TILE_PX + pad)) * zoom
            label(big, (x, y * zoom + 16), hd, FONT_S)
            label(big, (x, y * zoom + 29), f"tank {tw:.0f}px wide", FONT_S)
    path = OUT / f"{d.name}-board.png"
    big.convert("RGB").save(path)
    return path


def render_tint(d: Path, stems: list[str]) -> Path:
    pad, lab = 8, 18
    sheet = Image.new("RGBA", (pad + len(TINTS) * (CELL + 4), pad + lab + len(stems) * (lab + CELL + pad)), BG)
    neutral = Image.new("RGBA", (CELL, CELL), NEUTRAL)
    for k, t in enumerate(TINTS):
        label(sheet, (pad + k * (CELL + 4), pad), t, FONT_S)
    for r, stem in enumerate(stems):
        y = pad + lab + r * (lab + CELL + pad)
        label(sheet, (pad, y), f"{stem} @120")
        for k, t in enumerate(TINTS.values()):
            sheet.paste(on(neutral, tinted(d / f"{stem}-w120_base.png", t)), (pad + k * (CELL + 4), y + lab))
    path = OUT / f"{d.name}-tint.png"
    sheet.convert("RGB").save(path)
    return path


def render_match(d: Path, stems: list[str], match: dict) -> Path:
    units = json.loads((FOUNDRY / "manifest.json").read_text())["units"]
    _, src_a = load(FOUNDRY / units["units"]["armor"]["source"])
    panel_w = 420
    panels = []
    for stem in stems:
        m = match[stem]
        _, a = load(d / "src" / f"{stem}-w90.png")
        k, (x, y) = m["k"], m["offset"]
        sh, sw = src_a.shape
        size = (round(sw * k), round(sh * k))
        s = resize_mask(src_a >= 0.5, size)
        win = np.zeros_like(s)
        y1, x1 = min(a.shape[0], y + size[1]), min(a.shape[1], x + size[0])
        win[: y1 - y, : x1 - x] = a[y:y1, x:x1] >= 0.5
        rows = int(size[1] * (1.0 - TRACK_BAND))
        img = np.zeros(s.shape + (3,), dtype=np.uint8)
        img[:] = NEUTRAL[:3]
        img[win & ~s] = (60, 220, 235)
        img[s & ~win] = (235, 70, 60)
        img[s & win] = (170, 170, 170)
        img[rows:] = (img[rows:] * 0.35).astype(np.uint8)  # below the comparison rows
        p = Image.fromarray(img, "RGB")
        p = p.resize((panel_w, round(p.height * panel_w / p.width)), Image.LANCZOS)
        panels.append((stem, m, p))
    pad, lab = 10, 34
    h = pad + sum(lab + p.height + pad for _, _, p in panels)
    sheet = Image.new("RGBA", (pad * 2 + panel_w, h), BG)
    y = pad
    for stem, m, p in panels:
        label(sheet, (pad, y), f"{stem}: tank scale k={m['k']}  silhouette IoU {m['silhouette_iou']}", FONT_S)
        label(sheet, (pad, y + 15), f"cobalt-panel IoU {m['cobalt_iou']}  (dimmed: track rows, not compared)", FONT_S)
        sheet.paste(p, (pad, y + lab))
        y += lab + p.height + pad
    path = OUT / f"{d.name}-match.png"
    sheet.convert("RGB").save(path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    args = ap.parse_args()
    d = args.dir.resolve()
    match = json.loads((d / "match.json").read_text())
    stems = sorted(match)
    for p in (render_side(d, stems), render_board(d, stems, match), render_tint(d, stems), render_match(d, stems, match)):
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
