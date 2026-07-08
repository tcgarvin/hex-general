#!/usr/bin/env python3
"""Cut named UI sprite assets out of ratified NBP UI-kit sheets.

The UI-kit sheets (`art/foundry/ui/kit-*.png`, 2048x2048) place several
finished sprites on a flat ~#7f7f7f gray background with small caption labels
near each item, generated in one NBP pass per UI-STYLE.md §5 ("Asset
production path"). This is the deterministic "cut" step: crop each item by a
fixed pixel box (measured once against the ratified sheets and hardcoded
below, same spirit as `tile-foundry/cut.py`'s window grid) and strip the flat
background to transparency, without ever touching the caption text — the
crop boxes were measured to stop short of it.

Background removal is a tolerance flood fill seeded from the crop's own
border ring, not a global chroma key: only background-colored pixels that are
*reachable from the border* through other background-colored pixels become
transparent, so a light patch inside an item's interior (e.g. the brass
nameplate's specular highlight) is never hollowed out. Texture swatches are
full-bleed square crops with no background to strip, so they skip this step
entirely (see `TEXTURE_NAMES`).

Usage:
    python3 scripts/ui-kit-cut.py
    python3 scripts/ui-kit-cut.py --out-dir /tmp/try --no-montage

Outputs land in `art/foundry/ui/cut/`: one PNG per manifest entry, a
`manifest.json` (name, source, box, output size), and a labelled
`montage.png` review sheet on a dark checkerboard.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "art" / "foundry" / "ui"
DEFAULT_OUT_DIR = SOURCE_DIR / "cut"

BG_TOLERANCE = 12.0  # euclidean RGB distance treated as "background"
FEATHER_RADIUS = 1.0  # gaussian blur radius on the alpha edge, in px

Box = tuple[int, int, int, int]  # (left, top, right, bottom), PIL convention


class Crop(NamedTuple):
    source: str  # filename under SOURCE_DIR
    box: Box


# Crop boxes were measured directly against the ratified 2048x2048 sheets:
# each item's tight non-background bounding box (found via a border-median
# background-distance scan, done *per item column* — a naive full-width row
# scan hides a short item's own caption gap behind a taller neighbor's still-
# active content in the same row band, which bit nameplate/iconbtn-* during
# development) padded by a flat 6px margin so an item's own soft glow/shadow
# is never clipped without reaching the caption text below it. Texture
# swatches use an *inward* margin instead, since they are full-bleed squares
# with no frame to pad outward from.
PANELS = "kit-panels.png"
BUTTONS = "kit-buttons.png"
BLANK = "kit-buttons-blank.png"

MANIFEST: dict[str, Crop] = {
    # kit-panels.png
    "panel-frame": Crop(PANELS, (109, 114, 1267, 952)),
    "inset-well": Crop(PANELS, (1362, 121, 1933, 946)),
    "tab-active": Crop(PANELS, (113, 1061, 529, 1407)),
    "tab-inactive": Crop(PANELS, (569, 1061, 984, 1407)),
    "nameplate": Crop(PANELS, (1119, 1168, 1933, 1357)),
    "tex-wood": Crop(PANELS, (336, 1515, 739, 1885)),
    "tex-canvas": Crop(PANELS, (822, 1516, 1225, 1890)),
    "tex-parchment": Crop(PANELS, (1307, 1515, 1711, 1886)),
    # kit-buttons.png
    "btn-move": Crop(BUTTONS, (95, 183, 453, 612)),
    "btn-barrage": Crop(BUTTONS, (469, 183, 828, 612)),
    "btn-rest": Crop(BUTTONS, (845, 183, 1202, 612)),
    "btn-hold": Crop(BUTTONS, (1219, 183, 1578, 612)),
    "btn-clear": Crop(BUTTONS, (1595, 183, 1952, 612)),
    "btn-state-up": Crop(BUTTONS, (95, 821, 453, 1209)),
    "btn-state-hover": Crop(BUTTONS, (469, 821, 828, 1209)),
    "btn-state-pressed": Crop(BUTTONS, (845, 821, 1202, 1209)),
    "btn-state-disabled": Crop(BUTTONS, (1220, 821, 1577, 1208)),
    "btn-state-active": Crop(BUTTONS, (1594, 821, 1953, 1209)),
    "lock-guard-closed": Crop(BUTTONS, (95, 1489, 637, 1934)),
    "lock-guard-open": Crop(BUTTONS, (671, 1488, 1252, 1934)),
    "iconbtn-up": Crop(BUTTONS, (1297, 1539, 1615, 1866)),
    "iconbtn-pressed": Crop(BUTTONS, (1635, 1539, 1953, 1866)),
    # kit-buttons-blank.png — the ratified construction with blank faces and
    # plates, so icons/labels can be composited dynamically in the engine.
    # One sprite per interaction state, uniform across all order buttons.
    "btnblank-up": Crop(BLANK, (96, 184, 453, 571)),
    "btnblank-hover": Crop(BLANK, (470, 184, 828, 571)),
    "btnblank-pressed": Crop(BLANK, (846, 184, 1203, 571)),
    "btnblank-disabled": Crop(BLANK, (1220, 184, 1578, 571)),
    "btnblank-active": Crop(BLANK, (1595, 184, 1952, 571)),
    "commit-up": Crop(BLANK, (96, 821, 689, 1209)),
    "commit-pressed": Crop(BLANK, (720, 821, 1316, 1209)),
    "commit-disabled": Crop(BLANK, (1349, 821, 1952, 1209)),
    "iconbtn2-up": Crop(BLANK, (1297, 1489, 1615, 1934)),
    "iconbtn2-pressed": Crop(BLANK, (1636, 1489, 1953, 1934)),
}

# These crops are full-bleed texture squares: no frame, no background to
# strip. They keep an opaque alpha channel.
TEXTURE_NAMES = {"tex-wood", "tex-canvas", "tex-parchment"}

CHECKER = ((48, 48, 54), (66, 66, 74))
MONTAGE_BG = (30, 30, 36, 255)
LABEL_COLOR = (255, 255, 0)


def border_ring_mask(shape: tuple[int, int]) -> np.ndarray:
    """Boolean `HxW` mask that is True on the outermost 1px ring only."""
    h, w = shape
    ring = np.zeros((h, w), dtype=bool)
    ring[0, :] = True
    ring[-1, :] = True
    ring[:, 0] = True
    ring[:, -1] = True
    return ring


def flood_fill_background(rgb: np.ndarray, tolerance: float) -> np.ndarray:
    """Border-connected background mask: True where a pixel is both close in
    color to the crop's own border and reachable from the border through
    other close-in-color pixels.

    Mirrors `tile-foundry/cut.py`'s `dilate_mask` roll-OR growth pattern:
    iteratively grow the reachable region into 4-neighbours that also pass
    the color-distance test, until a fixed point. This is a flood fill
    implemented as repeated dilation intersected with the candidate mask,
    which keeps it in plain numpy (no scipy connected-components needed) and
    guarantees an interior patch that merely *resembles* the background
    (e.g. a light highlight) is never hollowed out unless it actually
    touches the border through a chain of similar pixels.
    """
    border = np.concatenate(
        [rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :]], axis=0
    )
    median_bg = np.median(border, axis=0)
    dist = np.sqrt(((rgb.astype(np.float64) - median_bg) ** 2).sum(axis=-1))
    candidate = dist < tolerance

    region = candidate & border_ring_mask(rgb.shape[:2])
    while True:
        grown = (
            region
            | np.roll(region, 1, axis=0)
            | np.roll(region, -1, axis=0)
            | np.roll(region, 1, axis=1)
            | np.roll(region, -1, axis=1)
        ) & candidate
        if np.array_equal(grown, region):
            return region
        region = grown


def strip_background(crop: Image.Image, tolerance: float, feather: float) -> Image.Image:
    """Convert an RGB(A) crop's flat background to transparency.

    Background pixels (border flood fill, see `flood_fill_background`) get
    alpha 0; everything else stays alpha 255. A small gaussian blur on the
    alpha channel gives the ~1px feathered edge called for in the spec so
    the cutout doesn't have a hard aliased edge against future panel faces.
    """
    rgb = np.array(crop.convert("RGB"))
    bg = flood_fill_background(rgb, tolerance)
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    alpha_img = Image.fromarray(alpha, mode="L")
    if feather > 0:
        alpha_img = alpha_img.filter(ImageFilter.GaussianBlur(radius=feather))
    out = crop.convert("RGB")
    out.putalpha(alpha_img)
    return out


def cut_asset(name: str, crop_spec: Crop, tolerance: float, feather: float) -> Image.Image:
    """Load, crop, and (unless a texture swatch) strip the background for one asset."""
    src_path = SOURCE_DIR / crop_spec.source
    sheet = Image.open(src_path)
    crop = sheet.crop(crop_spec.box)
    if name in TEXTURE_NAMES:
        return crop.convert("RGBA")
    return strip_background(crop, tolerance, feather)


def corner_alpha_ok(img: Image.Image) -> bool:
    """True if at least one of the four corner pixels is transparent.

    A quick sanity check that background stripping actually ran: every
    non-texture crop was padded outward, so its corners should sit in the
    stripped-background margin.
    """
    w, h = img.size
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    alpha = img.getchannel("A")
    return any(alpha.getpixel(xy) == 0 for xy in corners)


def build_montage(assets: dict[str, Image.Image], out_path: Path) -> tuple[int, int]:
    """Labelled review montage on a dark checkerboard, arbitrary aspect ratios.

    `tile-montage.py` forces every tile into a common square, which would
    badly distort these UI crops (wide nameplates, tall inset wells, etc.),
    so this composes directly with PIL instead, one row of same-ish-size
    items at a time via a simple shelf layout.
    """
    names = sorted(assets)
    pad, label_h, cell_pad = 24, 20, 16
    max_cell_w = max(assets[n].width for n in names) + cell_pad
    cols = 4
    row_heights = []
    rows: list[list[str]] = [names[i : i + cols] for i in range(0, len(names), cols)]
    for row in rows:
        row_heights.append(max(assets[n].height for n in row) + label_h + cell_pad)

    canvas_w = cols * max_cell_w + pad
    canvas_h = sum(row_heights) + pad
    canvas = Image.new("RGBA", (canvas_w, canvas_h), MONTAGE_BG)
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default(size=14)
    except TypeError:  # older Pillow without the size kwarg
        font = ImageFont.load_default()

    y = pad // 2
    for row, row_h in zip(rows, row_heights):
        x = pad // 2
        for name in row:
            img = assets[name]
            cx, cy = x + cell_pad // 2, y + label_h
            for yy in range(0, img.height, 16):
                for xx in range(0, img.width, 16):
                    draw.rectangle(
                        [cx + xx, cy + yy, cx + xx + 16, cy + yy + 16],
                        fill=CHECKER[(xx // 16 + yy // 16) % 2],
                    )
            canvas.alpha_composite(img, (cx, cy))
            draw.text((x, y), name, fill=LABEL_COLOR, font=font)
            x += max_cell_w
        y += row_h
    canvas.save(out_path)
    return canvas.size


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="output directory (default art/foundry/ui/cut)"
    )
    ap.add_argument("--tolerance", type=float, default=BG_TOLERANCE, help="background RGB distance tolerance")
    ap.add_argument("--feather", type=float, default=FEATHER_RADIUS, help="alpha edge feather radius, px")
    ap.add_argument("--no-montage", action="store_true", help="skip writing montage.png")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    assets: dict[str, Image.Image] = {}
    manifest_rows = []
    problems: list[str] = []

    for name, crop_spec in MANIFEST.items():
        img = cut_asset(name, crop_spec, args.tolerance, args.feather)
        if img.width == 0 or img.height == 0:
            problems.append(f"{name}: zero-size crop")
            continue
        if name not in TEXTURE_NAMES and not corner_alpha_ok(img):
            problems.append(f"{name}: no transparent corner pixel found (background strip may have failed)")

        out_path = args.out_dir / f"{name}.png"
        img.save(out_path)
        assets[name] = img
        manifest_rows.append(
            {
                "name": name,
                "source": crop_spec.source,
                "box": list(crop_spec.box),
                "output_size": list(img.size),
            }
        )
        print(f"{name:<22}{crop_spec.source:<18}{str(crop_spec.box):<28}{img.size}")

    manifest_path = args.out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_rows, indent=2) + "\n")
    print(f"wrote {len(manifest_rows)} assets + {manifest_path}")

    if not args.no_montage:
        montage_path = args.out_dir / "montage.png"
        size = build_montage(assets, montage_path)
        print(f"montage: {montage_path} ({size[0]}x{size[1]})")

    if problems:
        print("problems:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
