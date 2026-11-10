"""Measure how well a guided Codex key-art image (experiment P2) registers
against the hex layout guide (refs/hex_guide.json).

Run with scipy available (it is not in the system python):
  uv run --no-project --with scipy --with pillow --with numpy \
      python register.py raw/P2a-guided-keyart.png

Writes review/<name>.overlay.png (guide hex outlines in magenta, river
centreline cyan, road yellow, drawn over the output) and
review/<name>.classes.png (the colour-rule terrain classification), and
prints + saves review/<name>.registration.json.

Classification is a set of hand-tuned colour rules on a 9 px box-blurred
image (calibrated on P2a): water = low-saturation grey-teal, forest = dark
olive, rock = light tan, settlement = low-saturation brown/grey. It is a
probe-grade measure, good to a few pixels on mass centroids, not a segmenter.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hex_guide  # noqa: E402

CLASS_COLOURS = {
    "water": (40, 110, 230),
    "forest": (20, 120, 40),
    "rock": (230, 200, 120),
    "settlement": (220, 50, 50),
}
TERRAIN_CLASS = {"forest": "forest", "hills": "rock", "town": "settlement", "city": "settlement"}


def classify(rgb: np.ndarray) -> dict[str, np.ndarray]:
    """Boolean masks per class from a blurred float RGB array (H, W, 3) in 0..255."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = rgb.max(axis=-1)
    mn = rgb.min(axis=-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    greenish = (g >= r) & (b >= r - 6)  # hue >= ~90 deg: teal/grey-green water
    warm = (r > g) & (g > b)  # hue 0-60: rock, roofs, roads
    masks = {
        "water": greenish & (sat < 0.3) & (lum > 55),
        "forest": (lum < 74) & (sat > 0.35) & (g >= r - 2),
        "rock": warm & (lum > 108) & (sat > 0.18) & (sat < 0.5),
        "settlement": warm & (lum > 65) & (lum <= 118) & (sat < 0.36),
    }
    cleaned = {}
    for name, m in masks.items():
        m = ndimage.binary_opening(m, iterations=2)
        cleaned[name] = ndimage.binary_closing(m, iterations=3)
    return cleaned


def hex_mask(shape: tuple[int, int], corners: list[list[float]], shrink: float = 1.0) -> np.ndarray:
    """Boolean mask of a hex polygon, optionally shrunk about its centre."""
    cx = sum(p[0] for p in corners) / 6
    cy = sum(p[1] for p in corners) / 6
    pts = [(cx + (x - cx) * shrink, cy + (y - cy) * shrink) for x, y in corners]
    im = Image.new("L", (shape[1], shape[0]), 0)
    ImageDraw.Draw(im).polygon(pts, fill=1)
    return np.asarray(im, dtype=bool)


def river_profile(water: np.ndarray, line: list[list[float]], half_span: int = 90) -> list[dict]:
    """Signed lateral offset and width of the water band along a guide centreline.

    At each interior sample, walk the normal +-half_span px, take the water run
    nearest the centreline point; offset = run midpoint (signed, px), width = run length.
    """
    out = []
    h, w = water.shape
    for i in range(1, len(line) - 1):
        (x0, y0), (x1, y1) = line[i - 1], line[i + 1]
        tx, ty = x1 - x0, y1 - y0
        norm = math.hypot(tx, ty)
        nx, ny = -ty / norm, tx / norm
        px, py = line[i]
        ts = np.arange(-half_span, half_span + 1)
        xs = np.clip(np.round(px + nx * ts).astype(int), 0, w - 1)
        ys = np.clip(np.round(py + ny * ts).astype(int), 0, h - 1)
        on = water[ys, xs]
        if not on.any():
            out.append({"x": px, "y": py, "found": False})
            continue
        labels, n = ndimage.label(on)
        best = min(
            range(1, n + 1),
            key=lambda k: np.abs(ts[labels == k]).min(),
        )
        run = ts[labels == best]
        out.append(
            {
                "x": px,
                "y": py,
                "found": True,
                "offset": float((run.min() + run.max()) / 2),
                "width": int(run.max() - run.min() + 1),
            }
        )
    return out


def main() -> None:
    src = Path(sys.argv[1])
    layout = hex_guide.load_layout()
    canvas = layout["canvas"][0]
    im = Image.open(src).convert("RGB")
    if im.size != (canvas, canvas):
        im = im.resize((canvas, canvas), Image.LANCZOS)
    blurred = np.asarray(im.filter(ImageFilter.BoxBlur(4)), dtype=np.float64)
    masks = classify(blurred)
    shape = blurred.shape[:2]
    # Dirt roads share the rock colour rule; drop a 24 px band around the
    # guide's road so rock counts measure the hills only.
    road_band = Image.new("L", (canvas, canvas), 0)
    for line in layout["road"]:
        ImageDraw.Draw(road_band).line([tuple(p) for p in line], fill=1, width=24)
    masks["rock"] &= ~np.asarray(road_band, dtype=bool)
    hex_w = layout["hex_width"]

    review = HERE / "review"
    review.mkdir(exist_ok=True)
    stem = src.stem

    # Per-hex class fractions (inner 85% of each core hex).
    core = [hx for hx in layout["hexes"] if hx["core"]]
    per_hex = []
    for hx in core:
        m = hex_mask(shape, hx["corners"], 0.85)
        fr = {k: round(float(v[m].mean()), 3) for k, v in masks.items()}
        per_hex.append({"col": hx["col"], "row": hx["row"], "terrain": hx["terrain"], **fr})

    # Feature-class containment: share of each class's pixels (in the core
    # grid area) that lie inside hexes of the matching terrain.
    core_area = np.zeros(shape, dtype=bool)
    hex_masks = {}
    for hx in core:
        hm = hex_mask(shape, hx["corners"])
        hex_masks[(hx["col"], hx["row"])] = hm
        core_area |= hm
    containment = {}
    for terrain, cls in TERRAIN_CLASS.items():
        target = np.zeros(shape, dtype=bool)
        for hx in core:
            if TERRAIN_CLASS.get(hx["terrain"]) == cls:
                target |= hex_masks[(hx["col"], hx["row"])]
        pix = masks[cls] & core_area
        containment[cls] = {
            "class_px_in_matching_hexes": round(float((pix & target).sum() / max(pix.sum(), 1)), 3),
            "matching_hex_area_covered": round(float((pix & target).sum() / target.sum()), 3),
        }

    # Forest masses: forest pixels in a 1.25x-enlarged hex around each forest
    # hex, excluding the other forest hexes (adjacent masses touch).
    forest_hexes = [hx for hx in core if hx["terrain"] == "forest"]
    forests = []
    for hx in forest_hexes:
        hm = hex_masks[(hx["col"], hx["row"])]
        region = hex_mask(shape, hx["corners"], 1.25)
        for other in forest_hexes:
            if other is not hx:
                region &= ~hex_masks[(other["col"], other["row"])]
        comp = masks["forest"] & region
        cy, cx = ndimage.center_of_mass(comp)
        dx, dy = cx - hx["cx"], cy - hx["cy"]
        inter = (comp & hm).sum()
        forests.append(
            {
                "col": hx["col"],
                "row": hx["row"],
                "centroid_dx": round(dx, 1),
                "centroid_dy": round(dy, 1),
                "offset_px": round(math.hypot(dx, dy), 1),
                "offset_frac_hex_width": round(math.hypot(dx, dy) / hex_w, 3),
                "iou_with_hex": round(float(inter / (comp | hm).sum()), 3),
                "spill_frac": round(float(1 - inter / comp.sum()), 3),
            }
        )

    # River band along the guide centrelines (skip points within 45 px of the
    # fork centre, where the normals cross both branches).
    fork = next(h for h in layout["hexes"] if (h["col"], h["row"]) == (3, 2))
    samples = []
    for line in layout["river"]:
        for s in river_profile(masks["water"], line):
            if math.hypot(s["x"] - fork["cx"], s["y"] - fork["cy"]) > 45:
                samples.append(s)
    found = [s for s in samples if s["found"]]
    offs = np.abs([s["offset"] for s in found])
    widths = np.array([s["width"] for s in found])
    river = {
        "samples": len(samples),
        "found": len(found),
        "abs_offset_median_px": round(float(np.median(offs)), 1),
        "abs_offset_p90_px": round(float(np.percentile(offs, 90)), 1),
        "abs_offset_max_px": round(float(offs.max()), 1),
        "abs_offset_median_frac_hex_width": round(float(np.median(offs)) / hex_w, 3),
        "width_median_px": int(np.median(widths)),
        "guide_width_px": layout["river_width"],
    }

    result = {
        "image": str(src),
        "containment": containment,
        "forests": forests,
        "river": river,
        "per_hex": per_hex,
    }
    (review / f"{stem}.registration.json").write_text(json.dumps(result, indent=1))

    overlay = im.copy()
    hex_guide.draw_outlines(overlay, (255, 0, 255), 2)
    d = ImageDraw.Draw(overlay)
    for line in layout["river"]:
        d.line([tuple(p) for p in line], fill=(0, 255, 255), width=2)
    for line in layout["road"]:
        d.line([tuple(p) for p in line], fill=(255, 255, 0), width=1)
    overlay.save(review / f"{stem}.overlay.png")

    cls_im = np.full(shape + (3,), 235, dtype=np.uint8)
    for name, m in masks.items():
        cls_im[m] = CLASS_COLOURS[name]
    cls_pil = Image.fromarray(cls_im)
    hex_guide.draw_outlines(cls_pil, (0, 0, 0), 1)
    cls_pil.save(review / f"{stem}.classes.png")

    print(json.dumps({k: result[k] for k in ("containment", "forests", "river")}, indent=1))


if __name__ == "__main__":
    main()
