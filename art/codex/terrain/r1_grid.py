#!/usr/bin/env python3
"""Labelled grid of tiles (one per cell, on a dark ground) for quick review.

    python3 art/codex/terrain/r1_grid.py --out <png> [--px 128] [--cols 8] tile.png ...
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--px", type=int, default=128)
    ap.add_argument("--cols", type=int, default=8)
    ap.add_argument("--title", default="")
    ap.add_argument("tiles", nargs="+", type=Path)
    a = ap.parse_args()
    font = ImageFont.load_default(size=12)
    lab, pad, top = 16, 6, 24
    rows = (len(a.tiles) + a.cols - 1) // a.cols
    out = Image.new("RGB", (a.cols * (a.px + pad) + pad, top + rows * (a.px + lab + pad)), (30, 30, 34))
    d = ImageDraw.Draw(out)
    d.text((pad, 4), a.title, fill=(240, 240, 240), font=ImageFont.load_default(size=14))
    for i, p in enumerate(a.tiles):
        r, c = divmod(i, a.cols)
        x, y = pad + c * (a.px + pad), top + r * (a.px + lab + pad)
        im = Image.open(p).convert("RGBA").resize((a.px, a.px), Image.Resampling.LANCZOS)
        bg = Image.new("RGBA", im.size, (30, 30, 34, 255))
        bg.alpha_composite(im)
        out.paste(bg.convert("RGB"), (x, y + lab))
        d.text((x, y), p.stem[:22], fill=(230, 220, 150), font=font)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    out.save(a.out)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
