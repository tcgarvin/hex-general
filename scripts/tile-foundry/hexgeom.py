"""Shared flat-top hex geometry for the tile-foundry toolkit.

Mirrors `frontend/src/board/hex.ts` exactly so hex-masked tile art lines up
with the game's own rendering math. hex.ts is **flat-top** (flat edges top and
bottom, points east/west); a hex's pixel geometry is parameterised there by
`size`, the centre-to-corner radius (`HEX_SIZE = 32` in-game, giving 64px
tiles). This module follows the same `size` convention for the primitives
that mirror hex.ts one-for-one (`corner_points`, `axial_to_pixel`), and uses
`hex_width` (point-to-point width, i.e. `2 * size`) for the tile-foundry-level
helpers that operate on a square canvas, since that's the natural unit for a
square PNG tile.

    hex.ts function        here                      parameter
    ----------------        ----                      ---------
    hexCornerPoints          corner_points              size (radius)
    axialToPixel              axial_to_pixel            size (radius)
    AXIAL_DIRECTIONS          AXIAL_DIRECTIONS           n/a
    (n/a — tile-foundry only) hex_mask                  hex_width (= 2*size)
    (n/a — tile-foundry only) neighbor_pixel_offset      hex_width (= 2*size)
    (n/a — tile-foundry only) shared_edge                hex_width (= 2*size)
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

# Mirrors hex.ts's AXIAL_DIRECTIONS exactly, same order. Callers index into
# this list by direction_index, so the order is load-bearing.
AXIAL_DIRECTIONS: list[tuple[int, int]] = [
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, 0),
    (-1, 1),
    (0, 1),
]

_SQRT3 = math.sqrt(3)


def corner_points(cx: float, cy: float, size: float) -> list[tuple[float, float]]:
    """Six flat-top hex corners centred on `(cx, cy)`, matching hex.ts's `hexCornerPoints`.

    `size` is the centre-to-corner radius. Corner 0 is due east (angle 0),
    matching the flat-top orientation (points east/west, flat edges top/bottom).
    """
    points = []
    for i in range(6):
        angle = math.radians(60 * i)
        points.append((cx + size * math.cos(angle), cy + size * math.sin(angle)))
    return points


def axial_to_pixel(q: float, r: float, size: float) -> tuple[float, float]:
    """Flat-top axial `(q, r)` to pixel, matching hex.ts's `axialToPixel` exactly."""
    x = size * (1.5 * q)
    y = size * ((_SQRT3 / 2) * q + _SQRT3 * r)
    return (x, y)


def hex_mask(canvas_w: int, hex_width: float, supersample: int = 4) -> np.ndarray:
    """Antialiased alpha mask (uint8, `canvas_w x canvas_w`) of a hex centred in the canvas.

    `hex_width` is the point-to-point width (`2 *` the hex.ts `size` radius).
    Antialiasing is done by rasterizing the polygon at `supersample`x
    resolution with PIL (which has no antialiased polygon fill of its own)
    and box-downscaling back to `canvas_w`.
    """
    if hex_width > canvas_w:
        raise ValueError(f"hex_width {hex_width} exceeds canvas_w {canvas_w}")
    size = hex_width / 2
    big = canvas_w * supersample
    img = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(img)
    centre = canvas_w * supersample / 2
    draw.polygon(corner_points(centre, centre, size * supersample), fill=255)
    small = img.resize((canvas_w, canvas_w), Image.BOX)
    return np.array(small, dtype=np.uint8)


def neighbor_pixel_offset(direction_index: int, hex_width: float) -> tuple[float, float]:
    """Pixel offset of the neighbour hex's centre in axial direction `direction_index`.

    Matches hex.ts's `axialToPixel` applied to the direction vector (relative
    to the centre hex, whose own `axialToPixel` offset is the origin).
    """
    q, r = AXIAL_DIRECTIONS[direction_index]
    return axial_to_pixel(q, r, hex_width / 2)


def shared_edge(
    direction_index: int, hex_width: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Endpoints of the centre hex's edge shared with the neighbour in that direction.

    Endpoints are relative to the centre hex's own centre `(0, 0)`: the two
    corners of the centre hex nearest to the neighbour's centre.
    """
    size = hex_width / 2
    corners = corner_points(0.0, 0.0, size)
    nx, ny = neighbor_pixel_offset(direction_index, hex_width)

    def dist2(p: tuple[float, float]) -> float:
        return (p[0] - nx) ** 2 + (p[1] - ny) ** 2

    nearest = sorted(corners, key=dist2)[:2]
    return (nearest[0], nearest[1])
