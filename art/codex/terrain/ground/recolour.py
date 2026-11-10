"""Gentle global colour correction of a ground source toward the concept grass anchors.

Per pixel in HSV: H += hue_shift_deg, S *= sat_scale, V *= val_scale (clipped). The
same three numbers apply to every pixel, so grain contrast is kept (scaled by val_scale)
and nothing local changes. `--auto` derives the three numbers from the source's median
H/S/V and the targets, then multiplies each correction by `--strength` (1.0 = land on
the target medians).

Usage:
  python3 recolour.py <src.png> --out <dst.png> --auto [--strength 1.0]
  python3 recolour.py <src.png> --out <dst.png> --hue-shift 2.0 --sat-scale 0.86 --val-scale 1.04
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts" / "tile-foundry"))
from cut import rgb_to_hsv  # noqa: E402

# Concept clean open grass, median of 7 clean 64x64 boxes (scale_study.py).
TARGET_H, TARGET_S, TARGET_V = 61.1, 0.558, 0.408


def hsv_to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Vectorized inverse of cut.rgb_to_hsv: H in degrees, S/V in 0..1 -> float RGB 0..1."""
    h6 = (h % 360.0) / 60.0
    i = np.floor(h6).astype(int) % 6
    f = h6 - np.floor(h6)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=-1)


def auto_params(rgb: np.ndarray, strength: float) -> dict[str, float]:
    h, s, v = rgb_to_hsv(rgb / 255.0)
    hm, sm, vm = float(np.median(h)), float(np.median(s)), float(np.median(v))
    return {
        "hue_shift_deg": round((TARGET_H - hm) * strength, 2),
        "sat_scale": round(1 + (TARGET_S / sm - 1) * strength, 4),
        "val_scale": round(1 + (TARGET_V / vm - 1) * strength, 4),
    }


def apply(rgb: np.ndarray, params: dict[str, float]) -> np.ndarray:
    h, s, v = rgb_to_hsv(rgb / 255.0)
    h = h + params["hue_shift_deg"]
    s = np.clip(s * params["sat_scale"], 0, 1)
    v = np.clip(v * params["val_scale"], 0, 1)
    return np.clip(np.round(hsv_to_rgb(h, s, v) * 255), 0, 255).astype(np.uint8)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", required=True)
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--hue-shift", type=float, default=0.0)
    ap.add_argument("--sat-scale", type=float, default=1.0)
    ap.add_argument("--val-scale", type=float, default=1.0)
    args = ap.parse_args()
    rgb = np.asarray(Image.open(args.src).convert("RGB"), dtype=np.float64)
    if args.auto:
        params = auto_params(rgb, args.strength)
    else:
        params = {"hue_shift_deg": args.hue_shift, "sat_scale": args.sat_scale, "val_scale": args.val_scale}
    Image.fromarray(apply(rgb, params)).save(args.out)
    print(json.dumps(params))


if __name__ == "__main__":
    main()
