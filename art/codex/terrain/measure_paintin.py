"""Measure how well a P5 paint-in output preserved its input sheet
(refs/paintin_sheet.png + .json): magenta background, hexagon outline, and
the ground in the rim band (outer 15% of each hexagon, where no trees were
asked for).

Usage: python3 measure_paintin.py raw/P5a-paintin-forest.png
Prints a JSON summary and writes review/<name>.rimdiff.png (per-pixel
|output - input| luminance, x4, inside the hexagons).
"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
RIM_INSET = 0.85


def polygon_mask(size: int, pts: list[tuple[float, float]]) -> np.ndarray:
    """Boolean mask of a polygon on a square canvas."""
    im = Image.new("L", (size, size), 0)
    ImageDraw.Draw(im).polygon(pts, fill=1)
    return np.asarray(im, dtype=bool)


def is_magenta(a: np.ndarray) -> np.ndarray:
    """Pixels that read as the flat magenta background."""
    return (np.minimum(a[..., 0], a[..., 2]) - a[..., 1]) > 180


def lum(a: np.ndarray) -> np.ndarray:
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def main() -> None:
    src = Path(sys.argv[1])
    meta = json.loads((HERE / "refs" / "paintin_sheet.json").read_text())
    size = meta["canvas"]
    inp = np.asarray(Image.open(HERE / "refs" / "paintin_sheet.png").convert("RGB"), dtype=np.float64)
    out_im = Image.open(src).convert("RGB")
    native = out_im.size
    if native != (size, size):
        out_im = out_im.resize((size, size), Image.LANCZOS)
    out = np.asarray(out_im, dtype=np.float64)

    fg_in = ~is_magenta(inp)
    fg_out = ~is_magenta(out)
    # Best integer shift of the output silhouette onto the input's.
    best = max(
        ((dx, dy) for dx in range(-8, 9) for dy in range(-8, 9)),
        key=lambda d: (fg_in & np.roll(fg_out, (d[1], d[0]), axis=(0, 1))).sum()
        / (fg_in | np.roll(fg_out, (d[1], d[0]), axis=(0, 1))).sum(),
    )
    iou = (fg_in & fg_out).sum() / (fg_in | fg_out).sum()

    hexes = []
    rim_all = np.zeros((size, size), dtype=bool)
    for h in meta["hexes"]:
        cx, cy = h["cx"], h["cy"]
        outer = [tuple(p) for p in h["corners"]]
        inner = [(cx + (x - cx) * RIM_INSET, cy + (y - cy) * RIM_INSET) for x, y in outer]
        full = polygon_mask(size, outer)
        rim = full & ~polygon_mask(size, inner)
        rim_all |= rim
        grown = polygon_mask(size, [(cx + (x - cx) * 1.15, cy + (y - cy) * 1.15) for x, y in outer])
        local_fg = fg_out & grown
        d = np.abs(out[rim] - inp[rim]).mean(axis=1)
        hexes.append(
            {
                "rim_mean_abs_rgb_diff": round(float(d.mean()), 1),
                "rim_frac_diff_gt_30": round(float((d > 30).mean()), 3),
                "rim_lum_in": round(float(lum(inp[rim]).mean()), 1),
                "rim_lum_out": round(float(lum(out[rim]).mean()), 1),
                "hex_silhouette_iou": round(float((full & local_fg).sum() / (full | local_fg).sum()), 3),
            }
        )
    bg_in = ~fg_in
    result = {
        "image": str(src),
        "native_size": list(native),
        "silhouette_iou": round(float(iou), 3),
        "best_shift_px": list(best),
        "magenta_preserved_frac": round(float(is_magenta(out)[bg_in].mean()), 3),
        "rim_mean_abs_rgb_diff": round(float(np.mean([h["rim_mean_abs_rgb_diff"] for h in hexes])), 1),
        "rim_frac_diff_gt_30": round(float(np.mean([h["rim_frac_diff_gt_30"] for h in hexes])), 3),
        "per_hex": hexes,
    }
    review = HERE / "review"
    review.mkdir(exist_ok=True)
    (review / f"{src.stem}.paintin.json").write_text(json.dumps(result, indent=1))
    diff = np.abs(lum(out) - lum(inp)) * 4
    diff[~fg_in] = 0
    Image.fromarray(np.clip(diff, 0, 255).astype(np.uint8)).save(review / f"{src.stem}.rimdiff.png")
    print(json.dumps({k: v for k, v in result.items() if k != "per_hex"}, indent=1))
    for h in hexes:
        print(h)


if __name__ == "__main__":
    main()
