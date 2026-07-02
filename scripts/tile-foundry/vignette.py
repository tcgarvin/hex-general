#!/usr/bin/env python3
"""Whole-hex vignette compositor: segment feature clusters from a
white-background sheet and composite them centred onto the ratified plains
bases (see art/foundry/README.md § Terrain classes: settlement vignettes and
hill formations are whole-hex compositions on a grass rim — never swatch-cut,
so no building or ridge is ever truncated at a hex edge).

Three stages, all driven by `art/foundry/manifest.json`:

1. **Segmentation** (`find_clusters` + `build_vignette`): the sheet (a 2x2
   grid of clusters on pure white) is thresholded to alpha at a downsampled
   resolution, hole-filled (bright roofs/rock read as "white" but are
   interior), component-labelled, and the k largest clusters are lifted at
   full resolution — un-matted from white so partial-alpha edges don't halo,
   with highlights soft-clipped toward the concept register.
2. **Grass palette match** (`grass_match`, optional per section): rotates
   grass-classified pixels toward the ratified plains grass medians and
   drops whitish low-saturation pixels near the alpha boundary (the
   "whitish seam"). Rock, roofs, and other non-grass pixels stay put. Used
   by hills; city needs no recolour.
3. **Composition** (`compose_onto_base`): each vignette is scaled to
   `feature_px`, centred on its plains base pick, and reclipped to the
   base's own 1px-dilated hex mask.

The float math is load-bearing: this is the exact algorithm that produced
the ratified tiles in `frontend/src/board/tiles/{hills,city}/`, verified
byte-for-byte by `--verify` (and selftest.py).

Examples:
    python3 vignette.py --manifest art/foundry/manifest.json --section city \\
        --out-dir /tmp/city-out
    python3 vignette.py --manifest art/foundry/manifest.json --section hills \\
        --out-dir /tmp/hills-out --verify frontend/src/board/tiles/hills
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from compose import label_components

# -- segmentation constants (same white-sheet convention as the woods tree
# sheet; see manifest sections hills.segmentation / city.segmentation) ------
FG_THRESHOLD = 25.0  # "foreground": min-channel distance from white > 25
ALPHA_RAMP_RANGE = 60.0  # "alpha_ramp": (dist-25)*255/60 clipped 0..255
DOWNSAMPLE = 8  # cluster discovery / hole-fill resolution = sheet/8
INTERIOR_ERODE = 2  # low-res erosion of the forced-opaque interior


def block_max(a: np.ndarray, k: int) -> np.ndarray:
    """Max-pool a 2-D array by factor `k` (trailing remainder rows/cols dropped)."""
    h, w = a.shape
    return a[: h - h % k, : w - w % k].reshape(h // k, k, w // k, k).max(axis=(1, 3))


def erode_bool(mask: np.ndarray, iterations: int) -> np.ndarray:
    """Binary-erode a boolean mask, 4-neighbour roll-AND (mirror of cut.dilate_mask)."""
    for _ in range(iterations):
        mask = (
            mask
            & np.roll(mask, 1, axis=0)
            & np.roll(mask, -1, axis=0)
            & np.roll(mask, 1, axis=1)
            & np.roll(mask, -1, axis=1)
        )
    return mask


def dilate_bool(mask: np.ndarray, iterations: int) -> np.ndarray:
    """Binary-dilate a boolean mask, 4-neighbour roll-OR."""
    for _ in range(iterations):
        mask = (
            mask
            | np.roll(mask, 1, axis=0)
            | np.roll(mask, -1, axis=0)
            | np.roll(mask, 1, axis=1)
            | np.roll(mask, -1, axis=1)
        )
    return mask


def rgb_to_hsv(px: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised RGB(0..1) -> HSV with hue in degrees."""
    mx = px.max(-1)
    mn = px.min(-1)
    d = mx - mn
    m = d > 1e-6
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    h = np.zeros(mx.shape)
    i = m & (mx == r)
    h[i] = ((g - b)[i] / d[i]) % 6
    i = m & (mx == g)
    h[i] = ((b - r)[i] / d[i]) + 2
    i = m & (mx == b)
    h[i] = ((r - g)[i] / d[i]) + 4
    h *= 60
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0)
    return h, s, mx


def hsv_to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Vectorised HSV (hue degrees) -> RGB(0..1)."""
    h6 = (h % 360) / 60.0
    i = np.floor(h6).astype(int) % 6
    f = h6 - np.floor(h6)
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    out = np.zeros(h.shape + (3,))
    for idx, (rr, gg, bb) in enumerate(
        [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)]
    ):
        m = i == idx
        out[..., 0][m] = rr[m]
        out[..., 1][m] = gg[m]
        out[..., 2][m] = bb[m]
    return out


def find_clusters(
    sheet: np.ndarray, k: int
) -> list[tuple[np.ndarray, tuple[int, int, int, int]]]:
    """Return the k largest clusters as (inside_mask_lowres, fullres_bbox).

    Hole filling happens at the downsampled resolution: background components
    that don't touch the image border are enclosed by a cluster and belong to
    it. Result order is row-major by bbox centre so sheet position maps
    predictably to variant number.
    """
    dist = 255 - sheet.min(axis=2).astype(np.int16)
    fg_lo = block_max((dist > FG_THRESHOLD).astype(np.uint8), DOWNSAMPLE).astype(bool)

    bg_labels, _ = label_components(~fg_lo)
    border = np.zeros_like(fg_lo)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    outside_ids = set(np.unique(bg_labels[border & ~fg_lo]))
    inside = fg_lo | ((bg_labels > 0) & ~np.isin(bg_labels, sorted(outside_ids)))

    labels, n = label_components(inside)
    sizes = [(labels == i).sum() for i in range(1, n + 1)]
    order = np.argsort(sizes)[::-1][:k]
    out = []
    for i in order:
        m = labels == (i + 1)
        ys, xs = np.nonzero(m)
        bbox = (
            int(xs.min()) * DOWNSAMPLE,
            int(ys.min()) * DOWNSAMPLE,
            (int(xs.max()) + 1) * DOWNSAMPLE,
            (int(ys.max()) + 1) * DOWNSAMPLE,
        )
        out.append((m, bbox))
    out.sort(key=lambda t: ((t[1][1] + t[1][3]) // 2 // 512, (t[1][0] + t[1][2]) // 2))
    return out


def build_vignette(
    sheet: np.ndarray,
    inside_lo: np.ndarray,
    bbox: tuple[int, int, int, int],
    softclip_above: float,
    softclip_factor: float,
) -> Image.Image:
    """Lift one cluster from the sheet as a bbox-cropped RGBA sprite."""
    x0, y0, x1, y1 = bbox
    crop = sheet[y0:y1, x0:x1].astype(float)
    dist = 255 - crop.min(axis=2)
    alpha = np.clip((dist - FG_THRESHOLD) * (255 / ALPHA_RAMP_RANGE), 0, 255)

    # interior pixels are opaque no matter how white (bright roofs/rock);
    # the ramp only applies near the cluster edge
    inside_full = np.kron(
        erode_bool(inside_lo, INTERIOR_ERODE),
        np.ones((DOWNSAMPLE, DOWNSAMPLE), dtype=bool),
    )
    alpha = np.maximum(alpha, np.where(inside_full[y0:y1, x0:x1], 255.0, 0.0))

    # un-matte from white: edge pixels are art blended over white by the
    # generator; recover the art colour so partial-alpha edges don't halo
    rgb = crop.copy()
    a01 = np.clip(alpha / 255.0, 1e-3, 1.0)[..., None]
    partial = (alpha > 0) & (alpha < 255)
    unmatted = np.clip((rgb - (1.0 - a01) * 255.0) / a01, 0, 255)
    rgb = np.where(partial[..., None], unmatted, rgb)

    hi = rgb > softclip_above
    rgb[hi] = softclip_above + (rgb[hi] - softclip_above) * softclip_factor

    out = np.dstack([rgb, alpha]).astype(np.uint8)
    img = Image.fromarray(out, "RGBA")
    bb = img.getbbox()
    return img.crop(bb) if bb else img


def grass_match(sprite: Image.Image, params: dict) -> Image.Image:
    """Palette-match grass toward the plains medians; drop pale edge pixels.

    Operates on the uint8 sprite exactly as ratified (the candidate pipeline
    round-tripped through a PNG between segmentation and this step; PNG is
    lossless so operating on the in-memory uint8 array is identical).
    """
    im = np.asarray(sprite.convert("RGBA")).astype(float)
    rgb = im[..., :3] / 255.0
    alpha = im[..., 3].copy()
    h, s, v = rgb_to_hsv(rgb)

    pe = params["pale_edge"]
    near_edge = dilate_bool(alpha < 128, int(pe["dilate_px"]))
    pale = (v > pe["v_min"]) & (s < pe["s_max"])
    alpha[pale & near_edge & (alpha >= 128)] = 0

    opaque = alpha > 0
    grass_w = (
        np.clip((s - 0.12) / 0.10, 0, 1)
        * np.clip((h - 30) / 10, 0, 1)
        * np.clip((175 - h) / 10, 0, 1)
    )
    core = opaque & (grass_w > 0.9)
    med_h = np.median(h[core])
    med_s = np.median(s[core])
    med_v = np.median(v[core])
    h2 = h + grass_w * (params["target_h"] - med_h)
    s2 = np.clip(s * (1 + grass_w * (params["target_s"] / med_s - 1)), 0, 1)
    v2 = np.clip(v * (1 + grass_w * (params["target_v"] / med_v - 1)), 0, 1)
    hi = v2 > params["v_softclip"]
    v2[hi] = params["v_softclip"] + (v2[hi] - params["v_softclip"]) * params["v_softclip_k"]

    out_rgb = (hsv_to_rgb(h2, s2, v2) * 255).round().clip(0, 255)
    res = np.dstack([out_rgb, alpha]).astype(np.uint8)
    img = Image.fromarray(res, "RGBA")
    bb = img.getbbox()
    return img.crop(bb) if bb else img


def compose_onto_base(
    vignette: Image.Image, base_path: Path, tile_px: int, feature_px: int
) -> Image.Image:
    """Scale the vignette to `feature_px` and centre it on the plains base."""
    scale = feature_px / max(vignette.size)
    vw = max(1, round(vignette.width * scale))
    vh = max(1, round(vignette.height * scale))
    v = vignette.resize((vw, vh), Image.LANCZOS)

    tile = Image.open(base_path).convert("RGBA")
    base_alpha = np.array(tile)[..., 3].copy()
    tile.alpha_composite(v, ((tile_px - vw) // 2, (tile_px - vh) // 2))
    arr = np.array(tile)
    arr[..., 3] = base_alpha  # final clip: the base's own dilated hex mask
    return Image.fromarray(arr, "RGBA")


def verify_against(out_dir: Path, ref_dir: Path) -> bool:
    """Byte-compare every tile in out_dir with its counterpart in ref_dir."""
    ok = True
    for out_path in sorted(out_dir.glob("*.png")):
        ref_path = ref_dir / out_path.name
        if not ref_path.exists():
            print(f"MISSING in reference: {ref_path}")
            ok = False
            continue
        a = np.asarray(Image.open(out_path).convert("RGBA"))
        b = np.asarray(Image.open(ref_path).convert("RGBA"))
        if a.shape != b.shape or not np.array_equal(a, b):
            diff = 0 if a.shape != b.shape else int((a != b).any(axis=-1).sum())
            print(f"DIFFERS: {out_path.name} ({diff} px)")
            ok = False
        else:
            print(f"identical: {out_path.name}")
    return ok


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Whole-hex vignette compositor (settlements, hill formations)."
    )
    ap.add_argument("--manifest", required=True, type=Path, help="art/foundry/manifest.json")
    ap.add_argument("--section", required=True, help="manifest section (hills, city, ...)")
    ap.add_argument("--out-dir", required=True, type=Path, help="directory for {nn}.png tiles")
    ap.add_argument("--sprites-dir", type=Path, help="also save the segmented vignette sprites")
    ap.add_argument(
        "--verify",
        type=Path,
        help="byte-compare the output against this directory of ratified tiles",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = json.loads(args.manifest.read_text())
    if args.section not in manifest:
        raise SystemExit(f"section {args.section!r} not in {args.manifest}")
    section = manifest[args.section]
    root = args.manifest.parent

    sheet_path = root / section["sprite_sheet"]
    sheet = np.asarray(Image.open(sheet_path).convert("RGB"))
    comp = section["compose"]
    k = int(comp["variants"])
    clusters = find_clusters(sheet, k)
    if len(clusters) < k:
        raise SystemExit(f"only found {len(clusters)} clusters in {sheet_path}, wanted {k}")

    bases = sorted((root / comp["bases"]).glob("*.png"))
    if not bases:
        raise SystemExit(f"no base tiles in {root / comp['bases']}")

    vig = section["vignette"]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.sprites_dir:
        args.sprites_dir.mkdir(parents=True, exist_ok=True)

    for i, (inside_lo, bbox) in enumerate(clusters):
        sprite = build_vignette(
            sheet, inside_lo, bbox, vig["softclip_above"], vig["softclip_factor"]
        )
        if "grass_match" in section:
            sprite = grass_match(sprite, section["grass_match"])
        if args.sprites_dir:
            sprite.save(args.sprites_dir / f"vignette_{i}.png")
        tile = compose_onto_base(
            sprite, bases[i % len(bases)], int(comp["tile_px"]), int(comp["feature_px"])
        )
        tile.save(args.out_dir / f"{i:02d}.png")
        arr = np.asarray(sprite).astype(float)
        op = arr[..., 3] > 128
        lum = (0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2])[op]
        print(f"{i:02d}: bbox={bbox} sprite={sprite.size} mean_lum={lum.mean():.1f}")

    if args.verify:
        if not verify_against(args.out_dir, args.verify):
            return 1
        print("verify: all tiles byte-identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
