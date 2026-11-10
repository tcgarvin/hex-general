#!/usr/bin/env python3
"""Side-by-side review strip: candidate tiles next to a concept crop, at tile scale.

The concept crop is resampled onto tile geometry (x0.903 horizontally, x0.962
vertically; concept-read section 0) so a 128 px tile and the crop share one
scale. Rows: native 128 px per hex, shown x`--zoom`; then the 64 px board scale,
shown x`--zoom` as well (so detail lost at board scale is visible).

    python3 art/codex/terrain/r1_compare.py --ref <crop.png> --out <png> \
        [--label NAME=]tile.png ...
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = (30, 30, 34)


def on_bg(path: Path, size: int) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    if im.width != size:
        im = im.resize((size, size), Image.Resampling.LANCZOS)
    bg = Image.new("RGBA", im.size, BG + (255,))
    bg.alpha_composite(im)
    return bg.convert("RGB")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--zoom", type=int, default=3)
    ap.add_argument("--title", default="")
    ap.add_argument("tiles", nargs="+", help="[label=]path")
    args = ap.parse_args()
    items = []
    for t in args.tiles:
        label, _, path = t.rpartition("=")
        items.append((label or Path(path).stem, Path(path)))
    ref = Image.open(args.ref).convert("RGB")
    ref = ref.resize((round(ref.width * 0.903), round(ref.height * 0.962)), Image.Resampling.LANCZOS)
    z = args.zoom
    font = ImageFont.load_default(size=14)
    rows = []
    for px in (128, 64):
        tiles = [on_bg(p, px).resize((px * z, px * z), Image.Resampling.LANCZOS) for _, p in items]
        r = ref.resize((round(ref.width * px / 128), round(ref.height * px / 128)), Image.Resampling.LANCZOS)
        r = r.resize((r.width * z, r.height * z), Image.Resampling.LANCZOS)
        rows.append((px, tiles, r))
    pad, lab = 10, 20
    w = max(sum(t.width + pad for t in tiles) + r.width + 2 * pad for _, tiles, r in rows)
    h = 30 + sum(max(tiles[0].height, r.height) + lab + pad for _, tiles, r in rows)
    out = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(out)
    d.text((pad, 6), args.title, fill=(240, 240, 240), font=font)
    y = 30
    for px, tiles, r in rows:
        x = pad
        for (label, _), t in zip(items, tiles):
            d.text((x, y), f"{label} @{px}px", fill=(230, 220, 150), font=font)
            out.paste(t, (x, y + lab))
            x += t.width + pad
        d.text((x, y), f"concept @{px}px", fill=(150, 220, 230), font=font)
        out.paste(r, (x, y + lab))
        y += max(tiles[0].height, r.height) + lab + pad
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.save(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
