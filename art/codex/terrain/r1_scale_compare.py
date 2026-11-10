"""r1 step 1: compare paint-in sheet scales (and rim-pin vs ground-restore).

For each class (woods, hills) and each scale run under art/codex/terrain/<class>/
scale-<layout>/, take one representative cell, pin it both ways (rim pin only,
and with the difference-matte ground restore), and place it in a 7-hex flower
of r1 plains so the seam to the neighbours and any ring inside the rim are
visible. Beside each row sits the matching concept crop, resampled to the same
hex scale. Writes one image at 128 px per hex over one at 64 px per hex, and
prints per-scale numbers (registration, gate passes, rim-vs-inner ring step,
fine grain of the feature core at 64 px).

usage: python3 art/codex/terrain/r1_scale_compare.py --out art/codex/terrain/review/r1/scale-compare.png
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paintin import axial_to_pixel, ring_step  # noqa: E402
from paintin_pin import PinConfig, pin_tile  # noqa: E402
from paintin_sheet import load_base, load_sidecar, output_alpha, repo_path  # noqa: E402
from r1_terrain_measure import lum, texture  # noqa: E402

from paintin_geom import tile_distance  # noqa: E402

TERRAIN = REPO / "art" / "codex" / "terrain"
PLAINS = TERRAIN / "candidates" / "r1" / "plains"
CONCEPT = REPO / "docs" / "concept-art" / "concept1.png"
LAYOUTS = ["3x3", "4x4", "5x5", "6x6"]
# representative cell per layout (a sheet-interior slot)
PICK = {"3x3": 4, "4x4": 5, "5x5": 12, "6x6": 14}
# concept hex centre per class (concept-read.md hex ids) for the side crop
CONCEPT_HEX = {"woods": (4, 2), "hills": (11, 0)}
CW, CH = 141.7, 115.2
BG = (34, 34, 40)
FLOWER = [(0, 0), (1, -1), (1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1)]


def font(size: int) -> ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def flower(centre: np.ndarray, plains: list[np.ndarray]) -> Image.Image:
    """Centre tile with six plains neighbours, 128 px per hex."""
    pts = [axial_to_pixel(q, r, 64.0) for q, r in FLOWER]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y0 = min(xs) - 64, min(ys) - 64
    w, h = int(max(xs) - x0 + 64), int(max(ys) - y0 + 64)
    canvas = Image.new("RGBA", (w, h), BG + (255,))
    for i, (cx, cy) in enumerate(pts):
        tile = centre if i == 0 else plains[(i - 1) % len(plains)]
        canvas.alpha_composite(
            Image.fromarray(tile, "RGBA"), (round(cx - x0 - 64), round(cy - y0 - 64))
        )
    return canvas.convert("RGB")


def concept_crop(cls: str, size: tuple[int, int]) -> Image.Image:
    """Concept area around the class's hex, resampled to 128 px per hex width."""
    k, n = CONCEPT_HEX[cls]
    cx = 27.0 + 0.75 * CW * k
    cy = (184.6 if k % 2 == 1 else 127.0) + CH * n
    sx, sy = 128 / CW, (128 * math.sqrt(3) / 2) / CH
    half_w, half_h = size[0] / 2 / sx, size[1] / 2 / sy
    im = Image.open(CONCEPT).convert("RGB")
    cx = min(max(cx, half_w + 2), im.width - half_w - 2)  # keep the box on the map
    box = (cx - half_w, max(62.0, cy - half_h), cx + half_w, cy + half_h)
    out_w = round((box[2] - box[0]) * sx)
    out_h = round((box[3] - box[1]) * sy)
    crop = im.resize((out_w, out_h), Image.Resampling.LANCZOS, box=box)
    canvas = Image.new("RGB", size, BG)
    canvas.paste(crop, (0, size[1] - out_h))
    return canvas


def pins(work: Path) -> dict[str, dict[str, list[np.ndarray]]]:
    sidecar = load_sidecar(next(work.glob("*.sheet.json")))
    cfg = PinConfig.from_json(sidecar["pin"])
    alpha = output_alpha()
    out: dict[str, list[np.ndarray]] = {"rim": [], "restore": [], "cut": []}
    for cell in sidecar["cells"]:
        cut = np.asarray(
            Image.open(work / "cut" / cell["output"]).convert("RGB"), dtype=np.float64
        )
        base = load_base(repo_path(cell["base"])).tile
        out["cut"].append(cut)
        for mode, c in (
            ("rim", replace(cfg, restore=False)),
            ("restore", replace(cfg, restore=True)),
        ):
            out[mode].append(pin_tile(cut, base, alpha, None, None, c).rgba)
    return {"tiles": out, "sidecar": sidecar}  # type: ignore[dict-item]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    plains = [np.asarray(Image.open(p).convert("RGBA")) for p in sorted(PLAINS.glob("*.png"))]
    rows: list[tuple[str, list[Image.Image]]] = []
    numbers: dict[str, dict[str, float]] = {}
    d = tile_distance()
    core = d > 0.35 * 64 * math.sqrt(3) / 2
    for cls in ("woods", "hills"):
        per_mode: dict[str, list[Image.Image]] = {"rim": [], "restore": []}
        for layout in LAYOUTS:
            work = TERRAIN / cls / f"scale-{layout}"
            res = pins(work)
            tiles = res["tiles"]  # type: ignore[index]
            reg = json.loads((work / "registration.json").read_text())
            report = json.loads((work / "report.json").read_text())
            key = f"{cls} {layout}"
            numbers[key] = {
                "hex_px_on_sheet": res["sidecar"]["layout"]["hex_width"],  # type: ignore[index]
                "max_residual_tile_px": round(reg["max_residual_tile_px"], 2),
                "min_iou": round(reg["min_iou"], 3),
                "gate_pass": sum(t["passed"] for t in report["tiles"].values()),
                "cells": len(report["tiles"]),
            }
            for mode in ("rim", "restore"):
                steps = [ring_step(t) for t in tiles[mode]]
                numbers[key][f"ring_step_{mode}_median"] = round(float(np.median(steps)), 2)
                numbers[key][f"ring_step_{mode}_max"] = round(float(np.max(np.abs(steps))), 2)
            fine = [texture(t[..., :3].astype(float), core, 64)["fine"] for t in tiles["rim"]]
            numbers[key]["core_fine64"] = round(float(np.median(fine)), 2)
            numbers[key]["core_L"] = round(
                float(np.median([np.median(lum(t[..., :3].astype(float))[core]) for t in tiles["rim"]])), 1
            )
            pick = PICK[layout]
            for mode in ("rim", "restore"):
                per_mode[mode].append(flower(tiles[mode][pick], plains))
        size = per_mode["rim"][0].size
        cc = concept_crop(cls, size)
        for mode in ("rim", "restore"):
            rows.append((f"{cls}\n{mode}", [cc, *per_mode[mode]]))
    heads = ["concept (same scale)"] + [
        f"{lay} (sheet hex {numbers[f'woods {lay}']['hex_px_on_sheet']} px)" for lay in LAYOUTS
    ]
    blocks = []
    for scale in (1.0, 0.5):
        cw, chh = (round(s * scale) for s in rows[0][1][0].size)
        pad, lab_w, head_h = 10, 80, 22
        img = Image.new("RGB", (lab_w + len(heads) * (cw + pad), head_h + len(rows) * (chh + pad)), BG)
        dr = ImageDraw.Draw(img)
        for j, hd in enumerate(heads):
            dr.text((lab_w + j * (cw + pad), 4), hd, fill=(230, 230, 230), font=font(12 if scale == 1 else 10))
        for i, (lab, ims) in enumerate(rows):
            y = head_h + i * (chh + pad)
            dr.text((6, y + chh // 2 - 10), lab, fill=(255, 220, 90), font=font(13))
            for j, im in enumerate(ims):
                if scale != 1.0:
                    im = im.resize((cw, chh), Image.Resampling.LANCZOS)
                img.paste(im, (lab_w + j * (cw + pad), y))
        blocks.append((f"{int(128 * scale)} px per hex", img))
    W = max(b.width for _, b in blocks)
    H = sum(b.height + 28 for _, b in blocks)
    out = Image.new("RGB", (W, H), BG)
    y = 0
    dr = ImageDraw.Draw(out)
    for title, b in blocks:
        dr.text((8, y + 6), title, fill=(255, 255, 255), font=font(15))
        out.paste(b, (0, y + 28))
        y += b.height + 28
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.save(args.out)
    print(f"wrote {args.out}")
    for k, v in numbers.items():
        print(k, v)
    if args.json:
        args.json.write_text(json.dumps(numbers, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
