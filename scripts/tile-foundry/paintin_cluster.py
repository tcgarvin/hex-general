#!/usr/bin/env python3
"""Cluster paint-in: Codex paints ONE composition across 1-4 adjacent hexes.

A multi-hex site (a base that grows from 1 to 4 hexes) must read as one
compound, so it cannot be built from independently painted tiles. This tool
extends the `paintin.py` loop to *cluster cells*:

  sheet     each cluster is the union of its member hexes (flat-top, ~340 px
            per hex, abutting exactly) on #FF00FF. The ground under a cluster
            is ONE continuous crop of a ground source image (G2), so there is
            no seam between members; each member's 128 px base is that
            source cropped at the member's position.
  register  isotropic scale + translation fitted to the cluster silhouettes
            (union centroid, area-derived size, union IoU), fails loudly.
  cut       every member is resampled to its own 128 px tile from the same
            output, so neighbouring members agree across their shared edge.
            Pixels outside the union, near its OUTER boundary, or magenta take
            the base.
  pin       only the OUTER boundary of the cluster is pinned to the base (same
            exact band + ramp as `paintin_pin.PinConfig`); internal edges are
            left exactly as painted. Optional difference-matte ground restore
            (`paintin_pin.ground_restore`) per member.
  gate      per member, measured against the outer boundary only: rim_exact,
            rim_clear, rim_touch; plus per internal edge the overlap mismatch
            between the two members' tiles (`seam_*`).

Geometry: members are axial offsets (q, r) from the cluster's founding hex,
using hex.ts / hexgeom AXIAL_DIRECTIONS (edge i of a hex faces neighbour
AXIAL_DIRECTIONS[i]: 0 SE (1,0), 1 NE (1,-1), 2 N (0,-1), 3 NW (-1,0),
4 SW (-1,1), 5 S (0,1)).

usage:
    python3 scripts/tile-foundry/paintin_cluster.py run --job <job.json> \
        --prompt <prompt.txt> --ref docs/concept-art/concept1.png --out-dir <dir>
    (add --from-raw <dir>/raw/<name>.png to replay without calling Codex)

Job file (validated by `load_cluster_job`):
    {"name": "base-stages-a", "canvas": [1536, 1024], "hex_width": 340,
     "ground": "art/codex/terrain/ground/G2/source.png",
     "clusters": [{"name": "stage4_a", "centre": [420, 512],
                   "members": [[0, 0], [1, 0], [0, 1], [1, 1]],
                   "ground_origin": [300, 260]}],
     "pin": {"restore": true}, "gates": {}}
`centre` is the sheet position of the union's centroid; `ground_origin` the
source-image pixel (tile scale, 1:1) under the founding hex's centre.

A cluster may carry `"guide": {"margin": 18}`: a faint closed polygon drawn on
the SHEET only (never on the bases), marking where a perimeter goes. It is the
convex hull of the union, shrunk until every point of it is at least
`margin` tile px inside the outer boundary (`inscribed_guide`), so it
cuts straight across the union's concave corners instead of tracing the hex
outline. Codex paints over it; the pin and gates are unchanged.
`"guide": {"margin": 14, "shape": "blob", "seed": 3, "jitter": 0.3}` draws an
irregular rounded loop instead (`blob_guide`): per direction from the union's
centroid it may reach `margin` from the outer boundary, is pulled inward by a
seeded low-frequency jitter (up to `jitter` of the reach), and is the largest
smooth loop under that cap, so it bulges, pinches and sags across concave corners.
A blob guide may also be partial, for a perimeter still being built:
`"arc": 0.5, "start_deg": 150, "rest": "dashed"` draws only `arc` of the loop
solid, starting at polar angle `start_deg` about the union's centroid (sheet
convention: 0 = east, 90 = south since y grows down, so the arc runs clockwise
on screen), and the remainder either not at all (`"rest": "none"`) or as a
fainter dashed trace (`"rest": "dashed"`, `split_guide_arc` + `dash_polyline`),
optionally in its own colour and opacity (`"rest_rgb": [196, 168, 112]`,
`"rest_alpha": 0.7`), e.g. pale raw earth for a trench that is only marked out:
Codex paints what the line looks like.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from hexgeom import AXIAL_DIRECTIONS, axial_to_pixel  # noqa: E402
from paintin_geom import (  # noqa: E402
    EDGE_THETA_DEG,
    HEX_WIDTH,
    TILE_PX,
    hex_inset_distance,
    pixel_grid,
    smoothstep,
)
from paintin_pin import PinConfig, diff_mask, ground_restore  # noqa: E402
from paintin_sheet import (  # noqa: E402
    MAGENTA,
    MIN_GAP_PX,
    JobError,
    RegistrationError,
    foreground_weight,
    foreground_weight_at,
    magenta_level,
    output_alpha,
    repo_path,
    repo_rel,
    sha256_of,
)

R_TILE = TILE_PX / 2.0  # hex radius (centre to corner) in tile px
CUT_GUARD_TILE_PX = 0.75
CUT_MAGENTA_MAX = 0.05
CUT_MARGIN_PX = 6
GUIDE_RGB = (78, 62, 38)  # trodden earth: the guide line blends toward it
GUIDE_ALPHA = 0.6
GUIDE_WIDTH_TILE_PX = 1.6
GUIDE_RESTS = ("none", "dashed")  # how the unbuilt part of a partial guide is drawn
GUIDE_REST_ALPHA = 0.5  # the dashed remainder is fainter than the solid arc
GUIDE_DASH_TILE_PX = 4.0
GUIDE_GAP_TILE_PX = 3.0


# ------------------------------------------------------------------ geometry ----


def member_offset_px(q: int, r: int, radius: float) -> tuple[float, float]:
    """Pixel offset of axial (q, r) from the founding hex (flat-top, `radius`)."""
    return axial_to_pixel(q, r, radius)


def check_members(members: list[tuple[int, int]], where: str) -> None:
    """Members must be distinct, include (0, 0), and form one connected group."""
    if len(set(members)) != len(members):
        raise JobError(f"{where}: duplicate member offsets {members}")
    if (0, 0) not in members:
        raise JobError(f"{where}: members must include the founding hex (0, 0)")
    seen = {(0, 0)}
    frontier = [(0, 0)]
    while frontier:
        q, r = frontier.pop()
        for dq, dr in AXIAL_DIRECTIONS:
            n = (q + dq, r + dr)
            if n in members and n not in seen:
                seen.add(n)
                frontier.append(n)
    if seen != set(members):
        raise JobError(f"{where}: members {members} are not one connected group")


def outer_segments(
    members: list[tuple[int, int]], origin: tuple[int, int], centre: tuple[float, float],
    radius: float,
) -> list[tuple[float, float, float, float]]:
    """End points of every edge on the cluster's OUTER boundary.

    Coordinates are pixels in a frame where member `origin` is centred at `centre`
    and hexes have corner radius `radius`.
    """
    mset = set(members)
    segs = []
    for q, r in members:
        ox, oy = member_offset_px(q - origin[0], r - origin[1], radius)
        cx, cy = centre[0] + ox, centre[1] + oy
        for edge, (dq, dr) in enumerate(AXIAL_DIRECTIONS):
            if (q + dq, r + dr) in mset:
                continue
            a0 = math.radians(EDGE_THETA_DEG[edge] - 30.0)
            a1 = math.radians(EDGE_THETA_DEG[edge] + 30.0)
            segs.append(
                (
                    cx + radius * math.cos(a0),
                    cy + radius * math.sin(a0),
                    cx + radius * math.cos(a1),
                    cy + radius * math.sin(a1),
                )
            )
    return segs


def outer_distance(
    xs: np.ndarray,
    ys: np.ndarray,
    members: list[tuple[int, int]],
    origin: tuple[int, int],
    centre: tuple[float, float],
    radius: float,
) -> np.ndarray:
    """Signed distance to the cluster's outer boundary: + inside the union, - outside.

    Internal edges (shared by two members) are not boundary, so a pixel next to
    one is as far from the boundary as the union's shape makes it.
    """
    best = np.full(xs.shape, np.inf)
    for x0, y0, x1, y1 in outer_segments(members, origin, centre, radius):
        dx, dy = x1 - x0, y1 - y0
        t = np.clip(((xs - x0) * dx + (ys - y0) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
        best = np.minimum(best, np.hypot(xs - (x0 + t * dx), ys - (y0 + t * dy)))
    inside = np.zeros(xs.shape, dtype=bool)
    for q, r in members:
        ox, oy = member_offset_px(q - origin[0], r - origin[1], radius)
        inside |= (
            # tolerance: a point exactly on an internal edge is inside, not outside
            hex_inset_distance(xs, ys, (centre[0] + ox, centre[1] + oy), 2 * radius) >= -1e-6
        )
    return np.where(inside, best, -best)


def union_coverage(
    out_w: int,
    out_h: int,
    centres: list[tuple[float, float]],
    hex_width: float,
    supersample: int = 4,
) -> np.ndarray:
    """Antialiased coverage (0..1) of the union of flat-top hexes."""
    big = Image.new("L", (out_w * supersample, out_h * supersample), 0)
    draw = ImageDraw.Draw(big)
    rad = hex_width / 2.0 * supersample
    for cx, cy in centres:
        pts = [
            (
                cx * supersample + rad * math.cos(math.radians(60 * k)),
                cy * supersample + rad * math.sin(math.radians(60 * k)),
            )
            for k in range(6)
        ]
        draw.polygon(pts, fill=255)
    small = big.resize((out_w, out_h), Image.Resampling.BOX)
    return np.asarray(small, dtype=np.float64) / 255.0


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain; counter-clockwise, no repeated end point."""
    pts = sorted(set(points))

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    upper: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _polygon_samples(poly: list[tuple[float, float]], step: float = 1.0) -> np.ndarray:
    out = []
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        n = max(1, math.ceil(math.hypot(x1 - x0, y1 - y0) / step))
        t = np.arange(n) / n
        out.append(np.stack([x0 + t * (x1 - x0), y0 + t * (y1 - y0)], axis=-1))
    return np.concatenate(out)


def inscribed_guide(members: list[tuple[int, int]], margin: float) -> list[tuple[float, float]]:
    """Perimeter guide in tile px relative to the founding hex's centre.

    The convex hull of the union's corners, scaled about the hull's centroid
    until every point on it is >= `margin` inside the outer boundary, then each
    vertex pushed outward along its ray as far as that clearance and convexity allow.
    """
    corners = []
    for q, r in members:
        ox, oy = member_offset_px(q, r, R_TILE)
        for k in range(6):
            a = math.radians(60 * k)
            corners.append((round(ox + R_TILE * math.cos(a), 6), round(oy + R_TILE * math.sin(a), 6)))
    hull = _convex_hull(corners)
    cx = sum(p[0] for p in hull) / len(hull)
    cy = sum(p[1] for p in hull) / len(hull)

    def scaled(s: float) -> list[tuple[float, float]]:
        return [(cx + s * (x - cx), cy + s * (y - cy)) for x, y in hull]

    def clear(s: float) -> bool:
        pts = _polygon_samples(scaled(s))
        d = outer_distance(pts[:, 0], pts[:, 1], members, (0, 0), (0.0, 0.0), R_TILE)
        return bool(d.min() >= margin)

    lo, hi = 0.0, 1.0
    if not clear(lo + 1e-3):
        raise JobError(f"no guide fits {members} with margin {margin}")
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if clear(mid) else (lo, mid)
    # then push each vertex out along its own ray (a few sweeps, staying convex), so
    # the polygon reaches into the union's lobes while its edges clear the concave corners
    factors = [lo] * len(hull)

    def poly_of(fs: list[float]) -> list[tuple[float, float]]:
        return [(cx + f * (x - cx), cy + f * (y - cy)) for f, (x, y) in zip(fs, hull)]

    def convex(poly: list[tuple[float, float]]) -> bool:
        n = len(poly)
        crosses = [
            (poly[(i + 1) % n][0] - poly[i][0]) * (poly[(i + 2) % n][1] - poly[(i + 1) % n][1])
            - (poly[(i + 1) % n][1] - poly[i][1]) * (poly[(i + 2) % n][0] - poly[(i + 1) % n][0])
            for i in range(n)
        ]
        return all(c >= 0 for c in crosses) or all(c <= 0 for c in crosses)

    def ok(fs: list[float]) -> bool:
        if not convex(poly_of(fs)):
            return False
        pts = _polygon_samples(poly_of(fs))
        d = outer_distance(pts[:, 0], pts[:, 1], members, (0, 0), (0.0, 0.0), R_TILE)
        return bool(d.min() >= margin)

    for _ in range(4):
        for i in range(len(hull)):
            vlo, vhi = factors[i], 1.0
            for _ in range(20):
                mid = (vlo + vhi) / 2
                trial = factors[:i] + [mid] + factors[i + 1 :]
                vlo, vhi = (mid, vhi) if ok(trial) else (vlo, mid)
            factors[i] = vlo
    return poly_of(factors)


def _circular_smooth(r: np.ndarray, sigma: float) -> np.ndarray:
    n = len(r)
    k = np.arange(-int(3 * sigma), int(3 * sigma) + 1)
    w = np.exp(-0.5 * (k / sigma) ** 2)
    w /= w.sum()
    return np.array([np.dot(w, r[(i + k) % n]) for i in range(n)])


def blob_guide(
    members: list[tuple[int, int]], margin: float, seed: int, jitter: float, samples: int = 180
) -> list[tuple[float, float]]:
    """A smooth irregular loop >= `margin` inside the outer boundary (tile px, founding-hex frame)."""
    offs = [member_offset_px(q, r, R_TILE) for q, r in members]
    cx = sum(o[0] for o in offs) / len(offs)
    cy = sum(o[1] for o in offs) / len(offs)
    theta = np.arange(samples) / samples * 2 * math.pi
    steps = np.arange(0.0, 4 * R_TILE, 0.5)
    reach = np.empty(samples)
    for i, t in enumerate(theta):
        xs = cx + steps * math.cos(t)
        ys = cy + steps * math.sin(t)
        d = outer_distance(xs, ys, members, (0, 0), (0.0, 0.0), R_TILE)
        ok = d >= margin
        if not ok[0]:
            raise JobError(f"no blob guide fits {members} with margin {margin}")
        reach[i] = steps[np.argmin(ok)] - 0.5 if not ok.all() else steps[-1]
    rng = np.random.default_rng(seed)
    wobble = np.zeros(samples)
    for k in (2, 3, 4, 5):
        wobble += rng.uniform(0.4, 1.0) / k * np.sin(k * theta + rng.uniform(0, 2 * math.pi))
    wobble = (wobble - wobble.min()) / (wobble.max() - wobble.min())
    cap = reach * (1 - jitter * wobble)
    # largest smooth loop under the cap: erode (running minimum over +-3 sigma),
    # then Gaussian-smooth; every smoothed value averages minima that are each
    # <= the cap at the centre, so the loop never crosses the cap
    sigma = samples / 100
    half = int(3 * sigma)
    eroded = np.array([cap[(i + np.arange(-half, half + 1)) % samples].min() for i in range(samples)])
    r = np.minimum(cap, _circular_smooth(eroded, sigma))
    return [(cx + float(rr) * math.cos(t), cy + float(rr) * math.sin(t)) for rr, t in zip(r, theta)]


def union_centroid(members: list[tuple[int, int]]) -> tuple[float, float]:
    """Centroid of the member centres in tile px, founding-hex frame (the blob's pole)."""
    offs = [member_offset_px(q, r, R_TILE) for q, r in members]
    return (sum(o[0] for o in offs) / len(offs), sum(o[1] for o in offs) / len(offs))


def split_guide_arc(
    loop: list[tuple[float, float]], pole: tuple[float, float], arc: float, start_deg: float
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Split a closed loop into (solid arc, remainder), both open polylines in loop order.

    A loop point is on the solid arc when its polar angle about `pole`, measured
    from `start_deg` in the direction of increasing angle (clockwise on screen,
    y down), is below `360 * arc`. The remainder carries the solid arc's end
    points at both of its ends so the two parts meet. `arc == 1` returns the
    whole loop and an empty remainder. The loop must be star-shaped about `pole`
    in its own order (as `blob_guide` is); otherwise the arc would not be
    contiguous and this raises.
    """
    if not 0.0 < arc <= 1.0:
        raise ValueError(f"arc must be in (0, 1], got {arc}")
    if arc == 1.0:
        return list(loop), []
    rel = [
        (math.degrees(math.atan2(y - pole[1], x - pole[0])) - start_deg) % 360.0 for x, y in loop
    ]
    solid_mask = [a < 360.0 * arc for a in rel]
    n = len(loop)
    starts = [i for i in range(n) if solid_mask[i] and not solid_mask[i - 1]]
    if len(starts) != 1:
        raise ValueError(f"arc {arc} from {start_deg} deg is not one contiguous run of the loop ({len(starts)} runs)")
    order = list(range(starts[0], n)) + list(range(starts[0]))
    solid = [loop[i] for i in order if solid_mask[i]]
    rest = [loop[i] for i in order if not solid_mask[i]]
    return solid, [solid[-1], *rest, solid[0]]


def dash_polyline(
    pts: list[tuple[float, float]], dash: float, gap: float
) -> list[list[tuple[float, float]]]:
    """Cut an open polyline into dashes `dash` long separated by `gap` (same units)."""
    if dash <= 0 or gap <= 0:
        raise ValueError(f"dash and gap must be > 0, got {dash}, {gap}")
    dashes: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = [pts[0]]
    pos = 0.0  # distance into the current dash+gap period
    on = True
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        t = 0.0
        while seg - t > 1e-9:
            left = (dash if on else gap) - pos
            step = min(left, seg - t)
            t += step
            pos += step
            p = (x0 + (x1 - x0) * t / seg, y0 + (y1 - y0) * t / seg)
            if on:
                current.append(p)
            if pos >= (dash if on else gap) - 1e-9:
                if on and len(current) >= 2:
                    dashes.append(current)
                on, pos = not on, 0.0
                current = [p]
    if on and len(current) >= 2:
        dashes.append(current)
    return dashes


# ------------------------------------------------------------------- the job ----


@dataclass(frozen=True)
class Cluster:
    """One cluster cell of a job."""

    name: str
    centre: tuple[float, float]
    members: tuple[tuple[int, int], ...]
    ground_origin: tuple[int, int]
    guide_margin: float = 0.0  # 0: no perimeter guide drawn
    guide_shape: str = "hull"  # "hull" (inscribed_guide) or "blob" (blob_guide)
    guide_seed: int = 0
    guide_jitter: float = 0.0
    guide_arc: float = 1.0  # share of the blob loop drawn solid (1: closed loop)
    guide_start_deg: float = 0.0  # polar angle where the solid arc starts
    guide_rest: str = "none"  # the remainder: "none" (not drawn) or "dashed"
    guide_rest_rgb: tuple[int, int, int] = GUIDE_RGB  # colour of the dashed remainder
    guide_rest_alpha: float = GUIDE_REST_ALPHA


@dataclass(frozen=True)
class ClusterJob:
    path: Path
    name: str
    canvas: tuple[int, int]
    hex_width: int
    ground: Path
    clusters: tuple[Cluster, ...]
    pin: dict[str, Any]
    gates: dict[str, Any]


def _sheet_centres(job_hex: float, cl: Cluster) -> list[tuple[float, float]]:
    """Sheet centre of every member: the union's centroid sits on `cl.centre`."""
    offs = [member_offset_px(q, r, job_hex / 2.0) for q, r in cl.members]
    mx = sum(o[0] for o in offs) / len(offs)
    my = sum(o[1] for o in offs) / len(offs)
    return [(cl.centre[0] + ox - mx, cl.centre[1] + oy - my) for ox, oy in offs]


def load_cluster_job(path: Path) -> ClusterJob:
    """Load and validate a cluster job (the boundary: raise JobError with context)."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as err:
        raise JobError(f"{path}: cannot read job: {err}") from err
    if not isinstance(raw, dict):
        raise JobError(f"{path}: job must be a JSON object")
    name = raw.get("name")
    if not isinstance(name, str) or not name or not name.replace("-", "").replace("_", "").isalnum():
        raise JobError(f"{path}: name must be [a-z0-9_-]+, got {name!r}")
    canvas = raw.get("canvas")
    if not (isinstance(canvas, list) and len(canvas) == 2 and all(isinstance(v, int) and v > 0 for v in canvas)):
        raise JobError(f"{path}: canvas must be [width, height] ints")
    hex_width = raw.get("hex_width")
    if not isinstance(hex_width, int) or hex_width < TILE_PX:
        raise JobError(f"{path}: hex_width must be an int >= {TILE_PX}")
    ground = (path.parent / str(raw.get("ground", ""))).resolve()
    if not ground.is_file():
        raise JobError(f"{path}: ground source {ground} does not exist")
    with Image.open(ground) as g:
        gw, gh = g.size
    clusters_raw = raw.get("clusters")
    if not isinstance(clusters_raw, list) or not clusters_raw:
        raise JobError(f"{path}: clusters must be a non-empty list")
    clusters: list[Cluster] = []
    for i, c in enumerate(clusters_raw):
        where = f"{path}: clusters[{i}]"
        if not isinstance(c, dict):
            raise JobError(f"{where} must be an object")
        cname = c.get("name")
        if not isinstance(cname, str) or not cname.replace("_", "").isalnum():
            raise JobError(f"{where}: name must be [a-z0-9_]+")
        members = [tuple(m) for m in c.get("members", [])]
        if not members or not all(len(m) == 2 and all(isinstance(v, int) for v in m) for m in members):
            raise JobError(f"{where}: members must be a list of [q, r] int pairs")
        check_members(members, where)  # type: ignore[arg-type]
        centre = c.get("centre")
        origin = c.get("ground_origin")
        if not (isinstance(centre, list) and len(centre) == 2):
            raise JobError(f"{where}: centre must be [x, y]")
        if not (isinstance(origin, list) and len(origin) == 2 and all(isinstance(v, int) for v in origin)):
            raise JobError(f"{where}: ground_origin must be [x, y] ints")
        guide = c.get("guide", {})
        guide_keys = {"margin", "shape", "seed", "jitter", "arc", "start_deg", "rest", "rest_rgb", "rest_alpha"}
        if not isinstance(guide, dict) or set(guide) - guide_keys:
            raise JobError(f"{where}: guide keys are {', '.join(sorted(guide_keys))}")
        margin = guide.get("margin", 0.0)
        if not isinstance(margin, (int, float)) or not 0 <= margin < R_TILE:
            raise JobError(f"{where}: guide.margin must be 0..{R_TILE} tile px")
        shape = guide.get("shape", "hull")
        seed = guide.get("seed", 0)
        jitter = guide.get("jitter", 0.0)
        if shape not in ("hull", "blob") or not isinstance(seed, int):
            raise JobError(f"{where}: guide.shape must be hull|blob and guide.seed an int")
        if not isinstance(jitter, (int, float)) or not 0 <= jitter < 1:
            raise JobError(f"{where}: guide.jitter must be in [0, 1)")
        arc = guide.get("arc", 1.0)
        start_deg = guide.get("start_deg", 0.0)
        rest = guide.get("rest", "none")
        if isinstance(arc, bool) or not isinstance(arc, (int, float)) or not 0 < arc <= 1:
            raise JobError(f"{where}: guide.arc must be in (0, 1], got {arc!r}")
        if isinstance(start_deg, bool) or not isinstance(start_deg, (int, float)):
            raise JobError(f"{where}: guide.start_deg must be a number, got {start_deg!r}")
        if rest not in GUIDE_RESTS:
            raise JobError(f"{where}: guide.rest must be one of {GUIDE_RESTS}, got {rest!r}")
        if arc < 1 and (shape != "blob" or margin <= 0):
            raise JobError(f"{where}: a partial guide (arc < 1) needs shape blob and margin > 0")
        if arc == 1 and ("start_deg" in guide or "rest" in guide):
            raise JobError(f"{where}: guide.start_deg / guide.rest only apply with arc < 1")
        rest_rgb = guide.get("rest_rgb", list(GUIDE_RGB))
        rest_alpha = guide.get("rest_alpha", GUIDE_REST_ALPHA)
        if ("rest_rgb" in guide or "rest_alpha" in guide) and rest != "dashed":
            raise JobError(f"{where}: guide.rest_rgb / guide.rest_alpha only apply with rest dashed")
        if not (
            isinstance(rest_rgb, list) and len(rest_rgb) == 3
            and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 for v in rest_rgb)
        ):
            raise JobError(f"{where}: guide.rest_rgb must be [r, g, b] ints 0..255, got {rest_rgb!r}")
        if isinstance(rest_alpha, bool) or not isinstance(rest_alpha, (int, float)) or not 0 < rest_alpha <= 1:
            raise JobError(f"{where}: guide.rest_alpha must be in (0, 1], got {rest_alpha!r}")
        cl = Cluster(
            cname, (float(centre[0]), float(centre[1])), tuple(members),  # type: ignore[arg-type]
            (origin[0], origin[1]), float(margin), shape, seed, float(jitter),
            float(arc), float(start_deg), rest,
            (rest_rgb[0], rest_rgb[1], rest_rgb[2]), float(rest_alpha),
        )
        for (q, r) in cl.members:
            ox, oy = member_offset_px(q, r, R_TILE)
            x0, y0 = round(origin[0] + ox - R_TILE), round(origin[1] + oy - R_TILE)
            if x0 < 0 or y0 < 0 or x0 + TILE_PX > gw or y0 + TILE_PX > gh:
                raise JobError(f"{where}: member {(q, r)} ground crop leaves the {gw}x{gh} source")
        clusters.append(cl)
    if len({c.name for c in clusters}) != len(clusters):
        raise JobError(f"{path}: duplicate cluster names")
    # every member hex inside the canvas, and clusters at least MIN_GAP_PX apart
    w = float(hex_width)
    boxes = []
    for cl in clusters:
        cs = _sheet_centres(w, cl)
        x0 = min(c[0] for c in cs) - w / 2
        x1 = max(c[0] for c in cs) + w / 2
        y0 = min(c[1] for c in cs) - w * math.sqrt(3) / 4
        y1 = max(c[1] for c in cs) + w * math.sqrt(3) / 4
        if x0 < MIN_GAP_PX or y0 < MIN_GAP_PX or x1 > canvas[0] - MIN_GAP_PX or y1 > canvas[1] - MIN_GAP_PX:
            raise JobError(f"{path}: cluster {cl.name} does not fit the canvas with a {MIN_GAP_PX} px margin")
        boxes.append((cl.name, x0, y0, x1, y1))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            gap = max(b[1] - a[3], a[1] - b[3], b[2] - a[4], a[2] - b[4])
            if gap < MIN_GAP_PX:
                raise JobError(f"{path}: clusters {a[0]} and {b[0]} are closer than {MIN_GAP_PX} px")
    for key in ("pin", "gates"):
        if not isinstance(raw.get(key, {}), dict):
            raise JobError(f"{path}: {key} must be an object")
    return ClusterJob(
        path.resolve(), name, (canvas[0], canvas[1]), hex_width, ground,
        tuple(clusters), raw.get("pin", {}), raw.get("gates", {}),
    )


# ---------------------------------------------------------------------- sheet ----


def member_base(source: np.ndarray, cl: Cluster, q: int, r: int) -> np.ndarray:
    """The member's 128 px base: the ground source cropped at its position (float RGB)."""
    ox, oy = member_offset_px(q, r, R_TILE)
    x0 = round(cl.ground_origin[0] + ox - R_TILE)
    y0 = round(cl.ground_origin[1] + oy - R_TILE)
    return source[y0 : y0 + TILE_PX, x0 : x0 + TILE_PX].astype(np.float64)


def draw_guide(
    sheet: np.ndarray,
    pts: list[tuple[float, float]],
    scale: float,
    closed: bool = True,
    alpha: float = GUIDE_ALPHA,
) -> np.ndarray:
    """Blend a faint polyline (sheet px) toward GUIDE_RGB; closed loop unless `closed=False`."""
    return draw_guide_lines(sheet, [pts + pts[:1] if closed else pts], scale, alpha)


def draw_guide_lines(
    sheet: np.ndarray,
    lines: list[list[tuple[float, float]]],
    scale: float,
    alpha: float,
    rgb: tuple[int, int, int] = GUIDE_RGB,
) -> np.ndarray:
    """Blend open polylines (sheet px) toward `rgb` at opacity `alpha`."""
    ss = 4
    h, w = sheet.shape[:2]
    big = Image.new("L", (w * ss, h * ss), 0)
    draw = ImageDraw.Draw(big)
    for line in lines:
        draw.line(
            [(x * ss, y * ss) for x, y in line], fill=255,
            width=max(1, round(GUIDE_WIDTH_TILE_PX * scale * ss)), joint="curve",
        )
    a = np.asarray(big.resize((w, h), Image.Resampling.BOX), dtype=np.float64)[..., None] / 255.0
    a *= alpha
    return sheet * (1 - a) + np.array(rgb, dtype=np.float64) * a


def build_cluster_sheet(job: ClusterJob, out_dir: Path) -> tuple[Path, Path]:
    """Render the sheet + sidecar; also writes each member's base under bases/."""
    out_dir.mkdir(parents=True, exist_ok=True)
    source = np.asarray(Image.open(job.ground).convert("RGB"))
    cw, ch = job.canvas
    w = float(job.hex_width)
    sheet = np.empty((ch, cw, 3))
    sheet[:] = MAGENTA
    xs, ys = pixel_grid(max(cw, ch))
    xs, ys = xs[:ch, :cw], ys[:ch, :cw]
    clusters_json = []
    for cl in job.clusters:
        centres = _sheet_centres(w, cl)
        cover = union_coverage(cw, ch, centres, w)
        owner_d = np.full((ch, cw), -np.inf)
        content = np.zeros((ch, cw, 3))
        members_json = []
        # the ground is one continuous source crop, scaled to the sheet
        scale = w / TILE_PX
        for (q, r), (cx, cy) in zip(cl.members, centres):
            base = member_base(source, cl, q, r)
            base_path = out_dir / "bases" / cl.name / f"q{q}_r{r}.png"
            base_path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(base.astype(np.uint8)).save(base_path)
            d = hex_inset_distance(xs, ys, (cx, cy), w)
            mine = d > owner_d
            owner_d = np.where(mine, d, owner_d)
            # sample the source directly at sheet scale around this member
            ox, oy = member_offset_px(q, r, R_TILE)
            sx0 = cl.ground_origin[0] + ox - R_TILE  # source x at the member tile's left
            sy0 = cl.ground_origin[1] + oy - R_TILE
            box = (sx0 - 8, sy0 - 8, sx0 + TILE_PX + 8, sy0 + TILE_PX + 8)
            size = round((TILE_PX + 16) * scale)
            patch = np.asarray(
                Image.fromarray(source).resize((size, size), Image.Resampling.LANCZOS, box=box),
                dtype=np.float64,
            )
            px0 = round(cx - w / 2 - 8 * scale)
            py0 = round(cy - w / 2 - 8 * scale)
            region = np.zeros((ch, cw, 3))
            ya, xa = max(py0, 0), max(px0, 0)
            yb, xb = min(py0 + size, ch), min(px0 + size, cw)
            region[ya:yb, xa:xb] = patch[ya - py0 : yb - py0, xa - px0 : xb - px0]
            content = np.where(mine[..., None], region, content)
            members_json.append(
                {
                    "offset": [q, r],
                    "cx": cx,
                    "cy": cy,
                    "base": repo_rel(base_path),
                    "base_sha256": sha256_of(base_path),
                    "output": f"{cl.name}/q{q}_r{r}.png",
                }
            )
        sheet = content * cover[..., None] + sheet * (1 - cover[..., None])
        guide_pts: list[tuple[float, float]] = []
        arc_json: dict[str, Any] = {}
        if cl.guide_margin > 0:
            guide_pts = (
                blob_guide(list(cl.members), cl.guide_margin, cl.guide_seed, cl.guide_jitter)
                if cl.guide_shape == "blob"
                else inscribed_guide(list(cl.members), cl.guide_margin)
            )
            fx, fy = centres[cl.members.index((0, 0))]

            def to_sheet(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
                return [(fx + x * scale, fy + y * scale) for x, y in pts]

            if cl.guide_arc >= 1.0:
                sheet = draw_guide(sheet, to_sheet(guide_pts), scale)
            else:
                solid, rest = split_guide_arc(
                    guide_pts, union_centroid(list(cl.members)), cl.guide_arc, cl.guide_start_deg
                )
                sheet = draw_guide(sheet, to_sheet(solid), scale, closed=False)
                if cl.guide_rest == "dashed":
                    dashes = dash_polyline(rest, GUIDE_DASH_TILE_PX, GUIDE_GAP_TILE_PX)
                    sheet = draw_guide_lines(
                        sheet, [to_sheet(d) for d in dashes], scale, cl.guide_rest_alpha, cl.guide_rest_rgb
                    )
                arc_json = {
                    "guide_arc": {
                        "arc": cl.guide_arc,
                        "start_deg": cl.guide_start_deg,
                        "rest": cl.guide_rest,
                        "rest_rgb": list(cl.guide_rest_rgb),
                        "rest_alpha": cl.guide_rest_alpha,
                        "solid_tile_px": [[round(x, 2), round(y, 2)] for x, y in solid],
                        "rest_tile_px": [[round(x, 2), round(y, 2)] for x, y in rest],
                    }
                }
        clusters_json.append(
            {
                "name": cl.name,
                "centre": list(cl.centre),
                "members": members_json,
                "ground_origin": list(cl.ground_origin),
                "guide_tile_px": [[round(x, 2), round(y, 2)] for x, y in guide_pts],
                **arc_json,
            }
        )
    sheet_path = out_dir / f"{job.name}.sheet.png"
    Image.fromarray(np.clip(np.round(sheet), 0, 255).astype(np.uint8)).save(sheet_path)
    sidecar = {
        "tool": "scripts/tile-foundry/paintin_cluster.py",
        "version": 1,
        "job": repo_rel(job.path),
        "job_sha256": sha256_of(job.path),
        "name": job.name,
        "sheet": repo_rel(sheet_path),
        "canvas": list(job.canvas),
        "hex_width": job.hex_width,
        "ground": repo_rel(job.ground),
        "pin": job.pin,
        "gates": job.gates,
        "clusters": clusters_json,
    }
    sidecar_path = out_dir / f"{job.name}.sheet.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=1))
    return sheet_path, sidecar_path


# --------------------------------------------------------------- registration ----


@dataclass
class ClusterFit:
    name: str
    expected: tuple[float, float]
    measured: tuple[float, float]
    width_ratio: float
    residual_tile_px: float = 0.0
    iou: float = 0.0


@dataclass
class ClusterRegistration:
    scale: float
    tx: float
    ty: float
    background_level: float
    clusters: dict[str, ClusterFit] = field(default_factory=dict)

    def to_output(self, x: float, y: float) -> tuple[float, float]:
        return (self.scale * x + self.tx, self.scale * y + self.ty)

    def to_json(self) -> dict[str, Any]:
        return {
            "scale": self.scale,
            "tx": self.tx,
            "ty": self.ty,
            "background_level": self.background_level,
            "max_residual_tile_px": max(c.residual_tile_px for c in self.clusters.values()),
            "min_iou": min(c.iou for c in self.clusters.values()),
            "clusters": {k: asdict(v) for k, v in self.clusters.items()},
        }


def _cluster_bbox(cl: dict[str, Any], hex_width: float) -> tuple[float, float, float, float]:
    xs = [m["cx"] for m in cl["members"]]
    ys = [m["cy"] for m in cl["members"]]
    return (
        min(xs) - hex_width / 2,
        min(ys) - hex_width * math.sqrt(3) / 4,
        max(xs) + hex_width / 2,
        max(ys) + hex_width * math.sqrt(3) / 4,
    )


def register_clusters(
    output_rgb: np.ndarray,
    sidecar: dict[str, Any],
    max_residual_tile_px: float = 0.75,
    max_width_error: float = 0.02,
    min_iou: float = 0.97,
) -> ClusterRegistration:
    """Fit `out = s * sheet + t` to the cluster silhouettes and check each one."""
    weight, bg_level = foreground_weight(output_rgb)
    h, w = weight.shape
    hexw = float(sidecar["hex_width"])
    hex_area = 3 * math.sqrt(3) / 8 * hexw * hexw
    clusters = sidecar["clusters"]
    exp_area = sum(hex_area * len(c["members"]) for c in clusters)
    scale = math.sqrt(float(weight.sum()) / exp_area)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    tot = float(weight.sum())
    out_c = ((weight * (xs + 0.5)).sum() / tot, (weight * (ys + 0.5)).sum() / tot)
    areas = [hex_area * len(c["members"]) for c in clusters]
    sheet_c = (
        sum(a * c["centre"][0] for a, c in zip(areas, clusters)) / sum(areas),
        sum(a * c["centre"][1] for a, c in zip(areas, clusters)) / sum(areas),
    )
    tx, ty = out_c[0] - scale * sheet_c[0], out_c[1] - scale * sheet_c[1]
    measured: dict[str, tuple[tuple[float, float], float]] = {}
    for _ in range(2):
        for c in clusters:
            x0, y0, x1, y1 = _cluster_bbox(c, hexw)
            m = 20.0
            ax0 = max(0, int(scale * (x0 - m) + tx))
            ay0 = max(0, int(scale * (y0 - m) + ty))
            ax1 = min(w, int(scale * (x1 + m) + tx) + 1)
            ay1 = min(h, int(scale * (y1 + m) + ty) + 1)
            roi = weight[ay0:ay1, ax0:ax1]
            area = float(roi.sum())
            n = len(c["members"])
            if area < 0.5 * hex_area * n * scale * scale:
                raise RegistrationError(
                    f"cluster {c['name']} not found: foreground {area:.0f} px vs "
                    f"expected {hex_area * n * scale * scale:.0f}"
                )
            cx = float((roi * (xs[ay0:ay1, ax0:ax1] + 0.5)).sum() / area)
            cy = float((roi * (ys[ay0:ay1, ax0:ax1] + 0.5)).sum() / area)
            measured[c["name"]] = ((cx, cy), math.sqrt(area / n * 8 / (3 * math.sqrt(3))))
        pts = np.array([c["centre"] for c in clusters], dtype=np.float64)
        got = np.array([measured[c["name"]][0] for c in clusters])
        if len(clusters) >= 2:
            a = np.zeros((2 * len(pts), 3))
            b = np.zeros(2 * len(pts))
            a[0::2, 0], a[0::2, 1] = pts[:, 0], 1.0
            a[1::2, 0], a[1::2, 2] = pts[:, 1], 1.0
            b[0::2], b[1::2] = got[:, 0], got[:, 1]
            sol, *_ = np.linalg.lstsq(a, b, rcond=None)
            scale, tx, ty = float(sol[0]), float(sol[1]), float(sol[2])
        else:
            scale = float(measured[clusters[0]["name"]][1] / hexw)
            tx, ty = (got[0] - scale * pts[0]).tolist()
    reg = ClusterRegistration(scale, tx, ty, bg_level)
    failures = []
    for c in clusters:
        (mx, my), mw = measured[c["name"]]
        ex, ey = reg.to_output(*c["centre"])
        fit = ClusterFit(c["name"], (ex, ey), (mx, my), mw / (scale * hexw))
        fit.residual_tile_px = math.hypot(mx - ex, my - ey) * TILE_PX / (scale * hexw)
        centres = [reg.to_output(m["cx"], m["cy"]) for m in c["members"]]
        x0, y0, x1, y1 = _cluster_bbox(c, hexw)
        bx0, by0 = int(scale * x0 + tx) - 8, int(scale * y0 + ty) - 8
        bx1, by1 = int(scale * x1 + tx) + 8, int(scale * y1 + ty) + 8
        if bx0 < 0 or by0 < 0 or bx1 > w or by1 > h:
            failures.append(f"{c['name']}: predicted cluster runs off the output")
            reg.clusters[c["name"]] = fit
            continue
        pred = union_coverage(
            bx1 - bx0, by1 - by0, [(x - bx0, y - by0) for x, y in centres], scale * hexw
        ) > 0.5
        got_m = weight[by0:by1, bx0:bx1] > 0.5
        fit.iou = float((pred & got_m).sum() / (pred | got_m).sum())
        reg.clusters[c["name"]] = fit
        if fit.residual_tile_px > max_residual_tile_px:
            failures.append(f"{c['name']}: residual {fit.residual_tile_px:.2f} tile px")
        if abs(fit.width_ratio - 1) > max_width_error:
            failures.append(f"{c['name']}: size off by {100 * (fit.width_ratio - 1):+.1f}%")
        if fit.iou < min_iou:
            failures.append(f"{c['name']}: IoU {fit.iou:.3f} < {min_iou}")
    if failures:
        raise RegistrationError("cluster registration failed: " + "; ".join(failures))
    return reg


# ------------------------------------------------------------------------ cut ----


def cut_member(
    output_rgb: np.ndarray,
    reg: ClusterRegistration,
    cluster: dict[str, Any],
    member: dict[str, Any],
    base: np.ndarray,
    hexw: float,
) -> np.ndarray:
    """Resample one member to 128 px; outside the union / near its OUTER rim / magenta -> base."""
    members = [tuple(m["offset"]) for m in cluster["members"]]
    origin = tuple(member["offset"])
    ex, ey = reg.to_output(member["cx"], member["cy"])
    hex_out = reg.scale * hexw
    k = hex_out / HEX_WIDTH
    bx0, by0 = ex - hex_out / 2, ey - hex_out / 2
    ix0, iy0 = math.floor(bx0) - CUT_MARGIN_PX, math.floor(by0) - CUT_MARGIN_PX
    size = math.ceil(hex_out) + 2 * CUT_MARGIN_PX + 1
    h, w = output_rgb.shape[:2]
    if ix0 < 0 or iy0 < 0 or ix0 + size > w or iy0 + size > h:
        raise RegistrationError(f"{cluster['name']}/{origin}: cut window runs off the output")
    crop = output_rgb[iy0 : iy0 + size, ix0 : ix0 + size]
    xs, ys = pixel_grid(size)
    tx = (xs + ix0 - bx0) / k
    ty = (ys + iy0 - by0) / k
    dist = outer_distance(tx, ty, members, origin, (64.0, 64.0), R_TILE)  # type: ignore[arg-type]
    magenta = 1.0 - foreground_weight_at(magenta_level(crop), reg.background_level)
    replace_px = (dist < CUT_GUARD_TILE_PX) | (magenta > CUT_MAGENTA_MAX)
    base_img = Image.fromarray(np.round(base).astype(np.uint8))
    warped = base_img.transform(
        (size, size),
        Image.Transform.AFFINE,
        (1.0 / k, 0.0, (ix0 - bx0) / k, 0.0, 1.0 / k, (iy0 - by0) / k),
        resample=Image.Resampling.BICUBIC,
    )
    filled = np.where(replace_px[..., None], np.asarray(warped, dtype=np.float64), crop)
    src = Image.fromarray(np.clip(np.round(filled), 0, 255).astype(np.uint8))
    box = (bx0 - ix0, by0 - iy0, bx0 - ix0 + hex_out, by0 - iy0 + hex_out)
    return np.asarray(src.resize((TILE_PX, TILE_PX), Image.Resampling.LANCZOS, box=box), dtype=np.float64)


# ------------------------------------------------------------------ pin + gate ----


def member_distance(cluster: dict[str, Any], member: dict[str, Any]) -> np.ndarray:
    """Outer-boundary distance at every pixel centre of the member's 128 px tile."""
    xs, ys = pixel_grid(TILE_PX)
    members = [tuple(m["offset"]) for m in cluster["members"]]
    return outer_distance(xs, ys, members, tuple(member["offset"]), (64.0, 64.0), R_TILE)  # type: ignore[arg-type]


def pin_member(
    cut: np.ndarray, base: np.ndarray, dist: np.ndarray, cfg: PinConfig
) -> tuple[np.ndarray, np.ndarray]:
    """(RGBA uint8 tile, painted weight): outer rim = base exactly, internal edges as painted."""
    weight = smoothstep(cfg.rim_exact_px, cfg.rim_exact_px + cfg.rim_ramp_px, dist)
    interior = ground_restore(cut, base, cfg)[0] if cfg.restore else cut
    rgb = base * (1 - weight[..., None]) + interior * weight[..., None]
    out = np.empty((TILE_PX, TILE_PX, 4), dtype=np.uint8)
    out[..., :3] = np.clip(np.round(rgb), 0, 255).astype(np.uint8)
    out[..., 3] = output_alpha()
    return out, weight


@dataclass(frozen=True)
class ClusterGates:
    max_residual_tile_px: float = 0.75
    min_iou: float = 0.97
    rim_salient_max: float = 0.03
    rim_touch_px: float = 4.0
    rim_touch_max: int = 2
    seam_mean_max: float = 4.0  # painted detail + sub-pixel resampling gives ~1.5-3.5


def gate_member(
    final: np.ndarray, cut: np.ndarray, base: np.ndarray, dist: np.ndarray,
    cfg: PinConfig, gates: ClusterGates,
) -> list[dict[str, Any]]:
    """Outer-rim gates for one member (internal edges exempt)."""
    checks = []
    alpha = final[..., 3]
    rim = (alpha > 0) & (dist < cfg.rim_exact_px)
    worst = float(np.abs(final[..., :3][rim].astype(float) - np.clip(np.round(base[rim]), 0, 255)).max()) if rim.any() else 0.0
    checks.append({"name": "rim_exact", "value": worst, "limit": 0.0, "passed": worst == 0.0, "detail": f"max |final - base| {worst:.0f} over {int(rim.sum())} outer-rim px"})
    salient = diff_mask(cut, base) > 0.5
    band = (dist >= 0) & (dist < cfg.rim_exact_px + cfg.rim_ramp_px)
    frac = float(salient[band].mean()) if band.any() else 0.0
    checks.append({"name": "rim_clear", "value": round(frac, 4), "limit": gates.rim_salient_max, "passed": frac <= gates.rim_salient_max, "detail": f"{100 * frac:.1f}% of the outer rim band painted over"})
    touch = int((salient & (dist >= 0) & (dist < gates.rim_touch_px)).sum())
    checks.append({"name": "rim_touch", "value": touch, "limit": gates.rim_touch_max, "passed": touch <= gates.rim_touch_max, "detail": f"{touch} salient px within {gates.rim_touch_px:g} px of the outer boundary"})
    return checks


def seam_mismatch(
    a_tile: np.ndarray, b_tile: np.ndarray, a_off: tuple[int, int], b_off: tuple[int, int]
) -> tuple[float, float, int]:
    """Mean/max |A - B| over A's visible pixels that lie inside B's hex (the shared-edge overlap).

    B is sampled bilinearly at A's pixel centres (their grids differ by a sub-pixel
    offset), so a perfectly continuous painting gives ~0 here.
    """
    dx, dy = member_offset_px(b_off[0] - a_off[0], b_off[1] - a_off[1], R_TILE)
    xs, ys = pixel_grid(TILE_PX)
    bx, by = xs - dx, ys - dy  # A's pixel centres in B's tile coordinates
    inside_b = hex_inset_distance(bx, by) > 0.0
    sel = (a_tile[..., 3] > 0) & inside_b
    if not sel.any():
        return 0.0, 0.0, 0
    u, v = bx[sel] - 0.5, by[sel] - 0.5
    u0 = np.clip(np.floor(u).astype(int), 0, TILE_PX - 2)
    v0 = np.clip(np.floor(v).astype(int), 0, TILE_PX - 2)
    fu, fv = (u - u0)[:, None], (v - v0)[:, None]
    bt = b_tile[..., :3].astype(np.float64)
    sample = (
        bt[v0, u0] * (1 - fu) * (1 - fv) + bt[v0, u0 + 1] * fu * (1 - fv)
        + bt[v0 + 1, u0] * (1 - fu) * fv + bt[v0 + 1, u0 + 1] * fu * fv
    )
    diff = np.abs(a_tile[..., :3][sel].astype(np.float64) - sample).mean(axis=1)
    return float(diff.mean()), float(diff.max()), int(sel.sum())


# ------------------------------------------------------------------- pipeline ----


def process(
    sidecar: dict[str, Any], raw: Path, work: Path, restore: bool | None = None
) -> dict[str, Any]:
    """Register, cut, pin and gate every cluster; writes tiles/, cut/ and report.json."""
    gates = ClusterGates(**sidecar["gates"])
    cfg = PinConfig.from_json(sidecar["pin"])
    if restore is not None:
        cfg = replace(cfg, restore=restore)
    out_rgb = np.asarray(Image.open(raw).convert("RGB"), dtype=np.float64)
    reg = register_clusters(out_rgb, sidecar, gates.max_residual_tile_px, min_iou=gates.min_iou)
    hexw = float(sidecar["hex_width"])
    report: dict[str, Any] = {"raw": repo_rel(raw), "registration": reg.to_json(), "pin": cfg.to_json(), "clusters": {}}
    for cl in sidecar["clusters"]:
        tiles: dict[tuple[int, int], np.ndarray] = {}
        members_rep = {}
        positions = {}
        for i, m in enumerate(cl["members"]):
            base_path = repo_path(m["base"])
            if sha256_of(base_path) != m["base_sha256"]:
                raise JobError(f"{base_path}: base changed since the sheet was built")
            base = np.asarray(Image.open(base_path).convert("RGB"), dtype=np.float64)
            cut = cut_member(out_rgb, reg, cl, m, base, hexw)
            (work / "cut" / cl["name"]).mkdir(parents=True, exist_ok=True)
            Image.fromarray(np.clip(np.round(cut), 0, 255).astype(np.uint8)).save(work / "cut" / m["output"])
            dist = member_distance(cl, m)
            final, _ = pin_member(cut, base, dist, cfg)
            name = f"h{i}"
            out_path = work / "tiles" / cl["name"] / f"{name}.png"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(final, "RGBA").save(out_path)
            tiles[tuple(m["offset"])] = final  # type: ignore[index]
            positions[name] = m["offset"]
            checks = gate_member(final, cut, base, dist, cfg, gates)
            members_rep[name] = {"offset": m["offset"], "checks": checks, "passed": all(c["passed"] for c in checks)}
        seams = []
        offs = list(tiles)
        for a in offs:
            for b in offs:
                if a < b and (b[0] - a[0], b[1] - a[1]) in AXIAL_DIRECTIONS + [(-d[0], -d[1]) for d in AXIAL_DIRECTIONS]:
                    mean, mx, n = seam_mismatch(tiles[a], tiles[b], a, b)
                    seams.append({"a": list(a), "b": list(b), "mean": round(mean, 2), "max": round(mx, 1), "px": n, "passed": mean <= gates.seam_mean_max})
        (work / "tiles" / cl["name"] / "positions.json").write_text(
            json.dumps({"cluster": cl["name"], "positions": positions, "axial": "offsets from h0, hex.ts AXIAL_DIRECTIONS convention"}, indent=1) + "\n"
        )
        fit = reg.clusters[cl["name"]]
        report["clusters"][cl["name"]] = {
            "registration": asdict(fit),
            "members": members_rep,
            "seams": seams,
            "passed": all(m["passed"] for m in members_rep.values()) and all(s["passed"] for s in seams),
        }
    (work / "report.json").write_text(json.dumps(report, indent=1))
    return report


def assemble(tiles_dir: Path, hex_px: int = 128, pad: int = 12) -> Image.Image:
    """Place a cluster's member tiles in position (for previews)."""
    pos = json.loads((tiles_dir / "positions.json").read_text())["positions"]
    pts = {n: member_offset_px(q, r, hex_px / 2) for n, (q, r) in pos.items()}
    x0 = min(p[0] for p in pts.values()) - hex_px / 2 - pad
    y0 = min(p[1] for p in pts.values()) - hex_px / 2 - pad
    x1 = max(p[0] for p in pts.values()) + hex_px / 2 + pad
    y1 = max(p[1] for p in pts.values()) + hex_px / 2 + pad
    canvas = Image.new("RGBA", (math.ceil(x1 - x0), math.ceil(y1 - y0)), (34, 34, 40, 255))
    for n, (x, y) in pts.items():
        t = Image.open(tiles_dir / f"{n}.png").convert("RGBA")
        if hex_px != TILE_PX:
            t = t.resize((hex_px, hex_px), Image.Resampling.LANCZOS)
        canvas.alpha_composite(t, (round(x - x0 - hex_px / 2), round(y - y0 - hex_px / 2)))
    return canvas.convert("RGB")


def run(args: argparse.Namespace) -> int:
    job = load_cluster_job(args.job)
    work = args.out_dir
    sheet, side = build_cluster_sheet(job, work)
    sidecar = json.loads(side.read_text())
    if args.from_raw is not None:
        raw = args.from_raw
        run_json = raw.with_suffix(".run.json")
        codex = json.loads(run_json.read_text()) if run_json.exists() else {"not_recorded": True}
    else:
        from paintin import run_codex  # the shared Codex recipe

        raw = work / "raw" / f"{job.name}.png"
        if raw.exists() and not args.overwrite_raw:
            raise JobError(f"{raw} exists; replay it with --from-raw or pass --overwrite-raw")
        codex = run_codex(args.prompt, [sheet, *args.ref], raw).to_json()
        print(f"codex thread {codex['codex_thread']}, {codex['wall_seconds']} s")
    report = process(sidecar, raw, work)
    for name, c in report["clusters"].items():
        fails = [f"{m}:{ch['name']}={ch['value']}" for m, r in c["members"].items() for ch in r["checks"] if not ch["passed"]]
        fails += [f"seam {s['a']}-{s['b']} mean {s['mean']}" for s in c["seams"] if not s["passed"]]
        print(f"{'PASS' if c['passed'] else 'FAIL'} {name}: reg {c['registration']['residual_tile_px']:.2f} tile px, IoU {c['registration']['iou']:.3f}; seams {[s['mean'] for s in c['seams']]} {'; '.join(fails)}")
        preview = assemble(work / "tiles" / name)
        (work / "review").mkdir(exist_ok=True)
        preview.save(work / "review" / f"{name}.128.png")
        assemble(work / "tiles" / name, 64, 6).save(work / "review" / f"{name}.64.png")
    entry = {
        "name": job.name,
        "when": dt.datetime.now().isoformat(timespec="seconds"),
        "job": repo_rel(job.path),
        "job_sha256": sha256_of(job.path),
        "prompt": repo_rel(args.prompt),
        "prompt_sha256": hashlib.sha256(args.prompt.read_bytes()).hexdigest(),
        "sheet": repo_rel(sheet),
        "raw": repo_rel(raw),
        "raw_sha256": sha256_of(raw),
        "from_raw": args.from_raw is not None,
        "codex": codex,
        "refs": [repo_rel(sheet)] + [repo_rel(r) for r in args.ref],
        "registration": {k: report["registration"][k] for k in ("scale", "tx", "ty", "max_residual_tile_px", "min_iou")},
        "passed": [n for n, c in report["clusters"].items() if c["passed"]],
        "failed": [n for n, c in report["clusters"].items() if not c["passed"]],
    }
    manifest = work / "manifest.json"
    data = json.loads(manifest.read_text()) if manifest.exists() else {"runs": []}
    data["runs"].append(entry)
    manifest.write_text(json.dumps(data, indent=1))
    return 0 if all(c["passed"] for c in report["clusters"].values()) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="sheet -> Codex (or --from-raw) -> register -> cut -> pin -> gate")
    r.add_argument("--job", type=Path, required=True)
    r.add_argument("--prompt", type=Path, required=True)
    r.add_argument("--ref", type=Path, action="append", default=[])
    r.add_argument("--out-dir", type=Path, required=True)
    r.add_argument("--from-raw", type=Path)
    r.add_argument("--overwrite-raw", action="store_true")
    s = sub.add_parser("sheet", help="build the sheet + sidecar only")
    s.add_argument("--job", type=Path, required=True)
    s.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.cmd == "sheet":
        sheet, side = build_cluster_sheet(load_cluster_job(args.job), args.out_dir)
        print(f"wrote {sheet} and {side}")
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
