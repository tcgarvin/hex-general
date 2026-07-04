#!/usr/bin/env python3
"""Bridge tile compositor: bake a top-down-with-a-little-lift bridge deck + road
ramps onto a ratified painterly river tile to produce the "bridge" oriented
terrain class (ROADMAP Phase 8, Slice T4).

A bridge tile = a ratified river base (`frontend/src/board/tiles/river/*.png`)
+ a deck sprite (segmented from a white-background NBP deck sheet, exactly the
white-sheet route used by woods/city/hills — see `vignette.py`) + baked road
ramps drawn deterministically to match the render-time vector road overlay
(`Board.tsx`: colour 0x6b4f2a, width 6 world px = 12 tile px, running from the
hex centre to the connected edge midpoints).

The deck sprites carry a *little* perspective lift (low railings along the long
edges, a thin shaded front face along the south side — the same register as the
woods/city sprites), so the sheet contains BOTH orientations drawn natively:
horizontal decks (span east-west) for `straight_v`, and vertical decks (span
north-south) for the two turns. **The compositor never rotates a sprite** —
rotating a perspective sprite would swing its lit front face onto a side.

Three shapes (flat-top hexes; edge index -> compass 0=se 1=ne 2=n 3=nw 4=sw
5=s, edge = midpoint of that hex side):

- turn_se_sw / turn_ne_nw  (shape "turn_ns"): a vertical N-S deck + straight
  ramps from the n edge midpoint down through the deck to the s edge midpoint;
  the deck is centred on the *measured* water crossing along the x=64 line
  (the two turn bases cross that line off-centre).
- straight_v            (shape "straight_v_sesw"): a horizontal deck across the
  vertical water at the crossing, with gently curved ramps from the se edge
  midpoint to the deck's east end and the sw edge midpoint to the deck's west
  end.

Geometry is *measured, not assumed*: the water pixels of the **unmodified** base
tile along the deck's crossing line set the deck's span and centre. Water is the
desaturated grey-teal band — classified on hue (roughly 82-200 deg) as well as
low saturation, so the warm low-saturation tan/brown banks are *not* mistaken
for water. The deck spans the water plus a few px of bank each side.

After compositing, an automated placement gate asserts that along the crossing
line every base-water pixel is covered by deck pixels and that no road pixel
sits on former water — a bad tile raises instead of being emitted.

The composite preserves the base tile's alpha exactly (the base's own 1px-
dilated hex mask), so bridge tiles remain seam-compatible with river tiles.

Every parameter (base, sheet, sprite bbox + index, deck size, ramp control
points, seed) is recorded in a per-candidate JSON sidecar so the manifest
section can be written verbatim at apply time.

Two modes:

  candidates  Segment a deck sheet and composite the full candidate set (with
              JSON sidecars) for review — the generation mode.
  build       Manifest-driven replay: regenerate the *applied* tiles
              byte-for-byte from the "bridge" manifest section (committed deck
              sheet + the sidecar params folded in) over the ratified river
              bases; `--verify` byte-compares against the applied tiles like
              `river.py build --verify` (the selftest hook).

Example:
    python3 bridge.py candidates --sheet art/foundry/bridge/sheets/deck-sheet-v2.png \\
        --bases-dir art/foundry/bridge/work/bases \\
        --out-dir art/foundry/bridge/review/candidates
    python3 bridge.py build --manifest art/foundry/manifest.json --section bridge \\
        --out-dir /tmp/bridge --verify frontend/src/board/tiles/bridge
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from cut import dilate_mask
from hexgeom import axial_to_pixel, hex_mask  # noqa: F401  (hex_mask kept for parity)
from vignette import build_vignette, find_clusters, rgb_to_hsv

# -- tile / geometry constants ---------------------------------------------
TILE_PX = 128
HEX_WIDTH = 128  # point-to-point width (== 2 * hex.ts HEX_SIZE at 2x tile scale)
CENTRE = TILE_PX / 2.0

# render-time vector road (Board.tsx): stroke width 6 world px, tile is 2x
# world scale, so 12 tile px; colour 0x6b4f2a.
ROAD_COLOR = (0x6B, 0x4F, 0x2A)
ROAD_WIDTH_PX = 12

# deck: a touch wider than the road so it visibly shoulders past it, spanning
# the water + a few px of bank each side.
DEFAULT_DECK_WIDTH_PX = 16
DEFAULT_BANK_PX = 4

# water classification. Measured against the actual river tiles: water sits at
# hue ~85-106 deg, sat ~0.16-0.21, val ~0.46-0.49; the tan/brown banks are also
# low-ish sat but WARM (hue ~40-47) and are excluded by the hue gate; grass is
# sat 0.5+. Hue gate is the load-bearing addition over a sat-only test.
WATER_HUE_MIN = 82.0
WATER_HUE_MAX = 200.0
WATER_SAT_MAX = 0.36
WATER_VAL_MIN = 0.20
WATER_VAL_MAX = 0.75

# white-sheet segmentation register (same as woods/city/hills sheets).
SOFTCLIP_ABOVE = 135.0
SOFTCLIP_FACTOR = 0.45
N_DECKS = 8  # 4 horizontal (top row) + 4 vertical (bottom row)

# contact shadow under the deck's long edges (helps it sit on the water).
SHADOW_ALPHA = 100
SHADOW_BLUR = 2.0
SHADOW_GROW_PX = 2

# painterly ramp tonal noise amplitude (subtle; a flat stroke is an acceptable
# fallback per the art direction, this just softens the vector look).
RAMP_NOISE_AMP = 7.0

# deck coverage threshold: a sprite pixel counts as "deck" above this alpha.
DECK_COVER_ALPHA = 40


class PlacementError(RuntimeError):
    """Raised when the composited deck fails to cover the water crossing."""


def edge_midpoint(direction_index: int) -> tuple[float, float]:
    """Tile-pixel midpoint of the hex side toward `direction_index`.

    The shared-edge midpoint is half the neighbour-centre offset from centre
    (adjacent hex centres are `HEX_WIDTH * 1` apart along the offset; the edge
    sits at the halfway point). Direction indices match hex.ts / the river
    tile mask: 0=se 1=ne 2=n 3=nw 4=sw 5=s.
    """
    from hexgeom import AXIAL_DIRECTIONS

    dq, dr = AXIAL_DIRECTIONS[direction_index]
    ox, oy = axial_to_pixel(dq, dr, HEX_WIDTH / 2)
    return (CENTRE + ox / 2.0, CENTRE + oy / 2.0)


def water_mask(base: np.ndarray) -> np.ndarray:
    """Boolean mask of desaturated grey-teal water pixels inside the hex.

    Classifies on hue as well as saturation/value: the banks are also low
    saturation but warm-hued, so a sat-only test folds them into "water" and
    mis-measures the crossing. Operates on the raw, unmodified base tile.
    """
    rgb = base[..., :3].astype(float) / 255.0
    alpha = base[..., 3]
    hue, sat, val = rgb_to_hsv(rgb)
    return (
        (alpha > 128)
        & (hue >= WATER_HUE_MIN)
        & (hue <= WATER_HUE_MAX)
        & (sat < WATER_SAT_MAX)
        & (val > WATER_VAL_MIN)
        & (val < WATER_VAL_MAX)
    )


def _contiguous_run(flags: np.ndarray) -> tuple[int, int]:
    """Longest contiguous True run in a 1-D boolean array -> (lo, hi) inclusive.

    Raises if there is no True pixel — a bridge base with no water on its
    crossing line is a bad input, not a state to paper over.
    """
    idx = np.nonzero(flags)[0]
    if len(idx) == 0:
        raise ValueError("no water found on the deck crossing line")
    best_lo = best_hi = idx[0]
    run_lo = idx[0]
    prev = idx[0]
    for i in idx[1:]:
        if i == prev + 1:
            prev = i
        else:
            if prev - run_lo > best_hi - best_lo:
                best_lo, best_hi = run_lo, prev
            run_lo = prev = i
    if prev - run_lo > best_hi - best_lo:
        best_lo, best_hi = run_lo, prev
    return int(best_lo), int(best_hi)


def measure_crossing(base: np.ndarray, shape: str) -> dict:
    """Measure the water span/centre along the deck's crossing line.

    - turn_ns: vertical deck at x=CENTRE -> water y-extent (scan a 3-px strip).
    - straight_v_sesw: horizontal deck at y=CENTRE -> water x-extent.
    """
    wm = water_mask(base)
    c = int(round(CENTRE))
    if shape == "turn_ns":
        strip = wm[:, c - 1 : c + 2].any(axis=1)
        lo, hi = _contiguous_run(strip)
        return {"axis": "y", "lo": lo, "hi": hi, "span": hi - lo + 1, "centre": (lo + hi) / 2.0}
    if shape == "straight_v_sesw":
        strip = wm[c - 2 : c + 3, :].any(axis=0)
        lo, hi = _contiguous_run(strip)
        return {"axis": "x", "lo": lo, "hi": hi, "span": hi - lo + 1, "centre": (lo + hi) / 2.0}
    raise ValueError(f"unknown shape {shape!r}")


def debug_overlay(base_path: Path, shape: str, out_path: Path) -> dict:
    """Save a 4x NEAREST overlay of the water mask + crossing line + measured run.

    Water pixels tinted red, the crossing centre line drawn blue, the measured
    run endpoints marked green. Returns the crossing dict so callers can print.
    """
    base = np.asarray(Image.open(base_path).convert("RGBA"))
    cross = measure_crossing(base, shape)
    wm = water_mask(base)
    vis = base[..., :3].copy()
    vis[wm] = (0.4 * vis[wm] + 0.6 * np.array([230, 40, 40])).astype(np.uint8)
    c = int(round(CENTRE))
    if shape == "turn_ns":
        vis[:, c] = [40, 120, 230]
        vis[cross["lo"], c - 4 : c + 5] = [40, 230, 40]
        vis[cross["hi"], c - 4 : c + 5] = [40, 230, 40]
    else:
        vis[c, :] = [40, 120, 230]
        vis[c - 4 : c + 5, cross["lo"]] = [40, 230, 40]
        vis[c - 4 : c + 5, cross["hi"]] = [40, 230, 40]
    img = Image.fromarray(vis, "RGB").resize((TILE_PX * 4, TILE_PX * 4), Image.NEAREST)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return cross


def segment_decks(sheet_path: Path) -> list[dict]:
    """Segment the white-background deck sheet into 8 sprites, in a stable order.

    `find_clusters` sorts row-major with a fixed 512-px band, which is fragile
    when a row's centres straddle a band boundary. We re-sort deterministically
    by half (top row first, then bottom) and then by x, so the returned order is
    always: 0-3 = top-row (horizontal) left-to-right, 4-7 = bottom-row
    (vertical) left-to-right. Uses the same white-sheet lift as the vignette
    terrains (hole-filled, forced-opaque interior, un-matted).
    """
    sheet = np.asarray(Image.open(sheet_path).convert("RGB"))
    clusters = find_clusters(sheet, N_DECKS)
    if len(clusters) < N_DECKS:
        raise ValueError(f"found only {len(clusters)} decks in {sheet_path}, wanted {N_DECKS}")
    half = sheet.shape[0] / 2.0

    def sort_key(item: tuple[np.ndarray, tuple[int, int, int, int]]) -> tuple[int, float]:
        _, (x0, y0, x1, y1) = item
        cy = (y0 + y1) / 2.0
        cx = (x0 + x1) / 2.0
        return (0 if cy < half else 1, cx)

    clusters = sorted(clusters, key=sort_key)
    out = []
    for i, (inside_lo, bbox) in enumerate(clusters):
        sprite = build_vignette(sheet, inside_lo, bbox, SOFTCLIP_ABOVE, SOFTCLIP_FACTOR)
        out.append({"index": i, "bbox": [int(v) for v in bbox], "sprite": sprite})
    return out


def build_deck_image(sprite: Image.Image, length_px: int, width_px: int, vertical: bool) -> Image.Image:
    """Scale a natively-oriented deck sprite to its span x width footprint.

    `length_px` is the crossing span; `width_px` the road-matching width. The
    sprite is already drawn in the correct orientation (horizontal or vertical),
    so we only resize — **never rotate** (that would swing the lit front face).
    """
    if vertical:
        return sprite.resize((max(1, width_px), max(1, length_px)), Image.LANCZOS)
    return sprite.resize((max(1, length_px), max(1, width_px)), Image.LANCZOS)


def _bezier(p0, p1, p2, steps: int = 40) -> list[tuple[float, float]]:
    """Quadratic Bezier sample points (inclusive of both endpoints)."""
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
        y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
        pts.append((x, y))
    return pts


def draw_ramp(draw: ImageDraw.ImageDraw, pts: list[tuple[float, float]], width: int) -> None:
    """Draw a thick round-capped road ramp along a polyline."""
    draw.line(pts, fill=ROAD_COLOR, width=width, joint="curve")
    r = width / 2.0
    for cap in (pts[0], pts[-1]):
        draw.ellipse([cap[0] - r, cap[1] - r, cap[0] + r, cap[1] + r], fill=ROAD_COLOR)


def ramp_paths(shape: str, deck_centre: tuple[float, float], deck_len: int) -> list[dict]:
    """Ramp polylines/beziers from edge midpoints to the deck ends.

    Returns a list of `{"edge", "control_points", "points"}` records so the
    exact geometry lands in the sidecar. Endpoints hit the edge midpoints
    precisely (neighbouring road half-segments terminate there); the deck ends
    are the sprite's actual top/bottom (or east/west) extents, so the deck —
    composited on top of the ramps — covers the join and the road appears to
    stop at the deck's front face rather than running over it.
    """
    cx, cy = deck_centre
    ramps: list[dict] = []
    if shape == "turn_ns":
        top_end = (cx, cy - deck_len / 2.0)
        bot_end = (cx, cy + deck_len / 2.0)
        for edge, deck_end in ((2, top_end), (5, bot_end)):
            mid = edge_midpoint(edge)
            pts = [deck_end, mid]
            ramps.append({"edge": edge, "control_points": [], "points": pts})
    elif shape == "straight_v_sesw":
        east_end = (cx + deck_len / 2.0, cy)
        west_end = (cx - deck_len / 2.0, cy)
        for edge, deck_end in ((0, east_end), (4, west_end)):
            mid = edge_midpoint(edge)
            # gentle quarter-curve: leave the deck horizontally, arrive at the
            # midpoint from directly above it (control at (mid_x, deck_y)).
            ctrl = (mid[0], cy)
            pts = _bezier(deck_end, ctrl, mid)
            ramps.append({"edge": edge, "control_points": [ctrl], "points": pts})
    else:
        raise ValueError(f"unknown shape {shape!r}")
    return ramps


def deck_coverage(deck_img: Image.Image, pos: tuple[int, int]) -> np.ndarray:
    """Full-tile boolean mask of where the deck sprite covers (alpha > thresh)."""
    canvas = Image.new("L", (TILE_PX, TILE_PX), 0)
    canvas.paste(deck_img.split()[3], pos)
    return np.asarray(canvas) > DECK_COVER_ALPHA


def contact_shadow(deck_img: Image.Image, pos: tuple[int, int]) -> Image.Image:
    """A soft dark footprint just larger than the deck (fake contact shadow)."""
    layer = Image.new("RGBA", (TILE_PX, TILE_PX), (0, 0, 0, 0))
    alpha = np.asarray(deck_img)[..., 3]
    grown = dilate_mask(alpha > DECK_COVER_ALPHA, SHADOW_GROW_PX)
    sh = np.zeros((deck_img.height, deck_img.width, 4), dtype=np.uint8)
    sh[..., 3] = np.where(grown, SHADOW_ALPHA, 0).astype(np.uint8)
    layer.alpha_composite(Image.fromarray(sh, "RGBA"), pos)
    return layer.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))


def add_ramp_noise(tile: Image.Image, ramp_mask: np.ndarray, seed: int) -> None:
    """Subtle seeded tonal noise on the ramp pixels (painterly softening)."""
    rng = np.random.default_rng(seed)
    arr = np.asarray(tile).astype(float)
    noise = rng.normal(0.0, RAMP_NOISE_AMP, size=ramp_mask.shape)
    for ch in range(3):
        arr[..., ch] = np.where(ramp_mask, np.clip(arr[..., ch] + noise, 0, 255), arr[..., ch])
    tile.paste(Image.fromarray(arr.astype(np.uint8), "RGBA"))


def placement_gate(base: np.ndarray, shape: str, deck_cov: np.ndarray, ramp_mask: np.ndarray) -> None:
    """Fail loud unless the deck spans the water and no road sits on the water.

    1. Along the crossing centre line, every base-water pixel must be covered by
       deck pixels (otherwise the road crosses bare water — the v1 bug).
    2. No road/ramp pixel may remain visible on a former-water pixel (the deck,
       composited on top, must cover any ramp-water overlap).
    """
    wm = water_mask(base)
    c = int(round(CENTRE))
    if shape == "turn_ns":
        line_water = np.zeros(TILE_PX, dtype=bool)
        line_water_idx = np.nonzero(wm[:, c])[0]
        line_cov = deck_cov[:, c]
    else:
        line_water_idx = np.nonzero(wm[c, :])[0]
        line_cov = deck_cov[c, :]
        line_water = np.zeros(TILE_PX, dtype=bool)
    line_water[line_water_idx] = True
    uncovered = line_water & ~line_cov
    if uncovered.any():
        n = int(uncovered.sum())
        raise PlacementError(
            f"{shape}: deck does not span the water crossing — {n} water px on the "
            f"crossing line are uncovered by the deck (idx {np.nonzero(uncovered)[0].tolist()})"
        )

    road_on_water = ramp_mask & wm & ~deck_cov
    if road_on_water.any():
        n = int(road_on_water.sum())
        raise PlacementError(
            f"{shape}: {n} road/ramp px sit on former-water pixels not covered by the deck"
        )


def compose_bridge(
    base_path: Path, sprite: Image.Image, shape: str, deck_width_px: int, bank_px: int, seed: int
) -> tuple[Image.Image, dict]:
    """Composite one bridge tile; return (tile, sidecar-params). Gated."""
    base = np.asarray(Image.open(base_path).convert("RGBA"))
    base_alpha = base[..., 3].copy()
    cross = measure_crossing(base, shape)

    deck_len = int(round(cross["span"] + 2 * bank_px))
    vertical = shape == "turn_ns"
    if vertical:
        deck_centre = (CENTRE, cross["centre"])
    else:
        deck_centre = (cross["centre"], CENTRE)

    deck_img = build_deck_image(sprite, deck_len, deck_width_px, vertical)
    pos = (
        int(round(deck_centre[0] - deck_img.width / 2.0)),
        int(round(deck_centre[1] - deck_img.height / 2.0)),
    )
    deck_cov = deck_coverage(deck_img, pos)

    tile = Image.open(base_path).convert("RGBA")

    # 1. ramps (under the deck ends), on a dedicated layer so we can mask noise
    ramp_layer = Image.new("RGBA", (TILE_PX, TILE_PX), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ramp_layer)
    ramps = ramp_paths(shape, deck_centre, deck_len)
    for ramp in ramps:
        draw_ramp(rd, ramp["points"], ROAD_WIDTH_PX)
    ramp_mask = np.asarray(ramp_layer)[..., 3] > 0
    add_ramp_noise(ramp_layer, ramp_mask, seed)
    tile.alpha_composite(ramp_layer)

    # 2. soft contact shadow, then 3. the deck
    tile.alpha_composite(contact_shadow(deck_img, pos))
    tile.alpha_composite(deck_img, pos)

    # 4. placement gate — fail loud before we ever emit a bad tile
    placement_gate(base, shape, deck_cov, ramp_mask)

    # 5. reclip to the base's own alpha exactly (seam-compatible with river)
    arr = np.asarray(tile).copy()
    arr[..., 3] = base_alpha
    tile = Image.fromarray(arr, "RGBA")

    sidecar = {
        "shape": shape,
        "base": str(base_path),
        "seed": seed,
        "deck": {
            "width_px": deck_width_px,
            "length_px": deck_len,
            "bank_px": bank_px,
            "centre": [round(deck_centre[0], 2), round(deck_centre[1], 2)],
            "vertical": vertical,
            "rotation_deg": 0,
            "paste_pos": list(pos),
            "size": [deck_img.width, deck_img.height],
        },
        "water_crossing": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in cross.items()},
        "ramps": [
            {
                "edge": r["edge"],
                "edge_midpoint": [round(v, 2) for v in edge_midpoint(r["edge"])],
                "control_points": [[round(c, 2) for c in cp] for cp in r["control_points"]],
                "endpoints": [
                    [round(r["points"][0][0], 2), round(r["points"][0][1], 2)],
                    [round(r["points"][-1][0], 2), round(r["points"][-1][1], 2)],
                ],
                "width_px": ROAD_WIDTH_PX,
                "color": "0x6b4f2a",
            }
            for r in ramps
        ],
        "road_noise_amp": RAMP_NOISE_AMP,
        "shadow": {"alpha": SHADOW_ALPHA, "blur": SHADOW_BLUR, "grow_px": SHADOW_GROW_PX},
    }
    return tile, sidecar


# Candidate set: shape + base + deck-sprite selection per style family. Deck
# indices are the stable segment_decks order: 0-3 = horizontal decks (top row,
# wood_a wood_b stone_a stone_b) for straight_v; 4-7 = vertical decks (bottom
# row, wood_a wood_b stone_a stone_b) for the turns.
CANDIDATES = [
    # turn_se_sw (base turn_se_sw_00, vertical N-S deck)
    ("turn_se_sw_wood_a", "turn_ns", "turn_se_sw_00.png", 4, "wood", 4401),
    ("turn_se_sw_wood_b", "turn_ns", "turn_se_sw_00.png", 5, "wood", 4402),
    ("turn_se_sw_stone_a", "turn_ns", "turn_se_sw_00.png", 6, "stone", 4403),
    ("turn_se_sw_stone_b", "turn_ns", "turn_se_sw_00.png", 7, "stone", 4404),
    # turn_ne_nw (base turn_ne_nw_00, vertical N-S deck)
    ("turn_ne_nw_wood_a", "turn_ns", "turn_ne_nw_00.png", 4, "wood", 4411),
    ("turn_ne_nw_wood_b", "turn_ns", "turn_ne_nw_00.png", 5, "wood", 4412),
    ("turn_ne_nw_stone_a", "turn_ns", "turn_ne_nw_00.png", 6, "stone", 4413),
    ("turn_ne_nw_stone_b", "turn_ns", "turn_ne_nw_00.png", 7, "stone", 4414),
    # straight_v (base straight_v_00, horizontal deck across the vertical water)
    ("straight_v_wood_a", "straight_v_sesw", "straight_v_00.png", 0, "wood", 4421),
    ("straight_v_wood_b", "straight_v_sesw", "straight_v_00.png", 1, "wood", 4422),
    ("straight_v_stone_a", "straight_v_sesw", "straight_v_00.png", 2, "stone", 4423),
    ("straight_v_stone_b", "straight_v_sesw", "straight_v_00.png", 3, "stone", 4424),
]


def run_candidates(args: argparse.Namespace) -> int:
    """Original mode: segment the sheet and composite the full candidate set."""
    args.out_dir.mkdir(parents=True, exist_ok=True)

    decks = segment_decks(args.sheet)
    if args.sprites_dir:
        args.sprites_dir.mkdir(parents=True, exist_ok=True)
        for d in decks:
            d["sprite"].save(args.sprites_dir / f"deck_{d['index']:02d}.png")

    # sanity-check the water detector on each base before compositing
    if args.debug_dir:
        seen = {}
        for _, shape, base_name, *_ in CANDIDATES:
            if base_name in seen:
                continue
            cross = debug_overlay(
                args.bases_dir / base_name, shape, args.debug_dir / f"water-{base_name}"
            )
            seen[base_name] = cross
            print(f"water run {base_name} ({shape}): {cross}")

    for name, shape, base_name, deck_index, style, seed in CANDIDATES:
        base_path = args.bases_dir / base_name
        deck = decks[deck_index]
        tile, sidecar = compose_bridge(base_path, deck["sprite"], shape, args.deck_width, args.bank, seed)
        out_path = args.out_dir / f"{name}.png"
        tile.save(out_path)
        sidecar.update({
            "name": name,
            "style": style,
            "sheet": str(args.sheet),
            "sprite_index": deck_index,
            "sprite_bbox": deck["bbox"],
        })
        (args.out_dir / f"{name}.json").write_text(json.dumps(sidecar, indent=2) + "\n")
        print(
            f"wrote {out_path.name}  (deck {deck_index} {style}, "
            f"span {sidecar['water_crossing']['span']} -> len {sidecar['deck']['length_px']}, "
            f"gate OK)"
        )

    return 0


# ---------------------------------------------------------------- build ----


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


def run_build(args: argparse.Namespace) -> int:
    """Manifest-driven replay: regenerate the applied bridge tiles byte-for-byte.

    Reads the "bridge" manifest section — the committed combined deck sheet plus
    the per-tile params folded in from each applied candidate's sidecar (base
    river tile, deck-sprite index, deck width/bank, seed) — and re-runs the exact
    `segment_decks` + `compose_bridge` path that produced the ratified tiles. All
    paths resolve relative to the manifest's parent (art/foundry). `--verify`
    byte-compares the output against the applied tiles, the selftest hook.
    """
    manifest = json.loads(args.manifest.read_text())
    if args.section not in manifest:
        raise SystemExit(f"section {args.section!r} not in {args.manifest}")
    section = manifest[args.section]
    root = args.manifest.parent

    decks = segment_decks(root / section["sheet"])
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for t in section["tiles"]:
        base_path = root / t["base"]
        deck = decks[t["sprite_index"]]
        if deck["bbox"] != t["sprite_bbox"]:
            print(
                f"SHEET DRIFT: sprite {t['sprite_index']} bbox {deck['bbox']} != "
                f"recorded {t['sprite_bbox']} for {t['tile']}"
            )
            return 1
        tile, sidecar = compose_bridge(
            base_path, deck["sprite"], t["shape"], t["deck_width_px"], t["bank_px"], t["seed"]
        )
        tile.save(args.out_dir / t["tile"])
        print(
            f"{t['tile']}: deck {t['sprite_index']} over {t['base']}, "
            f"span {sidecar['water_crossing']['span']} -> len {sidecar['deck']['length_px']}, gate OK"
        )

    if args.verify:
        if not verify_against(args.out_dir, args.verify):
            return 1
        print("verify: all tiles byte-identical")
    return 0


# ------------------------------------------------------------------ CLI ----


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Composite painterly bridge tiles from river bases + deck sprites."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("candidates", help="segment a deck sheet and composite the full candidate set")
    c.add_argument("--sheet", required=True, type=Path, help="white-background deck sprite sheet")
    c.add_argument("--bases-dir", required=True, type=Path, help="directory holding the river base tiles")
    c.add_argument("--out-dir", required=True, type=Path, help="output directory for candidate tiles + sidecars")
    c.add_argument("--sprites-dir", type=Path, default=None, help="also dump the segmented deck sprites here")
    c.add_argument("--debug-dir", type=Path, default=None, help="dump water-detector overlays for the bases here")
    c.add_argument("--deck-width", type=int, default=DEFAULT_DECK_WIDTH_PX, help=f"deck width px (default {DEFAULT_DECK_WIDTH_PX})")
    c.add_argument("--bank", type=int, default=DEFAULT_BANK_PX, help=f"bank px each side of the water (default {DEFAULT_BANK_PX})")

    b = sub.add_parser("build", help="manifest-driven replay of the applied bridge tiles")
    b.add_argument("--manifest", required=True, type=Path, help="art/foundry/manifest.json")
    b.add_argument("--section", default="bridge", help="manifest section (default bridge)")
    b.add_argument("--out-dir", required=True, type=Path)
    b.add_argument(
        "--verify",
        type=Path,
        help="byte-compare the output against this directory of applied tiles",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cmd == "candidates":
        return run_candidates(args)
    return run_build(args)


if __name__ == "__main__":
    raise SystemExit(main())
