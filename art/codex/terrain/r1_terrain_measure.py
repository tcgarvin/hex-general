"""Measure r1 area-terrain candidate tiles (woods, hills, town, city) against the concept.

Metric definitions follow art/codex/terrain/direction/concept-read.md (L is
0.299R + 0.587G + 0.114B; "shrink s" is the hex scaled to (1 - s) about its
centre, i.e. inset s * 55.4 px at 128 px). Every metric is also computed on
the matching concept hexes, resampled to tile geometry (x 0.903, y 0.962), so
the table compares like with like. (Oriented river/road tiles are measured by
r1_measure.py.)

usage: python3 art/codex/terrain/r1_terrain_measure.py <class> <tile.png ...> [--json out.json]
       class: woods | hills | town | city
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
from paintin_geom import tile_distance  # noqa: E402

CONCEPT = REPO / "docs" / "concept-art" / "concept1.png"
APOTHEM = 64.0 * math.sqrt(3) / 2.0
# concept grid fit (concept-read.md section 0)
CW, CH = 141.7, 115.2
CONCEPT_HEXES = {
    "woods": [(4, 2), (6, 0), (7, 3), (8, 4), (8, 5), (12, 3), (12, 5)],
    "hills_tan": [(1, 0), (2, 1), (1, 1)],
    "hills_green": [(11, 0), (12, 0)],
    "town": [(9, 1)],
    "city": [(3, 4), (2, 5), (1, 4), (2, 4)],
    "plains": [(3, 0)],
}


def lum(a: np.ndarray) -> np.ndarray:
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def hsv(a: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = a / 255.0
    mx, mn = x.max(-1), x.min(-1)
    d = mx - mn + 1e-9
    s = np.where(mx > 0, d / (mx + 1e-9), 0.0)
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    h = (
        np.where(
            mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)
        )
        * 60
    )
    return h, s, mx


def concept_hex(k: int, n: int) -> np.ndarray:
    """A concept hex resampled to a 128 px regular flat-top tile (RGB float)."""
    cx = 27.0 + 0.75 * CW * k
    cy = (184.6 if k % 2 == 1 else 127.0) + CH * n
    im = Image.open(CONCEPT).convert("RGB")
    box = (cx - CW / 2, cy - CH / 2, cx + CW / 2, cy + CH / 2)
    hx = im.resize((128, round(128 * math.sqrt(3) / 2)), Image.Resampling.LANCZOS, box=box)
    out = np.zeros((128, 128, 3))
    y0 = (128 - hx.height) // 2
    out[y0 : y0 + hx.height] = np.asarray(hx, dtype=np.float64)
    return out


def load_tile(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)


def texture(rgb: np.ndarray, mask: np.ndarray, scale: int) -> dict[str, float]:
    im = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    mk = Image.fromarray((mask * 255).astype(np.uint8))
    if scale != 128:
        im = im.resize((scale, scale), Image.Resampling.LANCZOS)
        mk = mk.resize((scale, scale), Image.Resampling.NEAREST)
    a = np.asarray(im, dtype=np.float64)
    m = np.asarray(mk) > 127
    big = np.asarray(im.filter(ImageFilter.GaussianBlur(scale / 16)), dtype=np.float64)
    fine_b = np.asarray(im.filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float64)
    return {
        "fine": float(np.abs(lum(a) - lum(fine_b))[m].mean()),
        "mid": float(np.abs(lum(fine_b) - lum(big))[m].mean()),
    }


def grass_like(rgb: np.ndarray) -> np.ndarray:
    """Open-grass pixels: olive hue, saturated, mid value (calibrated on concept grass)."""
    h, s, _ = hsv(rgb)
    L = lum(rgb)
    return (h > 45) & (h < 80) & (s > 0.42) & (L > 72) & (L < 130)


def measure(cls: str, rgb: np.ndarray) -> dict[str, float]:
    d = tile_distance()
    hexm = d >= 1.0
    L = lum(rgb)
    h, s, _ = hsv(rgb)
    out: dict[str, float] = {"L_hex": float(np.median(L[hexm]))}
    if cls == "woods":
        core = d > 0.35 * APOTHEM
        out["core_L"] = float(np.median(L[core]))
        out["core_p90"] = float(np.percentile(L[core], 90))
        out["core_Lsd"] = float(L[core].std())
        out["core_hue"] = float(np.median(h[core]))
        out["core_S"] = float(np.median(s[core]))
        band = (d >= 0.03 * APOTHEM) & (d < 0.10 * APOTHEM)
        edge = (d >= 0.10 * APOTHEM) & (d < 0.20 * APOTHEM)
        out["band_3_10_L"] = float(np.median(L[band]))
        out["band_10_20_L"] = float(np.median(L[edge]))
        out["fine64_core"] = texture(rgb, core, 64)["fine"]
        out["canopy_share"] = float((L[hexm] < 75).mean())
    elif cls == "hills":
        dark = L[hexm] <= np.percentile(L[hexm], 15)
        px = rgb[hexm][dark]
        out["shade_R"], out["shade_G"], out["shade_B"] = (
            float(v) for v in np.median(px, axis=0)
        )
        out["shade_L"] = float(np.median(L[hexm][dark]))
        rock = (L > 125) & (h > 25) & (h < 58) & (s > 0.15) & (s < 0.55)
        out["rock_share"] = float(rock[hexm].mean())
        out["Lsd"] = float(L[hexm].std())
        out["fine128"] = texture(rgb, hexm, 128)["fine"]
        out["relief_share"] = float((~grass_like(rgb))[hexm].mean())
        out["dark_share"] = float((L[hexm] < 70).mean())
        lw = hexm & (L > 118) & (h < 56)
        out["lightwarm_share"] = float(lw[hexm].mean())
        out["lightwarm_S"] = float(np.median(s[lw])) if lw.any() else float("nan")
        out["fine64"] = texture(rgb, hexm, 64)["fine"]
        out["mid64"] = texture(rgb, hexm, 64)["mid"]
    else:  # town / city
        built = ~grass_like(rgb)
        out["built_share"] = float(built[hexm].mean())
        out["Lsd"] = float(L[hexm].std())
        roofs = (h < 35) & (s > 0.35) & (L > 40) & (L < 110)
        out["roof_share"] = float(roofs[hexm].mean())
        if roofs[hexm].any():
            rr = np.median(rgb[hexm & roofs], axis=0)
            out["roof_R"], out["roof_G"], out["roof_B"] = (float(v) for v in rr)
        out["fine128"] = texture(rgb, hexm, 128)["fine"]
        out["grey_share"] = float(((s < 0.2) & (L > 85))[hexm].mean())
        out["mid64"] = texture(rgb, hexm, 64)["mid"]
        core = d > 0.25 * APOTHEM
        out["core_L"] = float(np.median(L[core]))
        band = (d >= 1.0) & (d < 0.12 * APOTHEM + 1)
        out["outer_built"] = float(built[band].mean())
    return {k: round(v, 3 if "share" in k or "built" in k or k.endswith("_S") else 1) for k, v in out.items()}


def concept_rows(cls: str) -> dict[str, dict[str, float]]:
    keys = {"hills": ["hills_tan", "hills_green"]}.get(cls, [cls])
    rows = {}
    for key in keys:
        for k, n in CONCEPT_HEXES[key]:
            rows[f"concept {key} ({k},{n})"] = measure(cls, concept_hex(k, n))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cls", choices=["woods", "hills", "town", "city"])
    ap.add_argument("tiles", nargs="+", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--no-concept", action="store_true")
    args = ap.parse_args()
    rows = {} if args.no_concept else concept_rows(args.cls)
    for p in args.tiles:
        rows[str(p)] = measure(args.cls, load_tile(p))
    keys = list(next(iter(rows.values())).keys())
    keys += [k for r in rows.values() for k in r if k not in keys]
    width = max(len(k) for k in rows) + 1
    print(" " * width + " ".join(f"{k[:11]:>11}" for k in keys))
    for name, r in rows.items():
        print(f"{name:<{width}}" + " ".join(f"{r.get(k, float('nan')):>11}" for k in keys))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
