"""Round-2 site measurements: value ladder, footprint, grain, base perimeter inset.

Tile metrics (any 128 px hex tile, sites or the shipped town):
  feat%    share of the hex (10% inset) that is not undisturbed ground: pixels
           leaving the ground hue/saturation band or more than 14 L from the
           ground's L 97 (after a 1.5 px blur). Same definition as the
           art-director's sites1 critique, so the numbers compare.
  span     3rd-97th percentile x-extent of feature pixels, % of hex width
  featL    median L of feature pixels; p99L; bright% = feature pixels with L > 150
  fine64   fine grain energy at 64 px over the hex

Base perimeter (`perimeter`): every member's ground-restore matte is placed in
the cluster frame; for samples every 2 px along the footprint's OUTER outline,
the distance inward to the nearest painted pixel. A bank tracing the outline
gives a flat profile (low spread); a straight-sided fence across a lobed
footprint gives a varying one.

usage: python3 art/codex/terrain/sites2_measure.py tiles <png> [...]
       python3 art/codex/terrain/sites2_measure.py perimeter <stage_dir> [...]
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
from paintin_cluster import member_offset_px, outer_segments  # noqa: E402
from paintin_pin import PinConfig, ground_restore  # noqa: E402

GROUND_L = 97.0


def lum(a: np.ndarray) -> np.ndarray:
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def hsv(a: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = a / 255.0
    mx, mn = x.max(-1), x.min(-1)
    d = mx - mn + 1e-9
    s = np.where(mx > 0, d / (mx + 1e-9), 0)
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    return h, s, mx


def hex_mask(alpha: np.ndarray, shrink: float = 0.1) -> np.ndarray:
    h, w = alpha.shape
    cx, cy = (w - 1) / 2, (h - 1) / 2
    a = w / 2 * (1 - shrink)
    b = a * 0.866
    ys, xs = np.mgrid[0:h, 0:w]
    dx, dy = np.abs(xs - cx), np.abs(ys - cy)
    return (dy <= b) & (dx <= a - (a / 2) * (dy / b)) & (alpha > 250)


def fine64(rgb: np.ndarray, mask: np.ndarray) -> float:
    im = Image.fromarray(rgb.astype(np.uint8)).resize((64, 64), Image.Resampling.LANCZOS)
    m = np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).resize((64, 64), Image.Resampling.NEAREST)) > 127
    a = np.asarray(im, dtype=float)
    lb = lum(np.asarray(im.filter(ImageFilter.GaussianBlur(1.5)), dtype=float))
    return float(np.abs(lum(a) - lb)[m].mean())


def tile_metrics(path: Path) -> dict[str, float]:
    a = np.asarray(Image.open(path).convert("RGBA"), dtype=float)
    rgb, alpha = a[..., :3], a[..., 3]
    m = hex_mask(alpha, 0.0)
    blur = np.asarray(Image.fromarray(rgb.astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5)), dtype=float)
    h, s, _ = hsv(blur)
    ground = (h > 48) & (h < 80) & (s > 0.45) & (np.abs(lum(blur) - GROUND_L) < 14)
    f = m & ~ground
    L = lum(rgb[f])
    xs = np.where(f)[1]
    return {
        "feat_pct": round(100 * f.sum() / m.sum(), 1),
        "span": round(float(np.percentile(xs, 97) - np.percentile(xs, 3)) / 128 * 100),
        "featL": round(float(np.median(L)), 1),
        "p99L": round(float(np.percentile(L, 99)), 1),
        "bright_pct": round(float((L > 150).mean() * 100), 1),
        "fine64": round(fine64(rgb, hex_mask(alpha, 0.1)), 2),
    }


def perimeter_inset(stage_dir: Path) -> dict[str, float]:
    """Distance (tile px) from the outer outline inward to the nearest painted pixel."""
    pos = json.loads((stage_dir / "positions.json").read_text())
    src = REPO / pos["source"]
    work, cluster = src.parents[1], src.name
    members = [tuple(v) for v in pos["positions"].values()]
    r = 64.0
    offs = {m: member_offset_px(m[0], m[1], r) for m in members}
    x0 = min(o[0] for o in offs.values()) - 64
    y0 = min(o[1] for o in offs.values()) - 64
    w = math.ceil(max(o[0] for o in offs.values()) + 64 - x0)
    h = math.ceil(max(o[1] for o in offs.values()) + 64 - y0)
    painted = np.zeros((h, w), dtype=bool)
    cfg = PinConfig.from_json({"restore": True})
    for (q, rr), (ox, oy) in offs.items():
        base = np.asarray(Image.open(work / "bases" / cluster / f"q{q}_r{rr}.png").convert("RGB"), dtype=float)
        cut = np.asarray(Image.open(work / "cut" / cluster / f"q{q}_r{rr}.png").convert("RGB"), dtype=float)
        matte = ground_restore(cut, base, cfg)[1] > 0.5
        px, py = round(ox - x0 - 64), round(oy - y0 - 64)
        inside = hex_mask(np.full((128, 128), 255.0), 0.0)
        painted[py : py + 128, px : px + 128] |= matte & inside
    ys, xs = np.nonzero(painted)
    pts = np.stack([xs, ys], axis=-1).astype(float)
    samples = []
    for sx0, sy0, sx1, sy1 in outer_segments(members, (0, 0), (-x0, -y0), r):
        n = max(1, round(math.hypot(sx1 - sx0, sy1 - sy0) / 2))
        for t in np.arange(n) / n:
            samples.append((sx0 + t * (sx1 - sx0), sy0 + t * (sy1 - sy0)))
    d = np.array([np.sqrt(((pts - s) ** 2).sum(-1)).min() for s in samples])
    med = float(np.median(d))
    return {
        "min": round(float(d.min()), 1),
        "p10": round(float(np.percentile(d, 10)), 1),
        "median": round(med, 1),
        "p90": round(float(np.percentile(d, 90)), 1),
        "max": round(float(d.max()), 1),
        "share_within_3px_of_median": round(float((np.abs(d - med) <= 3).mean()), 2),
    }


def main() -> int:
    mode, *paths = sys.argv[1:]
    fn = tile_metrics if mode == "tiles" else perimeter_inset
    for p in paths:
        print(p, json.dumps(fn(Path(p))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
