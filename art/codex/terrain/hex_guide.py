"""Render a flat-top hex layout guide for guided Codex key-art (experiment P2).

Writes refs/hex_guide.png (flat code colours per terrain, river band, road
line, thin dark hex outlines) and refs/hex_guide.json (canvas size, hex size,
every hex's centre/corners/terrain, river and road paths as edge-midpoint
polylines) so a generated image can be registered against the layout.

Also exposes `load_layout()` and `draw_outlines()` for the registration step
(register.py).

Geometry comes from scripts/tile-foundry/hexgeom.py (mirrors the game's
hex.ts): flat-top, `size` = centre-to-corner radius. Layout is odd-q offset
(odd columns shifted down half a hex), converted to axial for hexgeom.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts" / "tile-foundry"))
import hexgeom  # noqa: E402

CANVAS = 1254  # Codex's square output size
HEX_WIDTH = 256  # point to point
SIZE = HEX_WIDTH / 2
COLS, ROWS = 6, 5
RIVER_WIDTH = 30
ROAD_WIDTH = 8
OUTLINE_WIDTH = 3

CODE_COLOURS: dict[str, tuple[int, int, int]] = {
    "grass": (150, 200, 90),
    "forest": (20, 90, 40),
    "hills": (170, 120, 60),
    "town": (235, 140, 40),
    "city": (200, 40, 50),
    "river": (40, 110, 230),
    "road": (245, 225, 80),
    "outline": (30, 30, 30),
}

# Edge directions of a flat-top hex, as screen angles of the edge midpoint.
EDGE_ANGLE_DEG: dict[str, float] = {
    "N": -90.0,
    "NE": -30.0,
    "SE": 30.0,
    "S": 90.0,
    "SW": 150.0,
    "NW": 210.0,
}

TERRAIN: dict[tuple[int, int], str] = {
    (0, 0): "hills",
    (1, 0): "hills",
    (0, 1): "hills",
    (3, 0): "forest",
    (0, 2): "forest",
    (5, 3): "forest",
    (1, 4): "forest",
    (5, 4): "forest",
    (4, 1): "town",
    (0, 3): "city",
    (1, 3): "city",
}

# River: (col, row) -> list of (entry edge, exit edge) segments; the fork hex
# has two segments sharing the entry.
RIVER: list[tuple[tuple[int, int], list[tuple[str, str]]]] = [
    ((2, -1), [("N", "S")]),
    ((2, 0), [("N", "S")]),
    ((2, 1), [("N", "SE")]),
    ((3, 1), [("NW", "S")]),
    ((3, 2), [("N", "S"), ("N", "SW")]),
    ((3, 3), [("N", "S")]),
    ((3, 4), [("N", "S")]),
    ((2, 3), [("NE", "S")]),
    ((2, 4), [("N", "S")]),
    ((2, 5), [("N", "S")]),
    ((3, 5), [("N", "S")]),
]

ROAD: list[tuple[tuple[int, int], list[tuple[str, str]]]] = [
    ((1, 3), [("SW", "N")]),
    ((1, 2), [("S", "NE")]),
    ((2, 2), [("SW", "NE")]),
    ((3, 1), [("SW", "NE")]),
    ((4, 1), [("SW", "C"), ("C", "SE"), ("C", "S")]),
    ((5, 1), [("NW", "SE")]),
    ((6, 2), [("NW", "SE")]),
    ((4, 2), [("N", "S")]),
    ((4, 3), [("N", "S")]),
    ((4, 4), [("N", "S")]),
    ((4, 5), [("N", "S")]),
]

# Hexes drawn around the core COLS x ROWS grid so the whole canvas is hexed;
# these partial hexes are always grass.
EXTRA_COLS = range(-1, COLS + 1)
EXTRA_ROWS = range(-1, ROWS + 1)


def hex_centre(col: int, row: int) -> tuple[float, float]:
    """Pixel centre of an odd-q offset hex, with the grid centred on the canvas."""
    q = col
    r = row - (col - (col & 1)) // 2
    x, y = hexgeom.axial_to_pixel(q, r, SIZE)
    grid_w = (COLS - 1) * 1.5 * SIZE + HEX_WIDTH
    grid_h = (ROWS + 0.5) * math.sqrt(3) * SIZE
    x0 = (CANVAS - grid_w) / 2 + SIZE
    y0 = (CANVAS - grid_h) / 2 + math.sqrt(3) / 2 * SIZE
    return (x0 + x, y0 + y)


def edge_mid(cx: float, cy: float, edge: str) -> tuple[float, float]:
    """Midpoint of the named edge of the hex centred on (cx, cy); "C" is the centre."""
    if edge == "C":
        return (cx, cy)
    apothem = math.sqrt(3) / 2 * SIZE
    a = math.radians(EDGE_ANGLE_DEG[edge])
    return (cx + apothem * math.cos(a), cy + apothem * math.sin(a))


def bezier(
    p0: tuple[float, float], c: tuple[float, float], p1: tuple[float, float], n: int = 24
) -> list[tuple[float, float]]:
    """Quadratic Bezier from p0 to p1 with control point c, as n+1 points."""
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t**2 * p1[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t**2 * p1[1]
        pts.append((x, y))
    return pts


def path_polylines(
    spec: list[tuple[tuple[int, int], list[tuple[str, str]]]],
) -> list[list[tuple[float, float]]]:
    """One smooth polyline per (entry, exit) segment, bending through the hex centre."""
    lines = []
    for (col, row), segments in spec:
        cx, cy = hex_centre(col, row)
        for entry, exit_ in segments:
            lines.append(bezier(edge_mid(cx, cy, entry), (cx, cy), edge_mid(cx, cy, exit_)))
    return lines


def draw_polyline(draw: ImageDraw.ImageDraw, pts, colour, width: int) -> None:
    """Thick polyline with round joins/caps (PIL's line joints leave notches)."""
    draw.line(pts, fill=colour, width=width)
    r = width / 2
    for x, y in pts:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)


def draw_outlines(im: Image.Image, colour=(255, 0, 255), width: int = OUTLINE_WIDTH) -> None:
    """Draw every hex outline of the layout onto `im` (which must be CANVAS square)."""
    draw = ImageDraw.Draw(im)
    for col in EXTRA_COLS:
        for row in EXTRA_ROWS:
            corners = hexgeom.corner_points(*hex_centre(col, row), SIZE)
            draw.line(corners + [corners[0]], fill=colour, width=width)


def build() -> tuple[Image.Image, dict]:
    """Render the guide image and its layout description."""
    im = Image.new("RGB", (CANVAS, CANVAS), CODE_COLOURS["grass"])
    draw = ImageDraw.Draw(im)
    hexes = []
    for col in EXTRA_COLS:
        for row in EXTRA_ROWS:
            terrain = TERRAIN.get((col, row), "grass")
            cx, cy = hex_centre(col, row)
            corners = hexgeom.corner_points(cx, cy, SIZE)
            draw.polygon(corners, fill=CODE_COLOURS[terrain])
            hexes.append(
                {
                    "col": col,
                    "row": row,
                    "cx": round(cx, 2),
                    "cy": round(cy, 2),
                    "terrain": terrain,
                    "core": 0 <= col < COLS and 0 <= row < ROWS,
                    "corners": [[round(x, 2), round(y, 2)] for x, y in corners],
                }
            )
    river = path_polylines(RIVER)
    road = path_polylines(ROAD)
    for line in road:
        draw_polyline(draw, line, CODE_COLOURS["road"], ROAD_WIDTH)
    for line in river:
        draw_polyline(draw, line, CODE_COLOURS["river"], RIVER_WIDTH)
    draw_outlines(im, CODE_COLOURS["outline"])
    layout = {
        "canvas": [CANVAS, CANVAS],
        "orientation": "flat-top, odd-q offset (odd columns shifted down)",
        "hex_width": HEX_WIDTH,
        "size": SIZE,
        "cols": COLS,
        "rows": ROWS,
        "code_colours": CODE_COLOURS,
        "river_width": RIVER_WIDTH,
        "road_width": ROAD_WIDTH,
        "hexes": hexes,
        "river": [[[round(x, 2), round(y, 2)] for x, y in line] for line in river],
        "road": [[[round(x, 2), round(y, 2)] for x, y in line] for line in road],
    }
    return im, layout


def load_layout() -> dict:
    """Read refs/hex_guide.json."""
    return json.loads((HERE / "refs" / "hex_guide.json").read_text())


def main() -> None:
    im, layout = build()
    out = HERE / "refs"
    out.mkdir(exist_ok=True)
    im.save(out / "hex_guide.png")
    (out / "hex_guide.json").write_text(json.dumps(layout, indent=1))
    print(f"guide {im.size}, {len(layout['hexes'])} hexes")


if __name__ == "__main__":
    main()
