#!/usr/bin/env python3
"""Emit frontend-ready UI chrome sprites from the ratified kit cuts.

The bridge between the foundry cuts (art/foundry/ui/cut/, produced by
ui-kit-cut.py) and what the Pixi chrome actually loads
(frontend/src/board/panels/chrome/). Each emit downscales a cut to roughly 2x
its on-screen size (crisp on hidpi, cheap on VRAM) and renames it for the
consumer. Deterministic: re-run after re-cutting; output is fully derived.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent.parent
CUT_DIR = REPO / "art" / "foundry" / "ui" / "cut"
DEFAULT_OUT_DIR = REPO / "frontend" / "src" / "board" / "panels" / "chrome"

# Feather width (source px) for a hollowed center's alpha edge, so the baked
# face fades into whatever tiles beneath it instead of ending on a hard seam.
HOLLOW_FEATHER = 12


class Emit(NamedTuple):
    source: str  # filename under CUT_DIR (without .png)
    scale: float
    # Inset (source px) of a rectangle to punch transparent out of the center.
    # For frames rendered as a NineSlice over a tiling face fill: the frame
    # keeps its rails/brackets plus a feathered ring of its baked face, and the
    # fill shows through the middle. 0 = solid emit.
    hollow: int = 0
    # Mirror-tile the emit into a 2x2 flip-book so a TilingSprite repeats it
    # seamlessly — the raw cuts have vignetted edges that read as grid seams on
    # any panel taller/wider than one period (the roster showed them).
    mirror: bool = False


# On-screen targets (1x): order buttons ~64px, the portrait well ~130px,
# nameplate height ~24px, commit button ~110px wide. Scales below land each
# sprite near 2x those targets.
EMITS: dict[str, Emit] = {
    "btn-up": Emit("btnblank-up", 0.40),
    "btn-hover": Emit("btnblank-hover", 0.40),
    "btn-pressed": Emit("btnblank-pressed", 0.40),
    "btn-disabled": Emit("btnblank-disabled", 0.40),
    "btn-active": Emit("btnblank-active", 0.40),
    "commit-up": Emit("commit-up", 0.40),
    "commit-pressed": Emit("commit-pressed", 0.40),
    "commit-disabled": Emit("commit-disabled", 0.40),
    "iconbtn-up": Emit("iconbtn2-up", 0.40),
    "iconbtn-pressed": Emit("iconbtn2-pressed", 0.40),
    # Emitted smaller than the buttons: both render as NineSliceSprites at
    # small on-screen sizes (well ~124px square, plate ~28px tall), and a
    # NineSlice border renders at the texture's own pixel thickness — the
    # emit scale is what sets the on-screen border weight.
    "well": Emit("inset-well", 0.25),
    "plate": Emit("nameplate", 0.25),
    # Parchment/wood emit at half scale *then* mirror: same pixel count (and
    # file size) as a plain 2x emit, seamless, rendered at tileScale 1.0. A 1x
    # density is fine for these low-contrast fills; 2x-and-mirrored would
    # quadruple the bundle for no visible gain. Canvas stays a plain emit — its
    # diagonal weave turns into herringbone chevrons when mirrored, and the
    # 140px dock strip is shorter than one tile period so seams never show.
    "canvas": Emit("tex-canvas", 1.00),
    "parchment": Emit("tex-parchment", 0.50, mirror=True),
    "wood": Emit("tex-wood", 0.50, mirror=True),
    "frame": Emit("panel-frame", 0.50),
    # The selected-unit dock's inset card (docs/UI-STYLE.md §4): the same
    # ratified frame, emitted thin enough that its ~94px source rail lands at
    # ~12px inside the 140px bottom strip, center hollowed (inset past the
    # ~140px bracket elbows) so tiled parchment provides the face.
    "frame-dock": Emit("panel-frame", 0.13, hollow=160),
    # The roster's outer frame (docs/UI-STYLE.md §4): same ratified frame at a
    # chunkier ~19px rail, hollowed past the bracket elbows — the surviving
    # ring of baked parchment reads as the page liner against the tiled face.
    "frame-roster": Emit("panel-frame", 0.20, hollow=160),
    "tab-active": Emit("tab-active", 0.50),
    "tab-inactive": Emit("tab-inactive", 0.50),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="output directory (default frontend/src/board/panels/chrome)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for name, emit in EMITS.items():
        src = CUT_DIR / f"{emit.source}.png"
        img = Image.open(src).convert("RGBA")
        if emit.hollow > 0:
            mask = Image.new("L", img.size, 255)
            ImageDraw.Draw(mask).rectangle(
                (emit.hollow, emit.hollow, img.width - emit.hollow, img.height - emit.hollow),
                fill=0,
            )
            mask = mask.filter(ImageFilter.GaussianBlur(HOLLOW_FEATHER))
            img.putalpha(ImageChops.multiply(img.getchannel("A"), mask))
        if emit.scale != 1.0:
            size = (
                max(1, round(img.width * emit.scale)),
                max(1, round(img.height * emit.scale)),
            )
            img = img.resize(size, Image.LANCZOS)
        if emit.mirror:
            quad = Image.new("RGBA", (img.width * 2, img.height * 2))
            quad.paste(img, (0, 0))
            quad.paste(img.transpose(Image.FLIP_LEFT_RIGHT), (img.width, 0))
            quad.paste(img.transpose(Image.FLIP_TOP_BOTTOM), (0, img.height))
            quad.paste(img.transpose(Image.ROTATE_180), (img.width, img.height))
            img = quad
        out = args.out_dir / f"{name}.png"
        img.save(out, optimize=True)
        print(f"{name:<18}{emit.source:<22}x{emit.scale:<6}{img.size}")

    print(f"emitted {len(EMITS)} sprites -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
