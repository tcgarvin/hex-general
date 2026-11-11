#!/usr/bin/env python3
"""Review renders for the brand emblem candidates (cut/<name>.png).

Writes into review/:
  small-sizes.png      per candidate: 64, 32, 16 px and a 4x zoom of the 16 px
                       version, on the header wood and on parchment, plus a
                       browser-tab favicon mock (light and dark tab).
  at256-wood.png,
  at256-parchment.png  every candidate at 256 px.
  header.png           each candidate at 36 px in the 48 px world header with
                       the HEX-GENERAL wordmark.

Usage: review_emblems.py label=cutname [label=cutname ...]
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import uikit as K  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "review")


def at(path: str, size: int) -> Image.Image:
    return Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS, reducing_gap=3.0)


def on(surface: str, w: int, h: int) -> Image.Image:
    return (K.wood(w, h) if surface == "wood" else K.parchment(w, h)).convert("RGBA")


def small_block(path: str, surface: str) -> Image.Image:
    sizes = (64, 32, 16)
    pad = 10
    w = sum(sizes) + 64 + pad * 5
    h = 64 + 2 * pad
    bg = on(surface, w, h)
    x = pad
    for s in sizes:
        bg.alpha_composite(at(path, s), (x, (h - s) // 2))
        x += s + pad
    sixteen = on(surface, 16, 16)
    sixteen.alpha_composite(at(path, 16))
    bg.paste(K.zoom(sixteen.convert("RGB"), 4), (x, (h - 64) // 2))
    return bg


def favicon_tabs(path: str) -> Image.Image:
    f = K.label_font(11)
    out = Image.new("RGBA", (200, 84), K.REVIEW_BG + (255,))
    for i, (tab, fg) in enumerate((((0xDE, 0xE1, 0xE6), (40, 40, 40)), ((0x35, 0x36, 0x3A), (230, 230, 230)))):
        y = 6 + i * 40
        d = ImageDraw.Draw(out)
        d.rounded_rectangle([4, y, 196, y + 32], radius=8, fill=tab)
        out.alpha_composite(at(path, 16), (14, y + 8))
        d.text((38, y + 16), "Hex-General", font=f, fill=fg, anchor="lm")
    return out


def small_sizes(items: list[tuple[str, str]]) -> Image.Image:
    f = K.label_font(12)
    blocks = [(lab, small_block(p, "wood"), small_block(p, "parchment"), favicon_tabs(p)) for lab, p in items]
    bw, bh = blocks[0][1].size
    lab_w = 96
    w = lab_w + 2 * bw + 200 + 30
    h = 24 + len(blocks) * (max(bh, 84) + 8)
    out = Image.new("RGBA", (w, h), K.REVIEW_BG + (255,))
    d = ImageDraw.Draw(out)
    d.text((lab_w, 6), "wood: 64 / 32 / 16 / 16 at 4x", font=f, fill=(220, 220, 220))
    d.text((lab_w + bw + 10, 6), "parchment: same", font=f, fill=(220, 220, 220))
    d.text((lab_w + 2 * bw + 20, 6), "favicon in a tab", font=f, fill=(220, 220, 220))
    for i, (lab, wood_b, parch_b, tabs) in enumerate(blocks):
        y = 24 + i * (max(bh, 84) + 8)
        d.text((6, y + bh // 2), lab, font=f, fill=(235, 235, 235), anchor="lm")
        out.alpha_composite(wood_b, (lab_w, y))
        out.alpha_composite(parch_b, (lab_w + bw + 10, y))
        out.alpha_composite(tabs, (lab_w + 2 * bw + 20, y))
    return out.convert("RGB")


def at256(items: list[tuple[str, str]], surface: str) -> Image.Image:
    cols, cell = 4, 280
    rows = (len(items) + cols - 1) // cols
    out = on(surface, cols * cell, rows * (cell + 20))
    d = ImageDraw.Draw(out)
    f = K.label_font(14)
    ink = K.IVORY if surface == "wood" else K.INK
    for i, (lab, p) in enumerate(items):
        x, y = (i % cols) * cell, (i // cols) * (cell + 20)
        out.alpha_composite(at(p, 256), (x + 12, y + 8))
        d.text((x + cell // 2, y + cell + 6), lab, font=f, fill=ink, anchor="mm")
    return out.convert("RGB")


def header(items: list[tuple[str, str]]) -> Image.Image:
    strips = []
    for lab, p in items:
        w, h = 420, 48
        im = on("wood", w, h)
        d = ImageDraw.Draw(im)
        d.rectangle([0, h - 2, w, h], fill=K.BRASS_600)
        im.alpha_composite(at(p, 36), (12, 5))
        K.text(d, (56, h // 2 - 1), "HEX-GENERAL", K.font("display", 16), K.IVORY)
        K.text(d, (200, h // 2 - 1), "TURN", K.font("label", 11), K.IVORY_400)
        K.text(d, (232, h // 2 - 1), "7", K.font("data", 14), K.IVORY)
        d.text((w - 8, h // 2), lab, font=K.label_font(11), fill=(200, 200, 200), anchor="rm")
        strips.append(im.convert("RGB"))
    cols = 2
    rows = (len(strips) + 1) // 2
    out = Image.new("RGB", (cols * 430, rows * 56), K.REVIEW_BG)
    for i, s in enumerate(strips):
        out.paste(s, ((i % cols) * 430, (i // cols) * 56))
    return K.zoom(out, 2) if out.width * 2 <= 1600 else out


def main() -> int:
    items = []
    for arg in sys.argv[1:]:
        lab, name = arg.split("=")
        items.append((lab, os.path.join(HERE, "cut", f"{name}.png")))
    os.makedirs(OUT, exist_ok=True)
    small_sizes(items).save(os.path.join(OUT, "small-sizes.png"))
    at256(items, "wood").save(os.path.join(OUT, "at256-wood.png"))
    at256(items, "parchment").save(os.path.join(OUT, "at256-parchment.png"))
    header(items).save(os.path.join(OUT, "header.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
