#!/usr/bin/env python3
"""Build the background + mask for a river-inpainting corridor.

Paints a water corridor (straight or S-curved) onto a plains hex, pinned to the
top/bottom edge centers so the resulting tile self-tiles. Emits:
  <out>_bg.png    plains + teal corridor  -> PixelLab `background_image`
  <out>_mask.png  corridor white / rest black -> PixelLab `inpainting.mask_image`

The hex silhouette is taken from the plains tile's own alpha (exact match).
Rule of thumb from the experiment: final river width ~= corridor width + ~8px.

Usage:
  python3 make_corridor.py --plains ../../frontend/src/board/tiles/plains/00.png \
      --out straight9 --width 9
  python3 make_corridor.py --out scurve9 --width 9 --amp 5
"""
import argparse
import math

import numpy as np
from PIL import Image

# muted grey-teal, matches the shipped straight_v water family
TEAL = (103, 133, 132, 255)


def build(plains_path: str, out_prefix: str, width: int, amp: float) -> None:
    arr = np.array(Image.open(plains_path).convert("RGBA"))
    h, w = arr.shape[:2]
    hexmask = arr[:, :, 3] > 0
    rows = np.where(hexmask.any(axis=1))[0]
    top_y, bot_y = int(rows.min()), int(rows.max())
    cx = w // 2
    half = width // 2

    corridor = np.zeros((h, w), bool)
    span = bot_y - top_y
    for y in range(top_y, bot_y + 1):
        t = (y - top_y) / span
        # sin(2*pi*t) is 0 at both ends -> endpoints stay pinned at cx (tiling)
        center = int(round(cx + amp * math.sin(2 * math.pi * t)))
        for x in range(center - half, center + half + 1):
            if 0 <= x < w and hexmask[y, x]:
                corridor[y, x] = True

    bg = arr.copy()
    bg[corridor] = TEAL
    Image.fromarray(bg).save(f"{out_prefix}_bg.png")

    mask = np.zeros((h, w), np.uint8)
    mask[corridor] = 255
    Image.fromarray(mask, "L").convert("RGB").save(f"{out_prefix}_mask.png")

    print(
        f"{out_prefix}: {width}px corridor, amp {amp}, rows {top_y}..{bot_y}, "
        f"{int(corridor.sum())} px"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--plains",
        default="../../frontend/src/board/tiles/plains/00.png",
        help="plains tile whose alpha defines the hex silhouette",
    )
    ap.add_argument("--out", required=True, help="output filename prefix")
    ap.add_argument("--width", type=int, default=9, help="corridor width in px")
    ap.add_argument(
        "--amp", type=float, default=0.0, help="S-curve amplitude in px (0 = straight)"
    )
    args = ap.parse_args()
    build(args.plains, args.out, args.width, args.amp)


if __name__ == "__main__":
    main()
