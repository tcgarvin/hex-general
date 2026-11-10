"""Recorded, feature-only colour grading for r2 paint-in tiles.

The grade is applied to a pinned tile (`<work>/tiles/<cell>.png`) with weight
f = restore matte x pin weight, recomputed from `<work>/cut/<cell>.png`, the
cell's base and the job's pin settings exactly as `paintin_pin.pin_tile` does.
f is 0 wherever the tile shows restored ground and on the pinned rim, so the
ground and the rim stay byte-identical to the ungraded tile (rim_exact holds).

Operations (all optional, applied in this order):
  --value-scale V        multiply RGB of feature pixels by V (city: darken core)
  --shade-pct P --shade-mult M --shade-warm r,g,b
                         feature pixels darker than the P-th percentile of
                         feature L (ramped over +/-8 L) are multiplied by M and
                         by the per-channel warm factors (tan hills: deeper
                         umber east faces)
  --lightwarm-s S        light-warm feature pixels (L > 118, hue < 56) have
                         their HSV saturation capped toward S (keeps V)
  --sat-scale K          multiply HSV saturation of feature pixels by K

usage: python3 art/codex/terrain/r2_grade.py --work <dir> --cell <name> --out <png> [ops]
Writes <out> and <out>.grade.json (parameters + source hashes).
"""

from __future__ import annotations

import argparse
import colorsys  # noqa: F401  (documented reference for the HSV maths below)
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
from paintin_pin import PinConfig, ground_restore, pin_target  # noqa: E402
from paintin_sheet import load_base, load_sidecar, repo_path  # noqa: E402


def lum(a: np.ndarray) -> np.ndarray:
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def rgb_to_hsv(a: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = a / 255.0
    mx, mn = x.max(-1), x.min(-1)
    d = mx - mn
    s = np.where(mx > 0, d / np.maximum(mx, 1e-9), 0.0)
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    dd = np.maximum(d, 1e-9)
    h = np.where(mx == r, ((g - b) / dd) % 6, np.where(mx == g, (b - r) / dd + 2, (r - g) / dd + 4))
    h = np.where(d > 0, h * 60.0, 0.0)
    return h, s, mx


def hsv_to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    c = v * s
    hp = (h / 60.0) % 6
    x = c * (1 - np.abs(hp % 2 - 1))
    z = np.zeros_like(h)
    idx = np.floor(hp).astype(int)
    r = np.choose(idx, [c, x, z, z, x, c])
    g = np.choose(idx, [x, c, c, x, z, z])
    b = np.choose(idx, [z, z, x, c, c, x])
    m = v - c
    return np.stack([r + m, g + m, b + m], axis=-1) * 255.0


def feature_weight(work: Path, cell: str) -> tuple[np.ndarray, dict]:
    sidecar = load_sidecar(next(work.glob("*.sheet.json")))
    entry = next(c for c in sidecar["cells"] if c["name"] == cell)
    cfg = PinConfig.from_json(sidecar["pin"])
    base = load_base(repo_path(entry["base"])).tile
    cut = np.asarray(Image.open(work / "cut" / entry["output"]).convert("RGB"), dtype=np.float64)
    _, weight = pin_target(base, None, None, cfg)
    matte = ground_restore(cut, base, cfg)[1] if cfg.restore else np.ones(weight.shape)
    return matte * weight, entry


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--cell", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--value-scale", type=float)
    ap.add_argument("--shade-pct", type=float)
    ap.add_argument("--shade-mult", type=float, default=0.8)
    ap.add_argument("--shade-warm", default="1.0,0.97,0.92")
    ap.add_argument("--lightwarm-s", type=float)
    ap.add_argument("--sat-scale", type=float)
    args = ap.parse_args()
    src = args.work / "tiles" / f"{args.cell}.png"
    tile = np.asarray(Image.open(src).convert("RGBA"))
    rgb = tile[..., :3].astype(np.float64)
    f, _ = feature_weight(args.work, args.cell)
    out = rgb.copy()
    feat = f > 0.5
    if args.value_scale is not None:
        out = out * args.value_scale
    if args.shade_pct is not None:
        L = lum(out)
        thr = float(np.percentile(L[feat], args.shade_pct))
        t = np.clip((thr + 8.0 - L) / 16.0, 0.0, 1.0)[..., None]
        warm = np.array([float(v) for v in args.shade_warm.split(",")])
        out = out * (1 - t) + out * args.shade_mult * warm * t
    if args.lightwarm_s is not None or args.sat_scale is not None:
        h, s, v = rgb_to_hsv(np.clip(out, 0, 255))
        L = lum(out)
        if args.sat_scale is not None:
            s = s * args.sat_scale
        if args.lightwarm_s is not None:
            lw = (L > 118) & (h < 56) & (s > args.lightwarm_s)
            s = np.where(lw, args.lightwarm_s + (s - args.lightwarm_s) * 0.25, s)
        out = hsv_to_rgb(h, s, v)
    final = rgb * (1 - f[..., None]) + out * f[..., None]
    res = tile.copy()
    res[..., :3] = np.clip(np.round(final), 0, 255).astype(np.uint8)
    rim = f == 0
    assert (res[rim] == tile[rim]).all(), "grade leaked outside the feature"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(res, "RGBA").save(args.out)
    record = {
        "tool": "art/codex/terrain/r2_grade.py",
        "source": str(src),
        "source_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        "params": {k: v for k, v in vars(args).items() if k not in ("work", "cell", "out") and v is not None},
        "out_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
    }
    Path(f"{args.out}.grade.json").write_text(json.dumps(record, indent=1) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
