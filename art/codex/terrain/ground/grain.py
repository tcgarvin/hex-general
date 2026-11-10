"""Grain and colour metrics for ground (plains) candidates.

Separates "felt" (smooth, low pixel-level detail) from "grass" (fine streaky
grain) with band-pass energy on luminance, computed with normalized Gaussian
convolution so a hex mask (or any region mask) can be honoured:

  lum_mean, lum_std     luminance (0.299R+0.587G+0.114B) over the region
  H, S, V               median HSV (H in degrees)
  hp1                   std of (lum - G_1.0 * lum): pixel-level grain
  bp2                   std of (G_1.0 - G_3.0): stroke-scale texture
  lf8                   std of G_8.0 * lum: low-frequency blotchiness
  coh                   mean structure-tensor coherence of the hp1 band (0 = isotropic
                        speckle, 1 = all strokes parallel): streakiness

Region: whole image, `--box l,t,r,b`, or (for RGBA tiles) alpha > 0 eroded by
`EDGE_TRIM` px so the blur never reads outside the hex.

Usage:
  python3 grain.py <image> [--box l,t,r,b ...] [--json]
"""

from __future__ import annotations

import argparse
import colorsys
import json

import numpy as np
from PIL import Image

EDGE_TRIM = 4


def gauss_kernel(sigma: float) -> np.ndarray:
    radius = max(1, int(round(3 * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum()


def blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur with edge replication."""
    k = gauss_kernel(sigma)
    r = len(k) // 2
    p = np.pad(a, ((0, 0), (r, r)), mode="edge")
    a = sum(k[i] * p[:, i : i + a.shape[1]] for i in range(len(k)))
    p = np.pad(a, ((r, r), (0, 0)), mode="edge")
    return sum(k[i] * p[i : i + a.shape[0], :] for i in range(len(k)))


def nblur(a: np.ndarray, m: np.ndarray, sigma: float) -> np.ndarray:
    """Mask-normalized blur: only pixels with m=1 contribute."""
    return blur(a * m, sigma) / np.maximum(blur(m, sigma), 1e-6)


def luminance(rgb: np.ndarray) -> np.ndarray:
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def erode(mask: np.ndarray, n: int) -> np.ndarray:
    for _ in range(n):
        mask = mask & np.roll(mask, 1, 0) & np.roll(mask, -1, 0) & np.roll(mask, 1, 1) & np.roll(mask, -1, 1)
    return mask


def metrics(rgb: np.ndarray, region: np.ndarray, support: np.ndarray) -> dict[str, float]:
    """Metrics of `rgb` (HxWx3 float) over boolean `region`; blurs use `support` pixels only."""
    lum = luminance(rgb)
    m = support.astype(np.float64)
    g1 = nblur(lum, m, 1.0)
    g3 = nblur(lum, m, 3.0)
    g8 = nblur(lum, m, 8.0)
    hp = lum - g1
    gy, gx = np.gradient(hp)
    jxx, jyy, jxy = nblur(gx * gx, m, 2.0), nblur(gy * gy, m, 2.0), nblur(gx * gy, m, 2.0)
    coh = np.sqrt((jxx - jyy) ** 2 + 4 * jxy**2) / np.maximum(jxx + jyy, 1e-6)
    px = rgb[region]
    sub = px[:: max(1, len(px) // 20000)] / 255.0
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in sub])
    return {
        "lum_mean": round(float(lum[region].mean()), 1),
        "lum_std": round(float(lum[region].std()), 1),
        "H": round(float(np.median(hsv[:, 0])) * 360, 1),
        "S": round(float(np.median(hsv[:, 1])), 3),
        "V": round(float(np.median(hsv[:, 2])), 3),
        "hp1": round(float(hp[region].std()), 2),
        "bp2": round(float((g1 - g3)[region].std()), 2),
        "lf8": round(float(g8[region].std()), 2),
        "coh": round(float(coh[region].mean()), 3),
    }


def image_metrics(path: str, box: tuple[int, int, int, int] | None = None) -> dict[str, float]:
    """Metrics for a file: whole image / box, or the eroded alpha region of an RGBA tile."""
    img = Image.open(path)
    rgb = np.asarray(img.convert("RGB"), dtype=np.float64)
    if box is not None:
        l, t, r, b = box
        rgb = rgb[t:b, l:r]
        full = np.ones(rgb.shape[:2], bool)
        return metrics(rgb, full, full)
    if img.mode == "RGBA":
        alpha = np.asarray(img)[..., 3] > 127
        return metrics(rgb, erode(alpha, EDGE_TRIM), alpha)
    full = np.ones(rgb.shape[:2], bool)
    return metrics(rgb, full, full)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--box", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    boxes = [tuple(int(v) for v in b.split(",")) for b in args.box] or [None]
    for b in boxes:
        m = image_metrics(args.image, b)  # type: ignore[arg-type]
        print(json.dumps(m) if args.json else f"{b}: {m}")


if __name__ == "__main__":
    main()
