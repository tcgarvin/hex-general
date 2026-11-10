"""Tile-space geometry for the paint-in toolchain (`paintin.py`).

Everything here works in **tile space**: a 128 px square tile whose flat-top hex
is 128 px point to point, centred at (64, 64) in continuous pixel coordinates
(pixel (i, j) has its centre at (i + 0.5, j + 0.5)). The same functions are used
at sheet resolution by passing a different `hex_width` and centre.

Edge conventions follow `frontend/src/board/riverTiles.ts` and `river.py`: edge
`e` is indexed like `hexgeom.AXIAL_DIRECTIONS` (0=SE 1=NE 2=N 3=NW 4=SW 5=S); its
outward radial sits at `EDGE_THETA_DEG[e]` (screen degrees, y down); the
frontend's render-time left-right mirror permutes edges by `MIRROR_EDGE`.

The edge-crossing pin needs a coordinate *along* each edge that two neighbours
agree on. Each edge gets a **canonical tangent**: edges 0-2 use their outward
normal rotated +90 degrees, edges 3-5 reuse the tangent of their opposite edge
(e - 3). Two hexes sharing an edge see that edge as `e` and `e + 3`, so a point
on it has the same tangent coordinate `t` in both tiles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

TILE_PX = 128
HEX_WIDTH = 128.0
SQRT3 = math.sqrt(3.0)

EDGE_THETA_DEG: dict[int, float] = {
    0: 30.0,
    1: -30.0,
    2: -90.0,
    3: -150.0,
    4: 150.0,
    5: 90.0,
}
MIRROR_EDGE: dict[int, int] = {0: 4, 4: 0, 1: 3, 3: 1, 2: 2, 5: 5}
EDGE_NAMES: dict[int, str] = {0: "se", 1: "ne", 2: "n", 3: "nw", 4: "sw", 5: "s"}


def apothem(hex_width: float = HEX_WIDTH) -> float:
    """Centre-to-edge-midpoint distance of a flat-top hex `hex_width` point to point."""
    return hex_width / 2.0 * SQRT3 / 2.0


def edge_normal(edge: int) -> tuple[float, float]:
    """Unit outward normal of `edge` in screen coordinates (y down)."""
    theta = math.radians(EDGE_THETA_DEG[edge])
    return (math.cos(theta), math.sin(theta))


def edge_tangent(edge: int) -> tuple[float, float]:
    """Canonical unit tangent of `edge`, shared by both hexes that meet there."""
    nx, ny = edge_normal(edge if edge < 3 else edge - 3)
    return (-ny, nx)


def edge_midpoint(
    edge: int, centre: tuple[float, float] = (64.0, 64.0), hex_width: float = HEX_WIDTH
) -> tuple[float, float]:
    """Midpoint of `edge` for a hex centred at `centre`."""
    nx, ny = edge_normal(edge)
    a = apothem(hex_width)
    return (centre[0] + a * nx, centre[1] + a * ny)


def pixel_grid(size: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixel-centre coordinates (x, y) of a `size` square canvas."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float64)
    return xs + 0.5, ys + 0.5


def hex_inset_distance(
    xs: np.ndarray,
    ys: np.ndarray,
    centre: tuple[float, float] = (64.0, 64.0),
    hex_width: float = HEX_WIDTH,
) -> np.ndarray:
    """Signed distance (px) from the hex boundary: positive inside, negative outside.

    Inside the hex this is the exact distance to the nearest edge line; outside it
    is the largest half-plane violation, which equals the true distance except in
    the corner wedges (where it slightly underestimates). Callers only use the
    outside values to tell "outside" from "inside".
    """
    x = xs - centre[0]
    y = ys - centre[1]
    reach = np.zeros_like(x)
    for edge in range(6):
        nx, ny = edge_normal(edge)
        reach = np.maximum(reach, x * nx + y * ny)
    return apothem(hex_width) - reach


def tile_distance(size: int = TILE_PX, hex_width: float = HEX_WIDTH) -> np.ndarray:
    """Signed hex-boundary distance at every pixel centre of a `size` tile."""
    xs, ys = pixel_grid(size)
    return hex_inset_distance(xs, ys, (size / 2.0, size / 2.0), hex_width)


def edge_frame(
    edge: int,
    xs: np.ndarray,
    ys: np.ndarray,
    centre: tuple[float, float] = (64.0, 64.0),
    hex_width: float = HEX_WIDTH,
) -> tuple[np.ndarray, np.ndarray]:
    """(depth, t) of points in `edge`'s frame.

    `depth` is the inward distance from the edge line (0 on the edge, positive
    inside the hex); `t` is the canonical tangent coordinate measured from the
    edge midpoint.
    """
    nx, ny = edge_normal(edge)
    tx, ty = edge_tangent(edge)
    x = xs - centre[0]
    y = ys - centre[1]
    depth = apothem(hex_width) - (x * nx + y * ny)
    t = x * tx + y * ty
    return depth, t


def smoothstep(lo: float, hi: float, v: np.ndarray) -> np.ndarray:
    """Hermite ramp: 0 at or below `lo`, 1 at or above `hi`."""
    if hi <= lo:
        raise ValueError(f"smoothstep needs hi > lo, got lo={lo} hi={hi}")
    s = np.clip((v - lo) / (hi - lo), 0.0, 1.0)
    return s * s * (3.0 - 2.0 * s)


def sample_bilinear(img: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Bilinear sample of an HxW or HxWxC float array at continuous coordinates.

    Pixel (i, j) holds the value at (i + 0.5, j + 0.5). Coordinates outside the
    image clamp to the border pixels.
    """
    h, w = img.shape[:2]
    fx = np.clip(xs - 0.5, 0.0, w - 1.0)
    fy = np.clip(ys - 0.5, 0.0, h - 1.0)
    x0 = np.floor(fx).astype(int)
    y0 = np.floor(fy).astype(int)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    ax = fx - x0
    ay = fy - y0
    if img.ndim == 3:
        ax = ax[..., None]
        ay = ay[..., None]
    top = img[y0, x0] * (1 - ax) + img[y0, x1] * ax
    bot = img[y1, x0] * (1 - ax) + img[y1, x1] * ax
    return top * (1 - ay) + bot * ay


def gaussian_blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur of an HxW or HxWxC float array (reflect padding)."""
    if sigma <= 0:
        raise ValueError(f"sigma must be positive, got {sigma}")
    radius = max(1, math.ceil(3.0 * sigma))
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    k /= k.sum()
    out = img.astype(np.float64)
    for axis in (0, 1):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (radius, radius)
        padded = np.pad(out, pad, mode="reflect")
        acc = np.zeros_like(out)
        for i, weight in enumerate(k):
            sl = [slice(None)] * out.ndim
            sl[axis] = slice(i, i + out.shape[axis])
            acc += weight * padded[tuple(sl)]
        out = acc
    return out


def max_filter(mask: np.ndarray, radius: int) -> np.ndarray:
    """Grey-level dilation of an HxW array with a disc of `radius` px."""
    if radius < 0:
        raise ValueError(f"radius must be >= 0, got {radius}")
    out = mask.copy()
    h, w = mask.shape
    padded = np.pad(mask, radius, mode="edge")
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > radius * radius or (dx == 0 and dy == 0):
                continue
            out = np.maximum(
                out,
                padded[radius + dy : radius + dy + h, radius + dx : radius + dx + w],
            )
    return out


def min_filter(mask: np.ndarray, radius: int) -> np.ndarray:
    """Grey-level erosion of an HxW array with a disc of `radius` px."""
    return -max_filter(-mask, radius)


# ------------------------------------------------------------------ guides ----

GUIDE_KINDS = ("river", "road")
DEFAULT_GUIDE_COLOR: dict[str, tuple[int, int, int]] = {
    "river": (88, 104, 108),
    "road": (150, 128, 88),
}
STRAIGHT_RUN_FRAC = 0.22  # dead-straight run inward of each crossing, x apothem
END_REACH_FRAC = 0.95  # an end cap's spring sits this far toward the far side
END_TAPER_START = 0.55  # fraction of the end-cap course at full width


@dataclass(frozen=True)
class Marker:
    """A flat rectangle drawn on the guide, e.g. where a bridge deck goes.

    The rectangle is centred on `centre` (tile px, default the hex centre), its
    long side running across the course between `axis_edges` (two opposite
    edges), then turned by `rotate_deg` (screen degrees, clockwise) — e.g. 90
    for a north-south deck over a river that runs east-west there.
    """

    kind: str
    axis_edges: tuple[int, int]
    length: float
    width: float
    color: tuple[int, int, int]
    centre: tuple[float, float] = (64.0, 64.0)
    rotate_deg: float = 0.0

    def long_axis(self) -> tuple[float, float]:
        """Unit vector along the rectangle's long side (screen coordinates)."""
        ax, ay = edge_normal(self.axis_edges[0])
        th = math.radians(self.rotate_deg)
        px, py = -ay, ax
        return (
            px * math.cos(th) - py * math.sin(th),
            px * math.sin(th) + py * math.cos(th),
        )


@dataclass(frozen=True)
class Guide:
    """A deterministic oriented guide: a band (river) or ribbon (road).

    The band enters and leaves through the midpoints of `edges`, `width` (tile
    px) wide and dead straight where it crosses each edge.

    The defaults reproduce the original *plain* guide: constant width, straight
    or single-curve courses, forks as radial arms into the centre, a linear
    end-cap taper. Any styling field switches to the *natural* geometry in
    `natural_strokes` (see there): `meander` (max lateral swing of the course
    inside the hex, tile px), `breathe` (relative width swing inside the hex),
    `ragged` (bank jitter amplitude, tile px, independent per bank), `round_px`
    (radius of the rounded fill where a tributary meets its main course), and
    `pond` (diameter of a still-water blob for a river guide with no edges).
    `seed` picks the meander / jitter draw. `hold_edges` + `hold_half` are for
    painting over a finished tile (bridges over a river tile): across those
    edges the pin keeps the base's own crossing, `hold_half` px either side of
    the midpoint, instead of Codex's repaint.
    """

    kind: str
    edges: tuple[int, ...]
    width: float
    color: tuple[int, int, int]
    marker: Marker | None  # None: no marker on this guide
    meander: float = 0.0
    breathe: float = 0.0
    ragged: float = 0.0
    round_px: float = 0.0
    pond: float = 0.0
    seed: int = 0
    hold_edges: tuple[int, ...] = ()
    hold_half: float = 0.0
    end_taper: float = 0.55  # road dead end: final width fraction (END_ROAD_TAPER_TO)

    def courses(self) -> list[np.ndarray]:
        """Centreline polylines in tile space, each an (N, 2) array."""
        if self.natural():
            return [s.points for s in natural_strokes(self)]
        return guide_courses(self.edges)

    def is_end_cap(self) -> bool:
        """True for a one-edge guide (a terminus that tapers inside the hex)."""
        return len(self.edges) == 1

    def natural(self) -> bool:
        """True when any styling field asks for the natural geometry."""
        return (
            self.meander > 0
            or self.breathe > 0
            or self.ragged > 0
            or self.round_px > 0
            or self.pond > 0
            or not self.edges
        )


def _bezier(p0, p1, p2, p3, n: int = 64) -> np.ndarray:
    s = np.linspace(0.0, 1.0, n)[:, None]
    pts = [np.asarray(p, dtype=np.float64) for p in (p0, p1, p2, p3)]
    return (
        (1 - s) ** 3 * pts[0]
        + 3 * (1 - s) ** 2 * s * pts[1]
        + 3 * (1 - s) * s**2 * pts[2]
        + s**3 * pts[3]
    )


def _line(p0, p1, n: int = 32) -> np.ndarray:
    s = np.linspace(0.0, 1.0, n)[:, None]
    return (1 - s) * np.asarray(p0, dtype=np.float64) + s * np.asarray(p1)


def _arm(edge: int, overshoot: float) -> tuple[np.ndarray, np.ndarray]:
    """(outside point, inner point) of an edge's straight entry run."""
    nx, ny = edge_normal(edge)
    a = apothem()
    run = STRAIGHT_RUN_FRAC * a
    outside = (64.0 + (a + overshoot) * nx, 64.0 + (a + overshoot) * ny)
    inner = (64.0 + (a - run) * nx, 64.0 + (a - run) * ny)
    return np.asarray(outside), np.asarray(inner)


def guide_courses(edges: tuple[int, ...], overshoot: float = 6.0) -> list[np.ndarray]:
    """Centreline polylines for a connected-edge set (tile space).

    One edge: a straight course from the edge toward the far side (the end cap
    taper is applied when rendering). Two opposite edges: a straight line. Two
    edges 120 degrees apart: straight entry runs joined by a cubic curve. Three
    edges: the opposite pair (if any) as a straight course, every other edge a
    straight radial arm into the centre. Sharp turns (adjacent edges) and masks
    of four or more edges are not supported (see riverTiles.ts) and raise.
    """
    if len(set(edges)) != len(edges) or not edges:
        raise ValueError(f"guide edges must be distinct and non-empty, got {edges}")
    if any(e not in EDGE_THETA_DEG for e in edges):
        raise ValueError(f"guide edges must be in 0..5, got {edges}")
    centre = np.array([64.0, 64.0])
    if len(edges) == 1:
        (edge,) = edges
        outside, _ = _arm(edge, overshoot)
        nx, ny = edge_normal(edge)
        reach = apothem() * (1.0 - END_REACH_FRAC)
        tip = (64.0 - reach * nx, 64.0 - reach * ny)
        return [_line(outside, tip, 96)]
    if len(edges) == 2:
        e1, e2 = edges
        gap = min((e2 - e1) % 6, (e1 - e2) % 6)
        if gap == 1:
            raise ValueError(f"sharp turns are not drawn (edges {edges})")
        o1, i1 = _arm(e1, overshoot)
        o2, i2 = _arm(e2, overshoot)
        if gap == 3:
            return [_line(o1, o2, 128)]
        handle = 0.55 * np.linalg.norm(i1 - centre)
        n1 = np.asarray(edge_normal(e1))
        n2 = np.asarray(edge_normal(e2))
        curve = _bezier(i1, i1 - n1 * handle, i2 - n2 * handle, i2)
        return [np.concatenate([_line(o1, i1), curve, _line(i2, o2)])]
    if len(edges) == 3:
        courses: list[np.ndarray] = []
        rest = list(edges)
        for a in edges:
            b = (a + 3) % 6
            if b in edges and a < b:
                o1, _ = _arm(a, overshoot)
                o2, _ = _arm(b, overshoot)
                courses.append(_line(o1, o2, 128))
                rest = [e for e in edges if e not in (a, b)]
        for e in rest:
            outside, _ = _arm(e, overshoot)
            courses.append(_line(outside, centre, 64))
        return courses
    raise ValueError(f"masks of {len(edges)} edges are not supported: {edges}")


def _course_radii(guide: Guide, course: np.ndarray) -> np.ndarray:
    """Per-sample band half-width along a course (tapers for an end cap)."""
    half = np.full(len(course), guide.width / 2.0)
    if guide.is_end_cap():
        s = np.linspace(0.0, 1.0, len(course))
        taper = 1.0 - smoothstep(END_TAPER_START, 1.0, s)
        half = half * (0.25 + 0.75 * taper)
    return half


def render_guide_mask(
    guide: Guide,
    out_px: int,
    hex_width: float,
    supersample: int = 4,
) -> np.ndarray:
    """Antialiased band coverage (float 0..1, `out_px` square) for a hex of
    `hex_width` px centred in the canvas. Tile-space geometry scales by
    `hex_width / 128`.
    """
    if guide.natural():
        return render_natural_mask(guide, out_px, hex_width)
    scale = hex_width / HEX_WIDTH
    offset = out_px / 2.0 - 64.0 * scale
    big = out_px * supersample
    canvas = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(canvas)
    for course in guide.courses():
        radii = _course_radii(guide, course)
        # dense resample so stamped discs overlap into a smooth band
        seg = np.linalg.norm(np.diff(course, axis=0), axis=1)
        arc = np.concatenate([[0.0], np.cumsum(seg)])
        step = max(0.25, guide.width / 16.0)
        dense = np.arange(0.0, arc[-1] + 1e-9, step)
        xs = np.interp(dense, arc, course[:, 0])
        ys = np.interp(dense, arc, course[:, 1])
        rs = np.interp(dense, arc, radii)
        for x, y, r in zip(xs, ys, rs):
            cx = (x * scale + offset) * supersample
            cy = (y * scale + offset) * supersample
            rr = r * scale * supersample
            draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=255)
    small = canvas.resize((out_px, out_px), Image.Resampling.BOX)
    return np.asarray(small, dtype=np.float64) / 255.0


def render_marker_mask(
    marker: Marker, out_px: int, hex_width: float, supersample: int = 4
) -> np.ndarray:
    """Antialiased coverage of a guide marker rectangle (see `Marker`)."""
    scale = hex_width / HEX_WIDTH
    a, b = marker.axis_edges
    if (b - a) % 6 != 3:
        raise ValueError(f"marker axis edges must be opposite, got {marker.axis_edges}")
    px, py = marker.long_axis()  # across the course
    ax, ay = py, -px  # along the course
    hl = marker.length / 2.0 * scale * supersample
    hw = marker.width / 2.0 * scale * supersample
    offset = out_px / 2.0 - 64.0 * scale
    cx = (marker.centre[0] * scale + offset) * supersample
    cy = (marker.centre[1] * scale + offset) * supersample
    corners = [
        (cx + sx * hl * px + sy * hw * ax, cy + sx * hl * py + sy * hw * ay)
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]
    big = out_px * supersample
    canvas = Image.new("L", (big, big), 0)
    ImageDraw.Draw(canvas).polygon(corners, fill=255)
    small = canvas.resize((out_px, out_px), Image.Resampling.BOX)
    return np.asarray(small, dtype=np.float64) / 255.0


def hex_coverage(
    out_w: int,
    out_h: int,
    centre: tuple[float, float],
    hex_width: float,
    supersample: int = 4,
) -> np.ndarray:
    """Antialiased flat-top hex coverage (float 0..1) on an `out_w` x `out_h` canvas.

    Unlike `hexgeom.hex_mask` the centre may sit anywhere (continuous coords).
    """
    big = Image.new("L", (out_w * supersample, out_h * supersample), 0)
    size = hex_width / 2.0 * supersample
    # PIL's polygon fill measured to within ~0.1 output px of unbiased with
    # no half-pixel offset at 4x supersampling (tested at fractional centres)
    cx = centre[0] * supersample
    cy = centre[1] * supersample
    pts = [
        (
            cx + size * math.cos(math.radians(60 * i)),
            cy + size * math.sin(math.radians(60 * i)),
        )
        for i in range(6)
    ]
    ImageDraw.Draw(big).polygon(pts, fill=255)
    small = big.resize((out_w, out_h), Image.Resampling.BOX)
    return np.asarray(small, dtype=np.float64) / 255.0


# --------------------------------------------------------- natural guides ----
#
# The plain guides above are constant-width straight bands, and Codex follows a
# guide literally: the 2026-11-09 river proof came back as a pale canal with
# ruler-straight banks, arrow-shaped forks and spear-tip ends. The natural
# geometry keeps the hard contract (every connected edge crossed at its
# midpoint, perpendicular, at full width, straight for the last
# NAT_STRAIGHT_PX) and draws a course that already looks like a river or a
# country track inside the hex:
#
# - two-edge courses swing in a soft single bulge or S-bend (`meander`), whose
#   envelope sin^2 has zero value and slope where it meets the straight runs;
# - width may breathe (`breathe`), returning to full width before each run;
# - each bank gets its own smooth noise (`ragged`) at a 2-5 px scale, faded
#   out over the straight runs so crossings stay clean;
# - a fork is a *tributary joining a main course*: the widest-angle pair of
#   edges is the main course, the third edge's stream curves in and merges at
#   a shallow downstream angle, and a morphological closing of radius
#   `round_px` rounds the confluence (it fills only the acute wedge, so there
#   is no pool);
# - a river end is the ratified spring head (river.py `_end_head`): straight
#   at full width over the pin zone, then a damped S-curve narrowing concavely
#   to a thread past the hex centre; a road end runs to just past the centre
#   and tapers softly;
# - a river guide with no edges is a pond (`pond` = mean diameter).
#
# Coverage is computed from distances to the stroke centrelines, so banks keep
# real concavities (stamped discs would fill every dip).

NAT_STRAIGHT_PX = 12.0  # straight full-width run inward of every crossing
NAT_OVERSHOOT_PX = 6.0  # strokes start this far outside the hex
NAT_STEP_PX = 0.5  # centreline sample spacing (tile px)
NAT_RAGGED_SIGMA_PX = 1.6  # bank-noise correlation length (tile px)
NAT_RAGGED_CLIP = 1.8  # bank noise is clipped at this many sigma (no specks)
NAT_RAGGED_FADE_PX = 6.0  # bank noise ramps in over this much course past the run
END_RIVER_TIP_PX = 82.0  # river spring tip, inbound distance from the crossing
END_RIVER_SNAKE_AMP = 8.0  # river head S-curve amplitude (river.py: 26/3)
END_RIVER_SNAKE_LEN = 40.0  # river head S-curve wavelength (river.py: 120/3)
END_RIVER_EXP = 1.6  # concave width exponent (river.py END_WIDTH_EXP)
END_RIVER_FLOOR_PX = 0.6  # half-width floor at the tip: a thread
END_ROAD_TIP_PX = 64.0  # road end: just past the hex centre
END_ROAD_TAPER_PX = 16.0  # road end narrows over its last this-many px...
END_ROAD_TAPER_TO = 0.55  # ...to this fraction of full width (default of Guide.end_taper)
TRIB_NARROW = 0.78  # a tributary narrows to this fraction before it joins
TRIB_MERGE_PULL = 2.6  # weight of the main's tangent in the merge direction
TRIB_BANK_ANGLE = 50.0  # angle to the main beyond which meeting its bank is penalised
FAN_BULGE_PX = 12.0  # fan forks: the main course bows this far away from the tributary
TRIB_SQUARE_WEIGHT = 0.3  # score weight of that penalty (vs max curvature, 1/px)


@dataclass(frozen=True)
class Stroke:
    """One centreline with per-sample half-width and bank-jitter weight."""

    points: np.ndarray  # (N, 2) tile px
    half: np.ndarray  # (N,) half-width, tile px
    jitter: np.ndarray  # (N,) 0..1 weight of the bank noise
    smooth_union: bool = True  # include in the un-jittered union used for rounding


def _resample(points: np.ndarray, step: float = NAT_STEP_PX) -> np.ndarray:
    """Polyline resampled at uniform arc-length spacing `step`."""
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    arc = np.concatenate([[0.0], np.cumsum(seg)])
    dense = np.arange(0.0, arc[-1] + 1e-9, step)
    if dense[-1] < arc[-1] - 1e-6:
        dense = np.append(dense, arc[-1])
    return np.stack(
        [np.interp(dense, arc, points[:, 0]), np.interp(dense, arc, points[:, 1])],
        axis=-1,
    )


def _arc_length(points: np.ndarray) -> np.ndarray:
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(seg)])


def _normals(points: np.ndarray) -> np.ndarray:
    """Unit left-normals (screen coords) of a polyline, by central differences."""
    d = np.gradient(points, axis=0)
    d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
    return np.stack([-d[:, 1], d[:, 0]], axis=-1)


def _tangents(points: np.ndarray) -> np.ndarray:
    d = np.gradient(points, axis=0)
    return d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)


def _run_points(edge: int) -> tuple[np.ndarray, np.ndarray]:
    """(outside point, inner point) of an edge's straight full-width run."""
    nx, ny = edge_normal(edge)
    a = apothem()
    outside = np.array([64.0 + (a + NAT_OVERSHOOT_PX) * nx, 64.0 + (a + NAT_OVERSHOOT_PX) * ny])
    inner = np.array([64.0 + (a - NAT_STRAIGHT_PX) * nx, 64.0 + (a - NAT_STRAIGHT_PX) * ny])
    return outside, inner


def _gap(a: int, b: int) -> int:
    return min((a - b) % 6, (b - a) % 6)


def _meander_offset(s: np.ndarray, rng: np.random.Generator, amp: float) -> np.ndarray:
    """Lateral offset along a middle section, s in 0..1: a bulge plus an S-bend.

    sin^2(pi s) is zero with zero slope at both ends, so the offset course joins
    the straight runs without a kink. The bulge/S mix is drawn from `rng` and
    scaled so the largest swing equals `amp`.
    """
    env = np.sin(np.pi * s) ** 2
    bulge = rng.uniform(-1.0, 1.0)
    snake = rng.choice([-1.0, 1.0]) * rng.uniform(0.5, 1.0)
    raw = env * (bulge + snake * np.sin(2.0 * np.pi * s))
    peak = float(np.abs(raw).max())
    return raw * (amp / peak) if peak > 1e-9 else raw


def _breathe_factor(s: np.ndarray, rng: np.random.Generator, amount: float) -> np.ndarray:
    """Relative half-width factor along a middle section: 1 at both ends."""
    phase = rng.uniform(0.0, 2.0 * np.pi)
    lobes = rng.uniform(1.0, 2.0)
    return 1.0 + amount * np.sin(np.pi * s) ** 2 * np.sin(2.0 * np.pi * lobes * s + phase)


def _jitter_weight(arc: np.ndarray, starts: list[float], ends: list[float]) -> np.ndarray:
    """0 inside the given [start, end] straight runs, ramping to 1 away from them."""
    w = np.ones_like(arc)
    for lo, hi in zip(starts, ends):
        dist = np.maximum(np.maximum(lo - arc, arc - hi), 0.0)
        w = np.minimum(w, smoothstep(0.0, NAT_RAGGED_FADE_PX, dist))
    return w


def _two_edge_stroke(
    guide: Guide,
    e1: int,
    e2: int,
    rng: np.random.Generator,
    meander: float,
    away_from: int = -1,
) -> Stroke:
    """A course between two edges: straight runs joined by a meandering middle.

    With `away_from` set to an edge index, the middle is instead one smooth
    bulge of FAN_BULGE_PX away from that edge's midpoint (fan forks, to make
    room for the tributary that enters between the two course edges).
    """
    o1, i1 = _run_points(e1)
    o2, i2 = _run_points(e2)
    centre = np.array([64.0, 64.0])
    if _gap(e1, e2) == 3:
        middle = _line(i1, i2, 256)
    else:
        handle = 0.55 * np.linalg.norm(i1 - centre)
        n1 = np.asarray(edge_normal(e1))
        n2 = np.asarray(edge_normal(e2))
        middle = _bezier(i1, i1 - n1 * handle, i2 - n2 * handle, i2, 256)
    middle = _resample(middle)
    s = _arc_length(middle) / _arc_length(middle)[-1]
    if away_from >= 0:
        mid = middle[len(middle) // 2]
        toward = np.asarray(edge_midpoint(away_from)) - mid
        sign = -1.0 if float(_normals(middle)[len(middle) // 2] @ toward) > 0 else 1.0
        offset = sign * FAN_BULGE_PX * np.sin(np.pi * s) ** 2
        rng.uniform()  # keep the draw sequence aligned with the meander branch
    else:
        offset = _meander_offset(s, rng, meander)
    middle = middle + _normals(middle) * offset[:, None]
    factor = _breathe_factor(s, rng, guide.breathe)
    run1 = _resample(_line(o1, i1, 8))
    run2 = _resample(_line(i2, o2, 8))
    pts = np.concatenate([run1[:-1], middle, run2[1:]])
    half = np.concatenate(
        [np.ones(len(run1) - 1), factor, np.ones(len(run2) - 1)]
    ) * (guide.width / 2.0)
    arc = _arc_length(pts)
    jitter = _jitter_weight(arc, [0.0, arc[len(run1) - 1 + len(middle) - 1]], [arc[len(run1) - 1], arc[-1]])
    return Stroke(pts, half, jitter)


def _end_stroke(guide: Guide, edge: int, rng: np.random.Generator) -> Stroke:
    """A river spring head or a road dead end entering through `edge`."""
    outside, _ = _run_points(edge)
    nx, ny = edge_normal(edge)
    a = apothem()
    crossing = np.array([64.0 + a * nx, 64.0 + a * ny])
    inward = -np.array([nx, ny])
    lateral = np.array([-inward[1], inward[0]])
    full = guide.width / 2.0
    river = guide.kind == "river"
    tip = END_RIVER_TIP_PX if river else END_ROAD_TIP_PX
    t = np.arange(0.0, tip + 1e-9, NAT_STEP_PX)
    plateau = NAT_STRAIGHT_PX
    s = np.clip((t - plateau) / (tip - plateau), 0.0, 1.0)
    sign = rng.choice([-1.0, 1.0])
    if river:
        lam = END_RIVER_SNAKE_LEN * rng.uniform(0.85, 1.15)
        amp = END_RIVER_SNAKE_AMP * rng.uniform(0.75, 1.0)
        off = sign * amp * np.sin(2 * np.pi * np.maximum(t - plateau, 0) / lam)
        off = off * np.sin(np.pi * s) ** 2
        half = np.where(
            t <= plateau,
            full,
            np.maximum(END_RIVER_FLOOR_PX, full * (1.0 - s) ** END_RIVER_EXP),
        )
    else:
        off = sign * guide.meander * np.sin(np.pi * s) ** 2 * np.sin(np.pi * s)
        taper = smoothstep(tip - END_ROAD_TAPER_PX, tip, t)
        half = full * (1.0 - (1.0 - guide.end_taper) * taper)
    inner = crossing[None, :] + t[:, None] * inward[None, :] + off[:, None] * lateral[None, :]
    run = _resample(_line(outside, crossing, 8))
    pts = np.concatenate([run[:-1], inner])
    halves = np.concatenate([np.full(len(run) - 1, full), half])
    arc = _arc_length(pts)
    run_end = arc[len(run) - 1] + plateau
    jitter = _jitter_weight(arc, [0.0], [run_end])
    # the thread narrows below the jitter amplitude: fade the noise with width
    jitter = jitter * np.clip(halves / full, 0.0, 1.0)
    return Stroke(pts, halves, jitter)


def fork_plan(edges: tuple[int, ...]) -> tuple[tuple[int, int], int]:
    """((main a, main b), tributary edge) for a three-edge fork.

    The main course is the widest-angle pair (a straight when the fork has an
    opposite pair, else a gentle turn). When all pairs tie (the 120 degree Y
    forks) the tributary is the edge on the mirror axis (N or S), so the tile
    stays left-right symmetric like its mask.
    """
    if len(edges) != 3:
        raise ValueError(f"a fork needs three edges, got {edges}")
    best: tuple[int, tuple[int, int], int] | None = None
    for t in edges:
        a, b = (e for e in edges if e != t)
        key = (_gap(a, b), 1 if t in (2, 5) else 0)
        score = key[0] * 10 + key[1]
        if best is None or score > best[0]:
            best = (score, (a, b), t)
    assert best is not None
    return best[1], best[2]


def _curvature(points: np.ndarray) -> np.ndarray:
    """Unsigned curvature (1/px) along a uniformly resampled polyline."""
    tang = _tangents(points)
    dtheta = np.abs(np.diff(np.unwrap(np.arctan2(tang[:, 1], tang[:, 0]))))
    return dtheta / NAT_STEP_PX


def _edge_clearance(points: np.ndarray, own_edge: int) -> float:
    """Smallest inward depth of `points` from any hex edge except `own_edge`."""
    depth = np.inf
    for e in range(6):
        if e == own_edge:
            continue
        nx, ny = edge_normal(e)
        d = apothem() - ((points[:, 0] - 64.0) * nx + (points[:, 1] - 64.0) * ny)
        depth = min(depth, float(d.min()))
    return depth


def _tributary_curve(
    inner: np.ndarray, inward: np.ndarray, join: np.ndarray, tau: np.ndarray
) -> np.ndarray:
    merge = inward + TRIB_MERGE_PULL * tau
    merge = merge / np.linalg.norm(merge)
    span = float(np.linalg.norm(join - inner))
    return _resample(
        _bezier(inner, inner + inward * 0.35 * span, join - merge * 0.6 * span, join, 128)
    )


def _tributary_stroke(
    guide: Guide, edge: int, main: Stroke, rng: np.random.Generator
) -> Stroke:
    """A tributary from `edge` curving into `main` at a shallow downstream angle.

    The join is searched, not fixed: every main-course sample between the
    straight runs, in both downstream directions, gives a candidate curve
    (straight run, then a cubic arriving along `inward + TRIB_MERGE_PULL * tau`,
    about 20 degrees off the main). Candidates that come within the rim margin
    or brush the main course away from the join are rejected; the survivor
    with the gentlest worst bend wins, penalised when it would still be
    meeting the main's bank square-on (the turn would hide inside the main's
    water and read as a T), with ties toward the hex centre. This keeps
    every fork a tributary merging into a river, never a T, a hook or an
    arrowhead, whatever the edge layout.
    """
    outside, inner = _run_points(edge)
    nx, ny = edge_normal(edge)
    inward = -np.array([nx, ny])
    pts = main.points
    tang = _tangents(pts)
    arc = _arc_length(pts)
    centre = np.array([64.0, 64.0])
    d_rim = hex_inset_distance(pts[:, 0], pts[:, 1])
    middle = np.flatnonzero(d_rim > NAT_STRAIGHT_PX + 4.0)
    margin = guide.width / 2.0 + 6.0
    best: tuple[float, np.ndarray] | None = None
    for j in middle[::4]:
        for sign in (1.0, -1.0):
            tau = tang[j] * sign
            if float(tau @ inward) < -0.2:
                continue  # would have to flow back upstream
            curve = _tributary_curve(inner, inward, pts[j], tau)
            if _edge_clearance(curve, edge) < margin:
                continue
            # the merge must show: where the tributary reaches the main's bank
            # it already runs within TRIB_BANK_ANGLE of the main's direction
            near = np.linalg.norm(curve[:, None, :] - pts[None, :, :], axis=-1).min(axis=1)
            at_bank = int(np.argmax(near <= guide.width / 2.0 + 1.0))
            heading = _tangents(curve)[at_bank]
            square = max(0.0, np.cos(np.radians(TRIB_BANK_ANGLE)) - float(heading @ tau))
            far = np.abs(arc - arc[j]) > 2.5 * guide.width
            gap = np.linalg.norm(curve[:-1, None, :] - pts[None, far, :], axis=-1)
            if far.any() and gap.min() < 1.2 * guide.width:
                continue
            score = (
                float(_curvature(curve).max())
                + TRIB_SQUARE_WEIGHT * square
                + 0.0008 * float(np.linalg.norm(pts[j] - centre))
            )
            if best is None or score < best[0]:
                best = (score, curve)
    if best is None:
        raise ValueError(f"no tributary course from edge {edge} fits {guide.edges}")
    curve = best[1]
    run = _resample(_line(outside, inner, 8))
    full_pts = np.concatenate([run[:-1], curve])
    s = np.concatenate([np.zeros(len(run) - 1), _arc_length(curve) / _arc_length(curve)[-1]])
    half = guide.width / 2.0 * (1.0 - (1.0 - TRIB_NARROW) * smoothstep(0.0, 0.6, s))
    arc_t = _arc_length(full_pts)
    jitter = _jitter_weight(arc_t, [0.0], [arc_t[len(run) - 1]])
    return Stroke(full_pts, half, jitter)


def _pond_strokes(guide: Guide, rng: np.random.Generator) -> list[Stroke]:
    """A still-water blob centred in the hex: a closed lobed outline as one stroke
    of zero half-width whose interior is filled by `render_natural_mask`."""
    theta = np.linspace(0.0, 2.0 * np.pi, 721)
    r = np.full_like(theta, guide.pond / 2.0)
    for k, amp in ((2, 0.10), (3, 0.07), (5, 0.03)):
        r *= 1.0 + amp * rng.uniform(0.5, 1.0) * np.cos(k * theta + rng.uniform(0, 2 * np.pi))
    pts = np.stack([64.0 + r * np.cos(theta), 64.0 + r * np.sin(theta)], axis=-1)
    return [Stroke(pts, np.zeros(len(pts)), np.ones(len(pts)))]


def _approach_stroke(guide: Guide) -> Stroke:
    """A road guide with no edges and a marker: short approaches either side of
    the marker along its long axis, tapering out (a free-standing bridge)."""
    if guide.marker is None:
        raise ValueError("a road guide with no edges needs a marker")
    lx, ly = guide.marker.long_axis()
    reach = guide.marker.length / 2.0 + 18.0
    c = np.asarray(guide.marker.centre)
    pts = _resample(_line(c - reach * np.array([lx, ly]), c + reach * np.array([lx, ly]), 8))
    arc = _arc_length(pts)
    end_dist = np.minimum(arc, arc[-1] - arc)
    half = guide.width / 2.0 * (0.35 + 0.65 * smoothstep(0.0, 12.0, end_dist))
    return Stroke(pts, half, smoothstep(0.0, 6.0, end_dist))


def natural_strokes(guide: Guide) -> list[Stroke]:
    """Every stroke of a natural guide (see the section comment)."""
    rng = np.random.default_rng(guide.seed)
    edges = guide.edges
    if not edges:
        if guide.kind == "river":
            if guide.pond <= 0:
                raise ValueError("a river guide with no edges needs pond > 0")
            return _pond_strokes(guide, rng)
        return [_approach_stroke(guide)]
    if len(edges) == 1:
        return [_end_stroke(guide, edges[0], rng)]
    if len(edges) == 2:
        e1, e2 = edges
        if _gap(e1, e2) == 1:
            raise ValueError(f"sharp turns are not drawn (edges {edges})")
        amp = guide.meander if _gap(e1, e2) == 3 else 0.6 * guide.meander
        return [_two_edge_stroke(guide, e1, e2, rng, amp)]
    if len(edges) == 3:
        (a, b), t = fork_plan(edges)
        amp = guide.meander if _gap(a, b) == 3 else 0.5 * guide.meander
        between = _gap(t, a) == 1 and _gap(t, b) == 1
        main = _two_edge_stroke(guide, a, b, rng, amp, away_from=t if between else -1)
        return [main, _tributary_stroke(guide, t, main, rng)]
    raise ValueError(f"masks of {len(edges)} edges are not supported: {edges}")


def _bank_noise(n: int, rng: np.random.Generator, step: float) -> np.ndarray:
    """Smooth unit-variance noise along a stroke, correlation NAT_RAGGED_SIGMA_PX."""
    sigma = NAT_RAGGED_SIGMA_PX / step
    pad = int(4 * sigma) + 1
    raw = rng.normal(0.0, 1.0, n + 2 * pad)
    k = np.exp(-0.5 * (np.arange(-pad, pad + 1) / sigma) ** 2)
    smooth = np.convolve(raw, k / k.sum(), mode="same")[pad : pad + n]
    unit = smooth / max(float(smooth.std()), 1e-9)
    return np.clip(unit, -NAT_RAGGED_CLIP, NAT_RAGGED_CLIP)


def _stroke_fields(
    stroke: Stroke, xs: np.ndarray, ys: np.ndarray, chunk: int = 4096
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(distance, index of nearest sample, side +1/-1) for every query point."""
    q = np.stack([xs.ravel(), ys.ravel()], axis=-1)
    dist = np.empty(len(q))
    idx = np.empty(len(q), dtype=int)
    for lo in range(0, len(q), chunk):
        d = np.linalg.norm(q[lo : lo + chunk, None, :] - stroke.points[None, :, :], axis=-1)
        i = np.argmin(d, axis=1)
        idx[lo : lo + chunk] = i
        dist[lo : lo + chunk] = d[np.arange(len(i)), i]
    normals = _normals(stroke.points)
    side = np.sign(((q - stroke.points[idx]) * normals[idx]).sum(axis=-1))
    side[side == 0] = 1.0
    return dist.reshape(xs.shape), idx.reshape(xs.shape), side.reshape(xs.shape)


def _polygon_inside(points: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Even-odd point-in-polygon test for a closed outline."""
    inside = np.zeros(xs.shape, dtype=bool)
    x0, y0 = points[:-1, 0], points[:-1, 1]
    x1, y1 = points[1:, 0], points[1:, 1]
    for a, b, c, d in zip(x0, y0, x1, y1):
        crosses = ((b > ys) != (d > ys)) & (xs < (c - a) * (ys - b) / (d - b + 1e-12) + a)
        inside ^= crosses
    return inside


def render_natural_mask(guide: Guide, out_px: int, hex_width: float) -> np.ndarray:
    """Antialiased coverage (0..1, `out_px` square) of a natural guide.

    Per pixel: distance to each stroke's centreline against that sample's
    half-width plus its bank's noise (both banks independent), antialiased over
    one output pixel. Forks add the rounded confluence fill: closing(smooth
    union) minus the smooth union, which is non-zero only in concave corners.
    """
    scale = hex_width / HEX_WIDTH
    offset = out_px / 2.0 - 64.0 * scale
    xs, ys = pixel_grid(out_px)
    tx = (xs - offset) / scale  # tile-space coordinates of the output pixels
    ty = (ys - offset) / scale
    rng = np.random.default_rng(guide.seed + 7919)
    cover = np.zeros((out_px, out_px))
    smooth = np.zeros((out_px, out_px))
    for stroke in natural_strokes(guide):
        dist, idx, side = _stroke_fields(stroke, tx, ty)
        if guide.kind == "river" and not guide.edges:
            inside = _polygon_inside(stroke.points, tx, ty)
            signed = np.where(inside, dist, -dist)
            n = len(stroke.points)
            noise = guide.ragged * _bank_noise(n, rng, NAT_STEP_PX)
            margin = signed + noise[idx]
            cover = np.maximum(cover, np.clip(0.5 + margin * scale, 0.0, 1.0))
            continue
        n = len(stroke.points)
        noise_l = _bank_noise(n, rng, NAT_STEP_PX)
        noise_r = _bank_noise(n, rng, NAT_STEP_PX)
        bank = np.where(side > 0, noise_l[idx], noise_r[idx])
        half = stroke.half[idx]
        jitter = guide.ragged * stroke.jitter[idx] * bank
        cover = np.maximum(cover, np.clip(0.5 + (half + jitter - dist) * scale, 0.0, 1.0))
        if stroke.smooth_union:
            smooth = np.maximum(smooth, np.clip(0.5 + (half - dist) * scale, 0.0, 1.0))
    if len(guide.edges) >= 3:
        union = smooth
        if guide.round_px > 0:
            r = max(1, int(round(guide.round_px * scale)))
            union = np.maximum(smooth, min_filter(max_filter(smooth, r), r))
            cover = np.maximum(cover, union - smooth)  # the rounded fill
        # only the final shore carries bank noise: where strokes overlap (or the
        # fill meets a stroke) one stroke's noise must not cut a grass sliver
        # into the water, so anything deeper inside the union than the noise
        # can reach is water
        reach = max(1, int(np.ceil(guide.ragged * NAT_RAGGED_CLIP * scale)) + 1)
        cover = np.maximum(cover, min_filter(union, reach))
    return cover


# ------------------------------------------------- job-file style fields ----

GUIDE_STYLE_LIMITS: dict[str, tuple[float, float]] = {
    "meander": (0.0, 20.0),
    "breathe": (0.0, 0.3),
    "ragged": (0.0, 3.0),
    "round_px": (0.0, 12.0),
    "pond": (0.0, 80.0),
    "hold_half": (0.0, 40.0),
    "end_taper": (0.2, 1.0),
}


def parse_guide_style(raw: dict) -> dict:
    """Validate the optional natural-guide fields of a job's guide object.

    Returns keyword arguments for `Guide` (only the keys present). Raises
    ValueError naming the bad field; the job loader wraps it with context.
    """
    out: dict = {}
    for key, (lo, hi) in GUIDE_STYLE_LIMITS.items():
        if key in raw:
            v = raw[key]
            if not isinstance(v, (int, float)) or not lo <= v <= hi:
                raise ValueError(f"guide.{key} must be a number in {lo}..{hi}, got {v!r}")
            out[key] = float(v)
    if "seed" in raw:
        if not isinstance(raw["seed"], int):
            raise ValueError(f"guide.seed must be an integer, got {raw['seed']!r}")
        out["seed"] = raw["seed"]
    if "hold_edges" in raw:
        he = raw["hold_edges"]
        if not isinstance(he, list) or not all(isinstance(e, int) and 0 <= e <= 5 for e in he):
            raise ValueError(f"guide.hold_edges must be a list of edges 0..5, got {he!r}")
        out["hold_edges"] = tuple(sorted(he))
    return out


def parse_marker_extras(raw: dict) -> dict:
    """Validate a marker's optional `centre` ([x, y] tile px) and `rotate_deg`."""
    out: dict = {}
    if "centre" in raw:
        c = raw["centre"]
        if not (
            isinstance(c, list)
            and len(c) == 2
            and all(isinstance(v, (int, float)) and 0 <= v <= TILE_PX for v in c)
        ):
            raise ValueError(f"marker.centre must be [x, y] in tile px, got {c!r}")
        out["centre"] = (float(c[0]), float(c[1]))
    if "rotate_deg" in raw:
        r = raw["rotate_deg"]
        if not isinstance(r, (int, float)):
            raise ValueError(f"marker.rotate_deg must be a number, got {r!r}")
        out["rotate_deg"] = float(r)
    return out


def style_from_sidecar(raw: dict) -> dict:
    """The natural-guide fields of a sidecar guide (trusted, written by asdict)."""
    out = {k: raw[k] for k in GUIDE_STYLE_LIMITS if k in raw}
    if "seed" in raw:
        out["seed"] = raw["seed"]
    if "hold_edges" in raw:
        out["hold_edges"] = tuple(raw["hold_edges"])
    return out


def marker_extras_from_sidecar(raw: dict) -> dict:
    """The optional marker fields of a sidecar marker (trusted)."""
    out = {}
    if "centre" in raw:
        out["centre"] = tuple(raw["centre"])
    if "rotate_deg" in raw:
        out["rotate_deg"] = raw["rotate_deg"]
    return out
