"""Colour statistics for terrain probes: mean luminance (0.299R+0.587G+0.114B)
and median HSV (H in degrees, S and V in 0..1), per image or per box.

Usage:
  python3 measure.py <image.png> [--box l,t,r,b ...]
With no --box the whole image is measured.
"""

import argparse
import colorsys

import numpy as np
from PIL import Image


def stats(rgb: np.ndarray) -> dict[str, float]:
    """Return luminance mean/std and median H, S, V for an (N, 3) uint8 array."""
    f = rgb.astype(np.float64)
    lum = 0.299 * f[:, 0] + 0.587 * f[:, 1] + 0.114 * f[:, 2]
    hsv = np.array([colorsys.rgb_to_hsv(*(p / 255.0)) for p in f[:: max(1, len(f) // 20000)]])
    return {
        "lum_mean": round(float(lum.mean()), 1),
        "lum_std": round(float(lum.std()), 1),
        "h_med_deg": round(float(np.median(hsv[:, 0])) * 360, 1),
        "s_med": round(float(np.median(hsv[:, 1])), 3),
        "v_med": round(float(np.median(hsv[:, 2])), 3),
        "rgb_mean": [round(float(c), 1) for c in f.mean(axis=0)],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--box", action="append", default=[])
    args = ap.parse_args()
    im = np.asarray(Image.open(args.image).convert("RGB"))
    if not args.box:
        print("whole", stats(im.reshape(-1, 3)))
        return
    pooled = []
    for b in args.box:
        l, t, r, bt = (int(v) for v in b.split(","))
        px = im[t:bt, l:r].reshape(-1, 3)
        pooled.append(px)
        print(b, stats(px))
    print("pooled", stats(np.concatenate(pooled)))


if __name__ == "__main__":
    main()
