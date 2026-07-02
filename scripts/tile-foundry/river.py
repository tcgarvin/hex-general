#!/usr/bin/env python3
"""River (oriented) tile pipeline: guide → gen → cut → recolor.

Rivers are *oriented* terrain — the water must cross its connected edges at
the edge midpoints so adjacent river hexes join seamlessly — so they can't
use the generic `cut.py` window search. This script owns the river-specific
stages around the shared foundry (`gen.py` does the NBP call as usual):

  guide    Layout diagram for gen.py: a grey band on white. `--kind straight`
           (the ratified straight_v guide) is a node-straightened meander: the
           centerline runs dead straight through every tile-period node (where
           the flat-edge crossings land) and bends in a single sin²
           out-and-back lobe between nodes. `--kind zigzag-v` / `zigzag-h`
           are the bend guides: long straight legs joined by exact 60° arcs,
           each corner a future turn-tile window and (zigzag-v) each diagonal
           mid-leg a straight_d window; corner/mid anchor positions go to a
           JSON sidecar (`--anchors-out`) that `cut --shape` searches around.
           `--kind fork` reuses the zigzag-h course and drops a straight S
           tributary band from each peak corner: peak + branch is a
           fork_se_sw_s junction window.
  cut      Search a generated swatch for hex windows whose crossings satisfy
           the tiling contract (centre + width match at each connected edge,
           straight entry spans; for `--shape` turns/diagonals additionally a
           boundary-ring containment gate), LANCZOS-downscale WIN→128, and
           report seam ratios vs the plains picks.
  recolor  Grass-weighted HSV palette-match toward the plains picks' medians
           (the ratified hills grass_match, with the saturation ramp raised
           so the desaturated grey-green water keeps its colour).
  build    Manifest-driven replay: re-derive the applied tiles byte-for-byte
           from the committed swatch (recorded cut windows + recolor), and
           re-check the committed guide against its recorded parameters.
           `--verify` compares against the ratified tiles (selftest hook).

Typical ratification flow (all picks are the user's call, never auto-apply):

    river.py guide --win 384 --band 40 --out guide.png
    gen.py --prompt-file river-prompt.txt --ref <grass_ref> --ref <creek_ref> \
           --ref guide.png --image-size 2K --out swatch.png
    river.py cut swatch.png --out-dir cands --win 384
    river.py recolor cands cands-recolored
    # user ratifies from hosted montages, then windows go into manifest.json
    river.py build --manifest art/foundry/manifest.json --section river \
                   --out-dir /tmp/river --verify frontend/src/board/tiles/river
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# two float HSV implementations live in the foundry with different epsilon
# handling; each stage must keep the one its ratified output was built with
# (cut's for water detection, vignette's for the recolour) or byte-for-byte
# reproducibility breaks.
from cut import bleed_mask  # noqa: E402
from cut import rgb_to_hsv as cut_rgb_to_hsv  # noqa: E402
from gate import (  # noqa: E402
    SEAM_BASELINE_FLOOR,
    dc_step,
    decorrelated_baseline,
    load_tile,
    seam_ratio_texture,
)
from hexgeom import hex_mask  # noqa: E402
from vignette import hsv_to_rgb  # noqa: E402
from vignette import rgb_to_hsv as vignette_rgb_to_hsv  # noqa: E402

TILE = 128
GUIDE_SIZE = 2048
WATER_SAT_MAX = 0.30
SEAM_MAX = 2.0

# cut gates, in TILE px; scaled to swatch px by WIN/TILE at runtime
T_EDGE_CENTRE_OFF = 3.0  # crossing centre vs canonical edge midpoint
T_TOP_BOT_MISMATCH = 2.0  # top vs bottom centre disagreement
T_WIDTH_MISMATCH = 4.0  # top vs bottom width disagreement
T_WIDTH_BAND = (12.0, 36.0)  # crossing water width
T_CENTRE_DRIFT = 26.0  # meander wander inside the tile
T_WATER_HALF_SPAN = 31.0  # water stays inside the flat-edge x-band
T_EDGE_STRAIGHT_SPAN = 18.0  # rows adjacent to each flat edge that must run straight
T_EDGE_STRAIGHT_DEV = 2.5  # max centreline x-deviation within that span

# recolor: grass weight ramps. The saturation ramp starts ABOVE the water's
# saturation (~0.21, vs hills' 0.12 start which would catch it) so the water
# keeps its ratified colour; tan banks sit outside the hue window.
SAT_RAMP = (0.30, 0.42)
HUE_RAMP_LO = (30.0, 40.0)
HUE_RAMP_HI = (165.0, 175.0)
CORE_W = 0.9


# ---------------------------------------------------------------- guide ----


def build_guide(win: int, band: float, seed: int) -> Image.Image:
    """Node-straightened meander band, periodic with the cut window's flat edges.

    Period = 2*EDGE_Y at cut scale (√3/2 · win). Around each period node the
    centerline holds a straight vertical plateau; between plateaus it swings
    in one smooth sin² out-and-back lobe (zero slope at both ends), lobes
    alternating side with rng-varied amplitude.
    """
    period = float(np.sqrt(3) / 2 * win)
    plateau = 24.0 * (win / TILE) / 2  # straight half-run around each node (swatch px)

    rng = np.random.default_rng(seed)
    y = np.arange(GUIDE_SIZE, dtype=np.float64)
    centre = np.full(GUIDE_SIZE, GUIDE_SIZE / 2.0)

    n_cells = int(np.ceil(GUIDE_SIZE / period)) + 1
    for k in range(n_cells):
        y0 = k * period + plateau  # bend start
        y1 = (k + 1) * period - plateau  # bend end
        amp = (1 if k % 2 == 0 else -1) * rng.uniform(0.7, 1.0) * 38.0 * (win / 320)
        sel = (y >= y0) & (y < y1)
        t = (y[sel] - y0) / (y1 - y0)
        centre[sel] += amp * np.sin(np.pi * t) ** 2

    img = np.full((GUIDE_SIZE, GUIDE_SIZE), 255, dtype=np.uint8)
    xs = np.arange(GUIDE_SIZE, dtype=np.float64)[None, :]
    img[np.abs(xs - centre[:, None]) <= band / 2.0] = 128
    return Image.fromarray(img, mode="L").convert("RGB")


# ------------------------------------------------- oriented shape geometry ----

# Screen angle (deg, y-down) of each edge's outward radial, indexed like
# hexgeom.AXIAL_DIRECTIONS: 0=SE 1=NE 2=N 3=NW 4=SW 5=S. The edge midpoint sits
# one apothem along this radial; the edge itself runs perpendicular to it.
EDGE_THETA_DEG = {0: 30.0, 1: -30.0, 2: -90.0, 3: -150.0, 4: 150.0, 5: 90.0}

# Generated oriented shapes -> their connected edges. Render-time L<->R
# mirroring (riverTiles.ts) supplies the missing orientations: turn_n_se
# mirrors to N+SW, turn_s_nw mirrors to S+NE, straight_d mirrors "\" to "/",
# and turn_se_sw / turn_ne_nw / fork_se_sw_s are their own mirror partners.
# fork_se_sw_s is the tributary junction: the SE->SW main course (a turn_se_sw
# peak) with a straight S branch leaving the corner.
SHAPES: dict[str, tuple[int, ...]] = {
    "straight_d": (0, 3),
    "turn_n_se": (2, 0),
    "turn_s_nw": (5, 3),
    "turn_se_sw": (0, 4),
    "turn_ne_nw": (1, 3),
    "fork_se_sw_s": (0, 4, 5),
}

ZZ_ARC_T = 70.0  # arc tangent length, swatch px at win=384 (turn radius = T*sqrt(3))
ZZ_RAY = 800.0  # entry/exit overshoot past the first/last corner (PIL clips)


def _unit(theta_deg: float) -> tuple[float, float]:
    th = math.radians(theta_deg)
    return (math.cos(th), math.sin(th))


def _straight_samples(p0: tuple[float, float], p1: tuple[float, float]) -> list[tuple[float, float]]:
    """~1px-spaced points from p0 to p1 inclusive."""
    n = max(2, int(math.ceil(math.hypot(p1[0] - p0[0], p1[1] - p0[1]))))
    return [
        (p0[0] + (p1[0] - p0[0]) * i / n, p0[1] + (p1[1] - p0[1]) * i / n) for i in range(n + 1)
    ]


def _arc_samples(
    corner: tuple[float, float],
    u_in: tuple[float, float],
    u_out: tuple[float, float],
    t_len: float,
) -> list[tuple[float, float]]:
    """~1px-spaced points along the arc rounding a 60° corner.

    The arc is tangent to the incoming leg at `corner - t_len*u_in` and to the
    outgoing leg at `corner + t_len*u_out`; its radius follows from the 60°
    turn: R = t_len / tan(30°).
    """
    r = t_len / math.tan(math.pi / 6)
    p1 = (corner[0] - t_len * u_in[0], corner[1] - t_len * u_in[1])
    p2 = (corner[0] + t_len * u_out[0], corner[1] + t_len * u_out[1])
    side = 1.0 if u_in[0] * u_out[1] - u_in[1] * u_out[0] > 0 else -1.0
    centre = (p1[0] - side * u_in[1] * r, p1[1] + side * u_in[0] * r)
    a1 = math.atan2(p1[1] - centre[1], p1[0] - centre[0])
    a2 = math.atan2(p2[1] - centre[1], p2[0] - centre[0])
    sweep = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
    n = max(2, int(math.ceil(abs(sweep) * r)))
    return [
        (centre[0] + r * math.cos(a1 + sweep * i / n), centre[1] + r * math.sin(a1 + sweep * i / n))
        for i in range(n + 1)
    ]


def zigzag_geometry(kind: str, win: int) -> dict:
    """Corner positions, alternating flow directions, and anchors for one zigzag kind.

    Layout constants are authored at win=384 on the 2048 canvas and scale with
    the window. zigzag-v flows top->bottom alternating S (90°) and SE (30°)
    legs: corners alternate turn_n_se / turn_s_nw and the SE mid-legs are
    straight_d windows. zigzag-h flows right->left alternating SW (150°) and
    NW (210°) legs: corners alternate turn_ne_nw / turn_se_sw (its mid-legs
    would be "/" diagonals — the render-time mirror covers those, so no
    anchors).
    """
    k = win / 384.0
    if kind == "diag":
        # one dead-straight SE leg through the canvas centre: straight_d
        # windows fit anywhere along it at any cut scale, so anchors just
        # pepper the usable stretch
        u = _unit(30.0)
        mid = GUIDE_SIZE / 2.0
        anchors = [
            {"shape": "straight_d", "cx": mid + t * u[0], "cy": mid + t * u[1]}
            for t in range(-600, 601, 300)
        ]
        return {"dirs": (30.0,), "corners": [], "anchors": anchors, "arc_t": 0.0}
    if kind == "zigzag-v":
        dirs = (90.0, 30.0)
        c0 = (320.0 * k, 520.0 * k)
        seg, n_corners = 560.0 * k, 4
        corner_shapes = ("turn_n_se", "turn_s_nw")
        mid_shapes: tuple[str | None, str | None] = (None, "straight_d")
    elif kind == "zigzag-h":
        dirs = (150.0, -150.0)
        c0 = (1690.0 * k, 1139.0 * k)
        seg, n_corners = 460.0 * k, 4
        corner_shapes = ("turn_ne_nw", "turn_se_sw")
        mid_shapes = (None, None)
    elif kind == "fork":
        # zigzag-h course whose peak (^) corners each drop a straight S branch
        # to the canvas bottom: peak + branch = the fork_se_sw_s junction. The
        # valleys stay turn_ne_nw anchors (bonus turn windows, same as zigzag-h).
        dirs = (150.0, -150.0)
        c0 = (1690.0 * k, 1139.0 * k)
        seg, n_corners = 460.0 * k, 4
        corner_shapes = ("turn_ne_nw", "fork_se_sw_s")
        mid_shapes = (None, None)
    else:
        raise ValueError(f"unknown zigzag kind {kind!r}")

    corners = [c0]
    for i in range(1, n_corners):
        u = _unit(dirs[i % 2])
        prev = corners[-1]
        corners.append((prev[0] + seg * u[0], prev[1] + seg * u[1]))

    anchors = [
        {"shape": corner_shapes[i % 2], "cx": c[0], "cy": c[1]} for i, c in enumerate(corners)
    ]
    for i in range(1, n_corners):
        shape = mid_shapes[i % 2]
        if shape is not None:
            a, b = corners[i - 1], corners[i]
            anchors.append({"shape": shape, "cx": (a[0] + b[0]) / 2, "cy": (a[1] + b[1]) / 2})
    branches = (
        [(c, (c[0], float(GUIDE_SIZE))) for i, c in enumerate(corners) if i % 2 == 1]
        if kind == "fork"
        else []
    )
    return {
        "dirs": dirs,
        "corners": corners,
        "anchors": anchors,
        "arc_t": ZZ_ARC_T * k,
        "branches": branches,
    }


def build_zigzag_guide(kind: str, win: int, band: float) -> tuple[Image.Image, list[dict]]:
    """Zigzag layout band + the shape anchors its windows will be searched around.

    The centerline (straight legs joined by exact 60° arcs) is sampled at ~1px
    spacing and stamped as band/2-radius discs — the same hard-edged grey-128
    band as the straight guide. The straight legs run well past the arc
    tangent points on each side of every corner, so each crossed edge keeps a
    dead-straight entry span inside its window.
    """
    geo = zigzag_geometry(kind, win)
    dirs, corners, arc_t = geo["dirs"], geo["corners"], geo["arc_t"]

    points: list[tuple[float, float]] = []
    if not corners:  # diag: one straight ray through the canvas centre
        u = _unit(dirs[0])
        mid = GUIDE_SIZE / 2.0
        reach = GUIDE_SIZE  # overshoots the corners; PIL clips
        points += _straight_samples(
            (mid - reach * u[0], mid - reach * u[1]), (mid + reach * u[0], mid + reach * u[1])
        )
    else:
        u_first, u_last = _unit(dirs[0]), _unit(dirs[len(corners) % 2])
        entry = (corners[0][0] - ZZ_RAY * u_first[0], corners[0][1] - ZZ_RAY * u_first[1])
        prev_exit = entry
        for i, c in enumerate(corners):
            u_in, u_out = _unit(dirs[i % 2]), _unit(dirs[(i + 1) % 2])
            points += _straight_samples(
                prev_exit, (c[0] - arc_t * u_in[0], c[1] - arc_t * u_in[1])
            )
            points += _arc_samples(c, u_in, u_out, arc_t)
            prev_exit = (c[0] + arc_t * u_out[0], c[1] + arc_t * u_out[1])
        last = corners[-1]
        points += _straight_samples(
            prev_exit, (last[0] + ZZ_RAY * u_last[0], last[1] + ZZ_RAY * u_last[1])
        )
    # fork kind: straight tributary bands dropping from the peak corners
    for p0, p1 in geo.get("branches", []):
        points += _straight_samples(p0, p1)

    img = Image.new("L", (GUIDE_SIZE, GUIDE_SIZE), 255)
    draw = ImageDraw.Draw(img)
    r = band / 2.0
    for x, y in points:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=128)
    return img.convert("RGB"), geo["anchors"]


# ------------------------------------------------------------------ cut ----


def channel_runs(water_row: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous [start, end] index runs of True in a 1-D bool row."""
    xs = np.where(water_row)[0]
    if len(xs) == 0:
        return []
    runs = []
    start = prev = xs[0]
    for x in xs[1:]:
        if x != prev + 1:
            runs.append((start, prev))
            start = x
        prev = x
    runs.append((start, prev))
    return runs


def channel_profile(
    water: np.ndarray, guess_x: int, band: int = 220
) -> tuple[np.ndarray, np.ndarray]:
    """Per-row channel centre and width from the longest water run near `guess_x`.

    Rows with no water run in the band get width 0 and centre NaN — the
    candidate loop rejects any window touching such a row.
    """
    h = water.shape[0]
    centres = np.full(h, np.nan)
    widths = np.zeros(h)
    lo, hi = guess_x - band, guess_x + band
    for y in range(h):
        runs = channel_runs(water[y, lo:hi])
        if not runs:
            continue
        start, end = max(runs, key=lambda r: r[1] - r[0])
        centres[y] = lo + (start + end) / 2.0
        widths[y] = end - start + 1
    return centres, widths


class Gates:
    """The cut gates in swatch px for one window size."""

    def __init__(self, win: int):
        k = win / TILE
        self.win = win
        self.edge_y = 0.8660254 * win / 2.0  # sqrt(3)/2 * win/2: flat-edge offset
        self.max_edge_centre_off = T_EDGE_CENTRE_OFF * k
        self.max_top_bot_mismatch = T_TOP_BOT_MISMATCH * k
        self.max_width_mismatch = T_WIDTH_MISMATCH * k
        self.width_band = (T_WIDTH_BAND[0] * k, T_WIDTH_BAND[1] * k)
        self.max_centre_drift = T_CENTRE_DRIFT * k
        self.max_water_half_span = T_WATER_HALF_SPAN * k
        self.edge_straight_span = int(round(T_EDGE_STRAIGHT_SPAN * k))
        self.edge_straight_dev = T_EDGE_STRAIGHT_DEV * k

    def measure(
        self, centres: np.ndarray, widths: np.ndarray, cx: float, cy: float
    ) -> dict | None:
        """Crossing + meander metrics for a window centred at (cx, cy), or None."""
        y_top = int(round(cy - self.edge_y + 1))
        y_bot = int(round(cy + self.edge_y - 1))
        ys = np.arange(y_top, y_bot + 1)
        seg_c, seg_w = centres[ys], widths[ys]
        if np.isnan(seg_c).any() or (seg_w <= 0).any():
            return None
        span = self.edge_straight_span
        return {
            "ct": float(seg_c[0] - cx),
            "cb": float(seg_c[-1] - cx),
            "wt": float(seg_w[0]),
            "wb": float(seg_w[-1]),
            "drift": float(np.max(np.abs(seg_c - cx))),
            "water_half": float(np.max(np.abs(seg_c - cx) + seg_w / 2.0)),
            "es_top": float(np.max(np.abs(seg_c[:span] - seg_c[0]))),
            "es_bot": float(np.max(np.abs(seg_c[-span:] - seg_c[-1]))),
        }

    def passes(self, m: dict) -> bool:
        return (
            abs(m["ct"]) <= self.max_edge_centre_off
            and abs(m["cb"]) <= self.max_edge_centre_off
            and abs(m["ct"] - m["cb"]) <= self.max_top_bot_mismatch
            and abs(m["wt"] - m["wb"]) <= self.max_width_mismatch
            and self.width_band[0] <= min(m["wt"], m["wb"])
            and max(m["wt"], m["wb"]) <= self.width_band[1]
            and m["drift"] <= self.max_centre_drift
            and m["water_half"] <= self.max_water_half_span
            and m["es_top"] <= self.edge_straight_dev
            and m["es_bot"] <= self.edge_straight_dev
        )


def window_score(m: dict) -> float:
    # meander is wanted: drift isn't penalized, crossings + edge entry dominate
    return (
        abs(m["ct"])
        + abs(m["cb"])
        + 0.5 * abs(m["wt"] - m["wb"])
        + 0.5 * (m["es_top"] + m["es_bot"])
    )


# ------------------------------------------------------------ shaped cut ----


def hex_boundary_dist(win: int) -> np.ndarray:
    """Distance (px, positive inside) from each window pixel to the hex boundary.

    A regular flat-top hexagon is the intersection of six half-planes whose
    outward normals sit at the edge radials; opposite pairs collapse to three
    |dot| terms.
    """
    half = win / 2.0
    ys, xs = np.mgrid[0:win, 0:win]
    x, y = xs - half + 0.5, ys - half + 0.5
    a = 0.8660254 * half
    c30, s30 = math.cos(math.pi / 6), math.sin(math.pi / 6)
    reach = np.maximum(np.abs(x * c30 + y * s30), np.abs(y))
    reach = np.maximum(reach, np.abs(x * -c30 + y * s30))
    return a - reach


def edge_crossing(water: np.ndarray, cx: int, cy: int, edge: int, gates: Gates) -> dict | None:
    """Channel centre/width against one edge of a window centred at (cx, cy).

    Measured in the edge's own frame: sample rows parallel to the edge from
    just inside the crossing inward over the straight-entry span. Returns the
    crossing centre offset from the edge midpoint (`c`, signed along the
    edge), the crossing width `w`, and the max centreline deviation over the
    span (`es`) — or None when any sampled row has no water (no crossing).
    """
    ux, uy = _unit(EDGE_THETA_DEG[edge])
    px_, py_ = -uy, ux
    span = gates.edge_straight_span
    tband = gates.win // 4
    ds = gates.edge_y - 1.0 - np.arange(span, dtype=np.float64)
    ts = np.arange(-tband, tband + 1, dtype=np.float64)
    xs = np.rint(cx + ds[:, None] * ux + ts[None, :] * px_).astype(int)
    ys = np.rint(cy + ds[:, None] * uy + ts[None, :] * py_).astype(int)
    h, w = water.shape
    if xs.min() < 0 or ys.min() < 0 or xs.max() >= w or ys.max() >= h:
        return None
    rows = water[ys, xs]

    centres = np.empty(span)
    widths = np.empty(span)
    for j in range(span):
        runs = channel_runs(rows[j])
        if not runs:
            return None
        start, end = min(runs, key=lambda r: abs((r[0] + r[1]) / 2.0 - tband))
        centres[j] = (start + end) / 2.0 - tband
        widths[j] = end - start + 1
    return {
        "c": float(centres[0]),
        "w": float(widths[0]),
        "es": float(np.max(np.abs(centres - centres[0]))),
    }


def contained(
    water: np.ndarray, cx: int, cy: int, edges: tuple[int, ...], gates: Gates, ring: np.ndarray
) -> bool:
    """No stray water at the window's boundary away from the connected edges.

    `ring` is the boolean (win, win) band just inside the hex boundary. Every
    water pixel in it must sit within the water half-span limit of one of the
    connected edge midpoints — i.e. the creek touches the rim only where
    it is supposed to cross.
    """
    half = gates.win // 2
    win_water = water[cy - half : cy + half, cx - half : cx + half]
    ys, xs = np.nonzero(ring & win_water)
    if len(ys) == 0:
        return False  # a river window with no rim water has no crossings at all
    x, y = xs - half + 0.5, ys - half + 0.5
    ok = np.zeros(len(ys), dtype=bool)
    for e in edges:
        ux, uy = _unit(EDGE_THETA_DEG[e])
        mx, my = gates.edge_y * ux, gates.edge_y * uy
        ok |= np.hypot(x - mx, y - my) <= gates.max_water_half_span
    return bool(ok.all())


def measure_shape(
    water: np.ndarray, cx: int, cy: int, shape: str, gates: Gates, ring: np.ndarray
) -> dict | None:
    """Every edge crossing + containment for one candidate window, or None.

    Gate thresholds are the straight_v ones applied per edge; widths must agree
    pairwise across all connected edges. The cross-edge centre-mismatch check
    only makes sense for opposite (parallel) edges, so turns and forks apply it
    to their opposite pairs only (forks have none).
    """
    edges = SHAPES[shape]
    ms = []
    for e in edges:
        m = edge_crossing(water, cx, cy, e, gates)
        if m is None:
            return None
        ms.append(m)
    if any(abs(m["c"]) > gates.max_edge_centre_off for m in ms):
        return None
    widths = [m["w"] for m in ms]
    if max(widths) - min(widths) > gates.max_width_mismatch:
        return None
    lo, hi = gates.width_band
    if not (lo <= min(widths) and max(widths) <= hi):
        return None
    if any(m["es"] > gates.edge_straight_dev for m in ms):
        return None
    for i, e_a in enumerate(edges):
        for j in range(i + 1, len(edges)):
            if (edges[j] - e_a) % 6 == 3 and abs(ms[i]["c"] + ms[j]["c"]) > (
                gates.max_top_bot_mismatch
            ):
                return None
    if not contained(water, cx, cy, edges, gates, ring):
        return None
    return {
        "cs": [m["c"] for m in ms],
        "ws": widths,
        "ess": [m["es"] for m in ms],
    }


def shape_window_score(m: dict) -> float:
    # the shaped-cut analogue of window_score: crossings + edge entry dominate,
    # width spread tie-breaks (identical to window_score for two-edge shapes)
    return (
        sum(abs(c) for c in m["cs"])
        + 0.5 * (max(m["ws"]) - min(m["ws"]))
        + 0.5 * sum(m["ess"])
    )


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """Binary max-filter with a (2r+1)² square, via shifted ORs."""
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy or dx:
                out |= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    return out


def run_cut_shape(args: argparse.Namespace) -> int:
    """Anchor-guided window search for one oriented shape (turns / straight_d)."""
    gates = Gates(args.win)
    rgb = np.asarray(Image.open(args.swatch).convert("RGB"))
    h_img, w_img = rgb.shape[:2]
    _h, s, _v = cut_rgb_to_hsv(rgb.astype(np.float64) / 255.0)
    water = s < WATER_SAT_MAX

    anchors = [a for a in json.loads(args.anchors.read_text()) if a["shape"] == args.shape]
    if not anchors:
        print(f"no {args.shape!r} anchors in {args.anchors}", file=sys.stderr)
        return 1

    hd = hex_boundary_dist(args.win)
    ring = (hd >= 0) & (hd <= 6.0)
    edges = SHAPES[args.shape]
    half = args.win // 2
    # prefilter: every edge midpoint must sit on (dilated) water before the
    # full rotated-frame measurement runs — the search grid is ~15k points per
    # anchor and nearly all of them miss the channel entirely.
    near_water = _dilate(water, 2)
    mids = []
    for e in edges:
        ux, uy = _unit(EDGE_THETA_DEG[e])
        mids.append((int(round(gates.edge_y * ux)), int(round(gates.edge_y * uy))))

    kept: list[tuple[float, int, int, dict]] = []
    for anchor in anchors:
        best: tuple[float, int, int, dict] | None = None
        ax, ay = int(round(anchor["cx"])), int(round(anchor["cy"]))
        for cy in range(ay - args.search_r, ay + args.search_r + 1, 2):
            for cx in range(ax - args.search_r, ax + args.search_r + 1, 2):
                if cx - half < 0 or cy - half < 0 or cx + half > w_img or cy + half > h_img:
                    continue
                if not all(near_water[cy + my, cx + mx] for mx, my in mids):
                    continue
                m = measure_shape(water, cx, cy, args.shape, gates, ring)
                if m is None:
                    continue
                sc = shape_window_score(m)
                if best is None or sc < best[0]:
                    best = (sc, cx, cy, m)
        if best is not None:
            kept.append(best)

    if not kept:
        print(f"no {args.shape} window passed the crossing gates", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    k = args.win / TILE
    print(f"shape {args.shape}, window {args.win}px ({k:.2f}:1); metrics in tile-scale px:")
    heads = "".join(
        f"{'c' + str(e):>7}{'w' + str(e):>6}{'es' + str(e):>6}" for e in edges
    )
    print(f"{'file':<36}{heads}{'score':>7}")
    paths = []
    for sc, cx, cy, m in kept:
        x0, y0 = cx - half, cy - half
        p = args.out_dir / f"cand_{args.shape}_x{x0:04d}_y{y0:04d}.png"
        cut_window(rgb, x0, y0, args.win).save(p)
        paths.append(p)
        cols = "".join(
            f"{m['cs'][i] / k:>7.1f}{m['ws'][i] / k:>6.0f}{m['ess'][i] / k:>6.1f}"
            for i in range(len(edges))
        )
        print(f"{p.name:<36}{cols}{sc / k:>7.2f}")

    grass_edges = tuple(d for d in range(6) if d not in edges)
    seam_report(paths, args.plains, dirs=grass_edges, self_dirs=())
    return 0


def cut_window(swatch_rgb: np.ndarray, x0: int, y0: int, win: int) -> Image.Image:
    """Crop one WIN-px window, LANCZOS-downscale to TILE, apply the hex mask."""
    crop = Image.fromarray(swatch_rgb[y0 : y0 + win, x0 : x0 + win])
    small = np.asarray(crop.resize((TILE, TILE), Image.LANCZOS))
    mask = bleed_mask(hex_mask(TILE, TILE), 1)
    return Image.fromarray(np.dstack([small, mask]), mode="RGBA")


def seam_report(
    paths: list[Path],
    plains_dir: Path,
    dirs: tuple[int, ...] = (0, 1, 3, 4),
    self_dirs: tuple[int, ...] = (2, 5),
) -> None:
    """Print worst grass-edge seam ratio vs the plains picks (over `dirs`).

    `self_dirs` (the water-crossing edges, meaningful for straight shapes that
    tile with themselves) adds a self-pairing column; shaped cuts pass `()`
    since their water edges pair with *other* shapes — those seams are judged
    on the assembled preview instead.
    """
    plains_tiles = [load_tile(p, TILE) for p in sorted(plains_dir.glob("*.png"))]
    print(
        "\nseam ratios (texture mode, max over listed pairings; gate at "
        f"{SEAM_MAX}) + worst |dc| step:"
    )
    print(f"{'file':<36}{'grass':>8}{'water-self':>11}{'dc':>7}")
    for p in paths:
        r_rgb = load_tile(p, TILE)[..., :3].astype(np.float64)
        r_base = max(decorrelated_baseline(r_rgb, TILE), SEAM_BASELINE_FLOOR)
        worst_grass = 0.0
        worst_dc = 0.0
        for parr in plains_tiles:
            p_rgb = parr[..., :3].astype(np.float64)
            p_base = max(decorrelated_baseline(p_rgb, TILE), SEAM_BASELINE_FLOOR)
            for d in dirs:
                worst_grass = max(
                    worst_grass,
                    seam_ratio_texture(r_rgb, p_rgb, d, TILE, r_base),
                    seam_ratio_texture(p_rgb, r_rgb, d, TILE, p_base),
                )
                worst_dc = max(worst_dc, abs(dc_step(r_rgb, p_rgb, d, TILE)))
        if self_dirs:
            water_self = max(
                seam_ratio_texture(r_rgb, r_rgb, d, TILE, r_base) for d in self_dirs
            )
            self_col, over_self = f"{water_self:>11.2f}", water_self > SEAM_MAX
        else:
            self_col, over_self = f"{'-':>11}", False
        flag = "" if worst_grass <= SEAM_MAX and not over_self else "  <-- over gate"
        print(f"{p.name:<36}{worst_grass:>8.2f}{self_col}{worst_dc:>7.1f}{flag}")


def run_cut(args: argparse.Namespace) -> int:
    gates = Gates(args.win)
    rgb = np.asarray(Image.open(args.swatch).convert("RGB"))
    h_img, w_img = rgb.shape[:2]
    _h, s, _v = cut_rgb_to_hsv(rgb.astype(np.float64) / 255.0)
    water = s < WATER_SAT_MAX

    guess_x = int(np.argmax(water.mean(axis=0)))
    centres, widths = channel_profile(water, guess_x)

    half = args.win // 2
    cands = []
    for cy in range(half, h_img - half, 2):
        y_top = int(round(cy - gates.edge_y + 1))
        y_bot = int(round(cy + gates.edge_y - 1))
        if np.isnan(centres[y_top]) or np.isnan(centres[y_bot]):
            continue
        cx = int(round((centres[y_top] + centres[y_bot]) / 2.0))
        if cx - half < 0 or cx + half > w_img:
            continue
        m = gates.measure(centres, widths, cx, cy)
        if m is None or not gates.passes(m):
            continue
        cands.append((window_score(m), cx, cy, m))

    cands.sort(key=lambda c: c[0])
    # thin out near-duplicate windows (within a hex of an already-kept one)
    kept: list[tuple[float, int, int, dict]] = []
    for c in cands:
        if all(abs(c[2] - k[2]) >= args.win for k in kept):
            kept.append(c)
        if len(kept) == args.top_n:
            break

    if not kept:
        print("no candidate window passed the crossing gates", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    k = args.win / TILE
    print(f"window {args.win}px ({k:.2f}:1); metrics in tile-scale px:")
    print(
        f"{'file':<28}{'ct':>7}{'cb':>7}{'wt':>6}{'wb':>6}{'drift':>7}{'whalf':>7}"
        f"{'esT':>6}{'esB':>6}{'score':>7}"
    )
    paths = []
    for sc, cx, cy, m in kept:
        x0, y0 = cx - half, cy - half
        p = args.out_dir / f"cand_x{x0:04d}_y{y0:04d}.png"
        cut_window(rgb, x0, y0, args.win).save(p)
        paths.append(p)
        print(
            f"{p.name:<28}{m['ct'] / k:>7.1f}{m['cb'] / k:>7.1f}{m['wt'] / k:>6.0f}"
            f"{m['wb'] / k:>6.0f}{m['drift'] / k:>7.1f}{m['water_half'] / k:>7.1f}"
            f"{m['es_top'] / k:>6.1f}{m['es_bot'] / k:>6.1f}{sc / k:>7.2f}"
        )

    seam_report(paths, args.plains)
    return 0


# -------------------------------------------------------------- recolor ----


def grass_weight(h: np.ndarray, s: np.ndarray) -> np.ndarray:
    return (
        np.clip((s - SAT_RAMP[0]) / (SAT_RAMP[1] - SAT_RAMP[0]), 0, 1)
        * np.clip((h - HUE_RAMP_LO[0]) / (HUE_RAMP_LO[1] - HUE_RAMP_LO[0]), 0, 1)
        * np.clip((HUE_RAMP_HI[1] - h) / (HUE_RAMP_HI[1] - HUE_RAMP_HI[0]), 0, 1)
    )


def grass_core_medians(img: Image.Image) -> tuple[float, float, float]:
    im = np.asarray(img.convert("RGBA")).astype(float)
    h, s, v = vignette_rgb_to_hsv(im[..., :3] / 255.0)
    core = (im[..., 3] > 128) & (grass_weight(h, s) > CORE_W)
    return float(np.median(h[core])), float(np.median(s[core])), float(np.median(v[core]))


def plains_target(plains_dir: Path) -> tuple[float, float, float]:
    """The plains picks' grass-core HSV medians — the recolor target."""
    picks = sorted(plains_dir.glob("*.png"))
    if not picks:
        raise SystemExit(f"no plains picks in {plains_dir}")
    meds = np.array([grass_core_medians(Image.open(p)) for p in picks])
    t = np.median(meds, axis=0)
    return float(t[0]), float(t[1]), float(t[2])


def recolor_tile(img: Image.Image, target: tuple[float, float, float]) -> Image.Image:
    """Shift the tile's grass toward `target` (H additive, S/V multiplicative),
    weighted per-pixel by `grass_weight` so water and tan banks stay put."""
    im = np.asarray(img.convert("RGBA")).astype(float)
    h, s, v = vignette_rgb_to_hsv(im[..., :3] / 255.0)
    w = grass_weight(h, s)
    core = (im[..., 3] > 128) & (w > CORE_W)
    med_h, med_s, med_v = (float(np.median(x[core])) for x in (h, s, v))
    h2 = h + w * (target[0] - med_h)
    s2 = np.clip(s * (1 + w * (target[1] / med_s - 1)), 0, 1)
    v2 = np.clip(v * (1 + w * (target[2] / med_v - 1)), 0, 1)
    out_rgb = (hsv_to_rgb(h2, s2, v2) * 255).round().clip(0, 255)
    return Image.fromarray(np.dstack([out_rgb, im[..., 3]]).astype(np.uint8), "RGBA")


def run_recolor(args: argparse.Namespace) -> int:
    target = plains_target(args.plains)
    print(f"plains target: h {target[0]:.1f} s {target[1]:.2f} v {target[2]:.2f}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for p in sorted(args.in_dir.glob("*.png")):
        img = Image.open(p)
        med = grass_core_medians(img)
        recolor_tile(img, target).save(args.out_dir / p.name)
        print(
            f"  {p.name}: grass h {med[0]:.1f}->{target[0]:.1f} "
            f"s {med[1]:.2f}->{target[1]:.2f} v {med[2]:.2f}->{target[2]:.2f}"
        )
    return 0


# -------------------------------------------------------------- preview ----

# Connected-edge set -> (tile shape, render-time L<->R mirror), the python twin
# of the frontend's riverTiles.ts mapping (extended to turns + forks). Keys are
# sorted tuples.
ORIENTED_TILES: dict[tuple[int, ...], tuple[str, bool]] = {
    (2, 5): ("straight_v", False),
    (0, 3): ("straight_d", False),
    (1, 4): ("straight_d", True),
    (0, 2): ("turn_n_se", False),
    (2, 4): ("turn_n_se", True),
    (3, 5): ("turn_s_nw", False),
    (1, 5): ("turn_s_nw", True),
    (0, 4): ("turn_se_sw", False),
    (1, 3): ("turn_ne_nw", False),
    (0, 4, 5): ("fork_se_sw_s", False),
}

# Travel directions of the default preview course: 12 hexes hitting all nine
# oriented variants (the five generated shapes plus every mirror) exactly once
# or twice, with no non-consecutive hexes adjacent.
PREVIEW_COURSE = "5,0,5,5,4,4,3,4,5,0,0"


def shape_files(tile_dirs: list[Path], shape: str) -> list[Path]:
    """Tile files for a shape across the given dirs: applied naming
    (`{shape}.png` / `{shape}_NN.png`) and cut-candidate naming (`cand_{shape}_*`)."""
    files: list[Path] = []
    for d in tile_dirs:
        files += sorted(d.glob(f"{shape}.png"))
        files += sorted(d.glob(f"{shape}_[0-9]*.png"))
        files += sorted(d.glob(f"cand_{shape}_*.png"))
    if not files:
        raise SystemExit(f"no tile found for shape {shape!r} in {[str(d) for d in tile_dirs]}")
    return files


def run_preview(args: argparse.Namespace) -> int:
    """Composite a winding river course over plains, board-style, for review."""
    from hexgeom import AXIAL_DIRECTIONS, axial_to_pixel

    def walk(start: tuple[int, int], travel: list[int]) -> list[tuple[int, int]]:
        coords = [start]
        for d in travel:
            dq, dr = AXIAL_DIRECTIONS[d]
            coords.append((coords[-1][0] + dq, coords[-1][1] + dr))
        return coords

    def chain_edges(
        coords: list[tuple[int, int]],
        travel: list[int],
        edge_sets: dict[tuple[int, int], set[int]],
        entry_dir: int | None,
    ) -> None:
        """Each hex connects back the way it came and out the way it leaves; the
        ends pretend the river continues off-frame in their travel direction
        (`entry_dir=None`), or, for a spur, the first hex arrived via `entry_dir`
        from the branch point."""
        in_dirs = [travel[0] if entry_dir is None else entry_dir] + travel
        out_dirs = travel + [travel[-1]]
        for c, i, o in zip(coords, in_dirs, out_dirs):
            edge_sets.setdefault(c, set()).update({(i + 3) % 6, o})

    travel = [int(t) for t in args.course.split(",")]
    coords = walk((0, 0), travel)
    edge_sets: dict[tuple[int, int], set[int]] = {}
    chain_edges(coords, travel, edge_sets, entry_dir=None)

    # spurs: tributaries branching off a main-course hex ("index:dir,dir,...").
    # The branch point gains the spur's first travel dir as an extra edge —
    # three edges make it a fork.
    for spec in args.spur or []:
        idx_s, dirs_s = spec.split(":")
        spur_travel = [int(t) for t in dirs_s.split(",")]
        bp = coords[int(idx_s)]
        edge_sets[bp].add(spur_travel[0])
        spur_coords = walk(bp, spur_travel)[1:]
        chain_edges(spur_coords, spur_travel[1:] or [spur_travel[0]], edge_sets, spur_travel[0])
        coords += spur_coords

    plains = [Image.open(p).convert("RGBA") for p in sorted(args.plains.glob("*.png"))]
    if not plains:
        raise SystemExit(f"no plains tiles in {args.plains}")
    shape_cache: dict[str, list[Path]] = {}
    uses: dict[str, int] = {}

    size = TILE / 2.0
    centres = [axial_to_pixel(q, r, size) for q, r in coords]
    pad = 1.6 * TILE
    x0 = min(c[0] for c in centres) - pad
    y0 = min(c[1] for c in centres) - pad
    x1 = max(c[0] for c in centres) + pad
    y1 = max(c[1] for c in centres) + pad
    canvas = Image.new("RGBA", (int(x1 - x0), int(y1 - y0)), (26, 26, 26, 255))

    def paste(img: Image.Image, q: int, r: int) -> None:
        cx, cy = axial_to_pixel(q, r, size)
        canvas.alpha_composite(img, (int(cx - size - x0), int(cy - size - y0)))

    qs = [q for q, _ in coords]
    rs = [r for _, r in coords]
    for q in range(min(qs) - 2, max(qs) + 3):
        for r in range(min(rs) - 2, max(rs) + 3):
            cx, cy = axial_to_pixel(q, r, size)
            if -pad <= cx - x0 <= x1 - x0 + pad and -pad <= cy - y0 <= y1 - y0 + pad:
                paste(plains[(q * 5 + r * 3) % len(plains)], q, r)

    print("course tiles:")
    for (q, r), edges in edge_sets.items():
        key = tuple(sorted(edges))
        shape, mirror = ORIENTED_TILES[key]
        files = shape_cache.setdefault(shape, shape_files(args.tiles, shape))
        img = Image.open(files[uses.get(shape, 0) % len(files)]).convert("RGBA")
        uses[shape] = uses.get(shape, 0) + 1
        if mirror:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        paste(img, q, r)
        print(f"  ({q:>3},{r:>3}) edges {key} -> {shape}{' (mirrored)' if mirror else ''}")

    canvas.convert("RGB").save(args.out)
    print(f"wrote {args.out} ({canvas.width}x{canvas.height})")
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
    manifest = json.loads(args.manifest.read_text())
    if args.section not in manifest:
        raise SystemExit(f"section {args.section!r} not in {args.manifest}")
    section = manifest[args.section]
    root = args.manifest.parent

    # the committed guide must match its recorded parameters (gen provenance)
    guide_cfg = section["layout_guide"]
    committed = np.asarray(Image.open(root / guide_cfg["file"]).convert("RGB"))
    rebuilt = np.asarray(
        build_guide(int(guide_cfg["win_px"]), float(guide_cfg["band_px"]), int(guide_cfg["seed"]))
    )
    if not np.array_equal(committed, rebuilt):
        print(f"GUIDE DIFFERS from recorded parameters: {guide_cfg['file']}")
        return 1
    print(f"guide reproduces from recorded parameters: {guide_cfg['file']}")

    cut_cfg = section["cut"]
    win = int(cut_cfg["win_px"])
    swatch = np.asarray(Image.open(root / section["swatch"]).convert("RGB"))
    target = plains_target(root / section["recolor"]["plains_picks"])

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for w in cut_cfg["windows"]:
        tile = cut_window(swatch, int(w["x0"]), int(w["y0"]), win)
        recolor_tile(tile, target).save(args.out_dir / w["tile"])
        print(f"{w['tile']}: window ({w['x0']}, {w['y0']})+{win}")

    # Additional swatches (bend/diagonal slices), each with its own zigzag/diag
    # guide and cut scale but sharing the section's recolor target.
    for src in section.get("sources", []):
        g = src["layout_guide"]
        committed_src = np.asarray(Image.open(root / g["file"]).convert("RGB"))
        rebuilt_src, _ = build_zigzag_guide(g["kind"], int(g["win_px"]), float(g["band_px"]))
        if not np.array_equal(committed_src, np.asarray(rebuilt_src.convert("RGB"))):
            print(f"GUIDE DIFFERS from recorded parameters: {g['file']}")
            return 1
        print(f"guide reproduces from recorded parameters: {g['file']}")

        src_cut = src["cut"]
        src_win = int(src_cut["win_px"])
        src_swatch = np.asarray(Image.open(root / src["swatch"]).convert("RGB"))
        for w in src_cut["windows"]:
            tile = cut_window(src_swatch, int(w["x0"]), int(w["y0"]), src_win)
            recolor_tile(tile, target).save(args.out_dir / w["tile"])
            print(f"{w['tile']}: window ({w['x0']}, {w['y0']})+{src_win} from {src['swatch']}")

    if args.verify:
        if not verify_against(args.out_dir, args.verify):
            return 1
        print("verify: all tiles byte-identical")
    return 0


# ------------------------------------------------------------------ CLI ----


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    default_plains = SCRIPT_DIR.parent.parent / "art" / "foundry" / "plains" / "picks"

    g = sub.add_parser("guide", help="layout diagram (straight meander or bend zigzag)")
    g.add_argument("--win", type=int, required=True, help="cut window px (sets the period)")
    g.add_argument("--band", type=float, required=True, help="band width, swatch px")
    g.add_argument(
        "--kind",
        choices=("straight", "zigzag-v", "zigzag-h", "diag", "fork"),
        default="straight",
        help="straight = the ratified straight_v meander; zigzag-* = the bend "
        "guides; diag = one straight SE leg for straight_d; fork = zigzag-h "
        "whose peaks each drop a straight S tributary (fork_se_sw_s windows)",
    )
    g.add_argument("--seed", type=int, default=23, help="straight kind only (lobe amplitudes)")
    g.add_argument("--out", type=Path, required=True)
    g.add_argument(
        "--anchors-out",
        type=Path,
        help="zigzag kinds: write the shape-anchor sidecar JSON here (for cut --shape)",
    )

    c = sub.add_parser("cut", help="search a swatch for gate-passing hex windows")
    c.add_argument("swatch", type=Path)
    c.add_argument("--out-dir", type=Path, required=True)
    c.add_argument("--win", type=int, default=384, help="window px, downscaled to 128")
    c.add_argument("--top-n", type=int, default=6)
    c.add_argument("--plains", type=Path, default=default_plains, help="plains picks dir")
    c.add_argument(
        "--shape",
        choices=("straight_v", *SHAPES),
        default="straight_v",
        help="straight_v = the original flat-edge column search; others need --anchors",
    )
    c.add_argument(
        "--anchors",
        type=Path,
        help="anchor sidecar JSON from `guide --kind zigzag-* --anchors-out`",
    )
    c.add_argument(
        "--search-r", type=int, default=120, help="search radius around each anchor, swatch px"
    )

    r = sub.add_parser("recolor", help="palette-match grass toward the plains picks")
    r.add_argument("in_dir", type=Path)
    r.add_argument("out_dir", type=Path)
    r.add_argument("--plains", type=Path, default=default_plains, help="plains picks dir")

    v = sub.add_parser("preview", help="composite a winding river course over plains")
    v.add_argument(
        "--tiles",
        action="append",
        required=True,
        type=Path,
        help="tile dir searched for shape files, in order (repeatable)",
    )
    v.add_argument("--plains", type=Path, default=default_plains, help="plains picks dir")
    v.add_argument(
        "--course",
        default=PREVIEW_COURSE,
        help="comma-separated travel directions (default exercises all nine variants)",
    )
    v.add_argument(
        "--spur",
        action="append",
        help='tributary branching off a course hex, "index:dir,dir,..." '
        "(repeatable); the branch point becomes a fork",
    )
    v.add_argument("--out", type=Path, required=True)

    b = sub.add_parser("build", help="manifest-driven replay of the ratified tiles")
    b.add_argument("--manifest", required=True, type=Path, help="art/foundry/manifest.json")
    b.add_argument("--section", default="river", help="manifest section (default river)")
    b.add_argument("--out-dir", required=True, type=Path)
    b.add_argument(
        "--verify",
        type=Path,
        help="byte-compare the output against this directory of ratified tiles",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cmd == "guide":
        if args.kind == "straight":
            img = build_guide(args.win, args.band, args.seed)
            img.save(args.out)
            print(f"wrote {args.out}  win {args.win}px, band {args.band:.0f}px, seed {args.seed}")
            return 0
        if args.anchors_out is None:
            raise SystemExit(f"guide --kind {args.kind} requires --anchors-out")
        img, anchors = build_zigzag_guide(args.kind, args.win, args.band)
        img.save(args.out)
        print(f"wrote {args.out}  {args.kind}, win {args.win}px, band {args.band:.0f}px")
        args.anchors_out.write_text(json.dumps(anchors, indent=1) + "\n")
        for a in anchors:
            print(f"  anchor {a['shape']:<12} ({a['cx']:.0f}, {a['cy']:.0f})")
        print(f"wrote {args.anchors_out}")
        return 0
    if args.cmd == "cut":
        if args.shape == "straight_v":
            return run_cut(args)
        if args.anchors is None:
            raise SystemExit(f"cut --shape {args.shape} requires --anchors")
        return run_cut_shape(args)
    if args.cmd == "recolor":
        return run_recolor(args)
    if args.cmd == "preview":
        return run_preview(args)
    return run_build(args)


if __name__ == "__main__":
    sys.exit(main())
