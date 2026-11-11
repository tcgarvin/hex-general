"""Raster helpers for the staff-map mocks: hex lookups, noise, blur, ink, blending.

Every per-pixel function here is shaped like a fragment shader on purpose (each
pixel reads a handful of hex values and some hashed noise), so whatever style is
picked can be ported to a WebGL pass or a one-off canvas bake in the browser.

World coordinates are hex-radius units of the frontend's flat-top layout
(``frontend/src/board/hex.ts axialToPixel`` with ``size = 1``): hex ``(col, row)``
in odd-q offset sits at ``x = 1.5 col``, ``y = sqrt(3) (row + (col & 1) / 2)``.
A `View` maps world coordinates to image pixels at ``radius`` pixels per unit,
so a hex is ``2 * radius`` pixels wide point to point (board-mock's ``--hex-px``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

SQRT3 = math.sqrt(3.0)
AXIAL_DIRECTIONS: tuple[tuple[int, int], ...] = (
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, 0),
    (-1, 1),
    (0, 1),
)

Rgb = np.ndarray  # float32 (3,) in 0..1


def rgb(color: int) -> Rgb:
    """``0xRRGGBB`` as a float RGB triple in 0..1."""
    return np.array(
        [(color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF], dtype=np.float32
    ) / np.float32(255.0)


@dataclass(frozen=True)
class View:
    """An image window onto the world: ``radius`` px per world unit."""

    radius: float
    x0: float  # world x of the image's left edge
    y0: float  # world y of the image's top edge
    width: int
    height: int

    @property
    def hex_px(self) -> float:
        """Point-to-point hex width in pixels."""
        return 2.0 * self.radius

    def to_px(self, wx: np.ndarray, wy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """World coordinates to (sub)pixel coordinates."""
        return (wx - self.x0) * self.radius, (wy - self.y0) * self.radius

    def world_grid(self) -> tuple[np.ndarray, np.ndarray]:
        """World coordinates of every pixel centre, as (H, W) float64 arrays."""
        xs = self.x0 + (np.arange(self.width, dtype=np.float64) + 0.5) / self.radius
        ys = self.y0 + (np.arange(self.height, dtype=np.float64) + 0.5) / self.radius
        return np.meshgrid(xs, ys)

    def world_bounds(self, margin: float = 0.0) -> tuple[float, float, float, float]:
        """``(x0, y0, x1, y1)`` in world units, grown by ``margin`` units."""
        return (
            self.x0 - margin,
            self.y0 - margin,
            self.x0 + self.width / self.radius + margin,
            self.y0 + self.height / self.radius + margin,
        )


class HexGrid:
    """The odd-q offset rectangle as flat arrays (index ``row * width + col``).

    Per-hex arrays handed to the samplers are *padded*: one extra trailing value
    stands for "off the map", and every lookup that misses the map indexes it.
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.size = width * height
        index = np.arange(self.size)
        self.col = index % width
        self.row = index // width
        self.cx = 1.5 * self.col
        self.cy = SQRT3 * (self.row + 0.5 * (self.col & 1))
        self.q = self.col
        self.r = self.row - (self.col - (self.col & 1)) // 2
        neighbors = np.full((self.size, 6), self.size, dtype=np.int64)
        for d, (dq, dr) in enumerate(AXIAL_DIRECTIONS):
            neighbors[:, d] = self.index_of(self.q + dq, self.r + dr)
        self.neighbors = neighbors
        self.ring = np.vstack([neighbors, np.full((1, 6), self.size, dtype=np.int64)])

    def index_of(self, q: np.ndarray, r: np.ndarray) -> np.ndarray:
        """Hex index of axial ``(q, r)``; ``size`` (the pad slot) when off the map."""
        q = np.asarray(q, dtype=np.int64)
        r = np.asarray(r, dtype=np.int64)
        row = r + (q - (q & 1)) // 2
        inside = (q >= 0) & (q < self.width) & (row >= 0) & (row < self.height)
        return np.where(inside, row * self.width + q, self.size)

    def world_size(self) -> tuple[float, float, float, float]:
        """Bounding box of every hex, corners included: ``(x0, y0, x1, y1)``."""
        return (
            -1.0,
            -SQRT3 / 2,
            1.5 * (self.width - 1) + 1.0,
            SQRT3 * (self.height - 0.5) + SQRT3 / 2,
        )

    def pad(self, values: np.ndarray, outside: float | int) -> np.ndarray:
        """``values`` with the off-map slot appended."""
        return np.concatenate([values, np.array([outside], dtype=values.dtype)])

    def smooth(self, values: np.ndarray, passes: int, keep: float = 0.4) -> np.ndarray:
        """Average each hex with its neighbours ``passes`` times (off-map = itself)."""
        out = values.astype(np.float32)
        for _ in range(passes):
            padded = np.concatenate([out, [0.0]]).astype(np.float32)
            nb = padded[self.neighbors]
            missing = self.neighbors == self.size
            nb[missing] = np.repeat(out[:, None], 6, axis=1)[missing]
            out = keep * out + (1.0 - keep) * nb.mean(axis=1)
        return out


def _axial_fraction(wx: np.ndarray, wy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return (2.0 / 3.0) * wx, (-1.0 / 3.0) * wx + (SQRT3 / 3.0) * wy


def nearest_hex(grid: HexGrid, wx: np.ndarray, wy: np.ndarray) -> np.ndarray:
    """Index (or the pad slot) of the hex containing each point (cube rounding)."""
    q, r = _axial_fraction(wx, wy)
    s = -q - r
    rq, rr, rs = np.round(q), np.round(r), np.round(s)
    dq, dr, ds = np.abs(rq - q), np.abs(rr - r), np.abs(rs - s)
    fix_q = (dq > dr) & (dq > ds)
    fix_r = ~fix_q & (dr > ds)
    rq = np.where(fix_q, -rr - rs, rq)
    rr = np.where(fix_r, -rq - rs, rr)
    return grid.index_of(rq.astype(np.int64), rr.astype(np.int64))


@dataclass(frozen=True)
class Triangles:
    """For each pixel, the three hex centres around it and barycentric weights.

    Hex centres form a triangular lattice; interpolating per-hex values over it
    gives a continuous field with no hex staircase, from three lookups a pixel.
    """

    i0: np.ndarray
    i1: np.ndarray
    i2: np.ndarray
    w0: np.ndarray
    w1: np.ndarray
    w2: np.ndarray

    def sample(self, padded: np.ndarray) -> np.ndarray:
        """Interpolate a padded per-hex float array at every pixel."""
        return (
            self.w0 * padded[self.i0]
            + self.w1 * padded[self.i1]
            + self.w2 * padded[self.i2]
        ).astype(np.float32)

    def label(self, padded: np.ndarray) -> np.ndarray:
        """The per-hex label holding the largest summed weight at each pixel."""
        o0, o1, o2 = padded[self.i0], padded[self.i1], padded[self.i2]
        s0 = self.w0 + self.w1 * (o1 == o0) + self.w2 * (o2 == o0)
        s1 = self.w1 + self.w0 * (o0 == o1) + self.w2 * (o2 == o1)
        s2 = self.w2 + self.w0 * (o0 == o2) + self.w1 * (o1 == o2)
        return np.where((s0 >= s1) & (s0 >= s2), o0, np.where(s1 >= s2, o1, o2))

    def smooth_label(
        self, padded: np.ndarray, ring: np.ndarray, keep: float = 0.4
    ) -> np.ndarray:
        """Like `label`, but each corner votes with its neighbourhood: a hex counts
        ``keep`` for its own label and ``(1 - keep) / 6`` per neighbour's. The
        boundaries come out rounded instead of following the hex lattice.

        ``ring`` is ``(size + 1, 6)``: each padded hex's six neighbour slots.
        """
        corners = (self.i0, self.i1, self.i2)
        weights = (self.w0, self.w1, self.w2)
        own = [padded[i] for i in corners]
        near = [padded[ring[i]] for i in corners]
        share = (1.0 - keep) / 6.0
        best_score = None
        best = own[0]
        for cand in own:
            score = np.zeros(cand.shape, dtype=np.float32)
            for w, o, nb in zip(weights, own, near):
                votes = keep * (o == cand) + share * (nb == cand[..., None]).sum(
                    axis=-1
                )
                score += w * votes
            if best_score is None:
                best_score, best = score, cand
            else:
                better = score > best_score
                best = np.where(better, cand, best)
                best_score = np.where(better, score, best_score)
        return best


def triangles(grid: HexGrid, wx: np.ndarray, wy: np.ndarray) -> Triangles:
    """The `Triangles` lookup for points ``(wx, wy)``."""
    q, r = _axial_fraction(wx, wy)
    q0, r0 = np.floor(q), np.floor(r)
    fq, fr = (q - q0).astype(np.float32), (r - r0).astype(np.float32)
    q0i, r0i = q0.astype(np.int64), r0.astype(np.int64)
    lower = (fq + fr) < 1.0
    # Lower triangle (q0,r0),(q0+1,r0),(q0,r0+1); upper (q0+1,r0+1),(q0+1,r0),(q0,r0+1).
    i0 = np.where(lower, grid.index_of(q0i, r0i), grid.index_of(q0i + 1, r0i + 1))
    i1 = grid.index_of(q0i + 1, r0i)
    i2 = grid.index_of(q0i, r0i + 1)
    w0 = np.where(lower, 1.0 - fq - fr, fq + fr - 1.0).astype(np.float32)
    w1 = np.where(lower, fq, 1.0 - fr).astype(np.float32)
    w2 = np.where(lower, fr, 1.0 - fq).astype(np.float32)
    return Triangles(i0, i1, i2, w0, w1, w2)


# --- noise ---------------------------------------------------------------------


def _hash(ix: np.ndarray, iy: np.ndarray, seed: int) -> np.ndarray:
    """A 32-bit integer hash of lattice points, as floats in [0, 1)."""
    with np.errstate(over="ignore"):
        h = ix.astype(np.int64).astype(np.uint32) * np.uint32(0x8DA6B343)
        h ^= iy.astype(np.int64).astype(np.uint32) * np.uint32(0xD8163841)
        h ^= np.uint32((seed * 0x9E3779B1) & 0xFFFFFFFF)
        h ^= h >> np.uint32(13)
        h *= np.uint32(0x5BD1E995)
        h ^= h >> np.uint32(15)
        h *= np.uint32(0x27D4EB2F)
        h ^= h >> np.uint32(16)
    return (h & np.uint32(0xFFFFFF)).astype(np.float32) / np.float32(16777216.0)


def hash01(ix: np.ndarray, iy: np.ndarray, seed: int) -> np.ndarray:
    """Public lattice hash in [0, 1) (jitter for stipples, building layouts)."""
    return _hash(np.asarray(ix), np.asarray(iy), seed)


def value_noise(x: np.ndarray, y: np.ndarray, seed: int) -> np.ndarray:
    """Smooth value noise in [0, 1] with unit lattice spacing."""
    ix, iy = np.floor(x), np.floor(y)
    fx, fy = (x - ix).astype(np.float32), (y - iy).astype(np.float32)
    ix, iy = ix.astype(np.int64), iy.astype(np.int64)
    ux = fx * fx * (3.0 - 2.0 * fx)
    uy = fy * fy * (3.0 - 2.0 * fy)
    a = _hash(ix, iy, seed)
    b = _hash(ix + 1, iy, seed)
    c = _hash(ix, iy + 1, seed)
    d = _hash(ix + 1, iy + 1, seed)
    return (a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy).astype(
        np.float32
    )


def fbm(
    x: np.ndarray, y: np.ndarray, freq: float, octaves: int, seed: int
) -> np.ndarray:
    """Fractal value noise, roughly in [-1, 1]; ``freq`` cycles per unit."""
    total = np.zeros(np.shape(x), dtype=np.float32)
    amp, norm = 1.0, 0.0
    for k in range(octaves):
        # Rotate each octave a little so lattice rows never line up.
        angle = 0.5 + 1.1 * k
        ca, sa = math.cos(angle), math.sin(angle)
        f = freq * (2.03**k)
        xr = (x * ca - y * sa) * f
        yr = (x * sa + y * ca) * f
        total += amp * (value_noise(xr, yr, seed + 101 * k) * 2.0 - 1.0)
        norm += amp
        amp *= 0.5
    return total / np.float32(norm)


# --- blur and field lines ---------------------------------------------------------


def _box(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    if r <= 0:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(np.float32)


def blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Approximate Gaussian blur (three box passes per axis), edges clamped."""
    if sigma < 0.35:
        return a.astype(np.float32)
    r = max(1, int(round(math.sqrt(12.0 * sigma * sigma / 3.0 + 1.0) / 2.0)))
    out = a.astype(np.float32)
    for _ in range(3):
        out = _box(out, r, 0)
        out = _box(out, r, 1)
    return out


def iso_line(field: np.ndarray, level: float, width_px: float) -> np.ndarray:
    """Alpha of a constant-width line along ``field == level`` (screen-space AA)."""
    gy, gx = np.gradient(field)
    g = np.sqrt(gx * gx + gy * gy) + 1e-6
    d = np.abs(field - level) / g
    return np.clip(width_px / 2.0 + 0.5 - d, 0.0, 1.0).astype(np.float32)


def contour_lines(field: np.ndarray, step: float, width_px: float) -> np.ndarray:
    """Alpha of constant-width lines at every multiple of ``step`` in ``field``."""
    v = field / step
    gy, gx = np.gradient(v)
    g = np.sqrt(gx * gx + gy * gy) + 1e-6
    d = np.abs(v - np.round(v)) / g
    alpha = np.clip(width_px / 2.0 + 0.5 - d, 0.0, 1.0)
    # Where the field is nearly flat the lines would smear into blobs.
    return (alpha * np.clip(g * 6.0, 0.0, 1.0)).astype(np.float32)


def smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


# --- blending ------------------------------------------------------------------------


def paint(canvas: np.ndarray, color: Rgb | np.ndarray, alpha: np.ndarray) -> None:
    """Alpha-blend ``color`` (a triple or an (H, W, 3) image) over ``canvas``."""
    a = np.clip(alpha, 0.0, 1.0)[..., None]
    canvas += (color - canvas) * a


def multiply(canvas: np.ndarray, color: Rgb | np.ndarray, alpha: np.ndarray) -> None:
    """Multiply ``canvas`` toward ``color`` by ``alpha`` (ink and tint on paper)."""
    a = np.clip(alpha, 0.0, 1.0)[..., None]
    canvas *= 1.0 - a * (1.0 - color)


def to_image(canvas: np.ndarray) -> Image.Image:
    """Float RGB canvas to an 8-bit PIL image."""
    return Image.fromarray(
        (np.clip(canvas, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB"
    )


# --- vector ink ------------------------------------------------------------------------


class Ink:
    """An antialiased coverage mask drawn with PIL at ``ss`` times supersampling.

    Coordinates are world units; widths and radii are screen pixels.
    """

    def __init__(self, view: View, ss: int = 3) -> None:
        self.view = view
        self.ss = ss
        self.image = Image.new("L", (view.width * ss, view.height * ss), 0)
        self.draw = ImageDraw.Draw(self.image)
        self._x0, self._y0, self._x1, self._y1 = view.world_bounds(margin=2.0)

    def _xy(self, wx: float, wy: float) -> tuple[float, float]:
        k = self.view.radius * self.ss
        return (wx - self.view.x0) * k, (wy - self.view.y0) * k

    def visible(self, wx: float, wy: float, margin: float = 0.0) -> bool:
        return (
            self._x0 - margin <= wx <= self._x1 + margin
            and self._y0 - margin <= wy <= self._y1 + margin
        )

    def polyline(self, points: np.ndarray, width_px: float, value: int = 255) -> None:
        """A round-jointed polyline through world ``points`` (n, 2)."""
        if len(points) < 2:
            return
        lo, hi = points.min(axis=0), points.max(axis=0)
        if hi[0] < self._x0 or lo[0] > self._x1 or hi[1] < self._y0 or lo[1] > self._y1:
            return
        k = self.view.radius * self.ss
        px = (points - np.array([self.view.x0, self.view.y0])) * k
        w = max(1, int(round(width_px * self.ss)))
        coords = [tuple(p) for p in px.tolist()]
        self.draw.line(coords, fill=value, width=w, joint="curve")
        r = w / 2.0
        for x, y in (coords[0], coords[-1]):
            self.draw.ellipse((x - r, y - r, x + r, y + r), fill=value)

    def circle(
        self,
        wx: float,
        wy: float,
        radius_px: float,
        value: int = 255,
        dx: float = 0.0,
        dy: float = 0.0,
    ) -> None:
        """A filled disc; ``dx``/``dy`` offset it in screen pixels."""
        x, y = self._xy(wx, wy)
        x += dx * self.ss
        y += dy * self.ss
        r = radius_px * self.ss
        self.draw.ellipse((x - r, y - r, x + r, y + r), fill=value)

    def ring(
        self, wx: float, wy: float, radius_px: float, width_px: float, value: int = 255
    ) -> None:
        x, y = self._xy(wx, wy)
        r = radius_px * self.ss
        self.draw.ellipse(
            (x - r, y - r, x + r, y + r),
            outline=value,
            width=max(1, int(round(width_px * self.ss))),
        )

    def polygon_px(
        self,
        wx: float,
        wy: float,
        points_px: Sequence[tuple[float, float]],
        value: int = 255,
    ) -> None:
        """A filled polygon given in screen-pixel offsets from a world point."""
        x, y = self._xy(wx, wy)
        self.draw.polygon(
            [(x + px * self.ss, y + py * self.ss) for px, py in points_px], fill=value
        )

    def line_px(
        self,
        wx: float,
        wy: float,
        points_px: Sequence[tuple[float, float]],
        width_px: float,
        value: int = 255,
    ) -> None:
        """An open polyline given in screen-pixel offsets from a world point."""
        x, y = self._xy(wx, wy)
        self.draw.line(
            [(x + px * self.ss, y + py * self.ss) for px, py in points_px],
            fill=value,
            width=max(1, int(round(width_px * self.ss))),
            joint="curve",
        )

    def polygon_world(self, points: np.ndarray, value: int = 255) -> None:
        coords = [self._xy(float(x), float(y)) for x, y in points]
        self.draw.polygon(coords, fill=value)

    def alpha(self) -> np.ndarray:
        """The mask downsampled to view size, as float coverage in 0..1."""
        small = self.image.resize((self.view.width, self.view.height), Image.BOX)
        return np.asarray(small, dtype=np.float32) / np.float32(255.0)


class Sprites:
    """An RGBA layer for markers (pins, counters), drawn supersampled."""

    def __init__(self, view: View, ss: int = 3) -> None:
        self.view = view
        self.ss = ss
        self.image = Image.new(
            "RGBA", (view.width * ss, view.height * ss), (0, 0, 0, 0)
        )
        self.draw = ImageDraw.Draw(self.image)

    def xy(self, wx: float, wy: float) -> tuple[float, float]:
        k = self.view.radius * self.ss
        return (wx - self.view.x0) * k, (wy - self.view.y0) * k

    def inside(self, wx: float, wy: float, margin_px: float = 20.0) -> bool:
        x, y = self.xy(wx, wy)
        m = margin_px * self.ss
        return -m <= x <= self.image.width + m and -m <= y <= self.image.height + m

    def composite_onto(self, canvas: np.ndarray) -> None:
        small = self.image.resize((self.view.width, self.view.height), Image.LANCZOS)
        arr = np.asarray(small, dtype=np.float32) / np.float32(255.0)
        paint(canvas, arr[..., :3], arr[..., 3])
