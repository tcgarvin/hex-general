#!/usr/bin/env python3
"""Render the review images for the packed-column sprite candidates.

Reads the base/mask pairs built by build_pairs.py and writes, under review/:

  <sheet>-full.png     every candidate at 128 px: base | mask, on a neutral grey
  <sheet>-tint.png     each candidate tinted as the renderer does (mask x colour
                       multiplied over the base) for the viewer blue and three
                       other owner colours from frontend/src/world/colors.ts
  board-<type>.png     board scale: per candidate, the DEPLOYED sprite of the
                       same type on plains, then the candidate on plains and on a
                       road tile. Tiles at 2*HEX_SIZE = 64 px and unit canvases at
                       1.5*HEX_SIZE = 48 px, exactly as WorldRenderer draws them;
                       rendered at 1x then shown 2x nearest so the pixels are
                       visible on a review page.
  picks.png            (with --picks) the first-pass picks next to the deployed sprites
  stack.png            (with --picks) 1, 3 and 6 columns in one hex, three layouts.

Usage:
  review_renders.py <sheet-dir> [...] [--picks infantry=<sheet>/<name>,...]
"""

from __future__ import annotations

import argparse
import colorsys
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
from unit import simulate_tint  # noqa: E402

FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
FONT_S = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
TILES = REPO / "frontend" / "src" / "board" / "tiles"
DEPLOYED = REPO / "frontend" / "src" / "board" / "units"
OUT = HERE / "review"
BG = (40, 40, 40, 255)
NEUTRAL = (74, 78, 70, 255)
INK = (225, 220, 205)
HEX_SIZE = 32
TILE_PX = 2 * HEX_SIZE  # placeTile: scale = 2*HEX_SIZE / tex.width
UNIT_PX = round(1.5 * HEX_SIZE)  # drawUnits: s = 1.5*HEX_SIZE / base.width
BOARD_ZOOM = 2
TYPES = {"inf": "infantry", "art": "artillery", "arm": "armor"}


def hsl(hue: float, sat: float = 0.62, light: float = 0.45) -> int:
    """colors.ts ownerColor's hslToRgb for a non-viewer owner (mid lightness)."""
    r, g, b = colorsys.hls_to_rgb(hue / 360.0, light, sat)
    return (round(r * 255) << 16) | (round(g * 255) << 8) | round(b * 255)


TINTS = {
    "viewer blue 3d7eff": 0x3D7EFF,
    "red (hue 0)": hsl(0),
    "olive (hue 75)": hsl(75),
    "purple (hue 285)": hsl(285),
}


def arr(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"))


def tinted(base: Path, tint: int) -> Image.Image:
    return Image.fromarray(simulate_tint(arr(base), arr(mask_of(base)), tint), "RGBA")


def mask_of(base: Path) -> Path:
    return base.with_name(base.name.replace("_base.png", "_mask.png"))


def resize_rgba(img: Image.Image, px: int) -> Image.Image:
    """Premultiplied LANCZOS resize (no dark/bright fringes from hidden RGB)."""
    return img.convert("RGBa").resize((px, px), Image.LANCZOS).convert("RGBA")


def on(backdrop: Image.Image, sprite: Image.Image) -> Image.Image:
    out = backdrop.copy().convert("RGBA")
    out.alpha_composite(sprite)
    return out


def label(sheet: Image.Image, xy: tuple[int, int], text: str, font=FONT) -> None:
    ImageDraw.Draw(sheet).text(xy, text, fill=INK, font=font)


def candidates(sheet_dir: Path) -> list[tuple[str, dict]]:
    """(name, build result) in sheet cell order."""
    cells = json.loads((sheet_dir / "cut.json").read_text())["cells"]
    built = json.loads((sheet_dir / "build.json").read_text())
    return [(c["file"][:-4], built[c["file"][:-4]]) for c in cells]


# ------------------------------------------------------------- renders ----


def render_full(sheet_dir: Path) -> Path:
    cands = candidates(sheet_dir)
    pad, lab, cell = 10, 18, 128
    cw = 2 * cell + 4
    cols, rows = 4, (len(cands) + 3) // 4
    sheet = Image.new("RGBA", (pad + cols * (cw + pad), pad + rows * (lab + cell + pad)), BG)
    neutral = Image.new("RGBA", (cell, cell), NEUTRAL)
    for i, (name, res) in enumerate(cands):
        x = pad + (i % cols) * (cw + pad)
        y = pad + (i // cols) * (lab + cell + pad)
        if "gate_failure" in res:
            label(sheet, (x, y), f"{name}  GATE FAIL")
            label(sheet, (x, y + lab + 50), res["gate_failure"][:42], FONT_S)
            continue
        label(sheet, (x, y), f"{name}  cov {res['coverage']:.2f}")
        base = Image.open(sheet_dir / f"{name}_base.png").convert("RGBA")
        mask = Image.open(sheet_dir / f"{name}_mask.png").convert("RGBA")
        sheet.paste(on(neutral, base), (x, y + lab))
        sheet.paste(on(neutral, mask), (x + cell + 4, y + lab))
    path = OUT / f"{sheet_dir.name}-full.png"
    sheet.convert("RGB").save(path)
    return path


def render_tint(sheet_dir: Path) -> Path:
    cands = [(n, r) for n, r in candidates(sheet_dir) if "gate_failure" not in r]
    pad, lab, cell, gap = 8, 18, 128, 24
    per_row = 2
    group_w = len(TINTS) * (cell + 4)
    rows = (len(cands) + per_row - 1) // per_row
    sheet = Image.new(
        "RGBA", (pad + per_row * (group_w + gap), pad + lab + rows * (lab + cell + pad)), BG
    )
    for k, tname in enumerate(TINTS):
        for g in range(per_row):
            label(sheet, (pad + g * (group_w + gap) + k * (cell + 4), pad), tname, FONT_S)
    neutral = Image.new("RGBA", (cell, cell), NEUTRAL)
    for i, (name, _) in enumerate(cands):
        gx = pad + (i % per_row) * (group_w + gap)
        y = pad + lab + (i // per_row) * (lab + cell + pad)
        label(sheet, (gx, y), name)
        for k, t in enumerate(TINTS.values()):
            sheet.paste(on(neutral, tinted(sheet_dir / f"{name}_base.png", t)), (gx + k * (cell + 4), y + lab))
    path = OUT / f"{sheet_dir.name}-tint.png"
    sheet.convert("RGB").save(path)
    return path


def board_tiles() -> tuple[Image.Image, Image.Image]:
    plains = resize_rgba(Image.open(TILES / "plains" / "00.png").convert("RGBA"), TILE_PX)
    road = resize_rgba(
        Image.open(TILES / "road" / "plains_straight_d_00.png").convert("RGBA"), TILE_PX
    )
    return plains, road


def board_unit(base: Path, tint: int = TINTS["viewer blue 3d7eff"]) -> Image.Image:
    return resize_rgba(tinted(base, tint), UNIT_PX)


def centred(tile: Image.Image, sprites: list[tuple[Image.Image, float, float]]) -> Image.Image:
    """Composite sprites onto a tile; (sprite, dx, dy) offsets from the hex centre."""
    out = tile.copy()
    c = TILE_PX / 2
    for sp, dx, dy in sprites:
        out.alpha_composite(sp, (round(c + dx - sp.width / 2), round(c + dy - sp.height / 2)))
    return out


def render_board(utype: str, items: list[tuple[str, Path]]) -> Path:
    """Per candidate: [deployed on plains | candidate on plains | candidate on road]."""
    plains, road = board_tiles()
    deployed = centred(plains, [(board_unit(DEPLOYED / f"{utype}_base.png"), 0, 0)])
    pad, lab, gap = 4, 9, 10
    strip_w = 3 * TILE_PX + 2 * pad
    per_row = 4
    rows = (len(items) + per_row - 1) // per_row
    w = pad + per_row * (strip_w + gap)
    h = pad + rows * (lab + TILE_PX + pad)
    sheet = Image.new("RGBA", (w, h), BG)
    for i, (lbl, base) in enumerate(items):
        x = pad + (i % per_row) * (strip_w + gap)
        y = pad + (i // per_row) * (lab + TILE_PX + pad)
        unit = board_unit(base)
        for k, panel in enumerate((deployed, centred(plains, [(unit, 0, 0)]), centred(road, [(unit, 0, 0)]))):
            sheet.alpha_composite(panel, (x + k * (TILE_PX + pad), y + lab))
    big = sheet.resize((w * BOARD_ZOOM, h * BOARD_ZOOM), Image.NEAREST)
    for i, (lbl, _) in enumerate(items):
        x = pad + (i % per_row) * (strip_w + gap)
        y = pad + (i // per_row) * (lab + TILE_PX + pad)
        label(big, (x * BOARD_ZOOM, y * BOARD_ZOOM), f"{lbl}   (deployed | column on plains | on road)", FONT_S)
    path = OUT / f"board-{utype}.png"
    big.convert("RGB").save(path)
    return path


def render_stack(picks: dict[str, Path]) -> Path:
    """1, 3 and 6 columns in one hex, three candidate layouts, viewer blue."""
    plains, road = board_tiles()
    order = ["infantry", "artillery", "armor", "infantry", "artillery", "armor"]
    full = {t: board_unit(p) for t, p in picks.items()}
    small_px = round(UNIT_PX * 0.62)
    small = {t: resize_rgba(tinted(p, TINTS["viewer blue 3d7eff"]), small_px) for t, p in picks.items()}

    def echelon(n: int, step_x: float = 7.0, step_y: float = -6.0) -> list[tuple[Image.Image, float, float]]:
        # a deck of cards: each extra column offset up-right, drawn back to front
        ox, oy = -(n - 1) * step_x / 2, -(n - 1) * step_y / 2
        return [(full[order[k]], ox + k * step_x, oy + k * step_y) for k in reversed(range(n))]

    def capped(n: int) -> list[tuple[Image.Image, float, float]]:
        # at most 3 cards visible; the count is carried by the renderer's pip badge
        return echelon(min(n, 3))

    def convoy(n: int) -> list[tuple[Image.Image, float, float]]:
        if n == 1:
            return [(full[order[0]], 0, 0)]
        # small sprites queued along the hex in two staggered files
        slots = [(-9, -10), (9, -6), (-9, 2), (9, 6), (-9, 14), (9, 18)]
        slots = slots[:n]
        cy = (min(s[1] for s in slots) + max(s[1] for s in slots)) / 2
        return [(small[order[k]], sx, sy - cy) for k, (sx, sy) in enumerate(slots)]

    def pips(tile: Image.Image, n: int) -> Image.Image:
        # WorldRenderer's stack-count badge for columns > 1
        if n <= 1:
            return tile
        out = tile.copy()
        d = ImageDraw.Draw(out)
        c = TILE_PX / 2
        bx, by, r = c + HEX_SIZE * 0.55, c - HEX_SIZE * 0.45, HEX_SIZE * 0.22
        d.ellipse((bx - r, by - r, bx + r, by + r), fill=(16, 17, 20, 255))
        for k in range(min(n, 6)):
            px = bx + (k - (min(n, 6) - 1) / 2) * 3
            d.ellipse((px - 1.2, by - 1.2, px + 1.2, by + 1.2), fill=(255, 255, 255, 255))
        return out

    panels: list[tuple[str, Image.Image]] = []
    for n in (1, 3, 6):
        panels.append((f"A all cards x{n}", centred(road, echelon(n))))
    for n in (1, 3, 6):
        panels.append((f"C 3 cards max + pips x{n}", pips(centred(road, capped(n)), n)))
    for n in (1, 3, 6):
        panels.append((f"B small convoy x{n}", centred(road, convoy(n))))

    pad, lab = 6, 0
    w = pad + 3 * (TILE_PX + pad)
    h = pad + 3 * (TILE_PX + pad)
    sheet = Image.new("RGBA", (w, h), BG)
    for i, (_, p) in enumerate(panels):
        sheet.alpha_composite(p, (pad + (i % 3) * (TILE_PX + pad), pad + (i // 3) * (TILE_PX + pad)))
    big = sheet.resize((w * 3, h * 3), Image.NEAREST)
    for i, (lbl, _) in enumerate(panels):
        label(big, ((pad + (i % 3) * (TILE_PX + pad)) * 3 + 2, (pad + (i // 3) * (TILE_PX + pad)) * 3), lbl, FONT_S)
    path = OUT / "stack.png"
    big.convert("RGB").save(path)
    return path


def render_picks(picks: dict[str, Path]) -> Path:
    """Per type: deployed (blue) | pick (blue) | pick base | pick mask, at 128 px."""
    pad, lab, cell = 10, 18, 128
    cols = ["deployed (blue)", "column pick (blue)", "pick base", "pick mask"]
    sheet = Image.new("RGBA", (pad + 4 * (cell + pad), pad + lab + len(picks) * (lab + cell + pad)), BG)
    neutral = Image.new("RGBA", (cell, cell), NEUTRAL)
    blue = TINTS["viewer blue 3d7eff"]
    for k, c in enumerate(cols):
        label(sheet, (pad + k * (cell + pad), pad), c, FONT_S)
    for r, (utype, base) in enumerate(picks.items()):
        y = pad + lab + r * (lab + cell + pad)
        label(sheet, (pad, y), f"{utype}: {base.parent.name}/{base.name.replace('_base.png', '')}")
        panels = [
            tinted(DEPLOYED / f"{utype}_base.png", blue),
            tinted(base, blue),
            Image.open(base).convert("RGBA"),
            Image.open(mask_of(base)).convert("RGBA"),
        ]
        for k, pnl in enumerate(panels):
            sheet.paste(on(neutral, pnl), (pad + k * (cell + pad), y + lab))
    path = OUT / "picks.png"
    sheet.convert("RGB").save(path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sheets", nargs="+", type=Path)
    ap.add_argument("--picks", help="infantry=<sheet>/<name>,artillery=...,armor=...")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    by_type: dict[str, list[tuple[str, Path]]] = {t: [] for t in TYPES.values()}
    for d in args.sheets:
        d = d.resolve()
        print(render_full(d))
        print(render_tint(d))
        for name, res in candidates(d):
            if "gate_failure" not in res:
                by_type[TYPES[name[:3]]].append((f"{d.name}/{name}", d / f"{name}_base.png"))
    for utype, items in by_type.items():
        print(render_board(utype, items))
    if args.picks:
        picks = {}
        for kv in args.picks.split(","):
            t, ref = kv.split("=")
            picks[t] = HERE / f"{ref}_base.png"
        print(render_stack(picks))
        print(render_picks(picks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
