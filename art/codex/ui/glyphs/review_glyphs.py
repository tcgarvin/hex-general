#!/usr/bin/env python3
"""In-context review renders for the glyph candidates (cut/<name>-<take>.png).

Writes into review/:
  sizes-<group>.png   each glyph at 16, 20, 24, 32 px, ivory on the header wood
                      and ink on parchment, one row per glyph, one block per take;
                      rendered at 1:1 then enlarged 2x nearest-neighbour.
  header-<take>.png   the world header strip mocked with that take's glyphs
                      (16 and 20 px glyph rows), 1:1 and 2x.
  roster-<take>.png   three roster rows on parchment with the unit glyphs.
  sheet-<take>.png    every glyph of a take large (96 px) on parchment, labelled.

Usage: review_glyphs.py --takes A,B[,C] [--pick name=take,...]
  --pick builds header-pick.png / roster-pick.png from a mixed selection.
"""

from __future__ import annotations

import argparse
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import uikit as K  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CUT = os.path.join(HERE, "cut")
OUT = os.path.join(HERE, "review")
SIZES = (16, 20, 24, 32)
GROUPS = {
    "economy-units": [
        "manpower", "fuel", "steel", "barracks", "depot", "workshop",
        "infantry", "armor", "artillery", "army", "turn", "column",
    ],
    "world": [
        "garrison", "base", "city", "farm", "oilfield", "steelmill",
        "factory", "travel", "deploy", "disband",
    ],
}


def glyph(name: str, take: str) -> str:
    return os.path.join(CUT, f"{name}-{take}.png")


def size_strip(path: str, surface: str) -> Image.Image:
    """The four sizes side by side on one surface."""
    pad = 6
    w = sum(SIZES) + pad * (len(SIZES) + 1)
    h = max(SIZES) + 2 * pad
    bg = K.wood(w, h) if surface == "wood" else K.parchment(w, h)
    colour = K.IVORY if surface == "wood" else K.INK
    x = pad
    for s in SIZES:
        K.stamp(bg, K.mask_at(path, s), colour, (x, (h - s) // 2))
        x += s + pad
    return bg


def sizes_sheet(names: list[str], takes: list[str]) -> Image.Image:
    lab_w, gap = 74, 10
    sample = size_strip(glyph(names[0], takes[0]), "wood")
    sw, sh = sample.size
    block = 2 * sw + 4
    head = 16
    w = lab_w + len(takes) * (block + gap)
    h = head + len(names) * (sh + 4)
    out = Image.new("RGB", (w, h), K.REVIEW_BG)
    d = ImageDraw.Draw(out)
    f = K.label_font(10)
    for t, take in enumerate(takes):
        d.text((lab_w + t * (block + gap) + 2, 3), f"take {take}: wood | parchment", font=f, fill=(220, 220, 220))
    for r, name in enumerate(names):
        y = head + r * (sh + 4)
        d.text((4, y + sh // 2), name, font=f, fill=(230, 230, 230), anchor="lm")
        for t, take in enumerate(takes):
            p = glyph(name, take)
            if not os.path.exists(p):
                continue
            x = lab_w + t * (block + gap)
            out.paste(size_strip(p, "wood"), (x, y))
            out.paste(size_strip(p, "parchment"), (x + sw + 4, y))
    return K.zoom(out, 2)


def header(pick: dict[str, str], gsize: int) -> Image.Image:
    """The world header (WorldShell WorldHeader): h-12, px-4, gap-5, text-sm."""
    w, h = 790, 48
    im = K.wood(w, h)
    d = ImageDraw.Draw(im)
    d.rectangle([0, h - 2, w, h], fill=K.BRASS_600)
    disp, lab, data = K.font("display", 16), K.font("label", 11), K.font("data", 14)
    cy = h // 2 - 1
    x = 16
    x += K.text(d, (x, cy), "HEX-GENERAL", disp, K.IVORY) + 20

    def chip(glyph_name: str | None, label: str | None, value: str, cap: str | None, net: str | None) -> None:
        nonlocal x
        if glyph_name is not None:
            K.stamp(im, K.mask_at(glyph(glyph_name, pick[glyph_name]), gsize), K.IVORY, (x, cy - gsize // 2))
            x += gsize + 5
        if label is not None:
            x += K.text(d, (x, cy + 1), label, lab, K.IVORY_400) + 6
        x += K.text(d, (x, cy), value, data, K.IVORY)
        if cap is not None:
            x += K.text(d, (x, cy), cap, data, K.IVORY_400)
        if net is not None:
            x += 5 + K.text(d, (x + 5, cy), net, data, K.VALID)
        x += 20

    chip(None, "TURN", "7", None, None)
    chip("turn", "RESOLVES", "3h 20m", None, None)
    chip("manpower", None, "12", "/30", "(+2)")
    chip("fuel", None, "20", "/30", "(+0)")
    chip("steel", None, "9", "/30", "(+1)")
    chip("army", None, "5", "/6", None)
    return im


def roster(pick: dict[str, str]) -> Image.Image:
    rows = [
        ("infantry", "INF", "(08,31)", "10/10", None),
        ("armor", "TANK", "(12,34)", "07/10", "column"),
        ("artillery", "ART", "(06,30)", "06/06", None),
    ]
    w, rh = 420, 30
    im = K.parchment(w, rh * len(rows) + 8)
    d = ImageDraw.Draw(im)
    data = K.font("data", 15)
    for i, (unit, short, at, hp, posture) in enumerate(rows):
        cy = 4 + i * rh + rh // 2
        K.stamp(im, K.mask_at(glyph(unit, pick[unit]), 20), K.INK, (10, cy - 10))
        K.text(d, (40, cy), short, data, K.INK)
        K.text(d, (110, cy), at, data, K.INK)
        K.text(d, (210, cy), hp, data, K.INK)
        if posture is not None:
            K.stamp(im, K.mask_at(glyph(posture, pick[posture]), 20), K.INK, (290, cy - 10))
            K.text(d, (316, cy), "COLUMN", data, K.INK)
        if i < len(rows) - 1:
            d.line([(8, 4 + (i + 1) * rh), (w - 8, 4 + (i + 1) * rh)], fill=K.PARCHMENT_400, width=1)
    return im


def stack(images: list[Image.Image], gap: int = 10) -> Image.Image:
    w = max(i.width for i in images)
    h = sum(i.height for i in images) + gap * (len(images) - 1)
    out = Image.new("RGB", (w, h), K.REVIEW_BG)
    y = 0
    for i in images:
        out.paste(i, (0, y))
        y += i.height + gap
    return out


def context(pick: dict[str, str]) -> Image.Image:
    """Header at 16 and 20 px glyphs plus the roster, 1:1 on top, 2x crop below."""
    h16, h20, ros = header(pick, 16), header(pick, 20), roster(pick)
    one = stack([h16, h20, ros])
    two = stack([K.zoom(h20, 2), K.zoom(ros, 2)])
    return stack([one, two], 16)


def big_sheet(take: str) -> Image.Image:
    names = GROUPS["economy-units"] + GROUPS["world"]
    cell, cols = 132, 6
    rows = (len(names) + cols - 1) // cols
    im = K.parchment(cols * cell, rows * cell)
    d = ImageDraw.Draw(im)
    f = K.label_font(11)
    for i, n in enumerate(names):
        p = glyph(n, take)
        if not os.path.exists(p):
            continue
        x, y = (i % cols) * cell, (i // cols) * cell
        K.stamp(im, K.mask_at(p, 96), K.INK, (x + 18, y + 6))
        d.text((x + cell // 2, y + 116), f"{n}-{take}", font=f, fill=K.INK, anchor="mm")
    return im


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", default="A,B")
    ap.add_argument("--pick", default="", help="name=take,... for the mixed context render")
    args = ap.parse_args()
    takes = args.takes.split(",")
    os.makedirs(OUT, exist_ok=True)
    base_takes = [t for t in takes if t in ("A", "B")]
    for group, names in GROUPS.items():
        sizes_sheet(names, base_takes).save(os.path.join(OUT, f"sizes-{group}.png"))
    all_names = GROUPS["economy-units"] + GROUPS["world"]
    extra = [t for t in takes if t not in base_takes]
    if extra:
        rerolled = [n for n in all_names if any(os.path.exists(glyph(n, t)) for t in extra)]
        # Two takes per block row keeps the image under the review page's 1600 px.
        sizes_sheet(rerolled, base_takes).save(os.path.join(OUT, "sizes-rerolls-AB.png"))
        sizes_sheet(rerolled, extra).save(os.path.join(OUT, "sizes-rerolls-CD.png"))
    for take in takes:
        pick = {n: take for n in all_names}
        if not all(os.path.exists(glyph(n, take)) for n in ["turn", "manpower", "fuel", "steel", "army", "infantry", "armor", "artillery", "column"]):
            continue
        context(pick).save(os.path.join(OUT, f"context-{take}.png"))
        big_sheet(take).save(os.path.join(OUT, f"sheet-{take}.png"))
    if args.pick:
        pick = {n: takes[0] for n in all_names}
        for kv in args.pick.split(","):
            n, t = kv.split("=")
            pick[n] = t
        context(pick).save(os.path.join(OUT, "context-pick.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
