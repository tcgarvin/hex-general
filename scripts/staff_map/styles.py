"""The far-zoom map styles: ``current``, ``staff-map``, ``staff-map-night``,
``painted-far`` and ``hybrid``.

Each style is a function of (snapshot, derived, view, viewer) to an RGB image,
deterministic and purely procedural. The printed base map of every style is
built from per-pixel passes over a few per-hex fields (terrain masks, height,
owner) plus polylines for rivers and roads; the ownership layer is a label
image with blurred boundary bands. See ``scripts/staff-map-mock.py``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image

from . import marks
from .marks import mix
from .raster import (
    SQRT3,
    Ink,
    Sprites,
    View,
    blur,
    contour_lines,
    fbm,
    hash01,
    iso_line,
    multiply,
    nearest_hex,
    paint,
    rgb,
    smoothstep,
    to_image,
    triangles,
    value_noise,
)
from .world import (
    CITY,
    HILLS,
    LAKE,
    NOBODY,
    RIVER,
    TOWN,
    VACANT,
    WOODS,
    Derived,
    Snapshot,
    owner_palette,
)

STYLES = ("current", "staff-map", "staff-map-night", "painted-far", "hybrid")
GRID_MODES = ("km", "hex", "none")

# Zoom anchors (hex radius in px) for the three review scales.
R_FAR, R_MID, R_NEAR = 2.66, 5.0, 11.0


@dataclass(frozen=True)
class Scene:
    snap: Snapshot
    derived: Derived
    view: View
    viewer: int  # owner code drawn in the viewer's blue
    colors: dict[int, int]
    grid_mode: str
    X: np.ndarray
    Y: np.ndarray

    def z(self, far: float, mid: float, near: float) -> float:
        """A parameter interpolated over log zoom between the three anchors."""
        lr = math.log(self.view.radius)
        a, b, c = math.log(R_FAR), math.log(R_MID), math.log(R_NEAR)
        if lr <= a:
            return far
        if lr <= b:
            return far + (mid - far) * (lr - a) / (b - a)
        if lr <= c:
            return mid + (near - mid) * (lr - b) / (c - b)
        return near

    @property
    def R(self) -> float:
        return self.view.radius

    def hexes_in_view(self, margin: float = 2.0) -> np.ndarray:
        g = self.derived.grid
        x0, y0, x1, y1 = self.view.world_bounds(margin)
        return np.flatnonzero((g.cx >= x0) & (g.cx <= x1) & (g.cy >= y0) & (g.cy <= y1))


def make_scene(
    snap: Snapshot, derived: Derived, view: View, viewer: int, grid_mode: str = "km"
) -> Scene:
    viewer_id = snap.players[viewer - 1]
    X, Y = view.world_grid()
    return Scene(
        snap, derived, view, viewer, owner_palette(snap, viewer_id), grid_mode, X, Y
    )


def render(style: str, scene: Scene) -> Image.Image:
    if style not in RENDERERS:
        raise ValueError(f"unknown style {style!r}; use one of {STYLES}")
    return RENDERERS[style](scene)


# ==============================================================================
# current: a faithful copy of today's overview (WorldRenderer.drawOverview etc.)
# ==============================================================================

TERRAIN_FILL = {  # frontend/src/board/palette.ts TERRAIN_FILL, by terrain code
    0: 0x68692E,
    1: 0x3A4420,
    2: 0x9C8A5E,
    3: 0x41504C,
    4: 0x877353,
    5: 0x8F7150,
    6: 0x5A4636,
    7: 0x41504C,
}
SITE_OVERVIEW_FILL = {  # frontend/src/world/worldTiles.ts
    "farm": 0xB39A4A,
    "oil_field": 0x26221C,
    "steel_mill": 0x8A4A2E,
    "factory": 0x6F6257,
}
ROAD_COLOR = 0xA78E62
OVERVIEW_HEX = 2  # the overview texture's hex radius in texture px
HEX_SIZE = 32  # board hex radius in world px (camera scale = R / HEX_SIZE)
NEUTRAL_SITE_MIN_SCALE = 0.12
TOKEN_STROKE = 0x101114


def _overview_texture(scene: Scene) -> np.ndarray:
    """``drawOverview``: each texel takes the hex under its corner point."""
    g = scene.derived.grid
    snap = scene.snap
    x0, y0, x1, y1 = g.world_size()
    w = math.ceil((x1 - x0) * OVERVIEW_HEX)
    h = math.ceil((y1 - y0) * OVERVIEW_HEX)
    tx, ty = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    wx = (tx + x0 * OVERVIEW_HEX) / OVERVIEW_HEX
    wy = (ty + y0 * OVERVIEW_HEX) / OVERVIEW_HEX
    idx = nearest_hex(g, wx, wy)
    fill = np.array([TERRAIN_FILL[int(t)] for t in snap.terrain], dtype=np.int64)
    for site in snap.sites:
        if site.kind in SITE_OVERVIEW_FILL:
            fill[site.hex] = SITE_OVERVIEW_FILL[site.kind]
    road_dot = np.zeros(g.size + 1, dtype=bool)
    road_dot[: g.size] = snap.road & (snap.terrain != RIVER)
    cx = g.pad(g.cx, 0.0)[idx] * OVERVIEW_HEX
    cy = g.pad(g.cy, 0.0)[idx] * OVERVIEW_HEX
    near_centre = (np.abs(cx - wx * OVERVIEW_HEX) < 0.9) & (
        np.abs(cy - wy * OVERVIEW_HEX) < 0.9
    )
    color = g.pad(fill, -1)[idx]
    color = np.where(road_dot[idx] & near_centre, ROAD_COLOR, color)
    tex = np.zeros((h, w, 4), dtype=np.float32)
    on = color >= 0
    tex[..., 0] = ((color >> 16) & 0xFF) / 255.0
    tex[..., 1] = ((color >> 8) & 0xFF) / 255.0
    tex[..., 2] = (color & 0xFF) / 255.0
    tex[..., 3] = on
    tex[~on, :3] = 0.0
    return tex


def _bilinear(tex: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Sample texel-space coordinates (texel k spans [k, k + 1)) bilinearly."""
    h, w = tex.shape[:2]
    fu, fv = u - 0.5, v - 0.5
    iu, iv = np.floor(fu).astype(np.int64), np.floor(fv).astype(np.int64)
    au, av = (fu - iu)[..., None], (fv - iv)[..., None]
    iu0, iu1 = np.clip(iu, 0, w - 1), np.clip(iu + 1, 0, w - 1)
    iv0, iv1 = np.clip(iv, 0, h - 1), np.clip(iv + 1, 0, h - 1)
    top = tex[iv0, iu0] * (1 - au) + tex[iv0, iu1] * au
    bot = tex[iv1, iu0] * (1 - au) + tex[iv1, iu1] * au
    return top * (1 - av) + bot * av


GAME_BACKGROUND = 0x14161A


def render_current(scene: Scene) -> Image.Image:
    g = scene.derived.grid
    x0, y0, _, _ = g.world_size()
    tex = _overview_texture(scene)
    u = (scene.X - x0) * OVERVIEW_HEX
    v = (scene.Y - y0) * OVERVIEW_HEX
    sampled = _bilinear(tex, u, v)
    canvas = np.empty(sampled.shape[:2] + (3,), dtype=np.float32)
    canvas[:] = rgb(GAME_BACKGROUND)
    paint(
        canvas, sampled[..., :3] / np.maximum(sampled[..., 3:], 1e-6), sampled[..., 3]
    )

    layer = Sprites(scene.view)
    snap = scene.snap
    camera = scene.R / HEX_SIZE
    for k, site in enumerate(snap.sites):
        holder = int(snap.holders[k])
        if holder == VACANT:
            continue
        x, y = g.cx[site.hex], g.cy[site.hex]
        if not layer.inside(x, y):
            continue
        if holder == NOBODY:
            if camera >= NEUTRAL_SITE_MIN_SCALE:
                marks.token(layer, x, y, 2.2, 0xB8B2A2, 0, outline_px=0, alpha=0.7)
        else:
            r = 5.0 if site.kind == "base" else 3.5
            marks.token(layer, x, y, r, scene.colors[holder], TOKEN_STROKE)
    # Units: one token per hex (the first deployed unit leads), garrisons included.
    by_hex: dict[int, list] = {}
    for u_ in snap.units:
        by_hex.setdefault(u_.hex, []).append(u_)
    r = max(HEX_SIZE * 0.6 * camera, 4.0)
    for hex_, units in sorted(by_hex.items()):
        x, y = g.cx[hex_], g.cy[hex_]
        if not layer.inside(x, y):
            continue
        lead = next((u_ for u_ in units if not u_.column), units[0])
        color = scene.colors[lead.owner]
        s = layer.ss
        px, py = layer.xy(x, y)
        stroke = marks.rgba(TOKEN_STROKE)
        width = max(1, int(round(r * 0.15 * s)))
        if lead.column:
            layer.draw.rounded_rectangle(
                (
                    px - 1.2 * r * s,
                    py - 0.7 * r * s,
                    px + 1.2 * r * s,
                    py + 0.7 * r * s,
                ),
                radius=0.3 * r * s,
                fill=marks.rgba(color),
                outline=stroke,
                width=width,
            )
        else:
            layer.draw.ellipse(
                (px - r * s, py - r * s, px + r * s, py + r * s),
                fill=marks.rgba(color),
                outline=stroke,
                width=width,
            )
    layer.composite_onto(canvas)
    return to_image(canvas)


# ==============================================================================
# shared terrain fields
# ==============================================================================


@dataclass(frozen=True)
class Fields:
    """Per-pixel terrain fields shared by the procedural styles."""

    woods: np.ndarray  # organic woods mask 0..1
    woods_field: np.ndarray  # the field it was thresholded from (edge lines)
    lake: np.ndarray
    lake_field: np.ndarray
    height: np.ndarray  # relief, ~0..1.3
    hills: np.ndarray  # organic hill-mass mask
    shade: np.ndarray  # hillshade, ~-1..1 (positive = lit)


def terrain_fields(scene: Scene) -> Fields:
    g, t = scene.derived.grid, scene.snap.terrain
    X, Y = scene.X, scene.Y
    tri = triangles(g, X, Y)
    wood_hex = g.smooth((t == WOODS).astype(np.float32), 1, keep=0.55)
    woods_field = tri.sample(g.pad(wood_hex, 0.0)) + 0.16 * fbm(X, Y, 1.1, 3, 11)
    woods = smoothstep(0.44, 0.52, woods_field)
    lake_hex = g.smooth((t == LAKE).astype(np.float32), 1, keep=0.6)
    lake_field = tri.sample(g.pad(lake_hex, 0.0)) + 0.07 * fbm(X, Y, 1.4, 2, 23)
    lake = smoothstep(0.47, 0.53, lake_field)
    hill_hex = g.smooth((t == HILLS).astype(np.float32), 2, keep=0.5)
    hill_s = tri.sample(g.pad(hill_hex, 0.0))
    bumps = 0.5 + 0.5 * fbm(X, Y, 0.55, 3, 37)
    base_hex = g.smooth(
        (t == WOODS) * 0.10
        + (t == TOWN) * 0.04
        + (t == CITY) * 0.04
        - (t == LAKE) * 0.12
        - (t == RIVER) * 0.08,
        2,
        keep=0.5,
    )
    height = 1.25 * hill_s * (0.55 + 0.75 * bumps) + tri.sample(g.pad(base_hex, 0.0))
    height = height + 0.05 * fbm(X, Y, 0.18, 3, 41)
    hills = smoothstep(0.30, 0.42, hill_s + 0.08 * fbm(X, Y, 1.2, 2, 43))
    # Hillshade from the world-space gradient, light from the north-west.
    gy, gx = np.gradient(height)
    gx, gy = gx * scene.R, gy * scene.R  # per world unit
    k = 1.6
    nx, ny, nz = -gx * k, -gy * k, np.ones_like(gx)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    lx, ly, lz = -0.55, -0.62, 0.56
    lit = (nx * lx + ny * ly + nz * lz) / norm
    shade = ((lit - lz) / (1.0 - lz)).astype(np.float32)
    return Fields(
        woods, woods_field, lake, lake_field, height.astype(np.float32), hills, shade
    )


def _visible_lines(scene: Scene, lines: tuple[np.ndarray, ...]) -> list[np.ndarray]:
    x0, y0, x1, y1 = scene.view.world_bounds(2.0)
    out = []
    for pts in lines:
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        if hi[0] >= x0 and lo[0] <= x1 and hi[1] >= y0 and lo[1] <= y1:
            out.append(pts)
    return out


def line_mask(scene: Scene, lines: list[np.ndarray], width_px: float) -> np.ndarray:
    ink = Ink(scene.view)
    for pts in lines:
        ink.polyline(pts, width_px)
    return ink.alpha()


def _shift(a: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """``a`` moved by (dx, dy) pixels, edges repeated."""
    h, w = a.shape
    ys = np.clip(np.arange(h) - dy, 0, h - 1)
    xs = np.clip(np.arange(w) - dx, 0, w - 1)
    return a[np.ix_(ys, xs)]


# ==============================================================================
# ownership: label image and boundary bands
# ==============================================================================


@dataclass(frozen=True)
class Territory:
    label: np.ndarray  # per pixel owner code (0 none)
    border: np.ndarray  # band alpha along any owned boundary, owner side only
    front: np.ndarray  # band alpha where two players' land meets (both sides)
    front_core: np.ndarray  # thin line on the front itself
    color: np.ndarray  # (H, W, 3) owner colour per pixel


def _band(edges: np.ndarray, half_width_px: float) -> np.ndarray:
    """Antialiased band of ``half_width_px`` around a 2-px-wide edge mask."""
    sigma = max(0.5, half_width_px / 1.5)
    b = blur(edges.astype(np.float32), sigma)
    peak = 2.0 / (sigma * math.sqrt(2.0 * math.pi))
    level = peak * math.exp(-(half_width_px**2) / (2.0 * sigma * sigma))
    return smoothstep(level * 0.6, level * 1.4, b)


def territory(
    scene: Scene, border_px: float, front_px: float, core_px: float, warp: float = 0.28
) -> Territory:
    g = scene.derived.grid
    X, Y = scene.X, scene.Y
    wx = X + warp * fbm(X, Y, 0.7, 2, 71)
    wy = Y + warp * fbm(X, Y, 0.7, 2, 72)
    owner = g.pad(scene.derived.owner.astype(np.int32), np.int32(0))
    label = triangles(g, wx, wy).smooth_label(owner, g.ring)
    right = _shift(label, -1, 0)
    down = _shift(label, 0, -1)
    diff_r, diff_d = right != label, down != label
    edge = diff_r | diff_d | _shift(diff_r, 1, 0) | _shift(diff_d, 0, 1)
    both_r = diff_r & (right > 0) & (label > 0)
    both_d = diff_d & (down > 0) & (label > 0)
    front_edge = both_r | both_d | _shift(both_r, 1, 0) | _shift(both_d, 0, 1)
    owned = label > 0
    border = _band(edge, border_px) * owned
    front = _band(front_edge, front_px) * owned
    core = _band(front_edge, core_px)
    lut = np.zeros((max(scene.colors) + 1, 3), dtype=np.float32)
    for code, c in scene.colors.items():
        lut[code] = rgb(c)
    return Territory(label, border, front, core, lut[label])


def wax(scene: Scene, seed: int) -> np.ndarray:
    """Grease-pencil grain: streaky screen-space noise in ~0.55..1."""
    h, w = scene.view.height, scene.view.width
    py, px = np.mgrid[0:h, 0:w].astype(np.float32)
    streak = value_noise(px * 0.9 + py * 0.35, py * 0.9 - px * 0.35, seed)
    slow = value_noise(scene.X * 0.35, scene.Y * 0.35, seed + 5)
    return (0.55 + 0.3 * streak + 0.15 * slow).astype(np.float32)


def hatch(scene: Scene, spacing_px: float, width_px: float, seed: int) -> np.ndarray:
    """Hand-drawn diagonal hatching alpha in screen space."""
    h, w = scene.view.height, scene.view.width
    py, px = np.mgrid[0:h, 0:w].astype(np.float32)
    wobble = 0.9 * fbm(px / 37.0, py / 37.0, 1.0, 2, seed)
    t = (px + py) / math.sqrt(2.0) + wobble * spacing_px * 0.35
    d = np.abs((t % spacing_px) - spacing_px / 2.0)
    line = np.clip(width_px / 2.0 + 0.5 - d, 0.0, 1.0)
    # Crayon strokes skip: a noise along each stroke, distinct per stroke.
    along = (px - py) / math.sqrt(2.0)
    stroke = np.floor(t / spacing_px)
    skip = value_noise(along / 9.0, stroke * 0.37, seed + 1)
    return (line * smoothstep(0.18, 0.4, skip)).astype(np.float32)


# ==============================================================================
# staff-map (and night)
# ==============================================================================


@dataclass(frozen=True)
class Paper:
    paper: int
    paper_shadow: int
    fold_dark: int
    fold_light: int
    relief_dark: int
    relief_light: int
    relief_strength: float
    contour: int
    wood_tint: int
    wood_tint_alpha: float
    wood_edge: int
    wood_mark: int
    water: int
    shore: int
    water_line: int
    river: int
    road: int
    road_case: int
    road_fill: int
    ink: int
    town_fill: int
    grid: int
    grid_alpha: float
    counter_face: int
    unheld: int
    wash_alpha: float
    hatch_alpha: float
    border_alpha: float
    front_core: int
    fight: int
    shadow_alpha: float
    lighten_owner: float  # mix owner colours toward paper for the crayon layer


DAY = Paper(
    paper=0xEFE6CF,
    paper_shadow=0xDDCFB0,
    fold_dark=0xB8A887,
    fold_light=0xFBF6EA,
    relief_dark=0x6E5236,
    relief_light=0xFFFBF0,
    relief_strength=0.42,
    contour=0xA4703F,
    wood_tint=0xC3D49F,
    wood_tint_alpha=0.85,
    wood_edge=0x6E8A4A,
    wood_mark=0x55743C,
    water=0xB4D0DE,
    shore=0x2C6189,
    water_line=0x6F9CBD,
    river=0x2E6A99,
    road=0x9D3B26,
    road_case=0x3B2A22,
    road_fill=0xE38B4C,
    ink=0x2B2118,
    town_fill=0x6B5A4A,
    grid=0x4F7DA6,
    grid_alpha=0.30,
    counter_face=0xF4ECD8,
    unheld=0x8C877C,
    wash_alpha=0.13,
    hatch_alpha=0.42,
    border_alpha=0.9,
    front_core=0x2A1712,
    fight=0xE0442A,
    shadow_alpha=0.35,
    lighten_owner=0.0,
)

NIGHT = Paper(
    paper=0x151D29,
    paper_shadow=0x0D131C,
    fold_dark=0x070A10,
    fold_light=0x253246,
    relief_dark=0x04070B,
    relief_light=0x2F3D52,
    relief_strength=0.55,
    contour=0x8F6B3E,
    wood_tint=0x1E3A2C,
    wood_tint_alpha=0.9,
    wood_edge=0x4E7E5A,
    wood_mark=0x6E9E76,
    water=0x123048,
    shore=0x6FB2DD,
    water_line=0x3D6E92,
    river=0x5FA6D6,
    road=0xD07A45,
    road_case=0x0B0D10,
    road_fill=0xE59A5C,
    ink=0xE2D8C0,
    town_fill=0xB3A68C,
    grid=0x5C84AE,
    grid_alpha=0.30,
    counter_face=0x1A2230,
    unheld=0x7D8592,
    wash_alpha=0.16,
    hatch_alpha=0.45,
    border_alpha=0.95,
    front_core=0xF4EBD6,
    fight=0xFF6A3D,
    shadow_alpha=0.5,
    lighten_owner=0.12,
)


def _paper(scene: Scene, p: Paper) -> np.ndarray:
    X, Y = scene.X, scene.Y
    h, w = scene.view.height, scene.view.width
    canvas = np.empty((h, w, 3), dtype=np.float32)
    canvas[:] = rgb(p.paper)
    mottle = 0.5 + 0.5 * fbm(X, Y, 0.035, 4, 3)
    paint(canvas, rgb(p.paper_shadow), 0.55 * smoothstep(0.35, 0.9, mottle))
    py, px = np.mgrid[0:h, 0:w].astype(np.float32)
    fibre = value_noise(px * 0.8 + py * 0.15, py * 0.2 - px * 0.05, 5) - 0.5
    speck = value_noise(px * 0.45, py * 0.45, 6) - 0.5
    canvas *= (1.0 + 0.035 * fibre + 0.03 * speck)[..., None]
    # Folds: the sheet is folded into panels 60 world units wide and tall.
    panel = 60.0
    for coord, alpha in ((X, 1.0), (Y, 1.0)):
        d = (coord % panel) / panel
        dist = np.minimum(d, 1.0 - d) * panel * scene.R  # px to the fold
        crease = np.clip(1.2 - dist, 0.0, 1.0)
        side = np.clip(1.0 - np.abs(dist - 2.0) / 2.0, 0.0, 1.0) * (d < 0.5)
        paint(canvas, rgb(p.fold_dark), 0.22 * alpha * crease)
        paint(canvas, rgb(p.fold_light), 0.25 * alpha * side)
        # A faint panel-wide sheen difference, as a folded sheet catches light.
        canvas *= (1.0 + 0.012 * np.sin(2 * math.pi * coord / panel))[..., None]
    return canvas


def _printed_terrain(scene: Scene, p: Paper, f: Fields, canvas: np.ndarray) -> None:
    R = scene.R
    # Relief: hillshade in the hills only (plains stay clean paper).
    relief = np.clip(f.hills * 1.2 + 0.25 * np.clip(f.height, 0, 1), 0, 1)
    strength = p.relief_strength * scene.z(0.55, 0.9, 1.0)
    dark = np.clip(-f.shade, 0, 1) * relief * strength
    light = np.clip(f.shade, 0, 1) * relief * strength * 0.8
    paint(canvas, rgb(p.relief_dark), dark)
    paint(canvas, rgb(p.relief_light), light)
    # Brown hill tint, like a printed layer colour.
    paint(
        canvas, rgb(mix(p.paper, p.contour, 0.35)), scene.z(0.3, 0.45, 0.45) * f.hills
    )
    step = scene.z(0.5, 0.28, 0.16)
    width = scene.z(0.55, 0.7, 0.9)
    lines = contour_lines(f.height, step, width) * smoothstep(0.12, 0.3, f.height)
    paint(canvas, rgb(p.contour), lines * scene.z(0.0, 0.75, 0.85))
    # Woods: flat tint, a soft edge line, then the tree pattern.
    paint(canvas, rgb(p.wood_tint), f.woods * p.wood_tint_alpha)
    edge = iso_line(f.woods_field, 0.48, scene.z(0.5, 0.75, 1.0))
    paint(canvas, rgb(p.wood_edge), edge * scene.z(0.45, 0.7, 0.8))
    if R >= 3.5:
        paint(canvas, rgb(p.wood_mark), _tree_pattern(scene) * f.woods)
    # Lakes: fill, water-lining inside the shore, shore line.
    paint(canvas, rgb(p.water), f.lake)
    if R >= 4.0:
        for level, a in ((0.62, 0.55), (0.76, 0.35)):
            paint(
                canvas,
                rgb(p.water_line),
                iso_line(f.lake_field, level, 0.6) * a * f.lake,
            )
    paint(canvas, rgb(p.shore), iso_line(f.lake_field, 0.5, scene.z(0.7, 0.9, 1.2)))


def _tree_pattern(scene: Scene) -> np.ndarray:
    """Woods symbol: jittered dots (mid zoom) or small tree rings (near zoom)."""
    R = scene.R
    spacing = scene.z(3.4, 3.6, 6.5) / R  # world units between marks
    gx, gy = scene.X / spacing, scene.Y / spacing
    row = np.floor(gy)
    gx = gx + 0.5 * (row % 2)  # stagger rows
    cx, cy = np.floor(gx), row
    jx = hash01(cx, cy, 91) - 0.5
    jy = hash01(cx, cy, 92) - 0.5
    keep = hash01(cx, cy, 93) > 0.18
    dx = (gx - (cx + 0.5 + 0.35 * jx)) * spacing * R
    dy = (gy - (cy + 0.5 + 0.35 * jy)) * spacing * R
    d = np.sqrt(dx * dx + dy * dy)
    if R < 8.0:
        r = scene.z(0.55, 0.65, 0.9)
        return np.clip(r + 0.5 - d, 0.0, 1.0) * keep * 0.85
    # A small open ring with a short trunk tick: the deciduous-tree sign.
    ring = np.clip(0.45 + 0.5 - np.abs(d - 1.5), 0.0, 1.0)
    trunk = np.clip(0.4 + 0.5 - np.abs(dx), 0, 1) * ((dy > 1.4) & (dy < 2.8))
    return np.maximum(ring, trunk) * keep * 0.9


def _water_and_roads(scene: Scene, p: Paper, canvas: np.ndarray) -> None:
    d = scene.derived
    rivers = _visible_lines(scene, d.rivers)
    roads = _visible_lines(scene, d.roads)
    river_w = scene.z(1.0, 1.4, 2.0)
    paint(canvas, rgb(p.river), line_mask(scene, rivers, river_w))
    if scene.R >= 8.0:
        paint(canvas, rgb(p.road_case), line_mask(scene, roads, 2.9))
        paint(canvas, rgb(p.road_fill), line_mask(scene, roads, 1.5))
    else:
        paint(
            canvas,
            rgb(p.road),
            line_mask(scene, roads, scene.z(0.7, 1.05, 1.6)) * scene.z(0.7, 0.95, 0.95),
        )
    if scene.R >= 4.0:
        ink = Ink(scene.view)
        for x, y, angle in d.bridges:
            if not ink.visible(x, y):
                continue
            # Two short ticks either side of the road, across the river.
            ca, sa = math.cos(angle), math.sin(angle)
            half = scene.z(1.6, 2.2, 3.4)
            off = scene.z(1.2, 1.4, 2.2)
            for side in (-1, 1):
                ox, oy = -sa * off * side, ca * off * side
                ink.line_px(
                    x,
                    y,
                    [
                        (-ca * half + ox, -sa * half + oy),
                        (ca * half + ox, sa * half + oy),
                    ],
                    0.8,
                )
        paint(canvas, rgb(p.ink), ink.alpha())


def _settlements(scene: Scene, p: Paper, canvas: np.ndarray) -> None:
    """Towns as small building clusters, cities as hatched built-up blocks."""
    snap, g = scene.snap, scene.derived.grid
    R = scene.R
    ink = Ink(scene.view)
    built = Ink(scene.view)
    for i in scene.hexes_in_view():
        t = int(snap.terrain[i])
        if t not in (TOWN, CITY):
            continue
        x, y = float(g.cx[i]), float(g.cy[i])
        if t == CITY:
            n = 9
            pts = []
            for k in range(n):
                a = 2 * math.pi * k / n + hash01(np.array(i), np.array(k), 5) * 0.4
                rr = (0.62 + 0.3 * float(hash01(np.array(i), np.array(k), 6))) * 0.95
                pts.append((x + rr * math.cos(a), y + rr * math.sin(a) * 0.9))
            if R < 4.0:
                built.circle(x, y, max(1.6, 0.85 * R))
                ink.circle(x, y, max(1.6, 0.85 * R))
            else:
                built.polygon_world(np.array(pts))
                ink.polygon_world(np.array(pts), value=0)
                for k in range(5):
                    bx = x + (float(hash01(np.array(i), np.array(k), 7)) - 0.5) * 0.9
                    by = y + (float(hash01(np.array(i), np.array(k), 8)) - 0.5) * 0.9
                    s = max(0.9, 0.16 * R)
                    ink.polygon_px(bx, by, [(-s, -s), (s, -s), (s, s), (-s, s)])
        else:
            if R < 4.0:
                ink.polygon_px(
                    x, y, [(-0.75, -0.75), (0.75, -0.75), (0.75, 0.75), (-0.75, 0.75)]
                )
                continue
            for k in range(5):
                bx = x + (float(hash01(np.array(i), np.array(k), 9)) - 0.5) * 0.85
                by = y + (float(hash01(np.array(i), np.array(k), 10)) - 0.5) * 0.7
                s = max(
                    0.7, (0.11 + 0.06 * float(hash01(np.array(i), np.array(k), 11))) * R
                )
                a = float(hash01(np.array(i), np.array(k), 12)) * 0.6 - 0.3
                ca, sa = math.cos(a), math.sin(a)
                corners = [(-s, -0.7 * s), (s, -0.7 * s), (s, 0.7 * s), (-s, 0.7 * s)]
                ink.polygon_px(
                    bx,
                    by,
                    [
                        (cx_ * ca - cy_ * sa, cx_ * sa + cy_ * ca)
                        for cx_, cy_ in corners
                    ],
                )
    area = built.alpha()
    if area.any():
        hatch_a = hatch(scene, scene.z(2.0, 2.2, 2.6), 0.7, 17)
        paint(canvas, rgb(p.town_fill), area * (0.25 + 0.55 * hatch_a))
        outline = iso_line(blur(area, 0.6), 0.5, scene.z(0.6, 0.8, 1.0))
        paint(canvas, rgb(p.ink), outline * (R >= 4.0))
    paint(canvas, rgb(p.ink), ink.alpha())


def _grid(scene: Scene, p: Paper, canvas: np.ndarray) -> None:
    R = scene.R
    if scene.grid_mode == "none":
        return
    if scene.grid_mode == "hex":
        if R < 4.0:
            return
        g = scene.derived.grid
        idx = nearest_hex(g, scene.X, scene.Y)
        lx = (scene.X - g.pad(g.cx, 0.0)[idx]) * R
        ly = (scene.Y - g.pad(g.cy, 0.0)[idx]) * R
        apothem = SQRT3 / 2 * R
        # Distance to the nearest edge: flat-top edge normals at 90 and +-30 degrees.
        m = np.maximum.reduce(
            [
                np.abs(ly),
                np.abs(lx * SQRT3 / 2 + ly / 2),
                np.abs(lx * SQRT3 / 2 - ly / 2),
            ]
        )
        d = apothem - m
        paint(canvas, rgb(p.grid), np.clip(0.35 + 0.5 - d, 0, 1) * 0.35)
        return
    # Kilometre-style squares: 8 world units, coarser lines kept when far out.
    base = 8.0
    every = 1 if R >= 8 else (2 if R >= 4 else 4)
    spacing = base * every
    for coord in (scene.X, scene.Y):
        d = np.abs(((coord / spacing) + 0.5) % 1.0 - 0.5) * spacing * R
        major = (
            np.round(coord / spacing).astype(np.int64) % (5 if every == 1 else 1)
        ) == 0
        width = np.where(major, 1.0, 0.6)
        paint(canvas, rgb(p.grid), np.clip(width / 2 + 0.5 - d, 0, 1) * p.grid_alpha)


def _grid_labels(scene: Scene, p: Paper, layer: Sprites) -> None:
    """Grid numbers along the top and left edges, as on a printed sheet."""
    if scene.grid_mode != "km" or scene.R < 4.0:
        return
    every = 1 if scene.R >= 8 else 2
    spacing = 8.0 * every
    f = marks.font(int(9 * layer.ss))
    x0, y0, x1, y1 = scene.view.world_bounds()
    color = marks.rgba(p.grid, 0.9)
    k = math.ceil(x0 / spacing)
    while k * spacing < x1:
        px, _ = layer.xy(k * spacing, y0)
        layer.draw.text(
            (px + 2 * layer.ss, 2 * layer.ss), f"{k % 100:02d}", font=f, fill=color
        )
        k += 1
    k = math.ceil(y0 / spacing)
    while k * spacing < y1:
        _, py = layer.xy(x0, k * spacing)
        layer.draw.text(
            (2 * layer.ss, py + 1 * layer.ss), f"{k % 100:02d}", font=f, fill=color
        )
        k += 1


def _site_symbols(scene: Scene, p: Paper, canvas: np.ndarray) -> None:
    if scene.R < 4.0:
        return
    g = scene.derived.grid
    ink = Ink(scene.view)
    size = scene.z(4.0, 4.6, 8.0)
    for site in scene.snap.sites:
        x, y = float(g.cx[site.hex]), float(g.cy[site.hex])
        if ink.visible(x, y):
            marks.site_symbol(ink, x, y, site.kind, size, site.level)
    paint(canvas, rgb(p.ink), ink.alpha())


def _owner_tones(scene: Scene, t: Territory, toward: int, amount: float) -> np.ndarray:
    if amount <= 0:
        return t.color
    return t.color + (rgb(toward) - t.color) * amount


def _grease(scene: Scene, p: Paper, canvas: np.ndarray, t: Territory) -> None:
    owned = (t.label > 0).astype(np.float32)
    grain = wax(scene, 77)
    tone = _owner_tones(scene, t, p.paper, p.lighten_owner)
    paint(canvas, tone, owned * p.wash_alpha)
    spacing = scene.z(3.2, 4.2, 6.0)
    ht = hatch(scene, spacing, scene.z(0.9, 1.1, 1.4), 31)
    paint(canvas, tone, owned * ht * grain * p.hatch_alpha)
    paint(canvas, tone, t.border * grain * p.border_alpha)
    paint(canvas, tone, t.front * np.clip(grain + 0.25, 0, 1))
    paint(canvas, rgb(p.front_core), t.front_core * np.clip(grain + 0.3, 0, 1))


def _markers(scene: Scene, p: Paper, layer: Sprites, style: str) -> None:
    """Pins and flags for held sites, grey marks for unheld ones and garrisons,
    unit counters (armies when far out) and fight bursts."""
    snap, d, g = scene.snap, scene.derived, scene.derived.grid
    R = scene.R
    near = R >= 8.0
    far = R < 4.0
    clean = style == "painted-far"
    for k, site in enumerate(snap.sites):
        holder = int(snap.holders[k])
        if holder == VACANT:
            continue
        x, y = float(g.cx[site.hex]), float(g.cy[site.hex])
        if not layer.inside(x, y):
            continue
        if holder == NOBODY:
            if far:
                continue
            if clean:
                marks.token(
                    layer,
                    x,
                    y,
                    scene.z(1.4, 1.8, 2.4),
                    0xB8B2A2,
                    0x2A2A2A,
                    outline_px=0.6,
                    alpha=0.85,
                )
            else:
                layer.draw.ellipse(
                    _ring_box(layer, x, y, scene.z(1.4, 1.9, 2.8)),
                    outline=marks.rgba(p.unheld, 0.9),
                    width=max(1, int(0.8 * layer.ss)),
                )
            garrison = d.garrisons.get(site.hex, 0)
            if garrison and not far:
                gx_, gy_ = (scene.z(3, 3.5, 6.0), scene.z(3, 3.5, 6.0))
                marks.garrison_mark(
                    layer,
                    x,
                    y,
                    scene.z(2.0, 2.2, 3.2),
                    garrison,
                    p.unheld,
                    p.ink,
                    gx_,
                    gy_,
                )
            continue
        color = scene.colors[holder]
        if clean:
            r = (5.0 if site.kind == "base" else 3.5) * scene.z(0.75, 1.0, 1.25)
            marks.token(
                layer,
                x,
                y,
                r,
                color,
                0x101114,
                ring=0xF4F0E6 if site.kind == "base" else None,
            )
        elif site.kind == "base":
            marks.flag(layer, x, y, scene.z(8.5, 10.0, 14.0), color, p.ink)
        else:
            head = scene.z(1.9, 2.7, 4.0) * (1.25 if site.kind == "city" else 1.0)
            off = (0.0, 0.0) if not near else (0.45 * R, -0.55 * R)
            marks.pin(
                layer, x, y, head, color, p.ink, offset=off, shadow_alpha=p.shadow_alpha
            )
    if far:
        for army in d.armies:
            if not layer.inside(army.x, army.y):
                continue
            w = min(9.0, 4.6 + 0.3 * army.count)
            marks.counter(
                layer,
                army.x,
                army.y,
                w,
                scene.colors[army.owner],
                p.ink,
                p.counter_face,
                army.lead,
                army.count,
                False,
                "block",
                rounded=clean,
            )
    else:
        w = scene.z(7.0, 9.0, 17.0)
        detail = "full" if near else "symbol"
        for st in d.stacks:
            x, y = float(g.cx[st.hex]), float(g.cy[st.hex])
            if not layer.inside(x, y):
                continue
            marks.counter(
                layer,
                x,
                y,
                w,
                scene.colors[st.owner],
                p.ink,
                p.counter_face,
                st.lead,
                st.count,
                st.column,
                detail,
                rounded=clean,
            )
    for hex_ in snap.fights:
        x, y = float(g.cx[hex_]), float(g.cy[hex_])
        if layer.inside(x, y):
            marks.burst(
                layer,
                x + 0.0,
                y - (0.55 * R if near else 0.0),
                scene.z(2.4, 3.2, 5.0),
                p.fight,
                p.ink,
            )


def _ring_box(
    layer: Sprites, x: float, y: float, r: float
) -> tuple[float, float, float, float]:
    px, py = layer.xy(x, y)
    rr = r * layer.ss
    return (px - rr, py - rr, px + rr, py + rr)


def _staff(scene: Scene, p: Paper) -> Image.Image:
    f = terrain_fields(scene)
    canvas = _paper(scene, p)
    _printed_terrain(scene, p, f, canvas)
    _water_and_roads(scene, p, canvas)
    _settlements(scene, p, canvas)
    _grid(scene, p, canvas)
    _site_symbols(scene, p, canvas)
    t = territory(
        scene, scene.z(1.0, 1.4, 2.0), scene.z(2.6, 3.4, 4.6), scene.z(0.9, 1.2, 1.6)
    )
    _grease(scene, p, canvas, t)
    layer = Sprites(scene.view)
    _grid_labels(scene, p, layer)
    _markers(scene, p, layer, "staff")
    layer.composite_onto(canvas)
    return to_image(canvas)


def render_staff(scene: Scene) -> Image.Image:
    return _staff(scene, DAY)


def render_staff_night(scene: Scene) -> Image.Image:
    return _staff(scene, NIGHT)


# ==============================================================================
# painted-far: the close-up tiles seen from high up
# ==============================================================================

# Feature colours sampled from frontend/src/board/tiles (alpha-weighted means of
# the brightest / darkest parts of each terrain's tiles, 2026-11-10).
PAINT = {
    "plains": 0x67682E,
    "plains_lo": 0x5A6127,
    "plains_hi": 0x78733A,
    "canopy": 0x343C1C,
    "canopy_dark": 0x22270F,
    "canopy_light": 0x4E5326,
    "hill": 0x857946,
    "hill_lit": 0xB4A272,
    "hill_dark": 0x4A4422,
    "water": 0x3F4E4B,
    "water_dark": 0x2B3633,
    "water_light": 0x5D6E68,
    "road": 0xA78E62,
    "road_edge": 0x5C4E33,
    "roof": 0x8C5A3C,
    "roof_light": 0xB08A60,
    "wall": 0x5A4636,
}


# The hybrid's colour-print palette: the painted feature colours lifted to the
# buff, green and ochre layer tints of a printed sheet.
PRINT = {
    "plains": 0xDCD5AA,
    "plains_lo": 0xD2CB9C,
    "plains_hi": 0xE6DFB8,
    "canopy": 0x9DB27A,
    "canopy_dark": 0x86A066,
    "canopy_light": 0xB2C48E,
    "hill": 0xCFB485,
    "hill_lit": 0xEDE2C4,
    "hill_dark": 0x9A7C52,
    "water": 0xA9C6D4,
    "water_dark": 0x4F7F9F,
    "water_light": 0xC3D8E2,
    "road": 0xA78E62,
    "road_edge": 0x5C4E33,
    "roof": 0x8C5A3C,
    "roof_light": 0xB08A60,
    "wall": 0x5A4636,
}


def blend_palette(a: dict[str, int], b: dict[str, int], t: float) -> dict[str, int]:
    """Each colour of ``a`` moved ``t`` of the way to ``b``'s."""
    return {k: mix(a[k], b[k], t) for k in a}


def _painted_base(
    scene: Scene, f: Fields, colors: dict[str, int] | None = None
) -> np.ndarray:
    C = PAINT if colors is None else colors
    X, Y = scene.X, scene.Y
    h, w = scene.view.height, scene.view.width
    canvas = np.empty((h, w, 3), dtype=np.float32)
    canvas[:] = rgb(C["plains"])
    n1 = fbm(X, Y, 0.12, 4, 201)
    n2 = fbm(X, Y, 0.9, 3, 202)
    paint(canvas, rgb(C["plains_hi"]), np.clip(n1, 0, 1) * 0.8)
    paint(canvas, rgb(C["plains_lo"]), np.clip(-n1, 0, 1) * 0.8)
    canvas *= (1.0 + 0.05 * n2)[..., None]
    # Hills: ochre ground with strong relief.
    paint(canvas, rgb(C["hill"]), f.hills * 0.8)
    relief = np.clip(f.hills * 1.3 + 0.3 * np.clip(f.height, 0, 1), 0, 1)
    paint(canvas, rgb(C["hill_lit"]), np.clip(f.shade, 0, 1) * relief * 0.75)
    paint(canvas, rgb(C["hill_dark"]), np.clip(-f.shade, 0, 1) * relief * 0.8)
    # Woods: canopy with clumps, a drop shadow to the south-east.
    shadow = _shift(
        f.woods, int(round(0.25 * scene.R)) + 1, int(round(0.25 * scene.R)) + 1
    )
    multiply(canvas, rgb(0x6A6A55), np.clip(shadow - f.woods, 0, 1) * 0.6)
    clumps = fbm(X, Y, 2.2, 3, 211)
    canopy = np.empty_like(canvas)
    canopy[:] = rgb(C["canopy"])
    paint(canopy, rgb(C["canopy_light"]), np.clip(clumps, 0, 1) * 0.9)
    paint(canopy, rgb(C["canopy_dark"]), np.clip(-clumps, 0, 1) * 0.9)
    lit = np.clip(f.shade, -1, 1)
    canopy *= (1.0 + 0.25 * lit)[..., None]
    paint(canvas, canopy, f.woods)
    rim = iso_line(f.woods_field, 0.47, scene.z(0.7, 1.0, 1.4))
    paint(canvas, rgb(C["canopy_dark"]), rim * 0.5)
    # Lakes
    water = np.empty_like(canvas)
    water[:] = rgb(C["water"])
    paint(water, rgb(C["water_light"]), np.clip(fbm(X, Y, 0.8, 2, 221), 0, 1) * 0.35)
    paint(canvas, water, f.lake)
    paint(
        canvas,
        rgb(C["water_dark"]),
        iso_line(f.lake_field, 0.5, scene.z(0.8, 1.1, 1.6)) * 0.8,
    )
    return canvas


def _painted_lines(scene: Scene, canvas: np.ndarray) -> None:
    d = scene.derived
    rivers = _visible_lines(scene, d.rivers)
    roads = _visible_lines(scene, d.roads)
    R = scene.R
    rw = max(1.1, 0.36 * R)
    paint(
        canvas,
        rgb(PAINT["water_dark"]),
        line_mask(scene, rivers, rw + max(0.6, 0.12 * R)),
    )
    paint(canvas, rgb(PAINT["water"]), line_mask(scene, rivers, rw))
    if R >= 6:
        paint(
            canvas, rgb(PAINT["water_light"]), line_mask(scene, rivers, 0.3 * rw) * 0.4
        )
    road_w = max(0.8, 0.2 * R)
    if R >= 6:
        paint(
            canvas, rgb(PAINT["road_edge"]), line_mask(scene, roads, road_w + 0.9) * 0.6
        )
    paint(
        canvas,
        rgb(PAINT["road"]),
        line_mask(scene, roads, road_w) * scene.z(0.8, 0.9, 1.0),
    )


def _painted_settlements(scene: Scene, canvas: np.ndarray) -> None:
    snap, g = scene.snap, scene.derived.grid
    R = scene.R
    roofs = Ink(scene.view)
    lights = Ink(scene.view)
    shadows = Ink(scene.view)
    paved = Ink(scene.view)
    for i in scene.hexes_in_view():
        t = int(snap.terrain[i])
        if t not in (TOWN, CITY):
            continue
        x, y = float(g.cx[i]), float(g.cy[i])
        n = 7 if t == TOWN else 14
        spread = 0.7 if t == TOWN else 1.0
        if t == CITY:
            paved.circle(x, y, 0.85 * R)
        for k in range(n):
            bx = x + (float(hash01(np.array(i), np.array(k), 31)) - 0.5) * spread * 1.5
            by = y + (float(hash01(np.array(i), np.array(k), 32)) - 0.5) * spread * 1.3
            s = max(
                0.7, (0.12 + 0.07 * float(hash01(np.array(i), np.array(k), 33))) * R
            )
            sq = [(-s, -0.75 * s), (s, -0.75 * s), (s, 0.75 * s), (-s, 0.75 * s)]
            shadows.polygon_px(
                bx, by, [(a + 0.35 * s + 0.4, b + 0.35 * s + 0.4) for a, b in sq]
            )
            roofs.polygon_px(bx, by, sq)
            lights.polygon_px(
                bx, by, [(-s, -0.75 * s), (s, -0.75 * s), (s, 0.0), (-s, 0.0)]
            )
    paint(canvas, rgb(PAINT["wall"]), blur(paved.alpha(), 0.15 * R) * 0.55)
    multiply(canvas, rgb(0x55513A), shadows.alpha() * 0.7)
    paint(canvas, rgb(PAINT["roof"]), roofs.alpha())
    paint(canvas, rgb(PAINT["roof_light"]), lights.alpha() * 0.55)


def _painted_ownership(scene: Scene, canvas: np.ndarray, t: Territory) -> None:
    owned = (t.label > 0).astype(np.float32)
    paint(canvas, t.color, owned * scene.z(0.34, 0.22, 0.15))
    bright = t.color + (1.0 - t.color) * 0.25
    paint(canvas, bright, t.border * 0.95)
    paint(canvas, t.color, t.front)
    paint(canvas, rgb(0x0E0F12), t.front_core * 0.95)


def render_painted(scene: Scene) -> Image.Image:
    f = terrain_fields(scene)
    canvas = _painted_base(scene, f)
    _painted_lines(scene, canvas)
    _painted_settlements(scene, canvas)
    t = territory(
        scene,
        scene.z(0.9, 1.1, 1.5),
        scene.z(2.2, 2.6, 3.2),
        scene.z(0.8, 0.9, 1.0),
        warp=0.12,
    )
    _painted_ownership(scene, canvas, t)
    layer = Sprites(scene.view)
    _markers(scene, DAY, layer, "painted-far")
    layer.composite_onto(canvas)
    return to_image(canvas)


# ==============================================================================
# hybrid
# ==============================================================================


HYBRID = replace(
    DAY,
    road=0x8C3B26,
    road_case=0x3B2A22,
    road_fill=0xC9A66B,  # the painted road colour, a touch lighter
    town_fill=0x5A4636,
    grid_alpha=0.22,
    wash_alpha=0.12,
)


def render_hybrid(scene: Scene) -> Image.Image:
    """A coloured print of the painted world: the painted-far ground, lightened
    toward paper (strongly far out, barely near the tile switch so the hand-off
    to tiles is a small step), under the staff map's ink, crayon and pins."""
    f = terrain_fields(scene)
    toward_paint = scene.z(0.0, 0.2, 0.72)
    canvas = _painted_base(scene, f, blend_palette(PRINT, PAINT, toward_paint))
    paper = _paper(scene, HYBRID)
    canvas *= paper / rgb(HYBRID.paper)  # paper grain and folds through the print
    step = scene.z(0.5, 0.28, 0.16)
    lines = contour_lines(f.height, step, scene.z(0.5, 0.65, 0.85)) * smoothstep(
        0.12, 0.3, f.height
    )
    paint(canvas, rgb(0x6B4E2E), lines * scene.z(0.0, 0.5, 0.55))
    paint(
        canvas,
        rgb(HYBRID.shore),
        iso_line(f.lake_field, 0.5, scene.z(0.7, 0.9, 1.2)) * 0.8,
    )
    _water_and_roads(scene, HYBRID, canvas)
    _settlements(scene, HYBRID, canvas)
    _grid(scene, HYBRID, canvas)
    _site_symbols(scene, HYBRID, canvas)
    t = territory(
        scene, scene.z(1.0, 1.4, 2.0), scene.z(2.6, 3.4, 4.6), scene.z(0.9, 1.2, 1.6)
    )
    _grease(scene, HYBRID, canvas, t)
    layer = Sprites(scene.view)
    _grid_labels(scene, HYBRID, layer)
    _markers(scene, HYBRID, layer, "staff")
    layer.composite_onto(canvas)
    return to_image(canvas)


RENDERERS: dict[str, Callable[[Scene], Image.Image]] = {
    "current": render_current,
    "staff-map": render_staff,
    "staff-map-night": render_staff_night,
    "painted-far": render_painted,
    "hybrid": render_hybrid,
}
