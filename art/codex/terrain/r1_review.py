#!/usr/bin/env python3
"""Per-shape review montage for one oriented terrain: each shape's r1 candidates
next to the shipped tiles, at 128 px (native) and 64 px (board scale), under a
header strip with the concept crop at both scales (tile geometry, concept-read
section 0).

    python3 art/codex/terrain/r1_review.py --terrain river --ref <crop.png> --out <png>
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BG = (30, 30, 34)


def tile(path: Path, px: int) -> Image.Image:
    im = Image.open(path).convert("RGBA").resize((px, px), Image.Resampling.LANCZOS)
    bg = Image.new("RGBA", im.size, BG + (255,))
    bg.alpha_composite(im)
    return bg.convert("RGB")


def groups(folder: Path, prefix: str) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for p in sorted(folder.glob(f"{prefix}*.png")):
        out.setdefault(re.sub(r"_\d+$", "", p.stem), []).append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--terrain", required=True)
    ap.add_argument("--prefix", default="", help="only names starting with this (road: plains_)")
    ap.add_argument("--ref", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    cand = groups(HERE / "candidates" / "r1" / a.terrain, a.prefix)
    shipped = groups(REPO / "frontend" / "src" / "board" / "tiles" / a.terrain, a.prefix)
    font = ImageFont.load_default(size=13)
    big = ImageFont.load_default(size=15)
    ncand = max(len(v) for v in cand.values())
    nship = max([len(v) for v in shipped.values()] + [1])
    pad, label_w, row_lab = 8, 150, 16
    w128 = (ncand + nship) * (128 + pad) + 20
    w64 = (ncand + nship) * (64 + pad) + 20
    ref = Image.open(a.ref).convert("RGB")
    ref = ref.resize((round(ref.width * 0.903), round(ref.height * 0.962)), Image.Resampling.LANCZOS)
    ref64 = ref.resize((ref.width // 2, ref.height // 2), Image.Resampling.LANCZOS)
    head = max(ref.height, 40) + 40
    width = label_w + w128 + w64 + pad
    height = head + len(cand) * (128 + row_lab + pad) + pad
    out = Image.new("RGB", (max(width, ref.width + ref64.width + 3 * pad), height), BG)
    d = ImageDraw.Draw(out)
    d.text((pad, 6), f"{a.terrain}: r1 candidates (yellow) | shipped (grey), at 128 px and at 64 px board scale; "
           "concept crop at the same two scales", fill=(240, 240, 240), font=big)
    out.paste(ref, (pad, 30))
    out.paste(ref64, (2 * pad + ref.width, 30))
    y = head
    for shape, paths in cand.items():
        d.text((pad, y + row_lab + 50), shape, fill=(255, 220, 90), font=font)
        x = label_w
        for px in (128, 64):
            for p in paths:
                d.text((x, y), p.stem[-2:], fill=(230, 220, 150), font=font)
                out.paste(tile(p, px), (x, y + row_lab))
                x += px + pad
            x += (ncand - len(paths)) * (px + pad)
            for p in shipped.get(shape, []):
                d.text((x, y), "shipped", fill=(170, 170, 170), font=font)
                out.paste(tile(p, px), (x, y + row_lab))
                x += px + pad
            x += (nship - len(shipped.get(shape, []))) * (px + pad) + 20
        y += 128 + row_lab + pad
    a.out.parent.mkdir(parents=True, exist_ok=True)
    out.save(a.out)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
