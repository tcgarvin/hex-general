#!/usr/bin/env python3
"""Mock the sign-in page over a key-art candidate, and measure how calm the panel area is.

The art is scaled to cover 1440x900 (centre crop), darkened with a radial
vignette, and a parchment sign-in card is placed over the calm region: centred
for `centre-*` candidates, centred in the right third for `right-*`. The card
is the ratified raw-edged parchment sheet from the chrome kit
(`art/foundry/ui/cut/sheet.png`), with an optional emblem, the title in
Allerta Stencil, two inputs labelled in Archivo Narrow and a drawn brass
button (fonts read from the repo's TTFs).

Calmness: mean absolute Laplacian of the luminance under the card divided by
the same over the whole frame (lower is calmer), and the mean luminance under
the card (0-255). Both are printed as JSON and written to review/calm.json.

Usage: mock_signin.py raw/<name>.png ... [--emblem path.png]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import uikit as K  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "review")
W, H = 1440, 900
CARD_W, CARD_H = 400, 470
SHEET = os.path.join(K.REPO, "art/foundry/ui/cut/sheet.png")


def cover(im: Image.Image) -> Image.Image:
    scale = max(W / im.width, H / im.height)
    big = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    x0, y0 = (big.width - W) // 2, (big.height - H) // 2
    return big.crop((x0, y0, x0 + W, y0 + H))


def vignette(im: Image.Image, strength: float = 0.55) -> Image.Image:
    ys, xs = np.mgrid[0:H, 0:W]
    d = np.sqrt(((xs - W / 2) / (W / 2)) ** 2 + ((ys - H / 2) / (H / 2)) ** 2) / np.sqrt(2)
    k = 1.0 - strength * np.clip((d - 0.35) / 0.65, 0, 1) ** 1.5
    arr = np.asarray(im, dtype=np.float64) * k[..., None]
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")


def card_origin(kind: str) -> tuple[int, int]:
    if kind == "right":
        cx = round(W * (0.62 + 1.0) / 2)
    else:
        cx = W // 2
    return cx - CARD_W // 2, (H - CARD_H) // 2


def calmness(im: Image.Image, box: tuple[int, int, int, int]) -> dict[str, float]:
    g = np.asarray(im.convert("L"), dtype=np.float64)
    lap = np.abs(4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:])
    x0, y0, x1, y1 = box
    inner = lap[y0 - 1 : y1 - 1, x0 - 1 : x1 - 1]
    return {
        "detail_ratio": round(float(inner.mean() / lap.mean()), 3),
        "panel_luma": round(float(g[y0:y1, x0:x1].mean()), 1),
    }


def brass_button(w: int, h: int, label: str) -> Image.Image:
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    c = 8
    poly = [(c, 0), (w - c, 0), (w, c), (w, h - c), (w - c, h), (c, h), (0, h - c), (0, c)]
    grad = Image.new("RGB", (w, h))
    gd = ImageDraw.Draw(grad)
    for y in range(h):
        t = y / (h - 1)
        top, bot = np.array(K.BRASS_400), np.array(K.BRASS_600)
        col = tuple(int(v) for v in (top * (1 - t) + bot * t))
        gd.line([(0, y), (w, y)], fill=col)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    im.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(im)
    d.polygon(poly, outline=K.WOOD_900, width=2)
    f = K.font("display", 20)
    d.text((w / 2, h / 2 + 1), label, font=f, fill=(0xF2, 0xD8, 0x9A), anchor="mm")
    d.text((w / 2, h / 2), label, font=f, fill=K.INK, anchor="mm")
    return im


def card(emblem: str | None) -> Image.Image:
    sheet = Image.open(SHEET).convert("RGBA").resize((CARD_W, CARD_H), Image.LANCZOS)
    shadow = Image.new("RGBA", (CARD_W + 40, CARD_H + 40), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 150), (20, 26), sheet.getchannel("A"))
    out = shadow.filter(ImageFilter.GaussianBlur(10))
    out.alpha_composite(sheet, (20, 20))
    d = ImageDraw.Draw(out)
    x0, y = 20 + 48, 20 + 44
    if emblem is not None:
        e = Image.open(emblem).convert("RGBA").resize((76, 76), Image.LANCZOS)
        out.alpha_composite(e, (20 + (CARD_W - 76) // 2, y - 10))
        y += 80
    d.text((20 + CARD_W / 2, y + 14), "HEX-GENERAL", font=K.font("display", 36), fill=K.INK, anchor="mm")
    y += 50
    lab, inp = K.font("label", 13), K.font("data", 16)
    for name, value in (("EMAIL", "commander@example.com"), ("PASSWORD", "•" * 10)):
        d.text((x0, y), name, font=lab, fill=(0x5A, 0x48, 0x36), anchor="lm")
        y += 12
        d.rectangle([x0, y, x0 + CARD_W - 96, y + 38], fill=(0xEF, 0xE0, 0xC0), outline=K.PARCHMENT_400, width=2)
        d.line([(x0 + 2, y + 2), (x0 + CARD_W - 98, y + 2)], fill=(0xC9, 0xB2, 0x88), width=2)
        d.text((x0 + 12, y + 20), value, font=inp, fill=K.INK, anchor="lm")
        y += 56
    y += 4
    btn = brass_button(CARD_W - 96, 48, "SIGN IN")
    out.alpha_composite(btn, (x0, y))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("raws", nargs="+")
    ap.add_argument("--emblem", default=None)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    report = {}
    c = card(args.emblem)
    for raw in args.raws:
        name = os.path.splitext(os.path.basename(raw))[0]
        kind = name.split("-")[0]
        art = cover(Image.open(raw).convert("RGB"))
        cx, cy = card_origin(kind)
        report[name] = calmness(art, (cx, cy, cx + CARD_W, cy + CARD_H))
        page = vignette(art).convert("RGBA")
        page.alpha_composite(c, (cx - 20, cy - 20))
        page.convert("RGB").save(os.path.join(OUT, f"mock-{name}.jpg"), quality=90)
    json.dump(report, open(os.path.join(OUT, "calm.json"), "w"), indent=1)
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
