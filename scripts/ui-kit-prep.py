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

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent.parent
CUT_DIR = REPO / "art" / "foundry" / "ui" / "cut"
DEFAULT_OUT_DIR = REPO / "frontend" / "src" / "board" / "panels" / "chrome"

# Feather width (source px) for a hollowed center's alpha edge, so the baked
# face fades into whatever tiles beneath it instead of ending on a hard seam.
HOLLOW_FEATHER = 12

# Color-keyed face stripping (Emit.keyface): euclidean RGB distance from the
# center-patch median within which a pixel counts as face fill, and the blur
# radius (source px) softening the resulting alpha edge.
KEYFACE_TOLERANCE = 85.0
KEYFACE_FEATHER = 3


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
    # Rotate the emit 90° so a landscape cut can 9-slice onto a portrait
    # target: a NineSlice stretches/compresses its edge bands to the target
    # size, so a patterned border (the stitched seam) keeps its pitch only
    # when the texture's aspect roughly matches the rendered aspect.
    rotate: bool = False
    # Inset (source px) of a rectangle within which pixels close in color to
    # the emit's center (its baked face fill) are made transparent. Unlike
    # `hollow`'s rectangle, this hugs non-face features that intrude into the
    # region — the frame's brass corner brackets survive while the parchment
    # face they sit on is stripped, so the rail can sit directly on a tiling
    # dark ground. 0 = off.
    keyface: int = 0


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
    "tab-active": Emit("tab-active", 0.50),
    "tab-inactive": Emit("tab-inactive", 0.50),
    # Stitched panel grounds (docs/UI-STYLE.md: roster panel ground). Measured
    # on the kit-panels2 cuts: panel edge at ~8px, stitch groove ends ~28px
    # from the crop edge, so the edge-through-stitch-plus-face ring is ~30
    # source px — 0.70 lands it at ~21px emitted (target 18-24). Hollow just
    # past the seam (28px + ~1.7 feather sigma) so the stitch stays ~95%
    # opaque and the baked face crossfades into the tiling leather/canvas
    # fill beneath.
    "stitch-leather": Emit("stitch-leather", 0.70, hollow=48),
    "stitch-canvas": Emit("stitch-canvas", 0.70, hollow=48),
    # Portrait-rotated stitched panels for the tall roster ground: rotated to
    # ~0.64 aspect (vs the roster's ~0.5) so the seam's stitch pitch stays
    # near-uniform across all four edges, and emitted at 0.55 so the 292px
    # roster renders the texture near 1:1 horizontally.
    "stitch-leather-tall": Emit("stitch-leather", 0.55, hollow=48, rotate=True),
    "stitch-canvas-tall": Emit("stitch-canvas", 0.55, hollow=48, rotate=True),
    # Raw-edged parchment sheets (frameless dock card + roster page). The
    # aged edge band measures ~22 source px on sheet and ~37 on sheet-worn
    # (alpha edge to 90%-of-face luminance), so each gets its own scale to
    # land the band at ~12px emitted (target 10-14); hollow past the band
    # with enough margin that the band itself stays opaque.
    "sheet": Emit("sheet", 0.55, hollow=52),
    "sheet-worn": Emit("sheet-worn", 0.35, hollow=64),
    # Portrait-rotated sheet for the tall roster page (same rationale as the
    # -tall stitched panels: keep the rough edge band's grain near-uniform).
    "sheet-tall": Emit("sheet", 0.55, hollow=52, rotate=True),
    # Dark saddle-leather fill, mirrored like parchment/wood (same rationale).
    "leather": Emit("tex-leather", 0.50, mirror=True),
    # frame-roster's construction with the baked parchment face color-keyed
    # out instead of rectangle-hollowed: a rectangle wide enough to clear the
    # corner brackets leaves a parchment liner band along the rails (the
    # 2026-07-08 roster review caught it), while the color key hugs the
    # brackets and lets the rail sit directly on the leather ground. The 90px
    # inset tucks just under the rail's inner shadow so no face sliver
    # survives between rail and ground.
    "frame-leather": Emit("panel-frame", 0.20, keyface=90),
}


def strip_face(img: Image.Image, inset: int) -> Image.Image:
    """Make the emit's baked face fill transparent inside `inset` (Emit.keyface).

    The face color is the median of a small center patch; pixels within
    KEYFACE_TOLERANCE of it (RGB euclidean) inside the inset rectangle go
    transparent, so intruding non-face features (brass corner brackets) keep
    their pixels while the face around them is stripped.
    """
    arr = np.asarray(img).astype(np.int16)
    h, w = arr.shape[:2]
    cy, cx = h // 2, w // 2
    ref = np.median(arr[cy - 20 : cy + 20, cx - 20 : cx + 20, :3].reshape(-1, 3), axis=0)
    dist = np.sqrt(((arr[:, :, :3] - ref) ** 2).sum(axis=2))
    region = np.zeros((h, w), dtype=bool)
    region[inset : h - inset, inset : w - inset] = True
    keep = ~((dist <= KEYFACE_TOLERANCE) & region)
    keep_mask = Image.fromarray((keep * 255).astype(np.uint8), "L").filter(
        ImageFilter.GaussianBlur(KEYFACE_FEATHER)
    )
    out = img.copy()
    out.putalpha(ImageChops.multiply(out.getchannel("A"), keep_mask))
    return out


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
        if emit.keyface > 0:
            img = strip_face(img, emit.keyface)
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
        if emit.rotate:
            img = img.transpose(Image.ROTATE_90)
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
