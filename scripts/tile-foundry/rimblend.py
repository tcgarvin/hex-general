#!/usr/bin/env python3
"""Rim blend: lerp an AI-repainted tile back to its deterministic input near
the hex boundary, restoring seam inheritance while keeping the repaint interior.

Motivation (NBP road-finishing experiment, art/foundry/road/review/experiment/):
whole-tile image-to-image repaints integrate roads with terrain relief far
better than the deterministic flat ribbon, but they repaint the outer grass rim,
breaking the foundry's seam model (the rim must stay ratified-plains so tiles
abut invisibly). This pass makes the extreme boundary pure deterministic input
and the interior pure repaint, with a smoothstep ramp between:

    out(p) = lerp(input(p), repaint(p), w(d(p)))
    d(p)   = distance (px) from the TRUE 128px flat-top hex boundary polygon
             (hexgeom.corner_points, undilated — not the 1px-bled alpha ring)
    w(d)   = smoothstep(0, BLEND, d)   # 0 at the boundary, 1 at d >= BLEND

Pixels outside the undilated hex core (including the 1px dilation bleed ring)
take the input verbatim (w = 0). The output alpha is the input's alpha exactly,
so the silhouette is unchanged. Because the deterministic input already carries
the 12px road crossing at each connected edge midpoint, the blend also pins the
repaint's road back to the deterministic position/width at every crossing.

Usage:
    # one tile
    python3 rimblend.py --input <flat-composite.png> --repaint <nbp-pp.png> \\
        --blend 8 --out <blended.png>

    # every nbp_*_g*.json sidecar in a dir (expects `input` + `pp` paths,
    # relative to --root), one output per sidecar per blend width
    python3 rimblend.py batch --sidecar-dir <dir> --root <repo-root> \\
        --blend 4 8 16 --out-dir <dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from hexgeom import corner_points, hex_mask

TILE_PX = 128
HEX_WIDTH = 128.0


def hex_boundary_distance(canvas_px: int = TILE_PX, hex_width: float = HEX_WIDTH) -> np.ndarray:
    """Per-pixel exact distance (px) from the flat-top hex boundary polygon.

    Distance is to the nearest of the six edge segments, sampled at pixel
    centres. Unsigned — callers zero the weight outside the hex core instead of
    relying on a sign.
    """
    centre = canvas_px / 2.0
    corners = corner_points(centre, centre, hex_width / 2.0)
    ys, xs = np.mgrid[0:canvas_px, 0:canvas_px]
    px = xs + 0.5
    py = ys + 0.5
    dist = np.full((canvas_px, canvas_px), np.inf)
    for i in range(6):
        ax, ay = corners[i]
        bx, by = corners[(i + 1) % 6]
        abx, aby = bx - ax, by - ay
        t = np.clip(((px - ax) * abx + (py - ay) * aby) / (abx * abx + aby * aby), 0.0, 1.0)
        dx = px - (ax + t * abx)
        dy = py - (ay + t * aby)
        dist = np.minimum(dist, np.hypot(dx, dy))
    return dist


def blend_weights(blend_px: float) -> np.ndarray:
    """Smoothstep weight map: 0 at the hex boundary, 1 at depth >= blend_px.

    Outside the undilated hex core (hex_mask core > 128; the 1px bleed ring is
    outside the core) the weight is 0, so those pixels take the input verbatim.
    """
    if blend_px <= 0:
        raise ValueError(f"blend_px must be positive, got {blend_px}")
    d = hex_boundary_distance()
    t = np.clip(d / blend_px, 0.0, 1.0)
    w = t * t * (3.0 - 2.0 * t)
    core = hex_mask(TILE_PX, TILE_PX) > 128
    w[~core] = 0.0
    return w


def rim_blend(input_path: Path, repaint_path: Path, blend_px: float) -> Image.Image:
    """Blend RGB from input->repaint across the rim; alpha is the input's exactly."""
    inp = np.asarray(Image.open(input_path).convert("RGBA")).astype(np.float64)
    rep = np.asarray(Image.open(repaint_path).convert("RGBA")).astype(np.float64)
    if inp.shape != (TILE_PX, TILE_PX, 4) or rep.shape != (TILE_PX, TILE_PX, 4):
        raise ValueError(
            f"expected {TILE_PX}x{TILE_PX} RGBA tiles, got input {inp.shape} repaint {rep.shape}"
        )
    w = blend_weights(blend_px)[..., None]
    out = inp.copy()
    out[..., :3] = inp[..., :3] * (1.0 - w) + rep[..., :3] * w
    out[..., 3] = inp[..., 3]
    return Image.fromarray(np.round(out).astype(np.uint8), "RGBA")


def run_batch(args: argparse.Namespace) -> int:
    """Blend every generation sidecar in --sidecar-dir at each --blend width."""
    args.out_dir.mkdir(parents=True, exist_ok=True)
    sidecars = sorted(args.sidecar_dir.glob("nbp_*_g*.json"))
    if not sidecars:
        raise SystemExit(f"no nbp_*_g*.json sidecars in {args.sidecar_dir}")
    count = 0
    for sc_path in sidecars:
        sc = json.loads(sc_path.read_text())
        input_path = args.root / sc["input"]
        repaint_path = args.root / sc["pp"]
        for b in args.blend:
            out_path = args.out_dir / f"{sc_path.stem}__b{int(b):02d}.png"
            rim_blend(input_path, repaint_path, b).save(out_path)
            count += 1
            print(f"wrote {out_path.name}  (blend {b}px)")
    print(f"blended {count} tiles into {args.out_dir}")
    return 0


def run_single(args: argparse.Namespace) -> int:
    args.out.parent.mkdir(parents=True, exist_ok=True)
    rim_blend(args.input, args.repaint, args.blend).save(args.out)
    print(f"wrote {args.out}  (blend {args.blend}px)")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Blend a repainted tile back to its input near the hex rim.")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("single", help="blend one input/repaint pair")
    s.add_argument("--input", required=True, type=Path, help="deterministic input composite")
    s.add_argument("--repaint", required=True, type=Path, help="repainted tile (128px, post-processed)")
    s.add_argument("--blend", required=True, type=float, help="ramp width in px")
    s.add_argument("--out", required=True, type=Path)

    b = sub.add_parser("batch", help="blend every nbp_*_g*.json sidecar in a dir")
    b.add_argument("--sidecar-dir", required=True, type=Path)
    b.add_argument("--root", required=True, type=Path, help="root the sidecar paths are relative to")
    b.add_argument("--blend", required=True, type=float, nargs="+", help="ramp widths in px")
    b.add_argument("--out-dir", required=True, type=Path)

    args = ap.parse_args(argv)
    if args.cmd is None:
        ap.error("choose a subcommand: single | batch")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cmd == "single":
        return run_single(args)
    return run_batch(args)


if __name__ == "__main__":
    raise SystemExit(main())
