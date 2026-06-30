#!/usr/bin/env python3
"""Build a labelled montage of pixel-art tiles for quick visual review.

Pixel-art hex/terrain tiles are tiny (32–64px) and have transparent corners, so
eyeballing them one-by-one is slow and a flat dark background hides alpha. This
renders every PNG nearest-neighbour-scaled on a checkerboard, labelled, in a
grid — one image you can open (or a vision-capable agent can read) to pick the
best variants in a single look.

Used by the `tile-smith` subagent (montage a freshly generated PixelLab batch)
and the `verify-board` skill, and handy ad hoc when comparing repo tiles.

Usage:
    # a directory tree of tiles (labels become "<parent>/<stem>")
    python scripts/tile-montage.py frontend/src/board/tiles --out /tmp/current.png

    # an explicit list / glob of a downloaded batch (labels become the stem)
    python scripts/tile-montage.py /tmp/batch/tile_*.png --out /tmp/batch.png

Options: --out (required), --scale (default 4), --cols (default 8).
Prints the output path and grid dimensions; exits non-zero on no inputs.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from PIL import Image, ImageDraw

CHECKER = ((80, 80, 90), (110, 110, 120))
BG = (40, 40, 48, 255)
LABEL_COLOR = (255, 255, 0)


def collect_pngs(inputs: list[str]) -> list[str]:
    """Expand inputs (dirs, globs, files) into a sorted list of PNG paths."""
    files: list[str] = []
    for item in inputs:
        if os.path.isdir(item):
            files.extend(glob.glob(os.path.join(item, "**", "*.png"), recursive=True))
        else:
            # A literal path the shell didn't expand, or a glob pattern.
            matches = glob.glob(item)
            files.extend(matches if matches else [item])
    # De-dupe, keep only real files, sort for stable indexing.
    seen = {os.path.abspath(f) for f in files if os.path.isfile(f)}
    return sorted(seen)


def label_for(path: str) -> str:
    """Short label: '<parent>/<stem>' when the parent dir is meaningful."""
    parent = os.path.basename(os.path.dirname(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    return f"{parent}/{stem}" if parent and parent != "tiles" else stem


def build(files: list[str], out: str, scale: int, cols: int) -> tuple[int, int]:
    tw = max(Image.open(f).size[0] for f in files) * scale
    pad, label = 18, 16
    n = len(files)
    rows = (n + cols - 1) // cols
    cw, ch = tw + pad, tw + label + pad
    img = Image.new("RGBA", (cols * cw, rows * ch), BG)
    draw = ImageDraw.Draw(img)
    for i, f in enumerate(files):
        tile = Image.open(f).convert("RGBA").resize((tw, tw), Image.NEAREST)
        col, row = i % cols, i // cols
        x, y = col * cw + pad // 2, row * ch + label
        for yy in range(0, tw, 16):
            for xx in range(0, tw, 16):
                draw.rectangle(
                    [x + xx, y + yy, x + xx + 16, y + yy + 16],
                    fill=CHECKER[(xx // 16 + yy // 16) % 2],
                )
        img.alpha_composite(tile, (x, y))
        draw.text((x, row * ch + 2), f"#{i} {label_for(f)}", fill=LABEL_COLOR)
    img.save(out)
    return img.size


def main() -> int:
    ap = argparse.ArgumentParser(description="Labelled montage of pixel-art tiles.")
    ap.add_argument("inputs", nargs="+", help="PNG files, globs, or directories")
    ap.add_argument("--out", required=True, help="output PNG path")
    ap.add_argument(
        "--scale", type=int, default=4, help="nearest-neighbour upscale (default 4)"
    )
    ap.add_argument("--cols", type=int, default=8, help="tiles per row (default 8)")
    args = ap.parse_args()

    files = collect_pngs(args.inputs)
    if not files:
        print(f"no PNG files matched: {args.inputs}", file=sys.stderr)
        return 1

    w, h = build(files, args.out, args.scale, args.cols)
    print(f"{args.out}  ({w}x{h}, {len(files)} tiles, {args.cols} cols)")
    for i, f in enumerate(files):
        print(f"  #{i}  {label_for(f)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
