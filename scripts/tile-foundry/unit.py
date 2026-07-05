#!/usr/bin/env python3
"""Unit sprite foundry: split ratified NBP unit crops into two-layer game assets.

Player colours must scale to 32+ players, so unit sprites ship as TWO
pixel-registered layers per unit instead of one PNG per colour:

  {unit}_base.png  the full sprite with the team-colour areas NEUTRALIZED to
                   grey (saturation dropped to 0, per-pixel value kept) and a
                   1px near-black rim baked around the outer silhouette.
  {unit}_mask.png  ONLY the team areas: RGB = the pixel's value channel (a
                   white-to-grey ramp encoding the original cobalt shading),
                   alpha = team-area coverage, zero elsewhere. No rim.

The renderer draws the mask over the base with ``tint = playerColor``
(multiply): ``tint x value`` reproduces the shaded team colour for any
player colour, nothing is ever baked.

Team areas in the ratified crops are painted in a keyable cobalt
(#3B5FC0 family, hue ~213-229 deg across all generation rounds). The
classifier is a SOFT weight — a feathered hue window times a saturation
ramp — so the base/mask boundary blends instead of haloing; parameters
live in ``manifest.json -> units.classify`` and were tuned until the base
shows no cobalt residue and the mask picks up no khaki/skin/gunmetal.

Pipeline per unit (all parameters in ``manifest.json -> units``):

  1. load the ratified crop (RGBA), gate against white matte fringe;
  2. un-matte partial-alpha edge pixels against the measured extraction
     background (mean RGB over alpha==0 — the crops were lifted from
     coloured sheet backgrounds, so AA edges carry background bleed);
  3. mirror horizontally where flagged (native gen facing is east; shipped
     units face WEST, user-ratified);
  4. classify team pixels at source resolution, split into base
     (weight-desaturated) and mask (weight x value) layers;
  5. LANCZOS-downscale both layers with the SAME premultiplied transform so
     the solid bbox width hits the ratified footprint (90px armor/artillery,
     81px infantry — "downscale ~10%, it dwarfs the tank"), paste centred
     on the 128x128 canvas;
  6. bake the rim: the base is composited OVER a 1px-dilated #14140f
     silhouette plate, so AA edges darken into the outline instead of
     leaving a translucent halo (base only — the mask gets no rim);
  7. gates (fail loud): bbox width, cobalt residue in the base, mask
     coverage band, mask-inside-base registration, centring.

``build --verify`` re-derives all six PNGs from the committed sources and
byte-compares against ``frontend/src/board/units/`` (selftest hook).
``review`` renders the tint-simulation montage and the on-plains trio strip
under ``art/foundry/units/review/``.

Examples:
    unit.py build --manifest art/foundry/manifest.json --section units \\
        --out-dir /tmp/units --verify frontend/src/board/units
    unit.py review --manifest art/foundry/manifest.json --section units \\
        --out-dir art/foundry/units/review
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# vignette's float-HSV pair (NOT cut's — see the two-implementations warning
# in river.py; unit.py's ratified outputs are built with this one).
from vignette import dilate_bool, hsv_to_rgb, rgb_to_hsv  # noqa: E402

CANVAS = 128
SOLID_ALPHA = 0.5  # "solid" bbox threshold, fraction of full alpha
RIM_SUPPORT_ALPHA = 0.25  # silhouette support for the rim plate: LANCZOS spreads
# alpha ~3px past the visible sprite, so a >0 support would bake a soft dark halo

# residue gate: after the split, no base pixel may still read as cobalt
RESIDUE_HUE = (205.0, 245.0)
RESIDUE_SAT_MIN = 0.25
RESIDUE_VAL_MIN = 0.12

# white-fringe gate on the source crop (partial-alpha AA pixels must not be
# near-white: that would mean a white matte the un-matte step can't recover)
FRINGE_VAL_MIN = 0.85
FRINGE_SAT_MAX = 0.15

REVIEW_TINTS = {"blue": 0x4F8CFF, "red": 0xFF5D5D}


class UnitBuildError(RuntimeError):
    """A gate failed or an input violated the pipeline contract."""


# ------------------------------------------------------------- helpers ----


def dilate8(mask: np.ndarray, iterations: int) -> np.ndarray:
    """Binary-dilate with the 8-neighbourhood (solid diagonal rim coverage)."""
    for _ in range(iterations):
        m = mask.copy()
        for ax, sh in ((0, 1), (0, -1), (1, 1), (1, -1)):
            m |= np.roll(mask, sh, axis=ax)
        m |= np.roll(np.roll(mask, 1, axis=0), 1, axis=1)
        m |= np.roll(np.roll(mask, 1, axis=0), -1, axis=1)
        m |= np.roll(np.roll(mask, -1, axis=0), 1, axis=1)
        m |= np.roll(np.roll(mask, -1, axis=0), -1, axis=1)
        mask = m
    return mask


def ramp(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Linear 0->1 ramp between lo and hi, clipped outside."""
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def resize_f(plane: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """LANCZOS-resize one float plane (PIL 'F' mode keeps full precision)."""
    return np.asarray(Image.fromarray(plane.astype(np.float32), "F").resize(size, Image.LANCZOS))


def solid_bbox(alpha: np.ndarray) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) inclusive bbox of alpha >= SOLID_ALPHA."""
    ys, xs = np.nonzero(alpha >= SOLID_ALPHA)
    if len(xs) == 0:
        raise UnitBuildError("sprite has no solid pixels")
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def team_weight(h: np.ndarray, s: np.ndarray, classify: dict) -> np.ndarray:
    """Soft cobalt classification weight in [0, 1] (hue window x sat ramp)."""
    lo, hi, feather = classify["hue_lo"], classify["hue_hi"], classify["hue_feather"]
    wh = ramp(h, lo, lo + feather) * (1.0 - ramp(h, hi - feather, hi))
    ws = ramp(s, classify["sat_lo"], classify["sat_hi"])
    return wh * ws


# ------------------------------------------------------------ pipeline ----


def load_crop(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Load a ratified crop as float (rgb 0..1, alpha 0..1), un-matted.

    Gates: must be RGBA; partial-alpha AA pixels must not be near-white
    (an unrecoverable white matte). Partial pixels are un-matted against
    the measured extraction background so edge AA carries art colour, not
    sheet-background bleed.
    """
    img = Image.open(path)
    if img.mode != "RGBA":
        raise UnitBuildError(f"{path}: expected RGBA, got {img.mode}")
    arr = np.asarray(img).astype(np.float64) / 255.0
    rgb, alpha = arr[..., :3], arr[..., 3]

    h, s, v = rgb_to_hsv(rgb)
    partial = (alpha > 0) & (alpha < 1)
    fringe = partial & (v > FRINGE_VAL_MIN) & (s < FRINGE_SAT_MAX)
    if fringe.any():
        raise UnitBuildError(f"{path}: {int(fringe.sum())} white-fringe pixels on the AA edge")

    transparent = alpha == 0
    if partial.any() and transparent.any():
        bg = rgb[transparent].mean(axis=0)
        a = alpha[..., None]
        unmatted = np.clip((rgb - (1.0 - a) * bg) / np.maximum(a, 1e-3), 0.0, 1.0)
        rgb = np.where(partial[..., None], unmatted, rgb)
    return rgb, alpha


def split_layers(
    rgb: np.ndarray, alpha: np.ndarray, classify: dict
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Split into (base_rgb, base_a, mask_value, mask_a) at source resolution."""
    h, s, v = rgb_to_hsv(rgb)
    w = team_weight(h, s, classify)
    base_rgb = hsv_to_rgb(h, s * (1.0 - w), v)
    return base_rgb, alpha, v, w * alpha


def scale_and_center(
    base_rgb: np.ndarray,
    base_a: np.ndarray,
    mask_v: np.ndarray,
    mask_a: np.ndarray,
    target_w: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Same premultiplied LANCZOS transform on both layers; centred 128 canvases.

    Returns (base_rgb, base_a, mask_v, mask_a) as CANVAS x CANVAS floats,
    base_a being the PRE-RIM alpha. Premultiplying before the resize stops
    under-alpha RGB from bleeding into edges; the mask alpha is clipped into
    the base alpha afterwards so registration is exact by construction.
    """
    ys, xs = np.nonzero(base_a > 0)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    crop = np.s_[y0:y1, x0:x1]
    base_rgb, base_a = base_rgb[crop], base_a[crop]
    mask_v, mask_a = mask_v[crop], mask_a[crop]

    sx0, _, sx1, _ = solid_bbox(base_a)
    scale = target_w / (sx1 - sx0 + 1)
    size = (max(1, round(base_a.shape[1] * scale)), max(1, round(base_a.shape[0] * scale)))

    ba = np.clip(resize_f(base_a, size), 0.0, 1.0)
    ma = np.clip(resize_f(mask_a, size), 0.0, 1.0)
    safe_ba = np.maximum(ba, 1e-6)
    safe_ma = np.maximum(ma, 1e-6)
    brgb = np.stack(
        [np.clip(resize_f(base_rgb[..., c] * base_a, size) / safe_ba, 0, 1) for c in range(3)],
        axis=-1,
    )
    mv = np.clip(resize_f(mask_v * mask_a, size) / safe_ma, 0.0, 1.0)
    ma = np.minimum(ma, ba)  # registration by construction

    bx0, by0, bx1, by1 = solid_bbox(ba)
    ox = round((CANVAS - (bx1 - bx0 + 1)) / 2) - bx0
    oy = round((CANVAS - (by1 - by0 + 1)) / 2) - by0
    if ox < 0 or oy < 0 or ox + size[0] > CANVAS or oy + size[1] > CANVAS:
        raise UnitBuildError(f"scaled sprite {size} does not fit the {CANVAS}px canvas")

    def paste(plane: np.ndarray) -> np.ndarray:
        canvas = np.zeros((CANVAS, CANVAS), dtype=np.float64)
        canvas[oy : oy + size[1], ox : ox + size[0]] = plane
        return canvas

    return (
        np.stack([paste(brgb[..., c]) for c in range(3)], axis=-1),
        paste(ba),
        paste(mv),
        paste(ma),
    )


def rim_plate(base_a: np.ndarray, rim: dict) -> np.ndarray:
    """Silhouette support (alpha >= RIM_SUPPORT_ALPHA) dilated by the rim width."""
    return dilate8(base_a >= RIM_SUPPORT_ALPHA, int(rim["px"]))


def bake_rim(base_rgb: np.ndarray, base_a: np.ndarray, plate: np.ndarray, rim: dict) -> np.ndarray:
    """Composite the base OVER a rim-coloured plate around the silhouette.

    Under-compositing (instead of only filling the dilated ring) makes the
    sprite's AA edge blend into the near-black outline rather than leaving a
    translucent halo over the terrain; alpha outside the plate is discarded
    (the faint LANCZOS spill). Returns a uint8 RGBA canvas.
    """
    colour = np.array(
        [(rim["color"] >> 16) & 255, (rim["color"] >> 8) & 255, rim["color"] & 255],
        dtype=np.float64,
    )
    a = base_a[..., None]
    rgb = base_rgb * 255.0 * a + colour * (1.0 - a)
    rgb = np.where(plate[..., None], rgb, 0.0)
    alpha = np.where(plate, 255.0, 0.0)
    return np.dstack([rgb, alpha]).round().clip(0, 255).astype(np.uint8)


def mask_to_png(mask_v: np.ndarray, mask_a: np.ndarray) -> np.ndarray:
    """Mask layer as uint8 RGBA: RGB = value grey, alpha = team coverage."""
    grey = (mask_v * 255.0)[..., None].repeat(3, axis=2)
    return np.dstack([grey, mask_a * 255.0]).round().clip(0, 255).astype(np.uint8)


# --------------------------------------------------------------- gates ----


def run_gates(
    name: str,
    base_png: np.ndarray,
    mask_png: np.ndarray,
    pre_rim_a: np.ndarray,
    target_w: int,
    coverage_band: tuple[float, float],
) -> dict:
    """All shipping gates for one unit; raises UnitBuildError on any failure."""
    for layer, png in (("base", base_png), ("mask", mask_png)):
        if png.shape != (CANVAS, CANVAS, 4):
            raise UnitBuildError(f"{name} {layer}: shape {png.shape} != ({CANVAS},{CANVAS},4)")

    x0, y0, x1, y1 = solid_bbox(pre_rim_a)
    width = x1 - x0 + 1
    if abs(width - target_w) > 1:
        raise UnitBuildError(f"{name}: bbox width {width} != target {target_w} (+-1)")
    cx, cy = (x0 + x1 + 1) / 2, (y0 + y1 + 1) / 2
    if abs(cx - CANVAS / 2) > 1 or abs(cy - CANVAS / 2) > 1:
        raise UnitBuildError(f"{name}: bbox centre ({cx},{cy}) off canvas centre (+-1)")

    h, s, v = rgb_to_hsv(base_png[..., :3].astype(np.float64) / 255.0)
    residue = (
        (pre_rim_a >= SOLID_ALPHA)
        & (h >= RESIDUE_HUE[0])
        & (h <= RESIDUE_HUE[1])
        & (s > RESIDUE_SAT_MIN)
        & (v > RESIDUE_VAL_MIN)
    )
    if residue.any():
        raise UnitBuildError(f"{name}: {int(residue.sum())} cobalt-residue pixels in the base")

    mask_a = mask_png[..., 3].astype(np.float64) / 255.0
    outside = (mask_a > 0) & (pre_rim_a <= 0)
    if outside.any():
        raise UnitBuildError(f"{name}: {int(outside.sum())} mask pixels outside the base alpha")

    coverage = float(mask_a.sum() / pre_rim_a.sum())
    lo, hi = coverage_band
    if not lo <= coverage <= hi:
        raise UnitBuildError(f"{name}: mask coverage {coverage:.3f} outside band [{lo}, {hi}]")

    return {"bbox": (x0, y0, x1, y1), "width": width, "height": y1 - y0 + 1, "coverage": coverage}


# --------------------------------------------------------------- build ----


def build_unit(root: Path, section: dict, name: str) -> tuple[np.ndarray, np.ndarray, dict]:
    """Full pipeline for one unit -> (base_png, mask_png, gate report)."""
    spec = section["units"][name]
    rgb, alpha = load_crop(root / spec["source"])
    if spec["mirror"]:
        rgb, alpha = rgb[:, ::-1], alpha[:, ::-1]

    base_rgb, base_a, mask_v, mask_a = split_layers(rgb, alpha, section["classify"])
    base_rgb, base_a, mask_v, mask_a = scale_and_center(
        base_rgb, base_a, mask_v, mask_a, int(spec["target_bbox_w"])
    )
    plate = rim_plate(base_a, section["rim"])
    base_png = bake_rim(base_rgb, base_a, plate, section["rim"])
    mask_png = mask_to_png(mask_v, mask_a * plate)
    report = run_gates(
        name, base_png, mask_png, base_a, int(spec["target_bbox_w"]), spec["coverage_band"]
    )
    return base_png, mask_png, report


def verify_against(out_dir: Path, ref_dir: Path, names: list[str]) -> bool:
    """Byte-compare the six built PNGs against the shipped assets."""
    ok = True
    for fname in [f"{n}_{layer}.png" for n in names for layer in ("base", "mask")]:
        out_path, ref_path = out_dir / fname, ref_dir / fname
        if not ref_path.exists():
            print(f"MISSING in reference: {ref_path}")
            ok = False
            continue
        a = np.asarray(Image.open(out_path).convert("RGBA"))
        b = np.asarray(Image.open(ref_path).convert("RGBA"))
        if a.shape != b.shape or not np.array_equal(a, b):
            diff = 0 if a.shape != b.shape else int((a != b).any(axis=-1).sum())
            print(f"DIFFERS: {fname} ({diff} px)")
            ok = False
        else:
            print(f"identical: {fname}")
    return ok


def cmd_build(args: argparse.Namespace) -> int:
    manifest = json.loads(args.manifest.read_text())
    if args.section not in manifest:
        raise SystemExit(f"section {args.section!r} not in {args.manifest}")
    section = manifest[args.section]
    root = args.manifest.parent
    args.out_dir.mkdir(parents=True, exist_ok=True)

    names = list(section["units"])
    for name in names:
        base_png, mask_png, report = build_unit(root, section, name)
        Image.fromarray(base_png, "RGBA").save(args.out_dir / f"{name}_base.png")
        Image.fromarray(mask_png, "RGBA").save(args.out_dir / f"{name}_mask.png")
        print(
            f"{name}: bbox {report['width']}x{report['height']} "
            f"coverage={report['coverage']:.3f} gates PASS"
        )

    if args.verify:
        if not verify_against(args.out_dir, args.verify, names):
            return 1
        print("verify: all unit assets byte-identical")
    return 0


# -------------------------------------------------------------- review ----


def simulate_tint(base_png: np.ndarray, mask_png: np.ndarray, tint: int) -> np.ndarray:
    """What the renderer does: mask x playerColor multiplied, over the base."""
    t = np.array([(tint >> 16) & 255, (tint >> 8) & 255, tint & 255], dtype=np.float64) / 255.0
    ma = (mask_png[..., 3].astype(np.float64) / 255.0)[..., None]
    tinted = mask_png[..., :3].astype(np.float64) * t
    out = base_png.astype(np.float64).copy()
    out[..., :3] = out[..., :3] * (1.0 - ma) + tinted * ma
    return out.round().clip(0, 255).astype(np.uint8)


def over_backdrop(png: np.ndarray, backdrop: Image.Image) -> Image.Image:
    """Alpha-composite a unit canvas over a CANVAS-sized backdrop copy."""
    out = backdrop.copy().convert("RGBA")
    out.alpha_composite(Image.fromarray(png, "RGBA"))
    return out


def cmd_review(args: argparse.Namespace) -> int:
    manifest = json.loads(args.manifest.read_text())
    section = manifest[args.section]
    root = args.manifest.parent
    repo = root.parent.parent
    args.out_dir.mkdir(parents=True, exist_ok=True)

    names = list(section["units"])
    built = {n: build_unit(root, section, n) for n in names}

    plains_dir = repo / "frontend" / "src" / "board" / "tiles" / "plains"
    plains = sorted(plains_dir.glob("*.png"))
    if not plains:
        raise SystemExit(f"no plains tiles in {plains_dir}")
    tile = Image.open(plains[0]).convert("RGBA")

    pad, cell = 8, CANVAS
    neutral = Image.new("RGBA", (cell, cell), (74, 78, 70, 255))

    # tint-simulation sheet: rows = units; cols = base, tinted blue, tinted red
    cols = 1 + len(REVIEW_TINTS)
    sheet = Image.new(
        "RGBA",
        (pad + cols * (cell + pad), pad + len(names) * (cell + pad)),
        (40, 40, 40, 255),
    )
    for r, name in enumerate(names):
        base_png, mask_png, _ = built[name]
        panels = [base_png] + [simulate_tint(base_png, mask_png, t) for t in REVIEW_TINTS.values()]
        for c, png in enumerate(panels):
            sheet.paste(over_backdrop(png, neutral), (pad + c * (cell + pad), pad + r * (cell + pad)))
    tint_path = args.out_dir / "units-tint-review.png"
    sheet.convert("RGB").save(tint_path)
    print(f"wrote {tint_path}")

    # trio strip on the ratified plains tile, 128px and 50px board scale (x2 nearest)
    trio = Image.new("RGBA", (pad + len(names) * (cell + pad), cell + 2 * pad), (40, 40, 40, 255))
    for c, name in enumerate(names):
        base_png, mask_png, _ = built[name]
        tinted = simulate_tint(base_png, mask_png, REVIEW_TINTS["blue"])
        trio.paste(over_backdrop(tinted, tile), (pad + c * (cell + pad), pad))
    trio_path = args.out_dir / "units-plains-trio-128.png"
    trio.convert("RGB").save(trio_path)
    print(f"wrote {trio_path}")

    board = trio.resize((round(trio.width * 50 / 128), round(trio.height * 50 / 128)), Image.LANCZOS)
    board = board.resize((board.width * 2, board.height * 2), Image.NEAREST)
    board_path = args.out_dir / "units-plains-trio-50board-x2.png"
    board.convert("RGB").save(board_path)
    print(f"wrote {board_path}")
    return 0


# ----------------------------------------------------------------- CLI ----


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Two-layer unit sprite foundry (base + tint mask).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build the six unit PNGs from the committed sources")
    b.add_argument("--manifest", required=True, type=Path)
    b.add_argument("--section", default="units")
    b.add_argument("--out-dir", required=True, type=Path)
    b.add_argument("--verify", type=Path, help="byte-compare against this directory")
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("review", help="render the tint-simulation + on-plains review images")
    r.add_argument("--manifest", required=True, type=Path)
    r.add_argument("--section", default="units")
    r.add_argument("--out-dir", required=True, type=Path)
    r.set_defaults(func=cmd_review)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
