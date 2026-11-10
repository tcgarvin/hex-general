#!/usr/bin/env python3
"""Build same-terrain join sprites: lens patches that bridge the grass moat.

Every terrain tile keeps a plain-ground rim (about 5-12 px at 128 px per hex) so
any tile can abut any other. Where two *same-terrain* hexes touch (woods-woods,
city-city) that rim leaves a grass moat between the features, so a 7-hex forest
reads as seven stamps. A join sprite sits centred on the shared edge, drawn over
both base tiles, and fills the moat with more of the feature so the pair merges.

Geometry (flat-top, edge numbering of frontend/src/board/riverTiles.ts): every
edge is shared by a hex A and its neighbour in direction 0 (SE), 1 (NE) or 2 (N),
so three edge kinds cover the grid: ``edge_se``, ``edge_ne`` and ``edge_n``. Each
is built natively from unrotated tile art (rotating or mirroring would flip the
light). The visible part is a lens: widest at the edge midpoint, tapering to
nothing before the two vertices, soft at its boundary, and clipped to the union
of the two hexes, so it never reaches into a third hex and leaves a natural waist.

Where *three* same-terrain hexes meet, the three lenses leave a small grass
star at the shared vertex. A vertex fill closes it: a soft disc centred on the
vertex, clipped to the three hexes. Every vertex is A's east corner (triangle A,
A+SE, A+NE: ``vert_e``) or A's north-east corner (triangle A, A+NE, A+N:
``vert_ne``).

Routes:
    patch   (deterministic) cut the lens from dense interior art of the terrain's
            own tiles, unrotated, feathered. Several variants per kind use
            different source tiles and offsets; offsets are ranked to avoid
            landmarks (a church, a square) so they are not visibly cloned.

Usage:
    python3 scripts/tile-foundry/joins.py \\
        --tiles art/codex/terrain/candidates/r1 \\
        --out art/codex/terrain/candidates/r1-joins --terrains woods city

Writes ``<out>/<terrain>/<kind>_<nn>.png``, ``<out>/joins.json`` (the
draw contract plus per-sprite provenance) and ``<out>/README.md``. Re-runnable
against any tile set with the same layout (e.g. ``--tiles .../r2``).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hexgeom import AXIAL_DIRECTIONS, axial_to_pixel, corner_points  # noqa: E402

HEX_WIDTH = 128  # native px per hex, point to point (same as the tiles)
HEX_SIZE = HEX_WIDTH / 2
APOTHEM = HEX_SIZE * math.sqrt(3) / 2  # centre to edge midpoint
CANVAS = 64  # join sprite canvas, square; anchor = canvas centre = edge midpoint
EDGE_HALF_LENGTH = HEX_SIZE / 2  # midpoint to vertex along the edge

# Orientation name -> AXIAL_DIRECTIONS index of B as seen from A.
ORIENTATIONS: dict[str, int] = {"se": 0, "ne": 1, "n": 2}
# Vertex kind -> i, for the triangle A, A + d_i, A + d_(i+1); the vertex is A's
# corner at screen angle 0 deg (i = 0) or 300 deg (i = 1).
VERTICES: dict[str, int] = {"e": 0, "ne": 1}
VERTEX_ANGLES = {0: 0.0, 1: 300.0}

# Candidate interior offsets (tile px from the tile centre) for the patch route:
# within this radius the lens stays inside every tile's feature footprint.
OFFSET_RADIUS = 15
OFFSET_STEP = 3
MIN_OFFSET_SPACING = 7  # distinct variants from one source stay this far apart


class JoinError(ValueError):
    """Bad builder input: missing tiles, unknown terrain, lens outside the pair."""


@dataclass(frozen=True)
class LensParams:
    """Lens size in native px: perpendicular half-width at the midpoint, half-length
    along the edge (< EDGE_HALF_LENGTH so it stops before the vertices), and the
    width of the soft alpha boundary."""

    half_width: float
    half_length: float
    feather: float


@dataclass(frozen=True)
class VertexParams:
    """Vertex fill: soft disc radius and boundary width, native px."""

    radius: float
    feather: float


DEFAULT_PARAMS: dict[str, LensParams] = {
    "woods": LensParams(half_width=19.0, half_length=27.0, feather=7.0),
    "city": LensParams(half_width=17.0, half_length=26.0, feather=5.0),
    "hills": LensParams(half_width=24.0, half_length=27.0, feather=9.0),
}
VERTEX_PARAMS: dict[str, VertexParams] = {
    "woods": VertexParams(radius=24.0, feather=9.0),
    "city": VertexParams(radius=22.0, feather=6.0),
    "hills": VertexParams(radius=24.0, feather=10.0),
}


def edge_frame(orientation: str) -> tuple[np.ndarray, np.ndarray]:
    """Unit vectors (normal A->B, along the edge) for an orientation, screen y-down."""
    dq, dr = AXIAL_DIRECTIONS[ORIENTATIONS[orientation]]
    bx, by = axial_to_pixel(dq, dr, HEX_SIZE)
    normal = np.array([bx, by]) / math.hypot(bx, by)
    along = np.array([-normal[1], normal[0]])
    return normal, along


def _pixel_grid(canvas: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixel-centre coordinates relative to the canvas centre (the anchor)."""
    ys, xs = np.mgrid[0:canvas, 0:canvas].astype(np.float64)
    return xs + 0.5 - canvas / 2, ys + 0.5 - canvas / 2


def lens_alpha(
    orientation: str, params: LensParams, canvas: int = CANVAS
) -> np.ndarray:
    """Soft lens alpha in [0, 1], centred on the canvas (= the edge midpoint).

    Half-width profile ``w(t) = half_width * (1 - (t / half_length)^2)`` along the
    edge: widest at the midpoint, zero at ``|t| = half_length``. Alpha ramps over
    ``feather`` px inside that boundary with a smoothstep.
    """
    if not 0 < params.half_length < EDGE_HALF_LENGTH:
        raise JoinError(
            f"half_length {params.half_length} must be in (0, {EDGE_HALF_LENGTH}) "
            "so the lens stops before the edge's vertices"
        )
    normal, along = edge_frame(orientation)
    xs, ys = _pixel_grid(canvas)
    t = xs * along[0] + ys * along[1]
    p = xs * normal[0] + ys * normal[1]
    width = params.half_width * np.clip(1 - (t / params.half_length) ** 2, 0, None)
    ramp = np.clip((width - np.abs(p)) / params.feather, 0, 1)
    alpha: np.ndarray = ramp * ramp * (3 - 2 * ramp)
    return alpha


def _union_mask(
    centres: list[tuple[float, float]], canvas: int, supersample: int = 4
) -> np.ndarray:
    """Antialiased coverage of the union of hexes (centres relative to the canvas
    centre), in [0, 1]."""
    big = canvas * supersample
    img = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(img)
    for dx, dy in centres:
        cx = (canvas / 2 + dx) * supersample
        cy = (canvas / 2 + dy) * supersample
        draw.polygon(corner_points(cx, cy, HEX_SIZE * supersample), fill=255)
    small = img.resize((canvas, canvas), Image.Resampling.BOX)
    return np.asarray(small, dtype=np.float64) / 255


def pair_centres(orientation: str) -> list[tuple[float, float]]:
    """Centres of A and B relative to their shared edge midpoint."""
    normal, _ = edge_frame(orientation)
    return [
        (sign * normal[0] * APOTHEM, sign * normal[1] * APOTHEM) for sign in (-1, 1)
    ]


def pair_union_mask(orientation: str, canvas: int = CANVAS) -> np.ndarray:
    """Coverage of hex A union hex B in an edge-anchored canvas."""
    return _union_mask(pair_centres(orientation), canvas)


def triangle_centres(vertex: str) -> list[tuple[float, float]]:
    """Centres of A, A + d_i, A + d_(i+1) relative to their shared vertex."""
    i = VERTICES[vertex]
    angle = math.radians(VERTEX_ANGLES[i])
    vx, vy = HEX_SIZE * math.cos(angle), HEX_SIZE * math.sin(angle)
    centres = [(-vx, -vy)]
    for d in (i, i + 1):
        dq, dr = AXIAL_DIRECTIONS[d]
        bx, by = axial_to_pixel(dq, dr, HEX_SIZE)
        centres.append((bx - vx, by - vy))
    return centres


def vertex_alpha(vertex: str, params: VertexParams, canvas: int = CANVAS) -> np.ndarray:
    """Soft disc on the shared vertex, clipped to the three hexes around it."""
    if not 0 < params.radius < HEX_SIZE / 2:
        raise JoinError(f"vertex radius {params.radius} must be in (0, {HEX_SIZE / 2})")
    xs, ys = _pixel_grid(canvas)
    ramp = np.clip((params.radius - np.hypot(xs, ys)) / params.feather, 0, 1)
    disc = ramp * ramp * (3 - 2 * ramp)
    clipped: np.ndarray = disc * _union_mask(triangle_centres(vertex), canvas)
    return clipped


def join_alpha(
    orientation: str, params: LensParams, canvas: int = CANVAS
) -> np.ndarray:
    """The lens clipped to the two hexes: the alpha every join sprite carries."""
    return lens_alpha(orientation, params, canvas) * pair_union_mask(
        orientation, canvas
    )


def landmark_mask(tile: Image.Image) -> np.ndarray:
    """Large grey, low-saturation blobs (church roofs, squares) in a tile, in [0, 1].

    A morphological opening drops thin features (streets), so what is left are
    the landmarks whose clones would be obvious. Grass and canopy score zero.
    """
    rgb = np.asarray(tile.convert("RGB"), dtype=np.float64)
    high = rgb.max(axis=2)
    low = rgb.min(axis=2)
    saturation = np.where(high > 0, (high - low) / np.maximum(high, 1), 0)
    grey = ((saturation < 0.28) & (high > 55)).astype(np.uint8) * 255
    opened = (
        Image.fromarray(grey, "L")
        .filter(ImageFilter.MinFilter(5))
        .filter(ImageFilter.MaxFilter(5))
    )
    return np.asarray(opened, dtype=np.float64) / 255


def rank_offsets(tile: Image.Image, weight: np.ndarray) -> list[tuple[int, int]]:
    """Candidate window offsets, best first: least landmark under ``weight`` (the
    sprite alpha), then nearest the tile centre. Deterministic."""
    landmarks = np.pad(landmark_mask(tile), CANVAS // 2)
    scored: list[tuple[float, float, tuple[int, int]]] = []
    for oy in range(-OFFSET_RADIUS, OFFSET_RADIUS + 1, OFFSET_STEP):
        for ox in range(-OFFSET_RADIUS, OFFSET_RADIUS + 1, OFFSET_STEP):
            if math.hypot(ox, oy) > OFFSET_RADIUS:
                continue
            x0 = HEX_WIDTH // 2 + ox
            y0 = HEX_WIDTH // 2 + oy
            window = landmarks[y0 : y0 + CANVAS, x0 : x0 + CANVAS]
            score = float((window * weight).sum() / weight.sum())
            scored.append((round(score, 4), math.hypot(ox, oy), (ox, oy)))
    scored.sort()
    picked: list[tuple[int, int]] = []
    for _, _, offset in scored:
        if all(
            math.hypot(offset[0] - p[0], offset[1] - p[1]) >= MIN_OFFSET_SPACING
            for p in picked
        ):
            picked.append(offset)
    return picked


def cut_patch(
    tile: Image.Image, offset: tuple[int, int], canvas: int = CANVAS
) -> np.ndarray:
    """An unrotated ``canvas``-square window of tile art centred at centre+offset."""
    if tile.size != (HEX_WIDTH, HEX_WIDTH):
        raise JoinError(f"tiles must be {HEX_WIDTH}x{HEX_WIDTH}, got {tile.size}")
    arr = np.asarray(tile.convert("RGBA"), dtype=np.float64)
    x0 = HEX_WIDTH // 2 + offset[0] - canvas // 2
    y0 = HEX_WIDTH // 2 + offset[1] - canvas // 2
    if x0 < 0 or y0 < 0 or x0 + canvas > HEX_WIDTH or y0 + canvas > HEX_WIDTH:
        raise JoinError(f"offset {offset} puts the window outside the tile")
    return arr[y0 : y0 + canvas, x0 : x0 + canvas]


@dataclass(frozen=True)
class JoinRecord:
    """Provenance for one written sprite."""

    file: str
    route: str
    source: str
    offset: tuple[int, int]


def join_kinds(terrain: str) -> dict[str, np.ndarray]:
    """Every join kind's clipped alpha for a terrain: three edges, two vertices."""
    kinds = {f"edge_{o}": join_alpha(o, DEFAULT_PARAMS[terrain]) for o in ORIENTATIONS}
    kinds.update(
        {f"vert_{v}": vertex_alpha(v, VERTEX_PARAMS[terrain]) for v in VERTICES}
    )
    return kinds


def build_patch_joins(
    tiles_dir: Path, terrain: str, out_dir: Path, variants: int
) -> list[JoinRecord]:
    """Deterministic route: unrotated patches of the terrain's own interior art."""
    sources = sorted((tiles_dir / terrain).glob("[0-9][0-9].png"))
    if not sources:
        raise JoinError(f"no {terrain}/NN.png tiles under {tiles_dir}")
    tiles = []
    for source in sources:
        with Image.open(source) as im:
            tiles.append(im.convert("RGBA"))
    records: list[JoinRecord] = []
    (out_dir / terrain).mkdir(parents=True, exist_ok=True)
    for kind_index, (kind, alpha) in enumerate(join_kinds(terrain).items()):
        for k in range(variants):
            # Walk sources so kinds and variants do not share a window.
            s_index = (k + kind_index) % len(tiles)
            ranked = rank_offsets(tiles[s_index], alpha)
            offset = ranked[(k // len(tiles) + kind_index) % min(3, len(ranked))]
            patch = cut_patch(tiles[s_index], offset)
            rgba = patch.copy()
            rgba[..., 3] = patch[..., 3] * alpha
            name = f"{kind}_{k:02d}.png"
            Image.fromarray(np.round(rgba).astype(np.uint8), "RGBA").save(
                out_dir / terrain / name
            )
            records.append(
                JoinRecord(f"{terrain}/{name}", "patch", str(sources[s_index]), offset)
            )
    return records


README = """# Same-terrain join sprites

Generated by `scripts/tile-foundry/joins.py` from `{tiles}`; do not hand-edit,
re-run the builder (`--tiles <set> --out <dir>`).

Joins bridge the grass moat that every tile's plain-ground rim leaves between
two adjacent hexes of the same terrain, so a forest or city reads as one mass.

- **Files:** `<terrain>/<kind>_<nn>.png`, RGBA, {canvas}x{canvas} px at the tiles'
  native scale ({hex_width} px per hex, point to point). Draw at
  `hex_width_on_screen / {hex_width}`, like the tiles. Never mirror or rotate a
  join: the light is painted in. Files of a kind are ordered like tile variants
  (`localeCompare`), indexed by the hash below.
- **Edge kinds** `edge_se`, `edge_ne`, `edge_n`: the edge between hex A and its
  neighbour B = A + `AXIAL_DIRECTIONS[dir]`, dir = 0, 1, 2 (frontend/src/board/
  hex.ts; edge numbering as riverTiles.ts). Every edge is exactly one (A, dir).
  Draw when A and B have the same terrain. **Anchor:** canvas centre on the edge
  midpoint, the mean of the two `axialToPixel` centres. Shape: a lens, widest at
  the midpoint, gone before both vertices, soft edge, clipped to A union B.
- **Vertex kinds** `vert_e`, `vert_ne`: the vertex shared by A, A + d_i and
  A + d_(i+1), i = 0 (A's east corner, screen angle 0) or i = 1 (A's north-east
  corner, screen angle 300 deg). Draw when all three have the same terrain; it
  closes the small grass star the three edge lenses leave. **Anchor:** canvas
  centre on that corner, `centre(A) + size * (cos a, sin a)`. Shape: soft disc,
  clipped to the three hexes.
- **Variant hash:** `joinVariantIndex(A.q, A.r, code, count)` with code = dir
  (0-2) for edges and 3 + i for vertices: `variantIndex` with a third term,
  `h = q * 0x1f1f1f1f + r * 0x85ebca6b + code * 0x27d4eb2f` (JS doubles), then
  `h ^= h >>> 15; h = Math.imul(h, 0x2c1b3c6d); h ^= h >>> 12; abs(h) % count`.
- **Draw order:** after every base tile sprite and before the grid and every
  other overlay (roads, units): all edge joins sorted by (A.r, A.q, dir), then
  all vertex fills sorted by (A.r, A.q, i).

Terrains: {terrains}. Per-sprite provenance (route, source tile, offset) is in
`joins.json`.
"""


def write_manifest(out_dir: Path, tiles_dir: Path, records: list[JoinRecord]) -> None:
    """``joins.json`` (draw contract + provenance) and the README."""
    terrains = sorted({r.file.split("/")[0] for r in records})
    manifest = {
        "hex_width": HEX_WIDTH,
        "canvas": CANVAS,
        "anchor": "canvas centre = shared edge midpoint (edge_*) or vertex (vert_*)",
        "edge_kinds": {f"edge_{o}": d for o, d in ORIENTATIONS.items()},
        "vertex_kinds": {f"vert_{v}": i for v, i in VERTICES.items()},
        "draw_order": "after base tiles: edges by (r, q, dir), then vertices by "
        "(r, q, i); before grid and overlays",
        "tiles": str(tiles_dir),
        "params": {
            t: {
                "edge": asdict(DEFAULT_PARAMS[t]),
                "vertex": asdict(VERTEX_PARAMS[t]),
            }
            for t in terrains
        },
        "sprites": [asdict(r) for r in records],
    }
    (out_dir / "joins.json").write_text(json.dumps(manifest, indent=1) + "\n")
    (out_dir / "README.md").write_text(
        README.format(
            tiles=tiles_dir,
            canvas=CANVAS,
            hex_width=HEX_WIDTH,
            terrains=", ".join(terrains),
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tiles", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--terrains", nargs="+", default=["woods", "city"], choices=DEFAULT_PARAMS
    )
    parser.add_argument("--variants", type=int, default=3)
    args = parser.parse_args(argv)
    if args.variants < 1:
        parser.error("--variants must be >= 1")
    if not args.tiles.is_dir():
        raise JoinError(f"tiles directory not found: {args.tiles}")

    records: list[JoinRecord] = []
    for terrain in args.terrains:
        records += build_patch_joins(args.tiles, terrain, args.out, args.variants)
    write_manifest(args.out, args.tiles, records)
    print(f"{len(records)} join sprites -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
