#!/usr/bin/env python3
"""Object-field compositor: segment tree sprites and composite them onto a
plains base to produce the "woods" terrain class (see art/foundry/README.md
§ Terrain classes: object fields are NEVER swatch-cut — trees are placed
whole so no canopy is ever truncated at a hex edge).

Two independent stages, both driven by `art/foundry/manifest.json`:

1. **Segmentation** (`segment_sprites`): the sprite sheet
   (`woods.sprite_sheet`, trees on a white background) is thresholded to
   alpha, connected-component-labelled, and each blob is classified
   deciduous/conifer by bounding-box aspect ratio, then contrast-graded
   toward the manifest's target mean luminance per class.
2. **Composition** (`place_trees` + `composite_tile`): a `random.Random`
   seeded per variant scatters trees over the ratified plains base
   (`woods.compose.base`), rejecting placements that cross the hex edge or
   crowd an already-placed tree, then draws soft ground shadows and the
   trees themselves (lower trees over higher ones) and reclips to a
   1px-dilated hex mask.

The RNG call sequence in `place_trees` (`randint`, `choice`, `uniform`,
`uniform`) and the float math in `segment_sprites` are load-bearing: this is
the exact algorithm that produced the ratified tiles in
`frontend/src/board/tiles/woods/`, and changing either reorders the random
stream or shifts pixel values away from that byte-exact result.

Examples:
    python3 compose.py --manifest art/foundry/manifest.json --section woods \\
        --out-dir /tmp/woods-out
    python3 compose.py --manifest art/foundry/manifest.json --section woods \\
        --out-dir /tmp/woods-out --verify frontend/src/board/tiles/woods
    python3 compose.py --manifest art/foundry/manifest.json --section woods \\
        --out-dir /tmp/woods-explore --seed-offset 100 --variants 8
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from cut import dilate_mask
from hexgeom import hex_mask

# -- segmentation constants (art/foundry/manifest.json woods.segmentation /
# woods.sprite_processing document these; the ones below are prose-only in
# the manifest — not plain JSON numbers — so they're named constants here,
# mirroring the manifest's current values) --------------------------------
FG_THRESHOLD = 25.0  # "foreground": min-channel distance from white > 25
ALPHA_RAMP_RANGE = 60.0  # "alpha_ramp": (dist-25)*255/60 clipped 0..255
MIN_BLOB_AREA = 400  # "connected_components": min area 400 px
CONIFER_ASPECT = 1.15  # "conifer_if": bbox h/w > 1.15
HIGHLIGHT_THRESHOLD = 135.0  # "highlight_softclip": above 135: ...
HIGHLIGHT_SOFTCLIP = 0.45  # ... 135 + (v-135)*0.45

# -- composition constants (also manifest prose, not JSON numbers) --------
PLACEMENT_MARGIN = 3.0  # candidate x/y drawn from [margin, TILE - margin]
DIST_Y_SCALE = 0.52  # "min_dist": sqrt(dx^2 + (0.52*dy)^2) >= factor*(w1+w2)
DEFAULT_MIN_DIST_FACTOR = 0.23  # ... >= 0.23*(w1+w2)
EDGE_INSET_BASE = 2.0  # "edge_inset": shrink 2 + 0.72*max(w,h)/2
EDGE_INSET_FACTOR = 0.72
MIN_TREE_WIDTH = 6
FLOOR_DISC_RADIUS_FACTOR = 0.72  # "floor_discs": r=0.72*w at crown centre
FLOOR_DISC_COLOR = (30, 44, 26, 185)
FLOOR_BLUR_RADIUS = 2
MASK_DILATE_PX = 1  # "final_clip": dilated (1px bleed) hex mask

Placement = tuple[float, float, int, Image.Image, int, bool]  # x, y_base, height, sprite, width, is_conifer


# ---------------------------------------------------------------------
# Stage 1: sprite sheet segmentation
# ---------------------------------------------------------------------


def label_components(fg: np.ndarray) -> tuple[np.ndarray, int]:
    """8-neighbour connected-component labels over a boolean foreground mask.

    Raster-scan discovery order (row-major, then column-major within a row)
    is load-bearing: it determines which blob gets which label number, which
    in turn determines the order sprites are appended to the deciduous/conifer
    pools in `segment_sprites` — and that order is what `random.Random.choice`
    indexes into during placement.
    """
    h, w = fg.shape
    labels = np.zeros((h, w), dtype=np.int32)
    cur = 0
    for sy in range(h):
        for sx in range(w):
            if fg[sy, sx] and labels[sy, sx] == 0:
                cur += 1
                stack = [(sy, sx)]
                labels[sy, sx] = cur
                while stack:
                    y, x = stack.pop()
                    for ny in range(max(0, y - 1), min(h, y + 2)):
                        for nx in range(max(0, x - 1), min(w, x + 2)):
                            if fg[ny, nx] and labels[ny, nx] == 0:
                                labels[ny, nx] = cur
                                stack.append((ny, nx))
    return labels, cur


def build_sprite(
    sheet: np.ndarray,
    alpha: np.ndarray,
    labels: np.ndarray,
    lb: int,
    target_lum: dict[str, float],
    desaturation: float,
) -> tuple[bool, Image.Image] | None:
    """Crop, classify, and contrast-grade one labelled blob into an RGBA sprite.

    Returns `None` for blobs under `MIN_BLOB_AREA` (segmentation noise), else
    `(is_conifer, sprite)`.
    """
    ys, xs = np.nonzero(labels == lb)
    if len(xs) < MIN_BLOB_AREA:
        return None
    bx0, bx1, by0, by1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    w, h = bx1 - bx0, by1 - by0
    rgb = sheet[by0:by1, bx0:bx1].copy()
    aa = np.where(labels[by0:by1, bx0:bx1] == lb, alpha[by0:by1, bx0:bx1], 0).astype(float) / 255
    lum = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    m = aa > 0.5
    is_conifer = (h / w) > CONIFER_ASPECT
    target = target_lum["conifer"] if is_conifer else target_lum["deciduous"]
    gain = target / max(lum[m].mean(), 1)
    rgb *= gain
    over = rgb > HIGHLIGHT_THRESHOLD
    rgb[over] = HIGHLIGHT_THRESHOLD + (rgb[over] - HIGHLIGHT_THRESHOLD) * HIGHLIGHT_SOFTCLIP
    mean = rgb.mean(axis=2, keepdims=True)
    rgb = mean + (rgb - mean) * desaturation
    sprite = Image.fromarray(
        np.dstack([np.clip(rgb, 0, 255).astype(np.uint8), (aa * 255).astype(np.uint8)]), "RGBA"
    )
    return is_conifer, sprite


def segment_sprites(
    sheet_path: Path, target_lum: dict[str, float], desaturation: float
) -> tuple[list[Image.Image], list[Image.Image]]:
    """Segment a white-background tree sprite sheet into (deciduous, conifer) sprite lists.

    Blob order within each list follows label-discovery order (see
    `label_components`) — that order is what `place_trees`'s `rng.choice`
    indexes into, so it must stay stable across runs of the same sheet.
    """
    sheet = np.array(Image.open(sheet_path).convert("RGB")).astype(float)
    dist_white = 255 - sheet.min(axis=2)
    fg = dist_white > FG_THRESHOLD
    alpha = np.clip((dist_white - FG_THRESHOLD) * (255 / ALPHA_RAMP_RANGE), 0, 255).astype(np.uint8)
    labels, cur = label_components(fg)

    decid: list[Image.Image] = []
    conif: list[Image.Image] = []
    for lb in range(1, cur + 1):
        result = build_sprite(sheet, alpha, labels, lb, target_lum, desaturation)
        if result is None:
            continue
        is_conifer, sprite = result
        (conif if is_conifer else decid).append(sprite)
    return decid, conif


# ---------------------------------------------------------------------
# Stage 2: composition
# ---------------------------------------------------------------------


def in_hex(x: float, y: float, shrink: float, s: float) -> bool:
    """Whether point `(x, y)` (canvas coords) lies inside a flat-top hex of
    centre-to-corner radius `s - shrink`, centred on the `2s`-square canvas.
    """
    px, py = x - s, y - s
    r = s - shrink
    q3 = math.sqrt(3)
    return (
        r > 0
        and abs(py) <= r * q3 / 2
        and abs(px * q3 / 2 + py / 2) <= r * q3 / 2
        and abs(px * q3 / 2 - py / 2) <= r * q3 / 2
    )


def place_trees(
    seed: int,
    tile_px: int,
    decid: list[Image.Image],
    conif: list[Image.Image],
    target_trees: int,
    max_attempts: int,
    conifer_quota: float,
    h_min: int,
    h_max: int,
    min_dist_factor: float,
) -> tuple[list[Placement], int]:
    """Scatter up to `target_trees` non-overlapping, edge-respecting trees.

    Preserves the reference RNG call order exactly: per attempt,
    `randint` (height) then `choice` (sprite) then `uniform` x2 (position).
    Rejecting a candidate (edge or overlap) does not roll back consumed
    randomness — that's how the reference behaves, and changing it would
    desync every draw after the first rejection from the ratified output.
    """
    s = tile_px / 2.0
    rng = random.Random(seed)
    placed: list[Placement] = []
    n_conif = 0
    for _ in range(max_attempts):
        if len(placed) >= target_trees:
            break
        conifer = n_conif < conifer_quota * max(len(placed), 3)
        th = rng.randint(h_min, h_max)
        pool = conif if conifer else decid
        spr = rng.choice(pool)
        tw = max(MIN_TREE_WIDTH, round(spr.size[0] * th / spr.size[1]))
        x, yb = rng.uniform(PLACEMENT_MARGIN, tile_px - PLACEMENT_MARGIN), rng.uniform(
            PLACEMENT_MARGIN, tile_px - PLACEMENT_MARGIN
        )
        if not in_hex(x, yb - th / 2, EDGE_INSET_BASE + max(tw, th) / 2 * EDGE_INSET_FACTOR, s):
            continue
        ok = True
        for px_, pyb, _pth, _pspr, ptw, _pconifer in placed:
            dx, dy = x - px_, (yb - pyb) * DIST_Y_SCALE
            if dx * dx + dy * dy < (min_dist_factor * (tw + ptw)) ** 2:
                ok = False
                break
        if ok:
            placed.append((x, yb, th, spr, tw, conifer))
            if conifer:
                n_conif += 1
    return placed, n_conif


def render_floor(tile_px: int, placed: list[Placement], hm_dilated: np.ndarray) -> np.ndarray:
    """Soft ground-shadow discs under each tree's crown, clipped to the dilated hex mask."""
    floor = Image.new("RGBA", (tile_px, tile_px), (0, 0, 0, 0))
    fd = ImageDraw.Draw(floor)
    for x, yb, th, _spr, tw, _conifer in placed:
        rr = tw * FLOOR_DISC_RADIUS_FACTOR
        fd.ellipse([x - rr, yb - th / 2 - rr, x + rr, yb - th / 2 + rr], fill=FLOOR_DISC_COLOR)
    floor = floor.filter(ImageFilter.GaussianBlur(FLOOR_BLUR_RADIUS))
    fa = np.array(floor)
    fa[..., 3] = np.where(hm_dilated, fa[..., 3], 0)
    return fa


def composite_tile(base_img: Image.Image, placed: list[Placement], hm_dilated: np.ndarray) -> Image.Image:
    """Composite floor discs then trees (lower `y_base` drawn last, i.e. on top) onto the base."""
    tile = base_img.copy()
    floor_arr = render_floor(tile.size[0], placed, hm_dilated)
    tile.alpha_composite(Image.fromarray(floor_arr, "RGBA"))
    for x, yb, th, spr, tw, _conifer in sorted(placed, key=lambda p: p[1]):
        tile.alpha_composite(spr.resize((tw, th), Image.LANCZOS), (int(x - tw / 2), int(yb - th)))
    ta = np.array(tile)
    ta[..., 3] = np.where(hm_dilated, ta[..., 3], 0)
    return Image.fromarray(ta, "RGBA")


def placements_record(
    variant: int,
    seed: int,
    placed: list[Placement],
    n_conif: int,
    decid: list[Image.Image],
    conif: list[Image.Image],
) -> dict:
    """Build one `placements.json` entry. Placement order is insertion (attempt) order,
    not composite draw order — matches `art/foundry/woods/placements.json`.
    """
    entries = []
    for x, yb, th, spr, tw, conifer in placed:
        pool = conif if conifer else decid
        entries.append(
            {
                "x": round(x, 3),
                "y_base": round(yb, 3),
                "height": th,
                "width": tw,
                "type": "conifer" if conifer else "deciduous",
                "sprite_index": pool.index(spr),
            }
        )
    return {
        "variant": variant,
        "seed": seed,
        "trees": len(placed),
        "conifers": n_conif,
        "placements": entries,
    }


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def load_manifest(manifest_path: Path, section: str) -> tuple[dict, dict]:
    """Load the manifest and return `(manifest, manifest[section])`."""
    manifest = json.loads(manifest_path.read_text())
    if section not in manifest:
        raise ValueError(f"manifest has no {section!r} section (have: {sorted(manifest)})")
    return manifest, manifest[section]


def verify_against(out_dir: Path, ref_dir: Path) -> bool:
    """Compare every PNG in `out_dir` against the same-named file in `ref_dir`
    by decoded RGBA pixel array (not file bytes — PNG encoder settings may
    differ). Prints one MATCH/MISMATCH line per file.
    """
    out_files = sorted(out_dir.glob("*.png"))
    if not out_files:
        print(f"no PNGs found in {out_dir} to verify", file=sys.stderr)
        return False

    all_match = True
    for out_path in out_files:
        ref_path = ref_dir / out_path.name
        if not ref_path.is_file():
            print(f"MISMATCH {out_path.name} (no reference at {ref_path})")
            all_match = False
            continue
        out_arr = np.array(Image.open(out_path).convert("RGBA"))
        ref_arr = np.array(Image.open(ref_path).convert("RGBA"))
        match = out_arr.shape == ref_arr.shape and np.array_equal(out_arr, ref_arr)
        print(f"{'MATCH' if match else 'MISMATCH'} {out_path.name}")
        if not match:
            all_match = False
    return all_match


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Composite sprite-sheet trees onto a plains base to render the woods object-field tileset."
    )
    ap.add_argument("--manifest", required=True, type=Path, help="path to art/foundry/manifest.json")
    ap.add_argument("--section", required=True, help="manifest section to render (only 'woods' is implemented)")
    ap.add_argument("--out-dir", required=True, type=Path, help="directory for rendered PNGs + placements.json")
    ap.add_argument(
        "--verify",
        type=Path,
        default=None,
        help="directory of reference PNGs to byte-compare (pixel array, not file bytes) the render against",
    )
    ap.add_argument(
        "--seed-offset",
        type=int,
        default=0,
        help="exploration knob: added to each variant's manifest seed (default 0 reproduces the ratified tiles)",
    )
    ap.add_argument(
        "--variants",
        type=int,
        default=None,
        help="exploration knob: number of variants to render (default: one per manifest seed)",
    )
    ap.add_argument(
        "--target-trees", type=int, default=None, help="override compose.target_trees (default: manifest value)"
    )
    ap.add_argument(
        "--max-attempts", type=int, default=None, help="override compose.max_attempts (default: manifest value)"
    )
    ap.add_argument(
        "--conifer-frac", type=float, default=None, help="override compose.conifer_quota (default: manifest value)"
    )
    ap.add_argument(
        "--min-dist-factor",
        type=float,
        default=DEFAULT_MIN_DIST_FACTOR,
        help=f"override the min-separation factor in the reference's min_dist formula (default {DEFAULT_MIN_DIST_FACTOR})",
    )
    ap.add_argument(
        "--tree-h-min", type=int, default=None, help="override compose.tree_height_px[0] (default: manifest value)"
    )
    ap.add_argument(
        "--tree-h-max", type=int, default=None, help="override compose.tree_height_px[1] (default: manifest value)"
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.manifest.is_file():
        print(f"manifest not found: {args.manifest}", file=sys.stderr)
        return 1

    try:
        manifest, section = load_manifest(args.manifest, args.section)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.section != "woods":
        print(
            f"--section {args.section!r} is not supported; compose.py only implements "
            "the 'woods' object-field compositor",
            file=sys.stderr,
        )
        return 1

    manifest_dir = args.manifest.resolve().parent
    tile_px = manifest["tile_spec"]["canvas_px"]
    compose_cfg = section["compose"]
    sprite_proc = section["sprite_processing"]

    sheet_path = manifest_dir / section["sprite_sheet"]
    # compose.base carries a trailing human-readable note after the path
    # template in the manifest (e.g. "plains/picks/{v}.png (the bleed-cut
    # ratified plains)") — the template itself never contains whitespace, so
    # the first token is the path.
    base_template = compose_cfg["base"].split()[0]
    seeds = compose_cfg["seeds"]
    target_lum = sprite_proc["target_mean_luminance"]
    desaturation = sprite_proc["desaturation"]

    target_trees = args.target_trees if args.target_trees is not None else compose_cfg["target_trees"]
    max_attempts = args.max_attempts if args.max_attempts is not None else compose_cfg["max_attempts"]
    conifer_quota = args.conifer_frac if args.conifer_frac is not None else compose_cfg["conifer_quota"]
    h_min = args.tree_h_min if args.tree_h_min is not None else compose_cfg["tree_height_px"][0]
    h_max = args.tree_h_max if args.tree_h_max is not None else compose_cfg["tree_height_px"][1]
    variants = args.variants if args.variants is not None else len(seeds)

    if not sheet_path.is_file():
        print(f"sprite sheet not found: {sheet_path}", file=sys.stderr)
        return 1

    decid, conif = segment_sprites(sheet_path, target_lum, desaturation)
    if not decid or not conif:
        print(
            f"segmentation found {len(decid)} deciduous / {len(conif)} conifer sprites "
            f"(need at least one of each) in {sheet_path}",
            file=sys.stderr,
        )
        return 1

    hm = hex_mask(tile_px, tile_px) > 128
    hm_dilated = dilate_mask(hm, MASK_DILATE_PX)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for v in range(variants):
        seed = seeds[v % len(seeds)] + args.seed_offset
        base_path = manifest_dir / base_template.format(v=f"{v:02d}")
        if not base_path.is_file():
            print(f"base tile not found: {base_path}", file=sys.stderr)
            return 1
        base_img = Image.open(base_path).convert("RGBA")

        placed, n_conif = place_trees(
            seed, tile_px, decid, conif, target_trees, max_attempts, conifer_quota, h_min, h_max, args.min_dist_factor
        )
        tile_img = composite_tile(base_img, placed, hm_dilated)
        out_path = args.out_dir / f"{v:02d}.png"
        tile_img.save(out_path)
        records.append(placements_record(v, seed, placed, n_conif, decid, conif))
        print(f"wrote {out_path} ({len(placed)} trees, {n_conif} conifer, seed {seed})")

    placements_path = args.out_dir / "placements.json"
    placements_path.write_text(json.dumps(records, indent=1) + "\n")
    print(f"wrote {placements_path}")

    if args.verify is not None:
        if not verify_against(args.out_dir, args.verify):
            print("FAIL: one or more rendered tiles do not match the reference", file=sys.stderr)
            return 1
        print("PASS: all rendered tiles match the reference")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
