"""Sites round 4 oil tank repaint: tank-area composite and tank luminance.

The s4-tanks-* sheets ask Codex to repaint only the storage tanks (and remove their
fence) on the already-picked oil tiles. This tool keeps everything else byte-stable:

  region     the tank compound of an ORIGINAL pick: pale, low-saturation tank-top
             pixels (L > 125, S < 0.35; round-ish components of >= 30 px with mean
             L >= 150, which drops steam and pump-house roofs), their bounding boxes
             merged and grown by `--grow` px (covers the fence and shadows), then
             feathered; zero within `--rim` px of the hex boundary.
  composite  out = original outside the region, the paint-in tile inside it (alpha
             from the original). Writes <out>.tanks.json: region size, source hashes
             and how many original non-ground pixels outside the tank tops changed.
             Optional `--lift`: mid-tone pixels of the repaint (L ramping in over
             75-95, so black mud stays put) are scaled by 1 + lift and L above
             `--compress-above` is compressed by `--compress-k` (recorded).
  measure    L (0.299R + 0.587G + 0.114B): over the original's tank-top pixels,
             over tank-like pixels of the given tile (region, L > 70, S < 0.42;
             approximate, it also catches track and bund earth) and over the region:
             mean, p90, p99.

usage: python3 art/codex/terrain/sites4_oilfac_tanks.py composite --orig <png> --new <png> --out <png>
       python3 art/codex/terrain/sites4_oilfac_tanks.py measure --orig <png> <tile.png> [...]
       python3 art/codex/terrain/sites4_oilfac_tanks.py maskpng --orig <png> --out <png>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts" / "tile-foundry"))
from paintin_geom import tile_distance  # noqa: E402

TOP_L = 125.0
TOP_S = 0.35
MIN_AREA = 30
TOP_MEAN_L = 150.0


def load(path: Path) -> tuple[np.ndarray, np.ndarray]:
    im = Image.open(path).convert("RGBA")
    a = np.asarray(im, dtype=np.float64)
    return a[..., :3], a[..., 3]


def lum(rgb: np.ndarray) -> np.ndarray:
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def sat(rgb: np.ndarray) -> np.ndarray:
    mx, mn = rgb.max(-1), rgb.min(-1)
    return np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0.0)


def components(mask: np.ndarray) -> list[np.ndarray]:
    """4-connected components as boolean masks (small tiles, plain flood fill)."""
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    h, w = mask.shape
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        comp = np.zeros_like(mask, dtype=bool)
        stack = [(y0, x0)]
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            comp[y, x] = True
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        out.append(comp)
    return out


def tank_tops(
    rgb: np.ndarray,
    alpha: np.ndarray,
    min_area: int = MIN_AREA,
    min_ratio: float = 0.6,
    top_mean: float = TOP_MEAN_L,
) -> np.ndarray:
    raw = (lum(rgb) > TOP_L) & (sat(rgb) < TOP_S) & (alpha > 250)
    tops = np.zeros_like(raw)
    for comp in components(raw):
        ys, xs = np.nonzero(comp)
        h, w = int(np.ptp(ys)) + 1, int(np.ptp(xs)) + 1
        roundish = min_ratio <= h / w <= 1.0 / min_ratio and comp.sum() >= 0.55 * h * w
        bright = float(lum(rgb)[comp].mean()) >= top_mean
        if comp.sum() >= min_area and roundish and bright:
            tops |= comp
    if not tops.any():
        raise SystemExit("no tank-top pixels found in the original")
    return tops


def region(
    orig: Path,
    grow: int,
    rim: float,
    feather: float,
    detect: dict[str, float] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """(feathered region weight 0..1, tank-top mask) for an original pick."""
    rgb, alpha = load(orig)
    tops = tank_tops(rgb, alpha, **(detect or {}))
    ys, xs = np.nonzero(tops)
    box = np.zeros_like(tops, dtype=np.uint8)
    box[max(ys.min() - grow, 0) : ys.max() + grow + 1, max(xs.min() - grow, 0) : xs.max() + grow + 1] = 255
    soft = Image.fromarray(box).filter(ImageFilter.GaussianBlur(feather))
    weight = np.asarray(soft, dtype=np.float64) / 255.0
    inside = tile_distance(rgb.shape[0], float(rgb.shape[0]))
    weight = np.where(inside >= rim, weight, 0.0)
    return weight, tops


def detect(args: argparse.Namespace) -> dict[str, float]:
    return {"min_area": args.min_area, "min_ratio": args.min_ratio, "top_mean": args.top_mean}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cmd_composite(args: argparse.Namespace) -> int:
    weight, tops = region(args.orig, args.grow, args.rim, args.feather, detect(args))
    o_rgb, o_alpha = load(args.orig)
    n_rgb, _ = load(args.new)
    if args.lift > 0:
        n_rgb = lift_tanks(n_rgb, args.lift, args.compress_above, args.compress_k)
    out = o_rgb * (1.0 - weight[..., None]) + n_rgb * weight[..., None]
    rgba = np.dstack([np.clip(np.rint(out), 0, 255), o_alpha]).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(args.out)
    changed = np.abs(rgba[..., :3].astype(int) - o_rgb.astype(int)).max(-1) > 0
    record = {
        "tool": "art/codex/terrain/sites4_oilfac_tanks.py",
        "orig": str(args.orig),
        "orig_sha256": sha(args.orig),
        "new": str(args.new),
        "new_sha256": sha(args.new),
        "grow": args.grow,
        "rim": args.rim,
        "feather": args.feather,
        "detect": detect(args),
        "lift": args.lift,
        "compress_above": args.compress_above,
        "compress_k": args.compress_k,
        "region_px": int((weight > 0).sum()),
        "changed_px": int(changed.sum()),
        "changed_outside_region_px": int((changed & (weight == 0)).sum()),
        "out_sha256": sha(args.out),
    }
    Path(f"{args.out}.tanks.json").write_text(json.dumps(record, indent=1) + "\n")
    print(f"wrote {args.out} (region {record['region_px']} px)")
    return 0


def smoothstep(lo: float, hi: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def lift_tanks(rgb: np.ndarray, lift: float, above: float, k: float) -> np.ndarray:
    """Brighten mid-tone pixels (L ramps in over 75-95, so the black mud stays) by
    1 + lift, then compress L above `above` by k (same RGB ratio)."""
    L = lum(rgb)
    scaled = rgb * (1.0 + lift * smoothstep(75.0, 95.0, L))[..., None]
    L2 = lum(scaled)
    target = np.where(L2 > above, above + k * (L2 - above), L2)
    return scaled * (target / np.maximum(L2, 1e-9))[..., None]


def tank_like(rgb: np.ndarray, weight: np.ndarray) -> np.ndarray:
    """Approximate tank pixels of a repainted tile: inside the region, L > 70, S < 0.42."""
    return (weight > 0.5) & (lum(rgb) > 70.0) & (sat(rgb) < 0.42)


def stats(values: np.ndarray) -> dict[str, float]:
    return {
        "mean": round(float(values.mean()), 1),
        "p90": round(float(np.percentile(values, 90)), 1),
        "p99": round(float(np.percentile(values, 99)), 1),
    }


def cmd_measure(args: argparse.Namespace) -> int:
    weight, tops = region(args.orig, args.grow, args.rim, args.feather, detect(args))
    for tile in args.tiles:
        rgb, _ = load(tile)
        L = lum(rgb)
        rec = {
            "orig_tops_px": stats(L[tops]),
            "tank_like": stats(L[tank_like(rgb, weight)]),
            "region": stats(L[weight > 0.5]),
        }
        print(f"{tile} {json.dumps(rec)}")
    return 0


def cmd_maskpng(args: argparse.Namespace) -> int:
    weight, tops = region(args.orig, args.grow, args.rim, args.feather, detect(args))
    rgb, _ = load(args.orig)
    vis = rgb * 0.5 + np.dstack([weight * 255, weight * 0, tops * 255]) * 0.5
    Image.fromarray(np.clip(vis, 0, 255).astype(np.uint8)).resize((384, 384), Image.Resampling.NEAREST).save(args.out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("composite", "measure", "maskpng"):
        p = sub.add_parser(name)
        p.add_argument("--orig", type=Path, required=True)
        p.add_argument("--grow", type=int, default=8)
        p.add_argument("--rim", type=float, default=8.0)
        p.add_argument("--feather", type=float, default=1.5)
        p.add_argument("--min-area", type=int, default=MIN_AREA)
        p.add_argument("--min-ratio", type=float, default=0.6)
        p.add_argument("--top-mean", type=float, default=TOP_MEAN_L)
        if name == "composite":
            p.add_argument("--new", type=Path, required=True)
            p.add_argument("--out", type=Path, required=True)
            p.add_argument("--lift", type=float, default=0.0)
            p.add_argument("--compress-above", type=float, default=145.0)
            p.add_argument("--compress-k", type=float, default=0.5)
        elif name == "measure":
            p.add_argument("tiles", type=Path, nargs="+")
        else:
            p.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    return {"composite": cmd_composite, "measure": cmd_measure, "maskpng": cmd_maskpng}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
