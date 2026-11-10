"""Area-terrain review montage: concept hexes | (shipped) | picks, at 128 and 64 px.

For woods, hills, town and city candidates. The concept hexes are cut from
docs/concept-art/concept1.png and resampled to the tile geometry
(r1_terrain_measure.concept_hex), so all columns share one scale. The 64 px row
is a LANCZOS half-size of the 128 px row (board scale); a 2x nearest copy of the
128 px row follows for close inspection. Labels are <set>/<class>/<nn>.

usage: python3 art/codex/terrain/area_review.py <class> --concept 4,2 7,3 \
           --picks art/codex/terrain/candidates/r1/woods/*.png ... --out <png>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from r1_terrain_measure import REPO, concept_hex  # noqa: E402

sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
from paintin_sheet import output_alpha  # noqa: E402

SHIPPED = REPO / "frontend" / "src" / "board" / "tiles"
BG = (34, 34, 40)


def font(size: int) -> ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def rgba_on_bg(img: Image.Image) -> Image.Image:
    bg = Image.new("RGBA", img.size, BG + (255,))
    bg.alpha_composite(img.convert("RGBA"))
    return bg.convert("RGB")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cls")
    ap.add_argument("--concept", nargs="+", required=True, help="concept hex ids k,n")
    ap.add_argument("--picks", nargs="+", type=Path, required=True)
    ap.add_argument("--shipped", action="store_true", help="add the first shipped tile")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--title", default="")
    args = ap.parse_args()
    alpha = Image.fromarray(output_alpha())
    items: list[tuple[str, Image.Image]] = []
    for spec in args.concept:
        k, n = (int(v) for v in spec.split(","))
        im = Image.fromarray(np.clip(concept_hex(k, n), 0, 255).astype(np.uint8)).convert("RGBA")
        im.putalpha(alpha)
        items.append((f"concept ({k},{n})", rgba_on_bg(im)))
    if args.shipped:
        shipped = sorted((SHIPPED / args.cls).glob("*.png"))
        items.append((f"shipped {shipped[0].stem}", rgba_on_bg(Image.open(shipped[0]))))
    for p in args.picks:
        items.append((f"{p.parent.parent.name}/{p.parent.name}/{p.stem}", rgba_on_bg(Image.open(p))))
    pad, lab = 10, 16
    rows = []
    for size in (128, 64):
        row = Image.new("RGB", (len(items) * (size + pad) + pad, size + lab + pad), BG)
        dr = ImageDraw.Draw(row)
        for i, (name, im) in enumerate(items):
            x = pad + i * (size + pad)
            if size == 128:
                dr.text((x, 2), name, fill=(220, 220, 220), font=font(10))
            row.paste(im if size == 128 else im.resize((64, 64), Image.Resampling.LANCZOS), (x, lab))
        rows.append(row)
    big = rows[0].resize((rows[0].width * 2, rows[0].height * 2), Image.Resampling.NEAREST)
    title_h = 24
    out = Image.new(
        "RGB", (big.width, title_h + rows[0].height + rows[1].height + big.height + 3 * pad), BG
    )
    dr = ImageDraw.Draw(out)
    dr.text((pad, 4), args.title or f"{args.cls}: concept | picks (128 px, 64 px, 128 px at 2x)", fill=(255, 255, 255), font=font(14))
    y = title_h
    for im in (rows[0], rows[1], big):
        out.paste(im, (0, y))
        y += im.height + pad
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.save(args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
