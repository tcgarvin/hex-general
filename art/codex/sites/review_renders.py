#!/usr/bin/env python3
"""Render the review images for the site-kind icon candidates.

For one icon set dir (base-1.png ... factory-2.png) render:
  <prefix>-full.png    12 icons at 128 px, kinds as columns, variants as rows
  <prefix>-small.png   32 px and 24 px rows on board-olive, panel-dark, paper
                       backgrounds at 1x, plus a 3x nearest blow-up of the 32 px row
  <prefix>-board.png   each icon at 64 px centred on a real town / city tile, 1x
  <prefix>-board-halo.png  (optional) same, with a 2 px paper halo round the icon
"""

from __future__ import annotations

import argparse
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

KINDS = ["base", "city", "farm", "oil_field", "steel_mill", "factory"]
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
TILES = "/home/timg/code/hex-general/frontend/src/board/tiles"
BACKGROUNDS = {
    "board olive": (104, 118, 58, 255),
    "panel dark": (38, 36, 34, 255),
    "paper": (232, 226, 210, 255),
}


def load(set_dir: str) -> dict[tuple[str, int], Image.Image]:
    return {
        (k, v): Image.open(os.path.join(set_dir, f"{k}-{v}.png")).convert("RGBA")
        for k in KINDS
        for v in (1, 2)
    }


def halo(icon: Image.Image, colour: tuple[int, int, int], radius: int) -> Image.Image:
    a = icon.getchannel("A").filter(ImageFilter.MaxFilter(radius * 2 + 1))
    a = a.point(lambda p: 255 if p > 40 else 0).filter(ImageFilter.GaussianBlur(0.6))
    base = Image.new("RGBA", icon.size, colour + (0,))
    base.putalpha(a)
    base.alpha_composite(icon)
    return base


def full(icons, bg, out):
    cw, lab = 140, 20
    im = Image.new("RGBA", (cw * 6 + 10, (128 + lab + 8) * 2 + 10), bg)
    d = ImageDraw.Draw(im)
    for c, k in enumerate(KINDS):
        for v in (1, 2):
            x, y = 10 + c * cw, 5 + (v - 1) * (128 + lab + 8)
            d.text((x, y), f"{k}-{v}", font=FONT, fill=label_colour(bg))
            im.alpha_composite(icons[(k, v)], (x, y + lab))
    im.save(out)


def label_colour(bg):
    return (20, 20, 20, 255) if sum(bg[:3]) > 380 else (235, 235, 225, 255)


def ink_for(icon: Image.Image, bg, light_ink: bool) -> Image.Image:
    """Recolour a monochrome ink icon to cream when it sits on a dark background."""
    if not light_ink or sum(bg[:3]) > 380:
        return icon
    out = Image.new("RGBA", icon.size, (236, 230, 212, 255))
    out.putalpha(icon.getchannel("A"))
    return out


def small(icons, out, light_ink=False):
    rows = []
    for name, bg in BACKGROUNDS.items():
        for size in (32, 24):
            rows.append((f"{size}px on {name}", size, bg))
    lw, gap = 150, 8
    width = lw + 12 * (32 + gap) + 10
    blow_h = 3 * 32 + 10
    height = len(rows) * 42 + 30 + len(BACKGROUNDS) * (blow_h + 4)
    im = Image.new("RGBA", (max(width, lw + 12 * (96 + 6)), height), (60, 60, 64, 255))
    d = ImageDraw.Draw(im)
    y = 6
    order = [(k, v) for v in (1, 2) for k in KINDS]
    for label, size, bg in rows:
        d.rectangle((lw - 4, y - 4, lw + 12 * (32 + gap), y + 36), fill=bg)
        d.text((6, y + 10), label, font=FONT, fill=(235, 235, 225, 255))
        for i, key in enumerate(order):
            ic = ink_for(icons[key], bg, light_ink).resize((size, size), Image.LANCZOS)
            im.alpha_composite(ic, (lw + i * (32 + gap) + (32 - size) // 2, y + (32 - size) // 2))
        y += 42
    y += 8
    for name, bg in BACKGROUNDS.items():
        d.text((6, y + 40), f"32px, 3x zoom\non {name}", font=FONT, fill=(235, 235, 225, 255))
        d.rectangle((lw - 4, y, lw + 12 * (96 + 6), y + blow_h), fill=bg)
        for i, key in enumerate(order):
            ic32 = Image.new("RGBA", (32, 32), bg)
            ic32.alpha_composite(ink_for(icons[key], bg, light_ink).resize((32, 32), Image.LANCZOS))
            im.paste(ic32.resize((96, 96), Image.NEAREST), (lw + i * (96 + 6), y + 5))
        y += blow_h + 4
    im.save(out)


def board(icons, out, with_halo):
    tiles = {
        "town": Image.open(f"{TILES}/town/00.png").convert("RGBA"),
        "city": Image.open(f"{TILES}/city/00.png").convert("RGBA"),
    }
    lab = 18
    im = Image.new("RGBA", (130 + 6 * 132, 4 * (128 + lab + 4) + 4), (40, 40, 48, 255))
    d = ImageDraw.Draw(im)
    r = 0
    for v in (1, 2):
        for tname, tile in tiles.items():
            y = 4 + r * (128 + lab + 4)
            d.text((6, y + lab + 50), f"variant {v}\non {tname}", font=FONT, fill=(235, 235, 225, 255))
            for c, k in enumerate(KINDS):
                x = 130 + c * 132
                d.text((x, y), k, font=FONT, fill=(255, 230, 120, 255))
                cell = tile.copy()
                ic = icons[(k, v)].resize((64, 64), Image.LANCZOS)
                if with_halo:
                    ic = halo(ic, (236, 230, 212), 2)
                cell.alpha_composite(ic, (32, 32))
                im.alpha_composite(cell, (x, y + lab))
            r += 1
    im.save(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("set_dir")
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--full-bg", choices=list(BACKGROUNDS), default="panel dark")
    ap.add_argument("--halo", action="store_true")
    ap.add_argument("--light-ink-on-dark", action="store_true")
    ap.add_argument("--full-only", action="store_true")
    a = ap.parse_args()
    icons = load(a.set_dir)
    full(icons, BACKGROUNDS[a.full_bg], f"{a.prefix}-full.png")
    if a.full_only:
        return
    small(icons, f"{a.prefix}-small.png", a.light_ink_on_dark)
    board(icons, f"{a.prefix}-board.png", False)
    if a.halo:
        board(icons, f"{a.prefix}-board-halo.png", True)


if __name__ == "__main__":
    main()
