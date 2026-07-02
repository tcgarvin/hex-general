#!/usr/bin/env python3
"""Adjacency-preview compositor: tile a small hex patch from cut variants.

Composes an `--cols`x`--rows` flat-top hex patch (using `hexgeom.axial_to_pixel`
with `size=hex_width/2`, matching the game's own geometry) from one or two
directories of pre-cut, hex-masked tile PNGs, so seams and repeats can be
eyeballed before they go anywhere near the board. `--layout uniform` uses only
the primary (`--tiles`) variants everywhere; `--layout mass` places a central
blob of primary tiles surrounded by `--context` variants (e.g. a woods patch
inside a plains field). Each hex's variant is chosen deterministically by a
coordinate hash, so patches are reproducible run to run.

Emits a full-resolution PNG plus a board-scale PNG (`--board-px-per-hex`,
default 50px/hex, matching typical in-game hex rendering size).

Example:
    python3 patch.py --tiles woods-pick/ --context plains-pick/ \\
        --layout mass --out preview/woods
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from PIL import Image

from gate import load_tile
from hexgeom import axial_to_pixel

DEFAULT_COLS = 8
DEFAULT_ROWS = 5
DEFAULT_BLOB_RADIUS = 2
DEFAULT_BOARD_PX_PER_HEX = 50.0
CANVAS_BACKGROUND = (128, 128, 128, 255)  # neutral gray so any uncovered gap between hexes is obvious


def load_variant_set(dir_path: Path, hex_width: int) -> list[Image.Image]:
    """Load every `*.png` in `dir_path`, sorted, validated square `hex_width`x`hex_width`."""
    paths = sorted(dir_path.glob("*.png"))
    if not paths:
        raise ValueError(f"no PNG tiles found in {dir_path}")
    return [Image.fromarray(load_tile(p, hex_width), mode="RGBA") for p in paths]


def variant_index(q: int, r: int, count: int) -> int:
    """Deterministic per-hex variant pick: `(q*92837111 ^ r*689287499) % count`."""
    if count <= 0:
        raise ValueError("tile set is empty")
    return (q * 92837111 ^ r * 689287499) % count


def axial_distance(q1: int, r1: int, q2: int, r2: int) -> int:
    """Hex grid distance between two axial coordinates."""
    return (abs(q1 - q2) + abs(q1 + r1 - q2 - r2) + abs(r1 - r2)) // 2


def compose_patch(
    primary: list[Image.Image],
    context: list[Image.Image],
    layout: str,
    hex_width: int,
    cols: int,
    rows: int,
    blob_radius: int,
) -> Image.Image:
    """Tile `cols`x`rows` hexes onto a canvas, picking primary vs. context per `layout`."""
    size = hex_width / 2.0
    positions = {(q, r): axial_to_pixel(q, r, size) for q in range(cols) for r in range(rows)}
    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    canvas_w = int(math.ceil(max_x - min_x + hex_width))
    canvas_h = int(math.ceil(max_y - min_y + hex_width))
    canvas = Image.new("RGBA", (canvas_w, canvas_h), CANVAS_BACKGROUND)

    primary_center = (cols // 2, rows // 2)
    for r in range(rows):
        for q in range(cols):
            x, y = positions[(q, r)]
            use_context = False
            if layout == "mass":
                cq, cr = primary_center
                use_context = axial_distance(q, r, cq, cr) > blob_radius
            variants = context if use_context else primary
            idx = variant_index(q, r, len(variants))
            tile = variants[idx]
            paste_x = int(round(x - min_x))
            paste_y = int(round(y - min_y))
            canvas.paste(tile, (paste_x, paste_y), tile)
    return canvas


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Compose a hex adjacency-preview patch from cut tile variants.")
    ap.add_argument("--tiles", required=True, type=Path, help="primary variant directory (hex_width-square PNGs)")
    ap.add_argument("--context", type=Path, default=None, help="context variant directory (required for --layout mass)")
    ap.add_argument("--layout", choices=["uniform", "mass"], default="uniform", help="default uniform")
    ap.add_argument("--hex-width", type=int, default=128, help="expected tile canvas size (default 128)")
    ap.add_argument("--cols", type=int, default=DEFAULT_COLS, help=f"patch width in hexes (default {DEFAULT_COLS})")
    ap.add_argument("--rows", type=int, default=DEFAULT_ROWS, help=f"patch height in hexes (default {DEFAULT_ROWS})")
    ap.add_argument(
        "--blob-radius",
        type=int,
        default=DEFAULT_BLOB_RADIUS,
        help=f"mass layout: axial radius of the central primary-tile blob (default {DEFAULT_BLOB_RADIUS})",
    )
    ap.add_argument(
        "--board-px-per-hex",
        type=float,
        default=DEFAULT_BOARD_PX_PER_HEX,
        help=f"board-scale point-to-point hex width in px (default {DEFAULT_BOARD_PX_PER_HEX})",
    )
    ap.add_argument("--out", required=True, type=Path, help="output prefix; writes PREFIX-full.png and PREFIX-board.png")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.layout == "mass" and args.context is None:
        print("--layout mass requires --context", file=sys.stderr)
        return 1
    if not args.tiles.is_dir():
        print(f"--tiles is not a directory: {args.tiles}", file=sys.stderr)
        return 1
    if args.context is not None and not args.context.is_dir():
        print(f"--context is not a directory: {args.context}", file=sys.stderr)
        return 1

    try:
        primary = load_variant_set(args.tiles, args.hex_width)
        context = load_variant_set(args.context, args.hex_width) if args.context is not None else []
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    canvas = compose_patch(primary, context, args.layout, args.hex_width, args.cols, args.rows, args.blob_radius)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    full_path = args.out.parent / f"{args.out.name}-full.png"
    board_path = args.out.parent / f"{args.out.name}-board.png"
    canvas.save(full_path)

    scale = args.board_px_per_hex / args.hex_width
    board_w = max(1, round(canvas.width * scale))
    board_h = max(1, round(canvas.height * scale))
    board = canvas.resize((board_w, board_h), Image.LANCZOS)
    board.save(board_path)

    print(f"wrote {full_path} ({canvas.width}x{canvas.height})")
    print(f"wrote {board_path} ({board_w}x{board_h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
