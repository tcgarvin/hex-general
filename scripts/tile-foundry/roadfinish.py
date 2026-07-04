#!/usr/bin/env python3
"""NBP road-finishing production driver (ROADMAP Phase 8, Slice T5).

The deterministic road foundry (``road.py``) bakes a flat 12px painterly ribbon
onto the ratified terrain bases. That ribbon reads as *pasted on top* — it does
not wear into the ground or grade over hill relief. This driver runs the
Nano-Banana-Pro (NBP = ``gemini-3-pro-image-preview`` on Vertex AI, keyless)
image-to-image *finish* pass proven in two prior experiment passes
(``art/foundry/road/review/experiment/``), then a deterministic post-chain that
tames NBP's two known failure modes — it blows road **width** up (18-24px vs the
12px input) and freelances road **colour** per generation — and pins the grassy
rim + crossings back to the deterministic input so tiles still abut invisibly.

## Pipeline (per generation)

    deterministic input composite (road.compose_road, flat treatment)
      -> NBP repaint (gen.py Vertex path; input composite + terrain refs)
      -> RAW gen saved verbatim  (the committed, reproducible "source")
      -> post-chain (deterministic, byte-reproducible from the raw):
           1. resize raw -> 128x128, reclip alpha to the input's mask
           2. road-mask recolour: classify the repaint's road pixels, palette-
              match them to the input ribbon's palette — hue + saturation matched
              to the ribbon median, value swept GENTLY (preserve NBP texture
              contrast). Kills the crossing colour splice AND tile-to-tile road
              colour variance.
           3. rim blend b08 (rimblend smoothstep) input<->recoloured: rim stays
              ratified-plains, crossings pinned to the deterministic 12px.
           4. gates (auto-reject; rejects kept for audit).

Only the RAW gen is non-reproducible; it is what gets committed (like the
vignette sheets). ``build`` replays the deterministic post-chain from a committed
raw byte-for-byte.

## Gates (auto-reject; a rejected gen still gets a sidecar)

- ``drift``  pre-blend road-centre drift <= 2px per connected edge crossing.
- ``width``  post-recolour mid-tile road CORE width <= 20px. Measured as the
             contiguous run of the *tight* core mask (brown hue, high sat — the
             packed dirt, excluding the soft anti-aliased grassy verge that
             inflates a naive width) along the perpendicular to the local
             centreline at the tile centre.
- ``rim``    post-blend grass-rim mean |dRGB| <= 2.5 per channel over the 8px
             inner ring (grass only; road pixels excluded).
- ``lum``    post-blend opaque-pixel mean luminance within the terrain's concept
             anchor band (grass 105.3) +/- 12.
- ``phantom`` (end shapes only) no cleaned road mass in 5px discs 12/18px inward
             of any NON-connected edge midpoint (fraction <= 0.25) — catches NBP
             extending an end cap into a phantom through-road.

## Recolour (the new production step)

``road.build_road_layer`` gives the exact deterministic ribbon; its pixels under
the input composite are the palette target (median HSV). The repaint's road is
classified by hue+saturation (generous mask, morphologically cleaned, hex-core
clipped). For the masked pixels: hue set to the ribbon hue (constant — kills
per-gen hue freelancing), saturation mean-matched to the ribbon (deviation
preserved), value mean-swept toward the ribbon with ``VALUE_STRENGTH`` (gentle,
so NBP's rut/dirt contrast survives). All params recorded per sidecar.

## ``road_nbp`` manifest section schema (consumed by ``build``)

Written at apply time from the ratified survivors' sidecars (this driver does NOT
edit the manifest). ``build`` reads it and replays the post-chain from the
committed raw gens, byte-for-byte::

    "road_nbp": {
      "class": "<prose provenance>",
      "model": "gemini-3-pro-image-preview",
      "post_chain": {
        "blend_px": 8,
        "recolour": {"hue_strength": 1.0, "sat_strength": 1.0, "value_strength": 0.5,
                     "recolour_hue": [6, 42], "recolour_smin": 25,
                     "core_hue": [8, 38], "core_smin": 60, "morph_size": 3}
      },
      "geometry": { ... road.py geometry constants, for provenance ... },
      "shapes": { "<shape>": [<edge indices>], ... },
      "tiles": [
        { "tile": "plains_straight_v_00.png",        # apply-time basename
          "terrain": "plains", "shape": "straight_v", "variant": "00",
          "base": "plains/picks/00.png",             # rel. to manifest parent
          "treatment": "flat", "seed": 71234, "gen": "g1",
          "source": "road/gens128/plains_straight_v_00.png",  # committed 128px source
          "raw": "road/production/gens/plains_straight_v_00__g1__raw.png",  # uncommitted
          "recolour_target": {"h": 24, "s": 154, "v": 107},  # ribbon median HSV
          "gate": {"core_width_px": 13.5, ...} },    # measurements (+ gate_waiver if any)
        ...
      ]
    }

Each tile's apply target is ``frontend/src/board/tiles/road/{tile}``. All
post-chain params live in the section (not the module) so a future constant
change can't silently break replay: ``build`` recomputes the input composite
deterministically (``road.compose_road`` over ``base``), takes the committed
128px source (or resize/reclips the raw), recolours with the section's params +
stored target, blends, and writes the tile.

## Committed source: the 128px intermediate (``gens128``), not the 2K raw

The 2K raw NBP gens (~210MB) are NOT committed. The committed per-tile source is
the **128×128 post-resize intermediate** (raw → LANCZOS resize → alpha-reclip to
the input mask, i.e. ``resize_reclip`` — the pipeline state immediately before
recolour). Everything downstream (recolour → rim-blend b08) is deterministic, so
byte-replay works from these ~30KB files. Each manifest tile carries both a
``source`` (``road/gens128/{tile}``, the committed truth ``build`` consumes) and a
``raw`` (``road/production/gens/{stem}__raw.png``, provenance + the regeneration
input for ``export-sources``). ``build`` prefers ``source``; it falls back to
``raw`` + ``resize_reclip`` only when no ``source`` is listed.

Three subcommands::

    # production loop (burns NBP calls; hard-capped)
    python3 roadfinish.py generate --out-dir art/foundry/road/production \\
        --bases-root frontend/src/board/tiles --max-calls 64

    # regenerate the committed 128px sources from the (uncommitted) 2K raws —
    # run once, pre-commit, with the raws present on disk
    python3 roadfinish.py export-sources --manifest art/foundry/manifest.json \\
        --section road_nbp

    # manifest replay of the applied tiles from gens128 (--verify byte-compares)
    python3 roadfinish.py build --manifest art/foundry/manifest.json \\
        --section road_nbp --out-dir /tmp/roadnbp --verify frontend/src/board/tiles/road
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import gen
import rimblend
import road
from bridge import edge_midpoint
from hexgeom import hex_mask

# ----------------------------------------------------------------- constants

TILE_PX = 128

# Concept-anchor luminance band (docs/concept-art/concept1.png). Plains and the
# grassy-hills bases both sit on the grass anchor.
GRASS_MEAN_LUM = 105.3
LUM_BAND = 12.0

# Post-chain params (recorded into every sidecar and the manifest section).
BLEND_PX = 8.0
HUE_STRENGTH = 1.0       # 1.0 => road hue set constant to the ribbon hue
SAT_STRENGTH = 1.0       # 1.0 => road sat mean-matched to the ribbon exactly
VALUE_STRENGTH = 0.5     # gentle value sweep; preserves NBP texture contrast
MORPH_SIZE = 3           # PIL close+open kernel for the recolour mask

# Road-pixel classifiers (PIL HSV, all channels 0-255; hue 0=red ~grass=55).
RECOLOUR_HUE = (6, 42)   # generous brown band -> everything we recolour
RECOLOUR_SMIN = 25
CORE_HUE = (8, 38)       # tight brown band -> the packed-dirt core for the width gate
CORE_SMIN = 60

# End shapes only: max cleaned-road-mask fraction in 5px discs placed 12/18px
# inward of each NON-connected edge midpoint. Catches NBP extending an end cap
# into a phantom through-road (calibrated on this round: real phantoms measure
# 0.39-1.0, clean hills tiles <= 0.12 from rocky-crest pixels reading as brown).
PHANTOM_FRAC_MAX = 0.25
PHANTOM_DEPTHS = (12.0, 18.0)
PHANTOM_DISC_R = 5.0

# Gate tolerances.
DRIFT_TOL_PX = 2.0
CORE_WIDTH_MAX_PX = 20.0
RIM_DELTA_MAX = 2.5
RIM_PX = 8

# NBP call config.
MODEL = "gemini-3-pro-image-preview"
IMAGE_SIZE = "2K"
ASPECT = "1:1"
MAX_WORKERS = 6
HARD_CAP = 64

# Terrain references (painterly register anchors), relative to the foundry root.
REFS = {
    "plains": ["refs/ref_1_x384_y256.png"],
    "hills": ["refs/hills_a.png", "refs/hills_b.png"],
}

# ---- generation matrix ----------------------------------------------------
# (terrain, shape, n_pick_slots). Base-variant index == pick-slot index, clamped
# to the number of ratified bases; diversity comes from the base + NBP
# stochasticity. Ordered by budget priority (earlier survives a tight cap).
MATRIX: list[tuple[str, str, int]] = [
    ("plains", "straight_v", 3),
    ("plains", "straight_d", 3),
    ("plains", "turn_n_se", 2),
    ("plains", "turn_s_nw", 2),
    ("plains", "turn_se_sw", 2),
    ("plains", "turn_ne_nw", 2),
    ("hills", "straight_v", 1),
    ("hills", "straight_d", 1),
    ("hills", "turn_n_se", 1),
    ("hills", "turn_s_nw", 1),
    ("hills", "turn_se_sw", 1),
    ("hills", "turn_ne_nw", 1),
    ("plains", "end_se", 1),
    ("plains", "end_ne", 1),
    ("plains", "end_n", 1),
    ("plains", "end_s", 1),
    ("hills", "end_se", 1),
    ("hills", "end_ne", 1),
    ("hills", "end_n", 1),
    ("hills", "end_s", 1),
]
ROLLS_PER_SLOT = 2


class RoadFinishError(RuntimeError):
    """Raised on a boundary / invariant violation in the finishing pipeline."""


def _rel(path: Path, root: Path) -> str:
    """Path relative to ``root`` when nested under it, else the absolute path."""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


# =============================================================== prompt build


def _shape_clause(shape: str) -> str:
    edges = road.SHAPES[shape]
    if len(edges) == 1:
        return (
            "The tile shows a single dirt road that enters from one hexagon edge, "
            "runs a short way in, and ends within the tile"
        )
    circ = min((edges[1] - edges[0]) % 6, (edges[0] - edges[1]) % 6)
    if circ == 3:
        return "The tile shows a single dirt road running straight across it, edge to edge"
    return "The tile shows a single dirt road curving gently through it, edge to edge"


_PLAINS_BODY = (
    "wear the road naturally into the ground — soft blended earthen shoulders, "
    "subtle wheel ruts and worn packed dirt along its length, a few blades of "
    "grass encroaching at the verge — so it reads as a real worn dirt track, not "
    "a flat ribbon pasted on top. Keep the grass calm and smooth: calm via "
    "smoothness, not paleness; keep the green saturated and mid-toned."
)
_HILLS_BODY = (
    "make the road INTERACT WITH THE HILL RELIEF instead of lying flat on top: "
    "where the road meets a mound, grade it into the slope as a shallow cutting "
    "— a shaded earthen bank on the uphill side, a little worn soil spilling to "
    "the downhill side, the road surface following the ground it crosses. The "
    "grassy mound is the subject: grade the road over and around it, do not erase "
    "it — keep the rounded ridges and rocky crests. Soft blended shoulders, "
    "subtle ruts and packed dirt."
)


def build_prompt(terrain: str, shape: str) -> str:
    """Compose the NBP finish prompt: experiment register + T5 width/colour/stability."""
    body = _PLAINS_BODY if terrain == "plains" else _HILLS_BODY
    register = "grass reference's" if terrain == "plains" else "hill references'"
    return (
        f"Repaint this top-down strategy-wargame hex tile in a painterly retro "
        f"16-bit style matching the {register} technique and palette. "
        f"{_shape_clause(shape)}. Finish it: {body} "
        # --- T5 width language ---
        "The road must stay EXACTLY as wide as the input's ribbon — a narrow "
        "single-lane dirt track; do not widen it, do not add extra lanes or a "
        "broad shoulder, do not flare it at the edges. "
        # --- T5 colour language ---
        "Keep the road's earthy brown close to the input ribbon's colour — the "
        "same warm packed-dirt brown; do not tint it grey, orange-red, or pale, "
        "and do not change its colour from one part of the road to another. "
        # --- centreline / geometry lock (from the experiment prompt) ---
        "Keep the road's exact centreline, its exact width, and the exact points "
        "where it meets each hexagon edge UNCHANGED — do not move, widen, narrow, "
        "reroute, bend, or add branches or forks to the road; it must still cross "
        "the same edges at the same spots. "
        # --- T5 stability language ---
        "Do not add, remove, move, or resize any terrain features; leave the "
        "grass near the hexagon border untouched. "
        "Straight overhead top-down view, flat even daylight, no long cast "
        "shadows. Render it as a finished game map tile in the same painterly "
        "retro 16-bit register as the reference: no hexagon grid lines, no border "
        "or outline, no vignette or edge darkening, no text, no UI, no units. "
        "Keep the outer rim of terrain around the hexagon edge exactly as in the "
        "input so the tile still abuts its neighbours. Output the same hexagonal "
        "tile at the same scale, framing and orientation; leave the four corners "
        "outside the hexagon empty."
    )


# ================================================================ post-chain


def _hsv(rgb_img: Image.Image) -> np.ndarray:
    """PIL HSV array (uint8 H,S,V) from an RGB(A) image."""
    return np.asarray(rgb_img.convert("RGB").convert("HSV")).astype(np.int16)


def resize_reclip(raw_img: Image.Image, input_alpha: np.ndarray) -> Image.Image:
    """Resize the raw NBP output to 128px and reclip its alpha to the input mask."""
    resized = raw_img.convert("RGB").resize((TILE_PX, TILE_PX), Image.LANCZOS)
    arr = np.dstack([np.asarray(resized), input_alpha]).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def _classify(hsv: np.ndarray, alpha: np.ndarray, hue: tuple[int, int], smin: int) -> np.ndarray:
    h, s = hsv[..., 0], hsv[..., 1]
    return (alpha > 0) & (h >= hue[0]) & (h <= hue[1]) & (s >= smin)


def _morph_clean(mask: np.ndarray, size: int) -> np.ndarray:
    """Binary close (fill) then open (despeckle) via PIL min/max filters."""
    img = Image.fromarray((mask * 255).astype(np.uint8), "L")
    img = img.filter(ImageFilter.MaxFilter(size)).filter(ImageFilter.MinFilter(size))  # close
    img = img.filter(ImageFilter.MinFilter(size)).filter(ImageFilter.MaxFilter(size))  # open
    return np.asarray(img) > 127


def recolour_mask(hsv: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Generous, cleaned, hex-core-clipped road mask used for recolouring."""
    raw = _classify(hsv, alpha, RECOLOUR_HUE, RECOLOUR_SMIN)
    core = hex_mask(TILE_PX, TILE_PX) > 128
    return _morph_clean(raw, MORPH_SIZE) & core


def ribbon_target(terrain: str, shape: str, seed: int, input_img: Image.Image) -> dict[str, int]:
    """Median HSV of the deterministic ribbon's pixels in the input composite."""
    layer, _ = road.build_road_layer(shape, "flat", seed)
    ribbon = np.asarray(layer)[..., 3] > 0
    hsv = _hsv(input_img)
    return {
        "h": int(np.median(hsv[..., 0][ribbon])),
        "s": int(np.median(hsv[..., 1][ribbon])),
        "v": int(np.median(hsv[..., 2][ribbon])),
    }


def apply_recolour(pp: Image.Image, mask: np.ndarray, target: dict[str, int]) -> tuple[Image.Image, dict]:
    """Palette-match the masked road pixels toward the ribbon target.

    Hue set constant to the ribbon hue (HUE_STRENGTH=1); saturation mean-matched
    (SAT_STRENGTH=1, per-pixel deviation preserved); value mean-swept gently
    (VALUE_STRENGTH<1, preserving NBP's rut/dirt contrast). Byte-reproducible.
    """
    hsv = _hsv(pp)
    alpha = np.asarray(pp)[..., 3].copy()
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    src = {"h": float(h[mask].mean()), "s": float(s[mask].mean()), "v": float(v[mask].mean())} if mask.any() else {"h": 0.0, "s": 0.0, "v": 0.0}

    if mask.any():
        # Hue: lerp toward a constant target hue (strength 1 => constant).
        h[mask] = np.round(h[mask] + HUE_STRENGTH * (target["h"] - h[mask])).astype(np.int16)
        # Saturation: mean-shift toward target (preserve deviation).
        s[mask] = np.clip(np.round(s[mask] + SAT_STRENGTH * (target["s"] - src["s"])), 0, 255).astype(np.int16)
        # Value: gentle mean-shift toward target (preserve deviation/contrast).
        v[mask] = np.clip(np.round(v[mask] + VALUE_STRENGTH * (target["v"] - src["v"])), 0, 255).astype(np.int16)

    out_hsv = np.dstack([h, s, v]).astype(np.uint8)
    out_rgb = np.asarray(Image.fromarray(out_hsv, "HSV").convert("RGB"))
    out = np.dstack([out_rgb, alpha]).astype(np.uint8)
    params = {
        "hue_strength": HUE_STRENGTH,
        "sat_strength": SAT_STRENGTH,
        "value_strength": VALUE_STRENGTH,
        "target_hsv": target,
        "source_mean_hsv": {k: round(val, 1) for k, val in src.items()},
        "masked_px": int(mask.sum()),
    }
    return Image.fromarray(out, "RGBA"), params


def rim_blend_arr(input_img: Image.Image, repaint_img: Image.Image, blend_px: float) -> Image.Image:
    """rimblend smoothstep (library use): input outside, repaint inside; input alpha."""
    inp = np.asarray(input_img.convert("RGBA")).astype(np.float64)
    rep = np.asarray(repaint_img.convert("RGBA")).astype(np.float64)
    w = rimblend.blend_weights(blend_px)[..., None]
    out = inp.copy()
    out[..., :3] = inp[..., :3] * (1.0 - w) + rep[..., :3] * w
    out[..., 3] = inp[..., 3]
    return Image.fromarray(np.round(out).astype(np.uint8), "RGBA")


def post_chain(
    raw_img: Image.Image, input_img: Image.Image, terrain: str, shape: str, seed: int
) -> tuple[Image.Image, Image.Image, dict]:
    """resize/reclip -> recolour -> rim blend b08. Returns (final, recoloured, params)."""
    input_alpha = np.asarray(input_img)[..., 3]
    pp = resize_reclip(raw_img, input_alpha)
    mask = recolour_mask(_hsv(pp), input_alpha)
    target = ribbon_target(terrain, shape, seed, input_img)
    recoloured, rc_params = apply_recolour(pp, mask, target)
    final = rim_blend_arr(input_img, recoloured, BLEND_PX)
    return final, recoloured, rc_params


# ================================================================= measure


def _densify(pts: list[tuple[float, float]], step: float = 1.0) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in zip(pts[:-1], pts[1:]):
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(length / step))
        for i in range(n):
            out.append((a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n))
    out.append(pts[-1])
    return out


def _solid(mask: np.ndarray, x: float, y: float) -> bool:
    xi, yi = int(round(x)), int(round(y))
    return 0 <= xi < TILE_PX and 0 <= yi < TILE_PX and bool(mask[yi, xi])


def _run_along(mask: np.ndarray, pt: tuple[float, float], direction: tuple[float, float]) -> tuple[float, float]:
    """Contiguous run length + signed centre offset of ``mask`` along ``direction`` through ``pt``.

    Recenters onto the nearest solid pixel within +/-4px if ``pt`` is off-mask.
    Returns (width_px, centre_offset_px). width 0 => no road here.
    """
    dx, dy = direction
    step = 0.5
    if not _solid(mask, pt[0], pt[1]):
        moved = False
        for k in np.arange(-4.0, 4.01, step):
            if _solid(mask, pt[0] + dx * k, pt[1] + dy * k):
                pt = (pt[0] + dx * k, pt[1] + dy * k)
                moved = True
                break
        if not moved:
            return 0.0, math.inf
    lo = 0.0
    k = -step
    while _solid(mask, pt[0] + dx * k, pt[1] + dy * k):
        lo = k
        k -= step
    hi = 0.0
    k = step
    while _solid(mask, pt[0] + dx * k, pt[1] + dy * k):
        hi = k
        k += step
    width = (hi - lo) + step
    centre = (hi + lo) / 2.0
    return width, centre


def _centreline_centre_tangent(shape: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """Point on the road centreline nearest the tile centre + its local unit tangent."""
    dense = _densify(road.road_path(shape)["points"])
    arr = np.asarray(dense)
    i = int(((arr[:, 0] - TILE_PX / 2) ** 2 + (arr[:, 1] - TILE_PX / 2) ** 2).argmin())
    p = dense[i]
    j, k = min(i + 3, len(dense) - 1), max(i - 3, 0)
    t = (dense[j][0] - dense[k][0], dense[j][1] - dense[k][1])
    length = math.hypot(*t) or 1.0
    return p, (t[0] / length, t[1] / length)


def measure(
    shape: str, input_img: Image.Image, recoloured: Image.Image, final: Image.Image
) -> dict:
    """All gate measurements. Uses recoloured (pre-blend) for drift/width, final for rim/lum."""
    rec_hsv, rec_a = _hsv(recoloured), np.asarray(recoloured)[..., 3]
    in_hsv, in_a = _hsv(input_img), np.asarray(input_img)[..., 3]
    rec_road = _classify(rec_hsv, rec_a, RECOLOUR_HUE, RECOLOUR_SMIN)
    in_road = _classify(in_hsv, in_a, RECOLOUR_HUE, RECOLOUR_SMIN)
    core = _classify(rec_hsv, rec_a, CORE_HUE, CORE_SMIN)

    # (1) per-edge crossing centre drift (pre-blend).
    geometry: dict[str, dict] = {}
    max_drift = 0.0
    inset = 5.0
    for edge in road.SHAPES[shape]:
        mid = edge_midpoint(edge)
        n = road.edge_normal(edge)
        t = road.edge_tangent(edge)
        p = (mid[0] + n[0] * inset, mid[1] + n[1] * inset)
        w_out, c_out = _run_along(rec_road, p, t)
        w_in, c_in = _run_along(in_road, p, t)
        drift = abs(c_out - c_in) if math.isfinite(c_out) and math.isfinite(c_in) else math.inf
        max_drift = max(max_drift, drift)
        geometry[str(edge)] = {
            "out_width": round(w_out, 1),
            "in_width": round(w_in, 1),
            "out_centre": round(c_out, 2) if math.isfinite(c_out) else None,
            "in_centre": round(c_in, 2) if math.isfinite(c_in) else None,
            "drift_px": round(drift, 2) if math.isfinite(drift) else None,
        }

    # (2) mid-tile core width (perpendicular to the local centreline tangent).
    p, tan = _centreline_centre_tangent(shape)
    perp = (-tan[1], tan[0])
    core_width, _ = _run_along(core, p, perp)

    # (3) grass-rim delta over the 8px inner ring (grass only).
    dist = rimblend.hex_boundary_distance()
    ring = (dist <= RIM_PX) & (hex_mask(TILE_PX, TILE_PX) > 128) & (in_a > 0) & (~in_road)
    fin = np.asarray(final).astype(np.float64)
    inp = np.asarray(input_img).astype(np.float64)
    rim_delta = [round(float(np.abs(fin[..., c][ring] - inp[..., c][ring]).mean()), 2) for c in range(3)]

    # (4) opaque mean luminance (Rec.601).
    op = np.asarray(final)[..., 3] > 0
    rgb = np.asarray(final)[..., :3].astype(np.float64)
    lum = float((0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2])[op].mean())

    # (5) end shapes: phantom road at NON-connected edges (pre-blend, cleaned mask).
    phantom = phantom_fractions(shape, recoloured) if len(road.SHAPES[shape]) == 1 else None

    return {
        "geometry": geometry,
        "max_drift_px": round(max_drift, 2) if math.isfinite(max_drift) else None,
        "core_width_px": round(core_width, 1),
        "rim_delta_rgb_grassonly": rim_delta,
        "rim_px": RIM_PX,
        "lum_out": round(lum, 1),
        "phantom_fracs": phantom,
    }


def phantom_fractions(shape: str, recoloured: Image.Image) -> dict[str, float]:
    """Cleaned road-mask fraction near each non-connected edge midpoint (end shapes)."""
    hsv = _hsv(recoloured)
    alpha = np.asarray(recoloured)[..., 3]
    mask = recolour_mask(hsv, alpha)
    ys, xs = np.mgrid[0:TILE_PX, 0:TILE_PX]
    fracs: dict[str, float] = {}
    for e in range(6):
        if e in road.SHAPES[shape]:
            continue
        mid = edge_midpoint(e)
        n = road.edge_normal(e)
        worst = 0.0
        for depth in PHANTOM_DEPTHS:
            cx, cy = mid[0] + n[0] * depth, mid[1] + n[1] * depth
            disc = ((xs - cx) ** 2 + (ys - cy) ** 2) <= PHANTOM_DISC_R**2
            worst = max(worst, float(mask[disc].mean()))
        fracs[str(e)] = round(worst, 3)
    return fracs


def evaluate_gates(meas: dict) -> dict:
    """Apply the gates. Returns per-gate pass + overall 'survivor' bool."""
    drift_ok = meas["max_drift_px"] is not None and meas["max_drift_px"] <= DRIFT_TOL_PX
    width_ok = meas["core_width_px"] <= CORE_WIDTH_MAX_PX and meas["core_width_px"] > 0
    rim_ok = all(d <= RIM_DELTA_MAX for d in meas["rim_delta_rgb_grassonly"])
    lum_ok = abs(meas["lum_out"] - GRASS_MEAN_LUM) <= LUM_BAND
    gates = {"drift": drift_ok, "width": width_ok, "rim": rim_ok, "lum": lum_ok}
    if meas.get("phantom_fracs") is not None:
        gates["phantom"] = all(f <= PHANTOM_FRAC_MAX for f in meas["phantom_fracs"].values())
    return {"gates": gates, "survivor": all(gates.values())}


# ================================================================= generate


@dataclass
class Job:
    terrain: str
    shape: str
    slot_nn: int
    variant: str  # base-variant basename stem, e.g. "00"
    roll: int
    tag: str  # "base" | "reroll"

    @property
    def slot_id(self) -> str:
        return f"{self.terrain}_{self.shape}_{self.slot_nn:02d}"

    @property
    def stem(self) -> str:
        return f"{self.slot_id}__g{self.roll}"


@dataclass
class SlotInput:
    input_img: Image.Image
    input_path: Path
    seed: int
    base_rel: str  # base path relative to the foundry root (for the manifest)


@dataclass
class GenResult:
    job: Job
    ok: bool
    survivor: bool = False
    latency_s: float = 0.0
    usage: dict = field(default_factory=dict)
    meas: dict = field(default_factory=dict)
    gates: dict = field(default_factory=dict)
    recolour: dict = field(default_factory=dict)
    target: dict = field(default_factory=dict)
    raw_path: str = ""
    tile_path: str = ""
    error: str = ""


def _clamp_variant(bases_root: Path, terrain: str, slot_nn: int) -> str:
    bases = sorted(bases_root.joinpath(terrain).glob("[0-9][0-9].png"))
    if not bases:
        raise RoadFinishError(f"no ratified bases in {bases_root / terrain}")
    return bases[min(slot_nn, len(bases) - 1)].stem


def prepare_slot(bases_root: Path, foundry_root: Path, out_dir: Path, terrain: str, shape: str, slot_nn: int) -> tuple[SlotInput, Job]:
    """Bake the deterministic input composite for a slot once (shared by its rolls)."""
    variant = _clamp_variant(bases_root, terrain, slot_nn)
    base_path = bases_root / terrain / f"{variant}.png"
    seed = road.derive_seed(terrain, shape, "flat", variant)
    input_img, _ = road.compose_road(base_path, shape, "flat", seed)
    slot_id = f"{terrain}_{shape}_{slot_nn:02d}"
    input_path = out_dir / "inputs" / f"{slot_id}.png"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_img.save(input_path)
    base_rel = _rel(base_path, foundry_root)
    return SlotInput(input_img, input_path, seed, base_rel), Job(terrain, shape, slot_nn, variant, 0, "base")


def nbp_generate(prompt: str, refs: list[Path], token: str) -> tuple[bytes, dict, float]:
    """One NBP generateContent call. Returns (image_bytes, usageMetadata, latency_s)."""
    body = gen.build_request(prompt, refs, ASPECT, IMAGE_SIZE)
    t0 = time.time()
    response = gen.call_vertex(MODEL, body, token)
    latency = time.time() - t0
    image_bytes = gen.extract_image(response)
    return image_bytes, response.get("usageMetadata", {}), latency


def run_one(job: Job, slot: SlotInput, foundry_root: Path, out_dir: Path, token: str) -> GenResult:
    """Full pipeline for one generation: NBP -> post-chain -> gates -> save + sidecar."""
    prompt = build_prompt(job.terrain, job.shape)
    refs = [foundry_root / r for r in REFS[job.terrain]]
    ref_paths = [slot.input_path] + refs

    gens_dir = out_dir / "gens"
    gens_dir.mkdir(parents=True, exist_ok=True)
    raw_path = gens_dir / f"{job.stem}__raw.png"

    try:
        image_bytes, usage, latency = nbp_generate(prompt, ref_paths, token)
    except SystemExit as exc:  # gen.call_vertex/extract raise SystemExit on API error
        return GenResult(job=job, ok=False, error=f"NBP call failed: {exc}")

    raw_path.write_bytes(image_bytes)
    raw_img = Image.open(raw_path)

    final, recoloured, rc_params = post_chain(raw_img, slot.input_img, job.terrain, job.shape, slot.seed)
    meas = measure(job.shape, slot.input_img, recoloured, final)
    verdict = evaluate_gates(meas)
    survivor = verdict["survivor"]

    tile_dir = out_dir / ("tiles" if survivor else "rejects")
    tile_dir.mkdir(parents=True, exist_ok=True)
    tile_path = tile_dir / f"{job.stem}.png"
    final.save(tile_path)

    raw_rel = _rel(raw_path, foundry_root)
    target = rc_params["target_hsv"]
    sidecar = {
        "slot_id": job.slot_id,
        "terrain": job.terrain,
        "shape": job.shape,
        "edges": road.SHAPES[job.shape],
        "variant": job.variant,
        "slot_nn": job.slot_nn,
        "roll": job.roll,
        "tag": job.tag,
        "seed": slot.seed,
        "treatment": "flat",
        "base": slot.base_rel,
        "model": MODEL,
        "image_size": IMAGE_SIZE,
        "aspect": ASPECT,
        "refs": [slot.input_path.name] + REFS[job.terrain],
        "prompt": prompt,
        "input": _rel(slot.input_path, foundry_root),
        "raw": raw_rel,
        "tile": _rel(tile_path, foundry_root),
        "latency_s": round(latency, 1),
        "usage": usage,
        "post_chain": {
            "blend_px": BLEND_PX,
            "recolour": rc_params,
            "recolour_target": target,
        },
        "gate_config": {
            "drift_tol_px": DRIFT_TOL_PX,
            "core_width_max_px": CORE_WIDTH_MAX_PX,
            "rim_delta_max": RIM_DELTA_MAX,
            "lum_band": [GRASS_MEAN_LUM - LUM_BAND, GRASS_MEAN_LUM + LUM_BAND],
        },
        "measurements": meas,
        "gates": verdict["gates"],
        "survivor": survivor,
    }
    (gens_dir / f"{job.stem}.json").write_text(json.dumps(sidecar, indent=2) + "\n")

    return GenResult(
        job=job,
        ok=True,
        survivor=survivor,
        latency_s=latency,
        usage=usage,
        meas=meas,
        gates=verdict["gates"],
        recolour=rc_params,
        target=target,
        raw_path=raw_rel,
        tile_path=str(tile_path),
    )


def run_generate(args: argparse.Namespace) -> int:
    out_dir: Path = args.out_dir
    bases_root: Path = args.bases_root
    foundry_root: Path = args.foundry_root
    cap = min(args.max_calls, HARD_CAP)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Prepare every slot's deterministic input composite (no NBP).
    slots: dict[str, tuple[SlotInput, Job]] = {}
    slot_order: list[str] = []
    for terrain, shape, n_slots in MATRIX:
        for slot_nn in range(n_slots):
            slot, proto = prepare_slot(bases_root, foundry_root, out_dir, terrain, shape, slot_nn)
            slots[proto.slot_id] = (slot, proto)
            slot_order.append(proto.slot_id)
    print(f"prepared {len(slots)} slot input composites; cap {cap} NBP calls")

    if args.dry_run:
        for sid in slot_order:
            print(f"  slot {sid}  base={slots[sid][0].base_rel}  seed={slots[sid][0].seed}")
        print("dry-run: no NBP calls made")
        return 0

    token = gen.get_access_token()

    # 2. Base jobs: ROLLS_PER_SLOT per slot, priority order, hard-capped.
    base_jobs: list[Job] = []
    for sid in slot_order:
        _, proto = slots[sid]
        for roll in range(1, ROLLS_PER_SLOT + 1):
            j = Job(proto.terrain, proto.shape, proto.slot_nn, proto.variant, roll, "base")
            if len(base_jobs) < cap:
                base_jobs.append(j)

    results: list[GenResult] = []
    calls = 0

    def submit_wave(jobs: list[Job]) -> list[GenResult]:
        nonlocal calls
        wave: list[GenResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {
                pool.submit(run_one, j, slots[j.slot_id][0], foundry_root, out_dir, token): j
                for j in jobs
            }
            for fut in concurrent.futures.as_completed(futs):
                res = fut.result()
                wave.append(res)
                tag = "SURVIVOR" if res.survivor else ("REJECT" if res.ok else "ERROR")
                extra = ""
                if res.ok:
                    fails = [g for g, ok in res.gates.items() if not ok]
                    extra = f" cw={res.meas['core_width_px']} drift={res.meas['max_drift_px']} fails={fails or '-'}"
                print(f"  [{tag}] {res.job.stem}{extra}")
        return wave

    print(f"wave 1: {len(base_jobs)} base gens (max_workers={MAX_WORKERS}) ...")
    results.extend(submit_wave(base_jobs))
    calls += len(base_jobs)

    # 3. Rerolls: slots with 0 survivors, priority order, until the cap.
    survivors_by_slot: dict[str, int] = {}
    roll_count: dict[str, int] = {}
    for r in results:
        roll_count[r.job.slot_id] = roll_count.get(r.job.slot_id, 0) + 1
        if r.survivor:
            survivors_by_slot[r.job.slot_id] = survivors_by_slot.get(r.job.slot_id, 0) + 1

    reroll_jobs: list[Job] = []
    for sid in slot_order:
        if survivors_by_slot.get(sid, 0) == 0 and calls + len(reroll_jobs) < cap:
            _, proto = slots[sid]
            nxt = roll_count.get(sid, 0) + 1
            reroll_jobs.append(Job(proto.terrain, proto.shape, proto.slot_nn, proto.variant, nxt, "reroll"))

    if reroll_jobs:
        print(f"wave 2: {len(reroll_jobs)} rerolls for slots with no survivor ...")
        results.extend(submit_wave(reroll_jobs))
        calls += len(reroll_jobs)

    write_summary(results, slots, slot_order, out_dir, foundry_root, calls)
    return 0


def write_summary(
    results: list[GenResult],
    slots: dict[str, tuple[SlotInput, Job]],
    slot_order: list[str],
    out_dir: Path,
    foundry_root: Path,
    calls: int,
) -> None:
    """Write _production_summary.json: per-slot rollup + a ready-to-paste section fragment."""
    by_slot: dict[str, list[GenResult]] = {sid: [] for sid in slot_order}
    for r in results:
        by_slot[r.job.slot_id].append(r)

    latencies = [r.latency_s for r in results if r.ok]
    reject_gate_counts: dict[str, int] = {"drift": 0, "width": 0, "rim": 0, "lum": 0}
    survivors = [r for r in results if r.survivor]
    rejects = [r for r in results if r.ok and not r.survivor]
    errors = [r for r in results if not r.ok]
    for r in rejects:
        for g, ok in r.gates.items():
            if not ok:
                reject_gate_counts[g] += 1

    slot_rollup = []
    section_tiles = []
    for sid in slot_order:
        rs = by_slot[sid]
        slot, _ = slots[sid]
        surv = [r for r in rs if r.survivor]
        # Recommendation: survivor with smallest core width (tightest road), then rim.
        pick = None
        if surv:
            pick = min(surv, key=lambda r: (r.meas["core_width_px"], sum(r.meas["rim_delta_rgb_grassonly"])))
        slot_rollup.append({
            "slot_id": sid,
            "base": slot.base_rel,
            "rolls": len(rs),
            "survivors": [r.job.stem for r in surv],
            "recommended": pick.job.stem if pick else None,
            "reason": (
                f"tightest core {pick.meas['core_width_px']}px, rim {pick.meas['rim_delta_rgb_grassonly']}"
                if pick else "NO SURVIVOR — all rolls failed a gate"
            ),
            "rolls_detail": [
                {
                    "stem": r.job.stem,
                    "survivor": r.survivor,
                    "core_width_px": r.meas.get("core_width_px"),
                    "max_drift_px": r.meas.get("max_drift_px"),
                    "rim": r.meas.get("rim_delta_rgb_grassonly"),
                    "lum": r.meas.get("lum_out"),
                    "gates": r.gates,
                    "error": r.error or None,
                }
                for r in rs
            ],
        })
        if pick:
            terrain, shape = pick.job.terrain, pick.job.shape
            section_tiles.append({
                "tile": f"{sid}.png",
                "terrain": terrain,
                "shape": shape,
                "variant": pick.job.variant,
                "base": slot.base_rel,
                "treatment": "flat",
                "seed": slot.seed,
                "raw": pick.raw_path,
                "recolour_target": pick.target,
            })

    summary = {
        "description": "NBP road-finishing production round (T5): per-slot rollup + apply-time section fragment",
        "model": MODEL,
        "counts": {
            "nbp_calls": calls,
            "survivors": len(survivors),
            "rejects": len(rejects),
            "errors": len(errors),
            "reject_gate_counts": reject_gate_counts,
        },
        "latency_s": {
            "total": round(sum(latencies), 1),
            "mean": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            "max": round(max(latencies), 1) if latencies else 0.0,
        },
        "usage_total": _sum_usage(results),
        "post_chain": {
            "blend_px": BLEND_PX,
            "recolour": {
                "hue_strength": HUE_STRENGTH, "sat_strength": SAT_STRENGTH, "value_strength": VALUE_STRENGTH,
                "recolour_hue": list(RECOLOUR_HUE), "recolour_smin": RECOLOUR_SMIN,
                "core_hue": list(CORE_HUE), "core_smin": CORE_SMIN, "morph_size": MORPH_SIZE,
            },
        },
        "slots": slot_rollup,
        "road_nbp_section_fragment": {
            "class": "NBP-finished painterly road tiles (T5 production round)",
            "model": MODEL,
            "post_chain": {
                "blend_px": BLEND_PX,
                "recolour": {
                    "hue_strength": HUE_STRENGTH, "sat_strength": SAT_STRENGTH, "value_strength": VALUE_STRENGTH,
                    "recolour_hue": list(RECOLOUR_HUE), "recolour_smin": RECOLOUR_SMIN,
                    "core_hue": list(CORE_HUE), "core_smin": CORE_SMIN, "morph_size": MORPH_SIZE,
                },
            },
            "shapes": {s: road.SHAPES[s] for s in {j[1] for j in MATRIX}},
            "tiles": section_tiles,
        },
    }
    (out_dir / "_production_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        f"\nsummary: {len(survivors)} survivors / {len(rejects)} rejects / {len(errors)} errors "
        f"over {calls} calls; reject gates {reject_gate_counts}"
    )
    print(f"wrote {out_dir / '_production_summary.json'}")


def _sum_usage(results: list[GenResult]) -> dict:
    total: dict[str, int] = {}
    for r in results:
        for k, v in (r.usage or {}).items():
            if isinstance(v, int):
                total[k] = total.get(k, 0) + v
    return total


# ============================================================= export-sources


def run_export_sources(args: argparse.Namespace) -> int:
    """Regenerate the committed 128px sources (``gens128``) from the 2K raws.

    For each manifest tile, re-derive the deterministic input composite (for its
    alpha mask), open the raw NBP gen, run ``resize_reclip`` (raw -> 128px LANCZOS
    -> alpha-reclip), and write ``{out-rel}/{tile}``. This is the one step that
    needs the (uncommitted, ~210MB) raw gens on disk, so it runs once pre-commit;
    ``build`` thereafter replays deterministically from these ~30KB sources.
    """
    manifest = json.loads(args.manifest.read_text())
    if args.section not in manifest:
        raise SystemExit(f"section {args.section!r} not in {args.manifest}")
    section = manifest[args.section]
    root = args.manifest.parent
    out_dir = root / args.out_rel
    out_dir.mkdir(parents=True, exist_ok=True)

    for t in section["tiles"]:
        raw_rel = t.get("raw")
        if not raw_rel:
            raise RoadFinishError(f"{t['tile']}: no 'raw' path to export a source from")
        raw_path = root / raw_rel
        if not raw_path.is_file():
            raise RoadFinishError(
                f"{t['tile']}: raw gen missing at {raw_path} — the 2K raws are "
                "uncommitted; run export-sources pre-commit with them present"
            )
        base_path = root / t["base"]
        input_img, _ = road.compose_road(base_path, t["shape"], t["treatment"], t["seed"])
        input_alpha = np.asarray(input_img)[..., 3]
        pp = resize_reclip(Image.open(raw_path), input_alpha)
        dest = out_dir / t["tile"]
        pp.save(dest)
        print(f"exported {_rel(dest, root)}  <- {raw_rel}")

    print(f"exported {len(section['tiles'])} sources into {_rel(out_dir, root)}")
    return 0


# ==================================================================== build


def run_build(args: argparse.Namespace) -> int:
    """Replay the deterministic post-chain from committed raw gens; --verify byte-compares."""
    manifest = json.loads(args.manifest.read_text())
    if args.section not in manifest:
        raise SystemExit(f"section {args.section!r} not in {args.manifest}")
    section = manifest[args.section]
    root = args.manifest.parent
    global BLEND_PX, HUE_STRENGTH, SAT_STRENGTH, VALUE_STRENGTH
    global RECOLOUR_HUE, RECOLOUR_SMIN, CORE_HUE, CORE_SMIN, MORPH_SIZE

    pc = section["post_chain"]
    rc = pc["recolour"]
    # Bind post-chain constants from the section so replay can't drift on a module edit.
    BLEND_PX = pc["blend_px"]
    HUE_STRENGTH, SAT_STRENGTH, VALUE_STRENGTH = rc["hue_strength"], rc["sat_strength"], rc["value_strength"]
    RECOLOUR_HUE, RECOLOUR_SMIN = tuple(rc["recolour_hue"]), rc["recolour_smin"]
    CORE_HUE, CORE_SMIN, MORPH_SIZE = tuple(rc["core_hue"]), rc["core_smin"], rc["morph_size"]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for t in section["tiles"]:
        base_path = root / t["base"]
        seed = t["seed"]
        input_img, _ = road.compose_road(base_path, t["shape"], t["treatment"], seed)
        input_alpha = np.asarray(input_img)[..., 3]
        # Committed truth is the 128px source (gens128); fall back to the raw only
        # when a tile predates the source export.
        if t.get("source"):
            src = t["source"]
            pp = Image.open(root / src).convert("RGBA")
        else:
            src = t["raw"]
            pp = resize_reclip(Image.open(root / src), input_alpha)
        mask = recolour_mask(_hsv(pp), input_alpha)
        recoloured, _ = apply_recolour(pp, mask, t["recolour_target"])
        final = rim_blend_arr(input_img, recoloured, BLEND_PX)
        final.save(args.out_dir / t["tile"])
        print(f"{t['tile']}: {t['shape']} over {t['base']} from {src}")

    if args.verify:
        ok = True
        for t in section["tiles"]:
            out_p = args.out_dir / t["tile"]
            ref_p = args.verify / t["tile"]
            if not ref_p.exists():
                print(f"MISSING in reference: {ref_p}")
                ok = False
                continue
            a = np.asarray(Image.open(out_p).convert("RGBA"))
            b = np.asarray(Image.open(ref_p).convert("RGBA"))
            if a.shape != b.shape or not np.array_equal(a, b):
                print(f"DIFFERS: {t['tile']}")
                ok = False
            else:
                print(f"identical: {t['tile']}")
        if not ok:
            return 1
        print("verify: all tiles byte-identical")
    return 0


# ================================================================ summarize


def _slot_meta(bases_root: Path, foundry_root: Path) -> tuple[dict[str, dict], list[str]]:
    """Slot metadata (base rel path, seed) in matrix priority order, no baking."""
    meta: dict[str, dict] = {}
    order: list[str] = []
    for terrain, shape, n_slots in MATRIX:
        for slot_nn in range(n_slots):
            variant = _clamp_variant(bases_root, terrain, slot_nn)
            sid = f"{terrain}_{shape}_{slot_nn:02d}"
            meta[sid] = {
                "terrain": terrain,
                "shape": shape,
                "variant": variant,
                "base": _rel(bases_root / terrain / f"{variant}.png", foundry_root),
                "seed": road.derive_seed(terrain, shape, "flat", variant),
            }
            order.append(sid)
    return meta, order


def run_summarize(args: argparse.Namespace) -> int:
    """Rebuild _production_summary.json from the sidecars on disk (idempotent).

    Robust across multiple generate invocations (e.g. throttled retries after a
    429 storm): every gens/*.json sidecar is a successful NBP generation.
    """
    out_dir: Path = args.out_dir
    meta, order = _slot_meta(args.bases_root, args.foundry_root)
    sidecars = sorted((out_dir / "gens").glob("*.json"))
    if not sidecars:
        raise RoadFinishError(f"no sidecars in {out_dir / 'gens'}")

    by_slot: dict[str, list[dict]] = {sid: [] for sid in order}
    for p in sidecars:
        sc = json.loads(p.read_text())
        by_slot[sc["slot_id"]].append(sc)

    reject_gate_counts: dict[str, int] = {}
    survivors, rejects, latencies = [], [], []
    usage_total: dict[str, int] = {}
    for scs in by_slot.values():
        for sc in scs:
            latencies.append(sc["latency_s"])
            for k, v in (sc.get("usage") or {}).items():
                if isinstance(v, int):
                    usage_total[k] = usage_total.get(k, 0) + v
            if sc["survivor"]:
                survivors.append(sc)
            else:
                rejects.append(sc)
                for g, ok in sc["gates"].items():
                    if not ok:
                        reject_gate_counts[g] = reject_gate_counts.get(g, 0) + 1

    slot_rollup, section_tiles = [], []
    for sid in order:
        scs = sorted(by_slot[sid], key=lambda s: s["roll"])
        surv = [s for s in scs if s["survivor"]]
        pick = None
        if surv:
            pick = min(
                surv,
                key=lambda s: (
                    s["measurements"]["core_width_px"],
                    sum(s["measurements"]["rim_delta_rgb_grassonly"]),
                ),
            )

        def stem(s: dict) -> str:
            return f"{s['slot_id']}__g{s['roll']}"

        slot_rollup.append({
            "slot_id": sid,
            "base": meta[sid]["base"],
            "rolls": len(scs),
            "survivors": [stem(s) for s in surv],
            "recommended": stem(pick) if pick else None,
            "reason": (
                f"tightest core {pick['measurements']['core_width_px']}px, "
                f"rim {pick['measurements']['rim_delta_rgb_grassonly']}"
                if pick
                else "NO SURVIVOR"
            ),
            "rolls_detail": [
                {
                    "stem": stem(s),
                    "survivor": s["survivor"],
                    "core_width_px": s["measurements"]["core_width_px"],
                    "max_drift_px": s["measurements"]["max_drift_px"],
                    "rim": s["measurements"]["rim_delta_rgb_grassonly"],
                    "lum": s["measurements"]["lum_out"],
                    "gates": s["gates"],
                }
                for s in scs
            ],
        })
        if pick:
            section_tiles.append({
                "tile": f"{sid}.png",
                "terrain": meta[sid]["terrain"],
                "shape": meta[sid]["shape"],
                "variant": meta[sid]["variant"],
                "base": meta[sid]["base"],
                "treatment": "flat",
                "seed": meta[sid]["seed"],
                "raw": pick["raw"],
                "recolour_target": pick["post_chain"]["recolour_target"],
            })

    recolour_cfg = {
        "hue_strength": HUE_STRENGTH, "sat_strength": SAT_STRENGTH, "value_strength": VALUE_STRENGTH,
        "recolour_hue": list(RECOLOUR_HUE), "recolour_smin": RECOLOUR_SMIN,
        "core_hue": list(CORE_HUE), "core_smin": CORE_SMIN, "morph_size": MORPH_SIZE,
    }
    summary = {
        "description": "NBP road-finishing production round (T5): per-slot rollup + apply-time section fragment",
        "model": MODEL,
        "counts": {
            "successful_gens": len(sidecars),
            "survivors": len(survivors),
            "rejects": len(rejects),
            "reject_gate_counts": reject_gate_counts,
            "slots_covered": sum(1 for s in slot_rollup if s["recommended"]),
            "slots_total": len(order),
        },
        "latency_s": {
            "total": round(sum(latencies), 1),
            "mean": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            "max": round(max(latencies), 1) if latencies else 0.0,
        },
        "usage_total": usage_total,
        "post_chain": {"blend_px": BLEND_PX, "recolour": recolour_cfg},
        "slots": slot_rollup,
        "road_nbp_section_fragment": {
            "class": "NBP-finished painterly road tiles (T5 production round)",
            "model": MODEL,
            "post_chain": {"blend_px": BLEND_PX, "recolour": recolour_cfg},
            "shapes": {s: road.SHAPES[s] for s in {j[1] for j in MATRIX}},
            "tiles": section_tiles,
        },
    }
    (out_dir / "_production_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        f"summarize: {len(sidecars)} gens, {len(survivors)} survivors, {len(rejects)} rejects; "
        f"{summary['counts']['slots_covered']}/{len(order)} slots covered"
    )
    return 0


# =================================================================== montage


# Per-group montage definitions: (out-name, slot_id prefixes to include).
MONTAGE_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("roadprod-plains-straights", ("plains_straight_v_", "plains_straight_d_")),
    ("roadprod-plains-turns", ("plains_turn_",)),
    ("roadprod-plains-ends", ("plains_end_",)),
    ("roadprod-hills", ("hills_",)),
]


def _load_summary(out_dir: Path) -> dict:
    p = out_dir / "_production_summary.json"
    if not p.is_file():
        raise RoadFinishError(f"no summary at {p}; run `generate` first")
    return json.loads(p.read_text())


def _slot_survivor_tiles(out_dir: Path, slot: dict) -> list[Path]:
    """Survivor tile paths for a slot (recommended first, then the rest)."""
    tiles_dir = out_dir / "tiles"
    ordered = []
    if slot["recommended"]:
        ordered.append(slot["recommended"])
    ordered += [s for s in slot["survivors"] if s != slot["recommended"]]
    return [tiles_dir / f"{stem}.png" for stem in ordered]


def run_montage(args: argparse.Namespace) -> int:
    """Build per-group review montages (via tile-montage.py) + board-scale strips."""
    out_dir: Path = args.out_dir
    dest: Path = args.montage_dir
    dest.mkdir(parents=True, exist_ok=True)
    summary = _load_summary(out_dir)
    slots = summary["slots"]
    montage_tool = Path(__file__).resolve().parents[1] / "tile-montage.py"

    # --- per-group montages: input composite first, then survivors, per slot ---
    for group_name, prefixes in MONTAGE_GROUPS:
        group_slots = [s for s in slots if s["slot_id"].startswith(prefixes)]
        staged: list[tuple[str, Path]] = []
        for si, slot in enumerate(group_slots):
            inp = out_dir / "inputs" / f"{slot['slot_id']}.png"
            staged.append((f"{si:02d}_0_input__{slot['slot_id']}", inp))
            for gi, tile in enumerate(_slot_survivor_tiles(out_dir, slot), start=1):
                staged.append((f"{si:02d}_{gi}__{tile.stem}", tile))
        if not staged:
            print(f"  {group_name}: no survivors, skipped")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            for label, src in staged:
                if src.is_file():
                    Image.open(src).convert("RGBA").save(tmpdir / f"{label}.png")
            out_png = dest / f"{group_name}.png"
            subprocess.run(
                ["python3", str(montage_tool), str(tmpdir), "--out", str(out_png), "--cols", "3", "--scale", "3"],
                check=True,
            )
        print(f"  wrote {out_png}")

    # --- board-scale strips (50px), reusing road.render_board ---
    _build_boardstrips(out_dir, dest, slots, args.bases_root, args.board_px)
    return 0


def _pick_tile(out_dir: Path, slots: list[dict], slot_id: str) -> Image.Image | None:
    for s in slots:
        if s["slot_id"] == slot_id and s["recommended"]:
            return Image.open(out_dir / "tiles" / f"{s['recommended']}.png").convert("RGBA")
    return None


def _build_boardstrips(out_dir: Path, dest: Path, slots: list[dict], bases_root: Path, board_px: int) -> None:
    """Three board-scale scenarios: straight-run consistency, turn flow, hills crossing."""
    fill = Image.open(bases_root / "plains" / "00.png").convert("RGBA")

    def band(qs: range, r_lo: int, r_hi: int) -> list[tuple[int, int]]:
        coords = []
        for q in qs:
            for r in range(r_lo, r_hi + 1):
                coords.append((q, r))
        return coords

    # Strip 1: a long vertical run of the 3 straight_v picks (colour/width consistency).
    sv_picks = [_pick_tile(out_dir, slots, f"plains_straight_v_{n:02d}") for n in range(3)]
    sv_picks = [t for t in sv_picks if t is not None]
    if sv_picks:
        placements = {}
        for i, r in enumerate(range(-3, 4)):
            placements[(0, r)] = sv_picks[i % len(sv_picks)]
        coords = band(range(-2, 3), -6, 6)
        board = road.render_board(placements, fill, board_px, coords)
        p = dest / "roadprod-boardstrip-straights.png"
        board.save(p)
        print(f"  wrote {p} (straight_v run x{len(sv_picks)} picks)")

    # Strip 2: a connected turn flow (turn_n_se se->n, mirrored turn_s_nw s->ne above).
    t_nse = _pick_tile(out_dir, slots, "plains_turn_n_se_00")
    t_snw = _pick_tile(out_dir, slots, "plains_turn_s_nw_00")
    if t_nse and t_snw:
        placements = {(0, 0): t_nse, (0, -1): road._flip(t_snw)}
        # add the other two turn picks alongside for board-scale eyeballing
        for n, sid in enumerate(("plains_turn_se_sw_00", "plains_turn_ne_nw_00"), start=1):
            t = _pick_tile(out_dir, slots, sid)
            if t:
                placements[(3 * n, -(3 * n // 2))] = t
        coords = band(range(-2, 8), -6, 6)
        board = road.render_board(placements, fill, board_px, coords)
        p = dest / "roadprod-boardstrip-turns.png"
        board.save(p)
        print(f"  wrote {p} (connected turn pair + other turns)")

    # Strip 3: a hills straight_v crossing between two plains straight_v hexes.
    plains_sv = _pick_tile(out_dir, slots, "plains_straight_v_00")
    hills_sv = _pick_tile(out_dir, slots, "hills_straight_v_00")
    if plains_sv and hills_sv:
        placements = {(0, -1): plains_sv, (0, 0): hills_sv, (0, 1): plains_sv}
        coords = band(range(-2, 3), -5, 5)
        board = road.render_board(placements, fill, board_px, coords)
        p = dest / "roadprod-boardstrip-hills-crossing.png"
        board.save(p)
        print(f"  wrote {p} (plains/hills/plains straight_v column)")


# ====================================================================== CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="NBP road-finishing production driver (generate | build).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="run the NBP production loop (hard-capped at 64 calls)")
    g.add_argument("--out-dir", required=True, type=Path, help="production output root")
    g.add_argument("--bases-root", type=Path, default=Path("frontend/src/board/tiles"),
                   help="root holding {terrain}/{nn}.png ratified bases")
    g.add_argument("--foundry-root", type=Path, default=Path("art/foundry"),
                   help="root that raw/base/input paths are stored relative to (default art/foundry)")
    g.add_argument("--max-calls", type=int, default=HARD_CAP, help=f"NBP call cap (clamped to {HARD_CAP})")
    g.add_argument("--dry-run", action="store_true", help="prepare inputs + print the plan; no NBP calls")

    s = sub.add_parser("summarize", help="rebuild the summary from sidecars on disk (idempotent)")
    s.add_argument("--out-dir", required=True, type=Path, help="production output root")
    s.add_argument("--bases-root", type=Path, default=Path("frontend/src/board/tiles"))
    s.add_argument("--foundry-root", type=Path, default=Path("art/foundry"))

    m = sub.add_parser("montage", help="build per-group review montages + board-scale strips")
    m.add_argument("--out-dir", required=True, type=Path, help="production output root (from generate)")
    m.add_argument("--montage-dir", type=Path, default=Path(".dev/montages"),
                   help="destination for montage PNGs (default .dev/montages)")
    m.add_argument("--bases-root", type=Path, default=Path("frontend/src/board/tiles"))
    m.add_argument("--board-px", type=int, default=50, help="board-scale hex px width (default 50)")

    e = sub.add_parser("export-sources", help="regenerate the committed 128px sources (gens128) from the 2K raws")
    e.add_argument("--manifest", required=True, type=Path)
    e.add_argument("--section", default="road_nbp")
    e.add_argument("--out-rel", default="road/gens128", help="dest dir relative to the manifest parent (default road/gens128)")

    b = sub.add_parser("build", help="manifest replay of the post-chain from committed 128px sources")
    b.add_argument("--manifest", required=True, type=Path)
    b.add_argument("--section", default="road_nbp")
    b.add_argument("--out-dir", required=True, type=Path)
    b.add_argument("--verify", type=Path, help="byte-compare output against this dir of applied tiles")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cmd == "generate":
        return run_generate(args)
    if args.cmd == "summarize":
        return run_summarize(args)
    if args.cmd == "montage":
        return run_montage(args)
    if args.cmd == "export-sources":
        return run_export_sources(args)
    return run_build(args)


if __name__ == "__main__":
    raise SystemExit(main())
