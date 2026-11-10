"""Review images for the ground candidates (writes into ../review/):

  ground-compare.png  labelled strip, every hex at the same pixel scale: concept open-grass
                      hex | shipped plains 00 | P1a grass hex | tile 00 of each set; top row
                      at 128 px per hex (1:1), bottom row the same tiles LANCZOS to 64 px
                      (board scale).
  ground-scale.png    cut-scale evidence: per source, a 1:1 cut (128 px window) and a 2:1
                      cut (256 px window LANCZOS to 128) at the same source spot, recoloured
                      the same way, beside the concept hex.

Usage: python3 compare.py G1 G2 G4 ...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from grain import image_metrics
from recolour import apply

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
from cut import bleed_mask  # noqa: E402
from hexgeom import hex_mask  # noqa: E402

REVIEW = HERE.parent / "review"
CONCEPT = REPO / "docs" / "concept-art" / "concept1.png"
P1A = HERE.parent / "raw" / "P1a-keyart-fullconcept.png"
# Centre of a clean open-grass hex in concept1.png (about 136 px point to point, so a
# 128 px hex mask eroded 4 px stays inside its grid line), and a clean spot in P1a.
CONCEPT_HEX_CENTRE = (347, 185)
P1A_HEX_CENTRE = (776, 752)
BG = (28, 28, 30)
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
FONT_B = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)


def hex_cut(path: Path, centre: tuple[int, int], inset: int = 0) -> Image.Image:
    """128 px hex-masked RGBA cut centred at `centre`; `inset` shrinks the mask (px per side)."""
    rgb = np.asarray(Image.open(path).convert("RGB"))
    cx, cy = centre
    crop = rgb[cy - 64 : cy + 64, cx - 64 : cx + 64]
    if inset:
        small = hex_mask(128 - 2 * inset, 128 - 2 * inset)
        mask = np.zeros((128, 128), np.uint8)
        mask[inset : 128 - inset, inset : 128 - inset] = small
    else:
        mask = bleed_mask(hex_mask(128, 128), 1)
    return Image.fromarray(np.dstack([crop, mask]), "RGBA")


def strip(cols: list[tuple[str, str, Image.Image]], px: int) -> Image.Image:
    pad, label_h = 12, 40
    w = len(cols) * (px + pad) + pad
    out = Image.new("RGB", (w, px + label_h + 2 * pad), BG)
    d = ImageDraw.Draw(out)
    for i, (title, sub, tile) in enumerate(cols):
        x = pad + i * (px + pad)
        t = tile if px == 128 else tile.resize((px, px), Image.LANCZOS)
        out.paste(t, (x, pad), t)
        if px == 128:
            d.text((x, px + pad + 4), title, fill=(235, 235, 235), font=FONT_B)
            d.text((x, px + pad + 21), sub, fill=(170, 170, 170), font=FONT)
    return out


def sub_of(img: Image.Image) -> str:
    tmp = REVIEW / "_tmp.png"
    img.save(tmp)
    m = image_metrics(str(tmp))
    tmp.unlink()
    return f"L{m['lum_mean']:.0f} S{m['S']:.2f} sd{m['lum_std']:.1f} g{m['hp1']:.1f}"


def compare(sets: list[str]) -> None:
    cols = []
    concept = hex_cut(CONCEPT, CONCEPT_HEX_CENTRE, inset=4)
    cols.append(("concept", sub_of(concept), concept))
    shipped = Image.open(REPO / "frontend/src/board/tiles/plains/00.png").convert("RGBA")
    cols.append(("shipped 00", sub_of(shipped), shipped))
    p1a = hex_cut(P1A, P1A_HEX_CENTRE)
    cols.append(("P1a grass", sub_of(p1a), p1a))
    for s in sets:
        t = Image.open(HERE / s / "plains" / "00.png").convert("RGBA")
        cols.append((f"{s} 00", sub_of(t), t))
    a, b = strip(cols, 128), strip(cols, 64)
    out = Image.new("RGB", (a.width, a.height + b.height + 24), BG)
    out.paste(a, (0, 0))
    d = ImageDraw.Draw(out)
    d.text((12, a.height + 4), "same tiles at board scale (64 px per hex, LANCZOS):", fill=(170, 170, 170), font=FONT)
    bb = Image.new("RGB", (a.width, b.height), BG)
    for i in range(len(cols)):
        bb.paste(b.crop((12 + i * 76, 0, 12 + i * 76 + 76, b.height)), (12 + i * 140, 0))
    out.paste(bb, (0, a.height + 20))
    out.save(REVIEW / "ground-compare.png")
    print(REVIEW / "ground-compare.png", out.size)


def scale_sheet(sets: list[str]) -> None:
    concept = hex_cut(CONCEPT, CONCEPT_HEX_CENTRE, inset=4)
    rows = []
    for s in sets:
        rec = json.loads((HERE / s / "set.json").read_text())
        raw = Image.open(HERE.parent / rec["raw"]).convert("RGB")  # unflattened raw for every set
        x, y = rec["tiles"][0]["source_offset_px"]
        x, y = min(x, raw.width - 256), min(y, raw.height - 256)  # both windows share a top-left
        params = {k: rec["recolour"][k] for k in ("hue_shift_deg", "sat_scale", "val_scale")}
        one = np.asarray(raw.crop((x, y, x + 128, y + 128)), np.float64)
        two = np.asarray(raw.crop((x, y, x + 256, y + 256)).resize((128, 128), Image.LANCZOS), np.float64)
        mask = bleed_mask(hex_mask(128, 128), 1)
        t1 = Image.fromarray(np.dstack([apply(one, params), mask]), "RGBA")
        t2 = Image.fromarray(np.dstack([apply(two, params), mask]), "RGBA")
        rows.append(strip([("concept", sub_of(concept), concept), (f"{s} 1:1", sub_of(t1), t1),
                           (f"{s} 2:1", sub_of(t2), t2)], 128))
    out = Image.new("RGB", (rows[0].width, sum(r.height for r in rows)), BG)
    y = 0
    for r in rows:
        out.paste(r, (0, y))
        y += r.height
    out.save(REVIEW / "ground-scale.png")
    print(REVIEW / "ground-scale.png", out.size)


if __name__ == "__main__":
    compare(sys.argv[1:])
    scale_sheet(sys.argv[1:])
