#!/usr/bin/env python3
"""Road tile compositor: bake a painterly road stroke onto ratified terrain
bases to produce the "road" oriented terrain class (ROADMAP Phase 8, Slice T5).

Unlike the bridge foundry — which segments a perspective deck sprite off a white
sheet — a road tile is *fully deterministic*: no AI generation, no network. A
road tile = a ratified painterly terrain base (plains/hills) + a road stroke
drawn to match the render-time vector road overlay (`Board.tsx`: colour
0x6b4f2a, width 6 world px = 12 tile px). Same colour/width/noise register as the
bridge road ramps (`bridge.py`), so a road hex and a bridge ramp read as one
continuous material across a shared edge.

## Geometry — the load-bearing constraint

The road must cross every connected edge **exactly at the edge midpoint**,
travelling **perpendicular to the edge** (along the midpoint->hex-centre line,
which for a regular hex is the edge normal), and must stay perfectly straight for
at least ``APPROACH_PX = 20`` tile px inward before any curvature. Curves that run
into the edge produced visible kinks at hex junctions in earlier experiments even
when endpoints matched — hence the hard straight-approach rule.

For a flat-top hex the edge-normal line through any edge midpoint passes through
the hex centre. Two such normals therefore intersect **at the centre**, which is
exactly the quadratic-Bezier control point that makes the tangents at the Bezier
endpoints continue the two straight approaches (G1 continuity). So:

- Straights (``straight_v`` {2,5}, ``straight_d`` {0,3}): antipodal edges — the
  whole path is one straight line through the centre.
- Gentle turns (edges 120° apart): two straight 20px approach segments from the
  edge midpoints inward along their normals, joined by a quadratic Bezier
  ``A0 -> centre -> A1`` (control point = the centre, the intersection of the two
  extended approach lines). Sampled at ``BEZIER_STEPS`` and drawn as a round-
  jointed polyline, like bridge.py's ramps.
- Ends (single edge): a straight line from the edge midpoint through the approach
  to the hex centre, terminated with a round cap.

Edge indices (flat-top, y-down): 0=se 1=ne 2=n 3=nw 4=sw 5=s (matches
`riverTiles.ts` / `hexgeom.AXIAL_DIRECTIONS`). Only the ten **canonical** shapes
are baked; the render-time L<->R mirror produces the rest (do NOT bake mirrors).

## Stroke treatment (two per shape, for the user to compare)

- ``flat``  — a flat ROAD_COLOR stroke + seeded per-pixel tonal noise (amp 7.0),
  byte-identical primitive to the bridge road ramp (imported ``add_ramp_noise`` /
  ``RAMP_NOISE_AMP``).
- ``edged`` — same, plus a darker outer band: a full-width (12px) stroke in
  ROAD_COLOR * ``EDGE_SHADE_FACTOR`` (0.75 value) is drawn first, then a
  ``CORE_WIDTH_PX`` (8px) core stroke in ROAD_COLOR on top, leaving a ~2px darker
  band each side. The band is baked BEFORE the noise pass. Fully deterministic;
  ``factor`` + ``core_width`` recorded in the sidecar.

## Compositing order (per tile)

load base RGBA -> draw the road layer (stroke + treatment + noise on its own RGBA
layer) -> ``alpha_composite`` onto the base -> **reclip the final alpha to the
base tile's own alpha exactly**. Seam compatibility with neighbouring ratified
tiles is inherited from the base; the road never extends the silhouette (the
round caps that overhang the hex edge at through-road midpoints are clipped away,
so the road visually stops at the edge, where the neighbour's half continues).

## Gates (fail loud — raise ``RoadGateError``, never skip)

For every connected edge: (1) a road pixel exists within a 3px disc of the edge
midpoint and the crossing width along the edge tangent is ROAD_WIDTH_PX ± 3;
(2) the straight approach is a road pixel every 2px for 20px inward and the
stroke centre stays < 1.5px off the normal line. Plus: the reclip preserves the
base alpha exactly (silhouette unchanged); for ends, the centre cap touches no
non-connected edge midpoint's 3px disc.

## Manifest ``road`` section schema (consumed by ``build``)

``build`` is a manifest-driven replay like ``bridge.py build`` (the selftest
hook). The ``road`` section does not exist in ``art/foundry/manifest.json`` yet;
at apply time it is written from the ratified candidates' sidecars and has the
shape::

    "road": {
      "class": "<prose provenance>",
      "geometry": {
        "approach_px": 20, "road_width_px": 12, "road_color": "0x6b4f2a",
        "core_width_px": 8, "edge_shade_factor": 0.75,
        "noise_amp": 7.0, "bezier_steps": 40
      },
      "shapes": { "<shape>": [<edge indices>], ... },
      "tiles": [
        { "tile": "plains_straight_v_00.png",   # target basename
          "terrain": "plains", "shape": "straight_v",
          "base": "plains/picks/00.png",         # relative to manifest parent
          "treatment": "flat", "seed": 71234 },
        ...
      ]
    }

Each tile's target path at apply time is
``frontend/src/board/tiles/road/{terrain}_{shape}_{nn}.png`` — the ``_{nn}``
suffix is the base-variant index; the frontend groups oriented variants by
stripping ``_\d+$``, so every variant of ``{terrain}_{shape}`` lands in one
oriented group. All geometry constants are read back from the section (not the
module) so a future constant change can't silently break replay. Base paths
resolve relative to the manifest's parent (``art/foundry``); the ratified
plains/hills picks (``{terrain}/picks/{nn}.png``) are byte-identical to the
applied ``frontend/src/board/tiles/{terrain}/{nn}.png`` bases.

Two modes:

  candidates  Bake the review set — every shape x both treatments x
              {plains, hills} over base variant 00 (10*2*2 = 40 tiles) with a
              JSON sidecar per tile — plus the montage + adjacency previews.
  build       Manifest-driven replay of the applied tiles; ``--verify DIR``
              byte-compares.

Example:
    python3 road.py candidates --out-dir art/foundry/road/review/candidates
    python3 road.py build --manifest art/foundry/manifest.json --section road \\
        --out-dir /tmp/road --verify frontend/src/board/tiles/road
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# Reuse the bridge foundry's constants + helpers so roads and bridge ramps are
# the same material by construction (prefer importing over copying).
from bridge import (  # noqa: F401
    CENTRE,
    HEX_WIDTH,
    RAMP_NOISE_AMP,
    ROAD_COLOR,
    ROAD_WIDTH_PX,
    TILE_PX,
    add_ramp_noise,
    edge_midpoint,
)
from hexgeom import AXIAL_DIRECTIONS, axial_to_pixel

# -- road geometry constants -----------------------------------------------
# Perfectly-straight run inward from each edge midpoint before any curvature.
APPROACH_PX = 20
# Quadratic-Bezier sampling density for turns (round-jointed polyline).
BEZIER_STEPS = 40

# `edged` treatment: a darker outer band via a full-width dark underlay + a
# narrower ROAD_COLOR core drawn on top (leaves ~2px dark each side).
EDGE_SHADE_FACTOR = 0.75
CORE_WIDTH_PX = 8
ROAD_DARK = tuple(int(round(c * EDGE_SHADE_FACTOR)) for c in ROAD_COLOR)

# Seed derivation: a fixed base + a stable hash of (terrain, shape, treatment,
# variant). hashlib (not Python's salted hash()) keeps it reproducible.
BASE_SEED = 71000

# Gate tolerances.
CROSSING_DISC_R = 3.0
CROSSING_WIDTH_TOL = 3.0
APPROACH_STEP_PX = 2.0
APPROACH_CENTRE_TOL = 1.5

# The ten canonical oriented shapes -> their connected edge sets. Names + edge
# sets mirror `riverTiles.ts` (ORIENTED_SINGLES / ORIENTED_PAIRS canonical
# entries). Mirrors are produced at render time and are NOT baked here.
SHAPES: dict[str, list[int]] = {
    "straight_v": [2, 5],
    "straight_d": [0, 3],
    "turn_n_se": [0, 2],
    "turn_s_nw": [3, 5],
    "turn_se_sw": [0, 4],
    "turn_ne_nw": [1, 3],
    "end_se": [0],
    "end_ne": [1],
    "end_n": [2],
    "end_s": [5],
}

TERRAINS = ("plains", "hills")


class RoadGateError(RuntimeError):
    """Raised when a composited road tile fails a placement gate."""


# ----------------------------------------------------------------- geometry


def edge_normal(edge: int) -> tuple[float, float]:
    """Unit vector from the edge midpoint toward the hex centre (the edge normal).

    For a flat-top hex the outward offset of the neighbour centre is
    ``axial_to_pixel(dir)``; the inward normal is its negation, normalised.
    """
    dq, dr = AXIAL_DIRECTIONS[edge]
    from hexgeom import axial_to_pixel

    ox, oy = axial_to_pixel(dq, dr, HEX_WIDTH / 2)
    length = math.hypot(ox, oy)
    return (-ox / length, -oy / length)


def edge_tangent(edge: int) -> tuple[float, float]:
    """Unit vector along the edge (perpendicular to the normal)."""
    nx, ny = edge_normal(edge)
    return (-ny, nx)


def _bezier(p0, p1, p2, steps: int) -> list[tuple[float, float]]:
    """Quadratic-Bezier sample points, inclusive of both endpoints."""
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
        y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
        pts.append((x, y))
    return pts


def road_path(shape: str) -> dict:
    """Build the road centreline polyline for a shape, matching the geometry rules.

    Returns ``{"points": [...], "control_point": [...]|None, "kind": str}``.
    The polyline endpoints are the connected edge midpoints (through-roads) or an
    edge midpoint + the hex centre (ends); round caps at the ends. The straight
    approaches are implicit first/last segments; for turns the middle is a
    Bezier through the centre.
    """
    edges = SHAPES[shape]
    centre = (CENTRE, CENTRE)

    if len(edges) == 1:
        edge = edges[0]
        mid = edge_midpoint(edge)
        return {"points": [mid, centre], "control_point": None, "kind": "end"}

    e0, e1 = edges
    m0 = edge_midpoint(e0)
    m1 = edge_midpoint(e1)

    # Antipodal edges (circular distance 3) => straight line through the centre.
    circ = min((e1 - e0) % 6, (e0 - e1) % 6)
    if circ == 3:
        return {"points": [m0, m1], "control_point": None, "kind": "straight"}

    # Gentle turn: straight approaches, then a Bezier whose control point is the
    # intersection of the two extended approach lines (== the hex centre for a
    # regular hex, since each edge normal passes through the centre).
    n0 = edge_normal(e0)
    n1 = edge_normal(e1)
    a0 = (m0[0] + n0[0] * APPROACH_PX, m0[1] + n0[1] * APPROACH_PX)
    a1 = (m1[0] + n1[0] * APPROACH_PX, m1[1] + n1[1] * APPROACH_PX)
    ctrl = _approach_intersection(a0, n0, a1, n1)
    bez = _bezier(a0, ctrl, a1, BEZIER_STEPS)
    return {
        "points": [m0, *bez, m1],
        "control_point": [round(ctrl[0], 4), round(ctrl[1], 4)],
        "kind": "turn",
    }


def _approach_intersection(a0, n0, a1, n1) -> tuple[float, float]:
    """Intersection of ray ``a0 + s*n0`` with ray ``a1 + t*n1`` (approach lines).

    Solves the 2x2 system; raises if the directions are parallel (would mean the
    two approaches never meet — not a gentle turn, a bug in the shape table).
    """
    det = n0[0] * (-n1[1]) - n0[1] * (-n1[0])
    if abs(det) < 1e-9:
        raise RoadGateError("approach lines are parallel; not a gentle turn")
    dx = a1[0] - a0[0]
    dy = a1[1] - a0[1]
    s = (dx * (-n1[1]) - dy * (-n1[0])) / det
    return (a0[0] + s * n0[0], a0[1] + s * n0[1])


# ------------------------------------------------------------------ drawing


def draw_stroke(draw: ImageDraw.ImageDraw, pts, width: int, color) -> None:
    """Thick round-capped, round-jointed stroke along a polyline.

    Byte-identical primitive to ``bridge.draw_ramp`` (line joint="curve" +
    ellipse end caps), generalised over colour so the `edged` treatment can draw
    a darker underlay pass.
    """
    draw.line(pts, fill=color, width=width, joint="curve")
    r = width / 2.0
    for cap in (pts[0], pts[-1]):
        draw.ellipse([cap[0] - r, cap[1] - r, cap[0] + r, cap[1] + r], fill=color)


def build_road_layer(shape: str, treatment: str, seed: int) -> tuple[Image.Image, dict]:
    """Draw the road stroke (+ treatment + noise) on its own transparent RGBA layer.

    Returns ``(layer, path_record)``. The layer is full-tile; noise is masked to
    the drawn road pixels only.
    """
    path = road_path(shape)
    pts = path["points"]
    layer = Image.new("RGBA", (TILE_PX, TILE_PX), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    if treatment == "edged":
        # Full-width dark underlay, then a narrower ROAD_COLOR core on top.
        draw_stroke(draw, pts, ROAD_WIDTH_PX, ROAD_DARK)
        draw_stroke(draw, pts, CORE_WIDTH_PX, ROAD_COLOR)
    elif treatment == "flat":
        draw_stroke(draw, pts, ROAD_WIDTH_PX, ROAD_COLOR)
    else:
        raise ValueError(f"unknown treatment {treatment!r}")

    road_mask = np.asarray(layer)[..., 3] > 0
    add_ramp_noise(layer, road_mask, seed)
    return layer, path


# -------------------------------------------------------------------- gates


def _road_alpha(layer: Image.Image) -> np.ndarray:
    return np.asarray(layer)[..., 3]


def _disc_has_road(alpha: np.ndarray, centre: tuple[float, float], radius: float) -> bool:
    cx, cy = centre
    y0 = max(0, int(math.floor(cy - radius)))
    y1 = min(TILE_PX - 1, int(math.ceil(cy + radius)))
    x0 = max(0, int(math.floor(cx - radius)))
    x1 = min(TILE_PX - 1, int(math.ceil(cx + radius)))
    for yy in range(y0, y1 + 1):
        for xx in range(x0, x1 + 1):
            if (xx - cx) ** 2 + (yy - cy) ** 2 <= radius * radius and alpha[yy, xx] > 0:
                return True
    return False


def _sample_alpha(alpha: np.ndarray, x: float, y: float) -> int:
    xi, yi = int(round(x)), int(round(y))
    if 0 <= xi < TILE_PX and 0 <= yi < TILE_PX:
        return int(alpha[yi, xi])
    return 0


def _crossing_width(alpha: np.ndarray, mid, tangent) -> float:
    """Contiguous road-pixel run length along the edge tangent through the midpoint."""
    tx, ty = tangent
    hits = []
    k = -20.0
    while k <= 20.0:
        x = mid[0] + tx * k
        y = mid[1] + ty * k
        hits.append((k, _sample_alpha(alpha, x, y) > 0))
        k += 0.5
    # longest contiguous True run around k=0
    best = run = 0.0
    prev = False
    for _, hit in hits:
        if hit:
            run += 0.5
            best = max(best, run)
        else:
            run = 0.0
        prev = hit
    _ = prev
    return best


def _stroke_centre_offset(alpha: np.ndarray, sample, normal) -> float:
    """Perp distance from the normal line of the road-pixel centroid across the tangent."""
    tx, ty = (-normal[1], normal[0])
    offsets = []
    k = -15.0
    while k <= 15.0:
        x = sample[0] + tx * k
        y = sample[1] + ty * k
        if _sample_alpha(alpha, x, y) > 0:
            offsets.append(k)
        k += 0.5
    if not offsets:
        return math.inf
    return abs(float(np.mean(offsets)))


def road_gate(shape: str, layer: Image.Image, base_alpha: np.ndarray, final_alpha: np.ndarray) -> None:
    """Fail loud unless the road crosses every connected edge cleanly and straight."""
    alpha = _road_alpha(layer)
    edges = SHAPES[shape]

    for edge in edges:
        mid = edge_midpoint(edge)
        normal = edge_normal(edge)
        tangent = edge_tangent(edge)

        # (1) crossing: a road pixel within the midpoint disc + correct width.
        if not _disc_has_road(alpha, mid, CROSSING_DISC_R):
            raise RoadGateError(
                f"{shape}: no road pixel within {CROSSING_DISC_R}px of edge {edge} midpoint {mid}"
            )
        width = _crossing_width(alpha, mid, tangent)
        if abs(width - ROAD_WIDTH_PX) > CROSSING_WIDTH_TOL:
            raise RoadGateError(
                f"{shape}: crossing width at edge {edge} is {width:.1f}px, "
                f"expected {ROAD_WIDTH_PX} ± {CROSSING_WIDTH_TOL}"
            )

        # (2) straight approach: road pixel every 2px for 20px, centred on normal.
        d = 0.0
        while d <= APPROACH_PX + 1e-6:
            sx = mid[0] + normal[0] * d
            sy = mid[1] + normal[1] * d
            if _sample_alpha(alpha, sx, sy) <= 0:
                raise RoadGateError(
                    f"{shape}: approach off edge {edge} not a road pixel at {d:.0f}px inward"
                )
            offset = _stroke_centre_offset(alpha, (sx, sy), normal)
            if offset > APPROACH_CENTRE_TOL:
                raise RoadGateError(
                    f"{shape}: approach off edge {edge} deviates {offset:.2f}px from the "
                    f"normal at {d:.0f}px inward (tol {APPROACH_CENTRE_TOL})"
                )
            d += APPROACH_STEP_PX

    # (3) reclip preserved the base silhouette exactly.
    if not np.array_equal(final_alpha, base_alpha):
        raise RoadGateError(f"{shape}: reclip altered the base alpha (silhouette changed)")

    # (4) for ends, the centre cap must not touch a non-connected edge's disc.
    if len(edges) == 1:
        for other in range(6):
            if other in edges:
                continue
            if _disc_has_road(alpha, edge_midpoint(other), CROSSING_DISC_R):
                raise RoadGateError(
                    f"{shape}: end cap touches non-connected edge {other}'s midpoint disc"
                )


# --------------------------------------------------------------- composite


def compose_road(base_path: Path, shape: str, treatment: str, seed: int) -> tuple[Image.Image, dict]:
    """Composite one road tile; return ``(tile, sidecar-params)``. Gated."""
    base = Image.open(base_path).convert("RGBA")
    base_alpha = np.asarray(base)[..., 3].copy()

    layer, path = build_road_layer(shape, treatment, seed)

    tile = base.copy()
    tile.alpha_composite(layer)

    # reclip to the base's own alpha exactly (seam-compatible with neighbours).
    arr = np.asarray(tile).copy()
    arr[..., 3] = base_alpha
    tile = Image.fromarray(arr, "RGBA")

    road_gate(shape, layer, base_alpha, np.asarray(tile)[..., 3])

    endpoints = [
        [round(path["points"][0][0], 2), round(path["points"][0][1], 2)],
        [round(path["points"][-1][0], 2), round(path["points"][-1][1], 2)],
    ]
    sidecar = {
        "shape": shape,
        "edges": SHAPES[shape],
        "base": str(base_path),
        "treatment": treatment,
        "seed": seed,
        "geometry": {
            "approach_px": APPROACH_PX,
            "road_width_px": ROAD_WIDTH_PX,
            "road_color": "0x%02x%02x%02x" % ROAD_COLOR,
            "bezier_steps": BEZIER_STEPS,
            "path_kind": path["kind"],
            "control_point": path["control_point"],
            "endpoints": endpoints,
            "edge_midpoints": {str(e): [round(v, 2) for v in edge_midpoint(e)] for e in SHAPES[shape]},
        },
        "noise_amp": RAMP_NOISE_AMP,
        "edge_shade": (
            {"factor": EDGE_SHADE_FACTOR, "core_width_px": CORE_WIDTH_PX, "dark_color": "0x%02x%02x%02x" % ROAD_DARK}
            if treatment == "edged"
            else None
        ),
    }
    return tile, sidecar


def derive_seed(terrain: str, shape: str, treatment: str, variant: str) -> int:
    """Deterministic per-tile seed: fixed base + stable hash of the tile identity."""
    key = f"{terrain}|{shape}|{treatment}|{variant}".encode()
    digest = hashlib.sha256(key).hexdigest()
    return BASE_SEED + int(digest[:8], 16) % 100000


# ------------------------------------------------------------- candidates


def run_candidates(args: argparse.Namespace) -> int:
    """Bake the review set: every shape x both treatments x {plains, hills} over base 00."""
    args.out_dir.mkdir(parents=True, exist_ok=True)
    bases_root = args.bases_root
    variant = "00"

    count = 0
    for terrain in TERRAINS:
        base_path = bases_root / terrain / f"{variant}.png"
        if not base_path.is_file():
            raise RoadGateError(f"missing base tile {base_path}")
        for shape in SHAPES:
            for treatment in ("flat", "edged"):
                seed = derive_seed(terrain, shape, treatment, variant)
                tile, sidecar = compose_road(base_path, shape, treatment, seed)
                name = f"{terrain}_{shape}__{treatment}"
                out_path = args.out_dir / f"{name}.png"
                tile.save(out_path)
                sidecar.update(
                    {
                        "name": name,
                        "terrain": terrain,
                        "variant": variant,
                        "apply_target": f"frontend/src/board/tiles/road/{terrain}_{shape}_{variant}.png",
                    }
                )
                (args.out_dir / f"{name}.json").write_text(json.dumps(sidecar, indent=2) + "\n")
                count += 1
                print(f"wrote {out_path.name}  (seed {seed}, {sidecar['geometry']['path_kind']}, gate OK)")

    print(f"baked {count} road candidates into {args.out_dir}")
    return 0


# ------------------------------------------------------------------ build


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
    """Manifest-driven replay: regenerate the applied road tiles byte-for-byte.

    Reads the ``road`` manifest section (see the module docstring for its schema)
    and re-runs ``compose_road`` per listed tile. Base paths resolve relative to
    the manifest's parent (``art/foundry``). ``--verify`` byte-compares against a
    directory of applied tiles — the selftest hook.
    """
    manifest = json.loads(args.manifest.read_text())
    if args.section not in manifest:
        raise SystemExit(f"section {args.section!r} not in {args.manifest}")
    section = manifest[args.section]
    root = args.manifest.parent

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for t in section["tiles"]:
        base_path = root / t["base"]
        tile, sidecar = compose_road(base_path, t["shape"], t["treatment"], t["seed"])
        tile.save(args.out_dir / t["tile"])
        print(f"{t['tile']}: {t['shape']} {t['treatment']} over {t['base']}, gate OK")

    if args.verify:
        if not verify_against(args.out_dir, args.verify):
            return 1
        print("verify: all tiles byte-identical")
    return 0


# ------------------------------------------------------ adjacency preview


def _flip(img: Image.Image) -> Image.Image:
    """Emulate the render-time horizontal (L<->R) mirror."""
    return img.transpose(Image.FLIP_LEFT_RIGHT)


def build_preview_placements(
    bases_root: Path, bridge_dir: Path, treatment: str
) -> tuple[dict[tuple[int, int], Image.Image], list[tuple[int, int]]]:
    """Compose the four adjacency scenarios; return ``(placements, fill_coords)``.

    All road tiles are composed on the fly for the given treatment so the preview
    matches the candidates exactly. Mirrored placements use a real L<->R image
    flip (the render-time convention). Each scenario is one axial column; the
    columns are shifted in ``r`` by ``-q/2`` so they line up in a horizontal band
    (flat-top axial otherwise marches each column down by half a hex). Scenarios:

    - a: a vertical 3-hex ``straight_v`` road column (plains), shared N/S edges.
    - b: a turn pair joined across a shared N/S edge — lower ``turn_n_se`` (se->n),
         upper ``turn_s_nw`` **mirrored** (represents {ne,s}, s->ne), so the crossing
         at the shared edge stays centred (edges 2/5 are mirror fixed points).
    - c: a plains ``straight_v`` road hex directly above the applied painterly
         bridge tile ``turn_se_sw_00`` (whose baked ramp runs N-S at edges 2/5),
         so the road->ramp seam is judgeable.
    - d: a road-on-hills ``straight_v`` hex between two road-on-plains ``straight_v``
         hexes (vertical column), for the road-over-relief read.

    ``fill_coords`` is a band of plain plains coords (2 hexes above/below each
    scenario column, plus buffer columns between) so every scenario is framed by
    grass and its seams are judgeable.
    """
    variant = "00"

    def road_tile(terrain: str, shape: str) -> Image.Image:
        seed = derive_seed(terrain, shape, treatment, variant)
        tile, _ = compose_road(bases_root / terrain / f"{variant}.png", shape, treatment, seed)
        return tile

    plains_sv = road_tile("plains", "straight_v")
    hills_sv = road_tile("hills", "straight_v")
    bridge_tile = Image.open(bridge_dir / "turn_se_sw_00.png").convert("RGBA")

    # Scenario columns and their band centre (r shifted by -q/2 to align rows).
    scenario_q = {"a": -4, "b": 0, "c": 4, "d": 8}
    rc = {q: -(q // 2) for q in scenario_q.values()}

    placements: dict[tuple[int, int], Image.Image] = {}
    # a: straight_v column.
    qa = scenario_q["a"]
    for dr in (-1, 0, 1):
        placements[(qa, rc[qa] + dr)] = plains_sv
    # b: turn pair — lower canonical turn_n_se, upper mirrored turn_s_nw.
    qb = scenario_q["b"]
    placements[(qb, rc[qb])] = road_tile("plains", "turn_n_se")
    placements[(qb, rc[qb] - 1)] = _flip(road_tile("plains", "turn_s_nw"))
    # c: road hex directly above the applied bridge tile.
    qc = scenario_q["c"]
    placements[(qc, rc[qc] - 1)] = plains_sv
    placements[(qc, rc[qc])] = bridge_tile
    # d: plains / hills / plains road column.
    qd = scenario_q["d"]
    placements[(qd, rc[qd] - 1)] = plains_sv
    placements[(qd, rc[qd])] = hills_sv
    placements[(qd, rc[qd] + 1)] = plains_sv

    fill_coords: list[tuple[int, int]] = []
    for q in range(min(scenario_q.values()) - 2, max(scenario_q.values()) + 3):
        centre_r = -(q // 2)
        for r in range(centre_r - 3, centre_r + 4):
            fill_coords.append((q, r))
    return placements, fill_coords


def render_board(
    placements: dict[tuple[int, int], Image.Image],
    fill_tile: Image.Image,
    hex_px: int,
    coords: list[tuple[int, int]],
) -> Image.Image:
    """Composite an axial hex grid at ``hex_px`` point-to-point width.

    Every coord in ``coords`` (plus every placement key) is drawn: a special
    placement if present, else the plain ``fill_tile`` (plain plains) so seams
    around each scenario are judgeable. Tiles are 128px source, scaled to
    ``hex_px``.
    """
    size = hex_px / 2.0
    coords = sorted(set(coords) | set(placements.keys()))
    centres = {(q, r): axial_to_pixel(q, r, size) for q, r in coords}
    xs = [c[0] for c in centres.values()]
    ys = [c[1] for c in centres.values()]
    margin = hex_px
    off_x = margin - min(xs) - hex_px / 2.0
    off_y = margin - min(ys) - hex_px / 2.0
    width = int(math.ceil(max(xs) - min(xs) + 2 * margin + hex_px))
    height = int(math.ceil(max(ys) - min(ys) + 2 * margin + hex_px))
    canvas = Image.new("RGBA", (width, height), (28, 30, 36, 255))

    scaled_cache: dict[int, Image.Image] = {}

    def scaled(tile: Image.Image) -> Image.Image:
        key = id(tile)
        if key not in scaled_cache:
            scaled_cache[key] = tile.resize((hex_px, hex_px), Image.LANCZOS)
        return scaled_cache[key]

    # Draw plain fill first, then specials on top (specials never overlap fill
    # positions, but this keeps ordering explicit).
    for q, r in coords:
        tile = placements.get((q, r), fill_tile)
        cx, cy = centres[(q, r)]
        px = int(round(cx + off_x - hex_px / 2.0))
        py = int(round(cy + off_y - hex_px / 2.0))
        canvas.alpha_composite(scaled(tile), (px, py))
    return canvas


def run_preview(args: argparse.Namespace) -> int:
    """Render the board-scale adjacency previews (both treatments, both scales)."""
    args.out_dir.mkdir(parents=True, exist_ok=True)
    fill_tile = Image.open(args.bases_root / "plains" / "00.png").convert("RGBA")

    written = []
    for treatment in ("flat", "edged"):
        placements, fill_coords = build_preview_placements(args.bases_root, args.bridge_dir, treatment)
        for hex_px in (args.board_px, 128):
            board = render_board(placements, fill_tile, hex_px, fill_coords)
            out_path = args.out_dir / f"road-preview-{treatment}-{hex_px}px.png"
            board.save(out_path)
            written.append(out_path)
            print(f"wrote {out_path}  ({board.width}x{board.height})")
    print(f"wrote {len(written)} adjacency previews into {args.out_dir}")
    return 0


# -------------------------------------------------------------------- CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Composite deterministic painterly road tiles onto terrain bases.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("candidates", help="bake the full road review set (40 tiles) + sidecars")
    c.add_argument("--out-dir", required=True, type=Path, help="output dir for candidate tiles + sidecars")
    c.add_argument(
        "--bases-root",
        type=Path,
        default=Path("frontend/src/board/tiles"),
        help="root holding {terrain}/{nn}.png bases (default frontend/src/board/tiles)",
    )

    p = sub.add_parser("preview", help="render the board-scale adjacency previews")
    p.add_argument("--out-dir", required=True, type=Path, help="output dir for the preview PNGs")
    p.add_argument(
        "--bases-root",
        type=Path,
        default=Path("frontend/src/board/tiles"),
        help="root holding {terrain}/{nn}.png bases (default frontend/src/board/tiles)",
    )
    p.add_argument(
        "--bridge-dir",
        type=Path,
        default=Path("frontend/src/board/tiles/bridge"),
        help="dir holding the applied bridge tiles (for the ramp-seam scenario)",
    )
    p.add_argument("--board-px", type=int, default=50, help="board-scale hex px width (default 50)")

    b = sub.add_parser("build", help="manifest-driven replay of the applied road tiles")
    b.add_argument("--manifest", required=True, type=Path, help="art/foundry/manifest.json")
    b.add_argument("--section", default="road", help="manifest section (default road)")
    b.add_argument("--out-dir", required=True, type=Path)
    b.add_argument("--verify", type=Path, help="byte-compare output against this dir of applied tiles")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cmd == "candidates":
        return run_candidates(args)
    if args.cmd == "preview":
        return run_preview(args)
    return run_build(args)


if __name__ == "__main__":
    raise SystemExit(main())
