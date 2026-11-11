#!/usr/bin/env python3
"""Render an offline board mock-up of a hex map from an arbitrary tiles directory.

Terrain tile candidates have to be judged as an *assembled map at board scale*,
not one tile at a time: seams, river continuity, mirrored shapes and the rhythm
of cosmetic variants only show up in context. This renders a scenario (or an
ad-hoc ASCII map) with the same tile-selection rules the frontend uses, without
touching the live frontend or needing the dev stack, so a candidate set can be
previewed before and after.

The selection logic is a line-for-line port of the frontend (keep in step):

    frontend/src/board/tiles.ts       discovery, variant groups, variantIndex
    frontend/src/board/courseGraph.ts courseGraphEdges (local junction pruning), shapePenalty
    frontend/src/board/riverTiles.ts  connectedEdges, riverOutletDirection, riverCourseEdges,
                                      orientedRiverTile
    frontend/src/board/roadTiles.ts   roadConnectedEdges, roadCourseEdges, roadTileRefs
    frontend/src/board/hexTile.ts     per-hex texture + mirror choice
    frontend/src/board/hex.ts         flat-top axial -> pixel (via tile-foundry/hexgeom.py)
    frontend/src/world/WorldRenderer.ts  tile sprite sizing and fallback fill (the game's renderer;
                                      the base fill / grid / vector road passes below mirror the
                                      retired 2-player Board.tsx, removed 2026-11-10, see git history)

`scripts/tests/test_board_mock.py` ports the frontend's own test cases.

Usage:
    python3 scripts/board-mock.py --map api/app/scenarios/twin_rivers.json --out /tmp/tr.png
    python3 scripts/board-mock.py --tiles /tmp/candidates --fallback frontend/src/board/tiles \\
        --map scripts/board-mock-maps/showcase.txt --out /tmp/showcase.png --hex-px 128

Options:
    --tiles DIR      tiles root laid out like frontend/src/board/tiles (the default)
    --fallback DIR   second root consulted per (terrain, shape) group the first lacks
    --map FILE       scenario .json (API format) or ASCII map (any other extension)
    --hex-px N       on-screen point-to-point hex width: 64 = board scale, 128 = max zoom
    --grid/--no-grid draw the thin hex grid overlay (default on, as the retired 2-player Board.tsx did)
    --crop c0,r0,c1,r1  render only authored (offset) cols c0..c1, rows r0..r1
                     inclusive; tile selection still sees the whole map

ASCII map format: one terrain character per hex in the scenario alphabet
(``.`` plains, ``w`` woods, ``h`` hills, ``~`` river, ``=`` bridge, ``t`` town,
``c`` city, ``o`` lake), rows top to bottom, authored in the scenario's flat-top odd-q offset
grid. Optionally a ``[roads]`` section follows with a same-sized grid of ``#``
(road) / ``.`` (none); the terrain section may be headed ``[terrain]``. Lines
starting with ``;`` and blank lines are ignored.

Prints the tile file used for each (terrain, shape) group and lists every hex
that fell back to ``--fallback``, to a generic ``00`` tile, or to a vector road,
so missing art is obvious. Raises on malformed maps or tile sets.

Lakes (``o``) draw by **neighbour mask** when any tile set has mask-painted lake
tiles (``lake/m<MM>_<VV>.png``, round 2: ``lake_mask`` / ``lake_tile_key``, variants
hashed like terrain; a mask with no tile falls back, listed under ``LAKE MASKS
WITHOUT TILES``); otherwise they are cosmetic-variant terrain (``lake/NN.png``,
hashed like woods), or their flat base fill when no tile set has any. The
default ``--tiles`` (the frontend's) ship the round 2 mask set since 2026-11-10,
selected the same way by ``frontend/src/board/lakeTiles.ts``. With a mask set, lake edge/vertex joins are not drawn. They are not river-like, except for the **mouth rule** (``mouth_direction``):
a river or bridge hex that ends at a lake gains one edge toward it, so its
oriented tile runs water to that shore, and ``--joins`` draws a ``lake/mouth_*``
sprite on that edge (plus lake-lake ``edge_*``/``vert_*`` joins like woods).
The **edge rule** (``edge_direction``, below the mouth rule) does the same for a
river or bridge hex on the map border: its course runs off the map instead of
ending in a cap.

``--variety DIR --variety-density low|medium|high`` (+ ``--variety-medium DIR
--variety-mix small|mostly-small|even|mostly-medium|medium``) (opt-in, candidates only, not
in the frontend): decorates open country with plains feature, homestead and
roadside variants by a deterministic per-hex rule (``variety_picks``, written to
port to ``hexTile.ts``); off by default, so other renders are unchanged.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR / "tile-foundry"))

from hexgeom import (  # noqa: E402  (tile-foundry mirror of hex.ts)
    AXIAL_DIRECTIONS,
    axial_to_pixel,
    corner_points,
    hex_mask,
)

DEFAULT_TILES = REPO_ROOT / "frontend" / "src" / "board" / "tiles"

# --- map vocabulary (api/app/engine/scenario.py) ------------------------------

TERRAIN_CHARS: dict[str, str] = {
    ".": "plains",
    "w": "woods",
    "h": "hills",
    "~": "river",
    "=": "bridge",
    "t": "town",
    "c": "city",
    "o": "lake",
}
ROAD_CHAR = "#"
NO_ROAD_CHAR = "."
COSMETIC_TERRAINS = ("plains", "woods", "hills", "town", "city", "lake")
# Terrains that may have no art at all yet: their hexes draw only the flat base
# fill (TERRAIN_FILL) instead of raising. Lake art is still a candidate set.
FILL_ONLY_OK_TERRAINS = ("lake",)
# Opt-in site overlay (``[sites]`` map section + ``--sites <dir>``): economic site
# tiles drawn in place of a plains hex's tile, from ``<dir>/<kind>/l<level>_<nn>.png``.
SITE_KINDS = ("base", "farm", "oil_field", "steel_mill", "factory")
SITE_LEVELS = (1, 2, 3)
# A base grows by footprint instead: stage N covers N hexes, axial offsets from
# its anchor (founding) hex, nested so each stage contains the previous one:
# anchor, + SE (1,0), + S (0,1), + the S hex's SE (1,1). Tiles live in
# ``<dir>/base/stage<N>_<variant>/h<i>.png`` with a positions.json beside them.
BASE_STAGES: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((0, 0),),
    2: ((0, 0), (1, 0)),
    3: ((0, 0), (1, 0), (0, 1)),
    4: ((0, 0), (1, 0), (0, 1), (1, 1)),
}
RIVER_LIKE = ("river", "bridge")
LAKE = "lake"
LAKE_MASK_KEY = re.compile(r"^lake/m\d\d$")

# --- render constants (frontend/src/board/{hex,palette}.ts, frontend/src/world/WorldRenderer.ts;
# the overlay passes follow the retired Board.tsx) -----

HEX_SIZE = 32  # world centre-to-corner radius; tiles land at 2*HEX_SIZE world px
BACKGROUND = 0x0A0A0B
HEX_STROKE = 0x1B1B1F
GRID_WIDTH = 2.0  # world px
# --grid-style concept: the concept art's engraved line, measured on
# docs/concept-art/concept1.png (hexes ~133 px wide): a ~1.5 px dark olive core
# with a ~2 px khaki highlight either side. World px (x2 = screen px at 128).
CONCEPT_CORE = 0x42401E
CONCEPT_CORE_ALPHA = 0.9
CONCEPT_CORE_WIDTH = 0.75
CONCEPT_HIGHLIGHT = 0x9E965A
CONCEPT_HIGHLIGHT_ALPHA = 0.8
CONCEPT_HIGHLIGHT_WIDTH = 2.75
CONCEPT_FEATHER = 0.6
GRID_STYLES = ("dark", "concept")
# --drift: two octaves of value noise on lattices rotated off the hex axes, with
# spacings (in hex widths) that do not divide the grid's 1.5/sqrt(3) periods.
DRIFT_DEFAULT = 0.05
DRIFT_OCTAVES: tuple[tuple[float, float, float], ...] = (
    # (lattice spacing in hex widths, rotation radians, weight)
    (5.3, 0.41, 1.0),
    (2.45, 1.07, 0.3),
)
DRIFT_NORMALISE = 1.35  # maps typical octave sums onto roughly [-1, 1]
ROAD_COLOR = 0xA78E62
ROAD_WIDTH = 4.75  # world px
# Textures at or below this width are the legacy pixel-art generation: the
# frontend samples them nearest-neighbour, and a bridge in that generation has no
# baked road ramps (so the vector road is still drawn over it).
LEGACY_TILE_PX = 64
TERRAIN_FILL: dict[str, int] = {
    "plains": 0x68692E,
    "woods": 0x3A4420,  # feature colours (not tile means) keep terrains distinct
    "hills": 0x9C8A5E,
    "river": 0x41504C,
    "bridge": 0x877353,
    "town": 0x8F7150,
    "city": 0x5A4636,
    "lake": 0x41504C,  # the river fill: one water colour
}


class MapError(ValueError):
    """A map file that cannot be rendered (bad grid, unknown char, bad bridge)."""


class TileSetError(ValueError):
    """A tiles directory that cannot serve the map (missing dir, missing base art)."""


# ==============================================================================
# Map input
# ==============================================================================


@dataclass(frozen=True)
class Hex:
    """One map hex: authored offset cell, canonical axial coord, terrain, road."""

    col: int
    row: int
    q: int
    r: int
    terrain: str
    road: bool


@dataclass(frozen=True)
class GameMap:
    """A rectangular authored map converted to axial (the loader's convention)."""

    name: str
    width: int
    height: int
    hexes: tuple[Hex, ...]
    by_axial: dict[tuple[int, int], Hex] = field(compare=False)
    # axial (q, r) -> (site kind, level); only ASCII maps with a [sites] section
    sites: dict[tuple[int, int], tuple[str, int]] = field(
        default_factory=dict, compare=False
    )

    def hex_at(self, q: int, r: int) -> Hex | None:
        """The hex at axial ``(q, r)``; ``None`` means off the map."""
        return self.by_axial.get((q, r))

    def terrain_at(self, q: int, r: int) -> str | None:
        """Terrain at axial ``(q, r)``; ``None`` means off the map."""
        h = self.by_axial.get((q, r))
        return None if h is None else h.terrain


def offset_to_axial(col: int, row: int) -> tuple[int, int]:
    """Authored flat-top odd-q offset cell to axial (``hex_coords.offset_to_axial``)."""
    return col, row - (col - (col & 1)) // 2


def _validate_grid(grid: Sequence[str], width: int, height: int, what: str) -> None:
    if len(grid) != height:
        raise MapError(f"{what} grid has {len(grid)} rows, expected height={height}")
    for row, line in enumerate(grid):
        if len(line) != width:
            raise MapError(
                f"{what} grid row {row} has {len(line)} chars, expected width={width}"
            )


def build_map(
    name: str, terrain: Sequence[str], roads: Sequence[str] | None
) -> GameMap:
    """Decode terrain + optional road grids, validating like the scenario loader."""
    if not terrain:
        raise MapError("terrain grid is empty")
    height = len(terrain)
    width = len(terrain[0])
    _validate_grid(terrain, width, height, "terrain")
    if roads is not None:
        _validate_grid(roads, width, height, "roads")
    hexes: list[Hex] = []
    for row in range(height):
        for col in range(width):
            char = terrain[row][col]
            if char not in TERRAIN_CHARS:
                raise MapError(
                    f"unknown terrain char {char!r} at col={col}, row={row}; "
                    f"expected one of {''.join(TERRAIN_CHARS)!r}"
                )
            road = False
            if roads is not None:
                road_char = roads[row][col]
                if road_char not in (ROAD_CHAR, NO_ROAD_CHAR):
                    raise MapError(
                        f"unknown road char {road_char!r} at col={col}, row={row}; "
                        f"use {ROAD_CHAR!r} for road or {NO_ROAD_CHAR!r} for none"
                    )
                road = road_char == ROAD_CHAR
            q, r = offset_to_axial(col, row)
            hexes.append(Hex(col, row, q, r, TERRAIN_CHARS[char], road))
    game_map = GameMap(
        name, width, height, tuple(hexes), {(h.q, h.r): h for h in hexes}
    )
    _validate_river_bridges(game_map)
    return game_map


def _validate_river_bridges(game_map: GameMap) -> None:
    """The loader's SPEC §3.4 rules: no road on river; a bridge is a road on a
    river. A lake is impassable open water, so it carries no road either."""
    for h in game_map.hexes:
        where = f"col={h.col}, row={h.row}"
        if h.terrain == "river" and h.road:
            raise MapError(f"road runs over river at {where}; use a bridge ('=')")
        if h.terrain == LAKE and h.road:
            raise MapError(f"road runs over lake at {where}; lakes are impassable")
        if h.terrain == "bridge":
            if not h.road:
                raise MapError(f"bridge at {where} has no road; mark it '#' in roads")
            if not any(
                game_map.terrain_at(h.q + dq, h.r + dr) == "river"
                for dq, dr in AXIAL_DIRECTIONS
            ):
                raise MapError(f"bridge at {where} is not adjacent to any river hex")


def parse_scenario(data: object, name: str) -> GameMap:
    """A scenario dict in the API format (``api/app/scenarios/*.json``)."""
    if not isinstance(data, dict):
        raise MapError(f"{name}: scenario JSON must be an object")
    terrain = data.get("terrain")
    roads = data.get("roads")
    if not isinstance(terrain, list) or not all(isinstance(s, str) for s in terrain):
        raise MapError(f"{name}: 'terrain' must be a list of strings")
    if roads is not None and (
        not isinstance(roads, list) or not all(isinstance(s, str) for s in roads)
    ):
        raise MapError(f"{name}: 'roads' must be a list of strings")
    game_map = build_map(str(data.get("name", name)), terrain, roads or None)
    for key in ("width", "height"):
        declared = data.get(key)
        actual = game_map.width if key == "width" else game_map.height
        if declared is not None and declared != actual:
            raise MapError(f"{name}: declared {key}={declared} but grid is {actual}")
    return game_map


def parse_sites(lines: Sequence[str], game_map: GameMap) -> GameMap:
    """Attach a ``[sites]`` section: one ``<col>,<row> <kind> <level>`` per line.

    Sites stand in open country: only on plains hexes without a road (never on a
    settlement, river, bridge or road), one per hex. A ``base`` takes a stage
    1-4 instead of a level and covers ``BASE_STAGES[stage]`` from the given
    (anchor) cell.
    """
    sites: dict[tuple[int, int], tuple[str, int]] = {}
    occupied: set[tuple[int, int]] = set()
    for line in lines:
        parts = line.split()
        where = f"{game_map.name}: [sites] line {line!r}"
        if len(parts) != 3 or parts[0].count(",") != 1:
            raise MapError(f"{where}: expected '<col>,<row> <kind> <level>'")
        try:
            col, row = (int(v) for v in parts[0].split(","))
            level = int(parts[2])
        except ValueError as exc:
            raise MapError(f"{where}: col, row and level must be integers") from exc
        kind = parts[1]
        if kind not in SITE_KINDS:
            raise MapError(
                f"{where}: unknown site kind {kind!r}; use one of {SITE_KINDS}"
            )
        levels = tuple(BASE_STAGES) if kind == "base" else SITE_LEVELS
        if level not in levels:
            raise MapError(f"{where}: {kind} level must be one of {levels}")
        if not (0 <= col < game_map.width and 0 <= row < game_map.height):
            raise MapError(
                f"{where}: cell is off the {game_map.width}x{game_map.height} map"
            )
        anchor = offset_to_axial(col, row)
        footprint = BASE_STAGES[level] if kind == "base" else ((0, 0),)
        for dq, dr in footprint:
            h = game_map.hex_at(anchor[0] + dq, anchor[1] + dr)
            if h is None:
                raise MapError(f"{where}: footprint hex {(dq, dr)} is off the map")
            if h.terrain != "plains" or h.road:
                raise MapError(
                    f"{where}: a site needs plains hexes without a road, found "
                    f"{h.terrain}{' + road' if h.road else ''} at col={h.col}, row={h.row}"
                )
            if (h.q, h.r) in occupied:
                raise MapError(
                    f"{where}: overlaps another site at col={h.col}, row={h.row}"
                )
            occupied.add((h.q, h.r))
        sites[anchor] = (kind, level)
    return replace(game_map, sites=sites)


def parse_ascii(text: str, name: str) -> GameMap:
    """An ad-hoc ASCII map: terrain grid, optionally followed by a ``[roads]`` grid
    and an opt-in ``[sites]`` list (see ``parse_sites``)."""
    sections: dict[str, list[str]] = {"terrain": [], "roads": [], "sites": []}
    current = "terrain"
    seen_roads = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            header = line[1:-1].strip().lower()
            if header not in sections:
                raise MapError(f"{name}: unknown section [{header}]")
            current = header
            seen_roads = seen_roads or header == "roads"
            continue
        sections[current].append(line)
    game_map = build_map(
        name, sections["terrain"], sections["roads"] if seen_roads else None
    )
    if sections["sites"]:
        game_map = parse_sites(sections["sites"], game_map)
    return game_map


def load_map(path: Path) -> GameMap:
    """Load a scenario ``.json`` or an ASCII map file."""
    if not path.is_file():
        raise MapError(f"map file not found: {path}")
    text = path.read_text()
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MapError(f"{path}: invalid JSON: {exc}") from exc
        return parse_scenario(data, path.stem)
    return parse_ascii(text, path.stem)


# ==============================================================================
# Tile-selection logic (ported from the frontend)
# ==============================================================================

# tiles.ts sorts glob keys with String.localeCompare (ICU root collation). ICU
# orders punctuation before digits before letters, compares case only as a
# tie-break (lowercase first), and its punctuation order differs from ASCII (e.g.
# "straight_v_00.png" sorts *before* "straight_v.png"). That order fixes the
# variantIndex -> file mapping, so it is reproduced here rather than using sorted().
_ICU_PUNCTUATION = "_-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$"


def _collation_key(name: str) -> tuple[tuple[tuple[int, int], ...], tuple[int, ...]]:
    primary: list[tuple[int, int]] = []
    tertiary: list[int] = []
    for ch in name:
        if ch in _ICU_PUNCTUATION:
            primary.append((0, _ICU_PUNCTUATION.index(ch)))
        elif ch.isdigit():
            primary.append((1, int(ch)))
        elif ch.isalpha():
            primary.append((2, ord(ch.lower())))
        else:
            primary.append((3, ord(ch)))
        tertiary.append(1 if ch.isupper() else 0)
    return tuple(primary), tuple(tertiary)


def locale_sorted(names: Iterable[str]) -> list[str]:
    """Sort like JS ``a.localeCompare(b)`` for the filename alphabet tiles use."""
    return sorted(names, key=lambda n: (_collation_key(n), n))


def oriented_key(name: str) -> str:
    """``tiles.ts orientedKey``: strip a trailing ``_<digits>`` variant suffix."""
    head, sep, tail = name.rpartition("_")
    if sep and tail.isdigit() and tail.isascii():
        return head
    return name


def _to_uint32(x: float) -> int:
    return int(x) % (1 << 32)


def _to_int32(x: int) -> int:
    x %= 1 << 32
    return x - (1 << 32) if x >= 1 << 31 else x


def _imul(a: int, b: int) -> int:
    return _to_int32((a % (1 << 32)) * (b % (1 << 32)))


def variant_index(q: int, r: int, count: int) -> int:
    """``tiles.ts variantIndex``, reproducing JS double/int32 semantics exactly."""
    if count <= 1:
        return 0
    # JS evaluates the products and sum in IEEE doubles before any int conversion.
    h_float = float(_to_int32(q)) * 0x1F1F1F1F + float(_to_int32(r)) * 0x85EBCA6B
    h = _to_int32(_to_uint32(h_float) ^ (_to_uint32(h_float) >> 15))
    h = _imul(h, 0x2C1B3C6D)
    h = _to_int32(h ^ ((h % (1 << 32)) >> 12))
    return abs(h) % count


def join_variant_index(q: int, r: int, code: int, count: int) -> int:
    """Join-sprite variant: ``variant_index`` with a third hash term.

    ``code`` is the edge direction 0 (SE), 1 (NE), 2 (N) from hex ``(q, r)``, or
    3 + i for the vertex shared by ``(q, r)``, its d_i and d_(i+1) neighbours, or
    5 + d for a river mouth from river hex ``(q, r)`` toward its lake in
    direction d (0..5). The frontend port is ``joinVariantIndex`` in
    frontend/src/board/joins.ts (all codes 0..10).
    """
    if code not in range(len(JOIN_KINDS)):
        raise ValueError(f"join code must be 0..{len(JOIN_KINDS) - 1}, got {code}")
    if count <= 1:
        return 0
    h_float = (
        float(_to_int32(q)) * 0x1F1F1F1F
        + float(_to_int32(r)) * 0x85EBCA6B
        + float(code) * 0x27D4EB2F
    )
    h = _to_int32(_to_uint32(h_float) ^ (_to_uint32(h_float) >> 15))
    h = _imul(h, 0x2C1B3C6D)
    h = _to_int32(h ^ ((h % (1 << 32)) >> 12))
    return abs(h) % count


def canonical_edge(
    a: tuple[int, int], b: tuple[int, int]
) -> tuple[tuple[int, int], int]:
    """The (hex, direction 0..2) naming the edge between adjacent axial hexes."""
    for owner, other in ((a, b), (b, a)):
        delta = (other[0] - owner[0], other[1] - owner[1])
        for direction in (0, 1, 2):
            if AXIAL_DIRECTIONS[direction] == delta:
                return owner, direction
    raise ValueError(f"{a} and {b} are not adjacent")


TerrainProbe = Callable[[int, int], "str | None"]
Segment = tuple[tuple[float, float], tuple[float, float]]
HexProbe = Callable[[int, int], "Hex | None"]


def connected_edges(q: int, r: int, terrain_at: TerrainProbe) -> list[int]:
    """``riverTiles.ts connectedEdges``: ascending directions to river-like hexes."""
    return [
        i
        for i, (dq, dr) in enumerate(AXIAL_DIRECTIONS)
        if terrain_at(q + dq, r + dr) in RIVER_LIKE
    ]


def shape_penalty(edges: Sequence[int]) -> int:
    """``courseGraph.ts shapePenalty``: awkwardness of a connected-edge set."""
    n = len(edges)
    if n == 0:
        return 0
    if n == 1:
        return 3
    if n == 2:
        raw = edges[1] - edges[0]
        gap = min(raw, 6 - raw)
        if gap == 3:
            return 0
        if gap == 2:
            return 1
        return 4
    return [5, 9, 14, 20][n - 3]


# Most chords one junction cluster may have and still be searched exhaustively
# (2^k subsets); a bigger cluster is a blob and keeps its raw masks.
MAX_CHORD_CANDIDATES = 16


def _hex_key(q: int, r: int) -> str:
    return f"{q},{r}"


class _DisjointSets:
    """Union-find over ``0..size-1`` (``courseGraph.ts DisjointSets``)."""

    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, x: int) -> int:
        p = self.parent
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


@dataclass(frozen=True)
class _Chord:
    """A junction-triangle edge: id ``"keyA|keyB"`` (keyA < keyB), node ends."""

    id: str
    a: int
    b: int


def course_graph_edges(
    coords: Iterable[tuple[int, int]], carries: Callable[[int, int], bool]
) -> dict[str, list[int]]:
    """``courseGraph.ts courseGraphEdges``: per-hex connected edges of the course
    graph over the carrying hexes among ``coords``, junction chords pruned locally.

    Raw adjacency over-connects junctions (the hexes around a join are mutually
    adjacent: hex-grid triangles). Every edge on such a triangle is a chord.
    Chords are clustered (chords sharing a hex share a cluster); each cluster of
    at most ``MAX_CHORD_CANDIDATES`` chords is solved alone by exhaustive search
    over its chord subsets: lowest summed ``shape_penalty`` over the cluster's
    hexes, then fewest drops, then the first (lowest) mask over the chord ids
    sorted as strings. A subset is valid when every dropped chord's ends stay
    connected through B (the non-chord edges, map-wide) plus the cluster's kept
    chords. Keys are ``"q,r"``; values ascending directions. ``coords`` must hold
    every carrying neighbour of a carrying hex in it, else ``ValueError``.
    """
    keys: list[str] = []
    nodes: list[tuple[int, int]] = []
    index_of: dict[str, int] = {}
    for q, r in coords:
        if not carries(q, r):
            continue
        key = _hex_key(q, r)
        if key in index_of:
            continue
        index_of[key] = len(keys)
        keys.append(key)
        nodes.append((q, r))
    n = len(keys)

    neighbour = [[-1] * 6 for _ in range(n)]
    for i, (q, r) in enumerate(nodes):
        for d, (dq, dr) in enumerate(AXIAL_DIRECTIONS):
            j = index_of.get(_hex_key(q + dq, r + dr))
            if j is not None:
                neighbour[i][d] = j
            elif carries(q + dq, r + dr):
                raise ValueError(
                    f"course graph: carrying hex ({q + dq}, {r + dr}) next to {keys[i]} "
                    "is not among the coords"
                )

    def edge_number(a: int, b: int) -> int:
        return a * n + b if a < b else b * n + a

    chord_index: dict[int, int] = {}
    chords: list[_Chord] = []

    def add_chord(a: int, b: int) -> None:
        e = edge_number(a, b)
        if e in chord_index:
            return
        chord_index[e] = len(chords)
        if keys[a] < keys[b]:
            chords.append(_Chord(f"{keys[a]}|{keys[b]}", a, b))
        else:
            chords.append(_Chord(f"{keys[b]}|{keys[a]}", b, a))

    for a in range(n):
        for d in range(6):
            b = neighbour[a][d]
            c = neighbour[a][(d + 1) % 6]
            if b >= 0 and c >= 0:
                add_chord(a, b)
                add_chord(a, c)
                add_chord(b, c)

    dropped: set[int] = set()
    if chords:
        plain = _DisjointSets(n)  # B: components over the non-chord edges
        by_chord = _DisjointSets(n)  # clusters: hexes joined by chords
        for a in range(n):
            for b in neighbour[a]:
                if b > a and edge_number(a, b) not in chord_index:
                    plain.union(a, b)
        for ch in chords:
            by_chord.union(ch.a, ch.b)
        clusters: dict[int, list[_Chord]] = {}
        for ch in chords:
            clusters.setdefault(by_chord.find(ch.a), []).append(ch)
        for cluster in clusters.values():
            if len(cluster) > MAX_CHORD_CANDIDATES:
                continue
            cluster.sort(key=lambda ch: ch.id)
            for ch in _solve_cluster(cluster, neighbour, plain, edge_number):
                dropped.add(edge_number(ch.a, ch.b))

    return {
        keys[a]: [
            d
            for d in range(6)
            if neighbour[a][d] >= 0 and edge_number(a, neighbour[a][d]) not in dropped
        ]
        for a in range(n)
    }


def _solve_cluster(
    cluster: Sequence[_Chord],
    neighbour: Sequence[Sequence[int]],
    plain: _DisjointSets,
    edge_number: Callable[[int, int], int],
) -> list[_Chord]:
    """``courseGraph.ts solveCluster``: the chords of one cluster to drop."""
    local = {edge_number(ch.a, ch.b): bit for bit, ch in enumerate(cluster)}
    hexes = list(dict.fromkeys(h for ch in cluster for h in (ch.a, ch.b)))
    hex_dirs: list[list[tuple[int, int]]] = []  # per hex: (direction, chord bit or -1)
    for h in hexes:
        hex_dirs.append(
            [
                (d, local.get(edge_number(h, nb), -1))
                for d, nb in enumerate(neighbour[h])
                if nb >= 0
            ]
        )

    labels: dict[int, int] = {}

    def label(h: int) -> int:
        return labels.setdefault(plain.find(h), len(labels))

    end_a = [label(ch.a) for ch in cluster]
    end_b = [label(ch.b) for ch in cluster]

    def connected_without(mask: int) -> bool:
        sets = _DisjointSets(len(labels))
        for j in range(len(cluster)):
            if not mask & (1 << j):
                sets.union(end_a[j], end_b[j])
        return all(
            sets.find(end_a[j]) == sets.find(end_b[j])
            for j in range(len(cluster))
            if mask & (1 << j)
        )

    best_mask, best_score, best_size = 0, math.inf, math.inf
    for mask in range(1 << len(cluster)):
        score = sum(
            shape_penalty([d for d, bit in dirs if bit < 0 or not mask & (1 << bit)])
            for dirs in hex_dirs
        )
        size = bin(mask).count("1")
        # lower score, then fewer drops; masks ascend, so the first such wins
        if score > best_score or (score == best_score and size >= best_size):
            continue
        if not connected_without(mask):
            continue
        best_mask, best_score, best_size = mask, score, size
    return [ch for j, ch in enumerate(cluster) if best_mask & (1 << j)]


def calm_outlet(river_dirs: Sequence[int], options: Sequence[int]) -> int | None:
    """The one outlet direction a river hex at the end of its course runs to.

    Shared by the mouth rule (``options`` = lake directions) and the edge rule
    (``options`` = off-map directions). ``river_dirs`` are the hex's directions
    (0..5) to river-like neighbours. Two or more means mid-course: ``None``. No
    options: ``None``. No river-like neighbour: the lowest option. One, in
    direction ``p``: straight (opposite ``p``) beats a gentle turn (two steps
    from ``p``); a sharp turn (next to ``p``) is never taken, so sharp-only gives
    ``None``; ties go to the lowest direction.
    """
    ranked = sorted(options)
    if len(river_dirs) > 1 or not ranked:
        return None
    if not river_dirs:
        return ranked[0]
    p = river_dirs[0]
    best: tuple[int, int] | None = None  # (rank: 0 straight, 1 gentle; direction)
    for d in ranked:
        gap = min((d - p) % 6, (p - d) % 6)
        if gap == 1:
            continue  # a sharp turn
        rank = 0 if gap == 3 else 1
        if best is None or rank < best[0]:
            best = (rank, d)
    return None if best is None else best[1]


def mouth_direction(river_dirs: Sequence[int], lake_dirs: Sequence[int]) -> int | None:
    """The mouth rule: which lake neighbour a river hex's course runs into.

    ``river_dirs`` are the hex's directions (0..5, ``AXIAL_DIRECTIONS``) to
    river-like (river/bridge) neighbours, ``lake_dirs`` its directions to lake
    neighbours. A hex with two or more river-like neighbours is mid-course and
    gets no mouth; neither does one with no lake neighbour. Otherwise:

    - no river-like neighbour (a lone river hex on the shore): the lowest lake
      direction;
    - one river-like neighbour in direction ``p``: the lake direction ``d`` that
      gives the calmest course ``{p, d}``: straight (``d`` opposite ``p``) beats a
      gentle turn (``d`` two steps from ``p``); a sharp turn (``d`` next to ``p``)
      is never taken, so if only sharp options exist there is no mouth. Ties go
      to the lowest direction.

    Returns the chosen direction, or ``None`` for "no mouth".
    """
    return calm_outlet(river_dirs, lake_dirs)


def edge_direction(
    river_dirs: Sequence[int], off_map_dirs: Sequence[int]
) -> int | None:
    """The edge rule: which off-map direction a river hex on the map border runs
    out through, so the river leaves the map instead of ending in a cap.

    Same choice as the mouth rule (``calm_outlet``) over the hex's off-map
    directions. It ranks below the mouth rule: callers skip hexes that already
    have a mouth (``map_edge_outlets``). ``None`` = no outlet (mid-course, not on
    the border, or only sharp options: the hex keeps its end cap).
    """
    return calm_outlet(river_dirs, off_map_dirs)


def lake_mouths(
    coords: Iterable[tuple[int, int]], terrain_at: TerrainProbe
) -> dict[str, int]:
    """River-mouth directions by hex key (``"q,r"``) for every river-like hex
    the mouth rule (``mouth_direction``) connects to a lake."""
    mouths: dict[str, int] = {}
    for q, r in coords:
        if terrain_at(q, r) not in RIVER_LIKE:
            continue
        lake_dirs = [
            i
            for i, (dq, dr) in enumerate(AXIAL_DIRECTIONS)
            if terrain_at(q + dq, r + dr) == LAKE
        ]
        d = mouth_direction(connected_edges(q, r, terrain_at), lake_dirs)
        if d is not None:
            mouths[_hex_key(q, r)] = d
    return mouths


def lake_mask(q: int, r: int, terrain_at: TerrainProbe) -> int:
    """The lake tile rule: bit d set when the neighbour in ``AXIAL_DIRECTIONS[d]``
    is lake (off the map counts as land). A lake hex draws ``lake_tile_key(mask)``
    when the tile set has mask-painted lake tiles."""
    mask = 0
    for d, (dq, dr) in enumerate(AXIAL_DIRECTIONS):
        if terrain_at(q + dq, r + dr) == LAKE:
            mask |= 1 << d
    return mask


def lake_tile_key(mask: int) -> str:
    """Variant-group key of a lake mask's tiles: ``lake/m<mask two digits>``."""
    if not 0 <= mask < 64:
        raise ValueError(f"lake mask must be 0..63, got {mask}")
    return f"{LAKE}/m{mask:02d}"


def lake_masks(
    coords: Iterable[tuple[int, int]], terrain_at: TerrainProbe
) -> dict[str, int]:
    """``lake_mask`` of every lake hex, by hex key."""
    return {
        _hex_key(q, r): lake_mask(q, r, terrain_at)
        for q, r in coords
        if terrain_at(q, r) == LAKE
    }


def map_edge_outlets(
    coords: Iterable[tuple[int, int]],
    terrain_at: TerrainProbe,
    mouths: dict[str, int],
) -> dict[str, int]:
    """Edge-rule directions by hex key for every river-like hex on the map
    border that the edge rule (``edge_direction``) lets run off the map. Hexes
    with a mouth are skipped: the mouth rule wins."""
    outlets: dict[str, int] = {}
    for q, r in coords:
        if terrain_at(q, r) not in RIVER_LIKE or _hex_key(q, r) in mouths:
            continue
        off_map = [
            i
            for i, (dq, dr) in enumerate(AXIAL_DIRECTIONS)
            if terrain_at(q + dq, r + dr) is None
        ]
        d = edge_direction(connected_edges(q, r, terrain_at), off_map)
        if d is not None:
            outlets[_hex_key(q, r)] = d
    return outlets


def with_outlets(
    river_course: dict[str, list[int]], outlets: dict[str, int]
) -> dict[str, list[int]]:
    """The river course with each outlet's edge (a lake mouth or a map-border
    exit) added, ascending as ``orientedRiverTile`` expects. An outlet hex has
    at most one river-like neighbour, so no junction chord was pruned from it,
    and a hex has at most one outlet."""
    return {
        key: sorted([*edges, outlets[key]]) if key in outlets else edges
        for key, edges in river_course.items()
    }


def river_course_edges(
    coords: Iterable[tuple[int, int]], terrain_at: TerrainProbe
) -> dict[str, list[int]]:
    """``riverTiles.ts riverCourseEdges``: the river course graph
    (``course_graph_edges`` over river/bridge hexes) plus each hex's outlet edge
    (``lake_mouths`` first, then ``map_edge_outlets``). Keys are ``"q,r"``."""
    coord_list = list(coords)
    mouths = lake_mouths(coord_list, terrain_at)
    outlets = {**map_edge_outlets(coord_list, terrain_at, mouths), **mouths}
    course = course_graph_edges(coord_list, lambda q, r: terrain_at(q, r) in RIVER_LIKE)
    return with_outlets(course, outlets)


def road_course_edges(hexes: Iterable[Hex], hex_at: HexProbe) -> dict[str, list[int]]:
    """``roadTiles.ts roadCourseEdges``: the road course graph
    (``course_graph_edges`` over road-flagged and bridge hexes)."""
    return course_graph_edges(
        [(h.q, h.r) for h in hexes], lambda q, r: _is_road_like(hex_at(q, r))
    )


@dataclass(frozen=True)
class OrientedTile:
    """``riverTiles.ts OrientedTile``: variant basename + render-time L/R flip."""

    name: str
    mirror: bool


def _o(name: str, mirror: bool = False) -> OrientedTile:
    return OrientedTile(name, mirror)


ORIENTED_SINGLES: dict[str, OrientedTile] = {
    "0": _o("end_se"),
    "4": _o("end_se", True),
    "1": _o("end_ne"),
    "3": _o("end_ne", True),
    "2": _o("end_n"),
    "5": _o("end_s"),
}

ORIENTED_PAIRS: dict[str, OrientedTile] = {
    "2,5": _o("straight_v"),
    "0,3": _o("straight_d"),
    "1,4": _o("straight_d", True),
    "0,2": _o("turn_n_se"),
    "2,4": _o("turn_n_se", True),
    "3,5": _o("turn_s_nw"),
    "1,5": _o("turn_s_nw", True),
    "0,4": _o("turn_se_sw"),
    "1,3": _o("turn_ne_nw"),
}

ORIENTED_TRIPLES: dict[str, OrientedTile] = {
    "0,2,5": _o("fork_se_n_s"),
    "2,4,5": _o("fork_se_n_s", True),
    "1,2,5": _o("fork_ne_n_s"),
    "2,3,5": _o("fork_ne_n_s", True),
    "0,1,3": _o("fork_se_ne_nw"),
    "1,3,4": _o("fork_se_ne_nw", True),
    "0,2,3": _o("fork_se_n_nw"),
    "1,2,4": _o("fork_se_n_nw", True),
    "0,3,4": _o("fork_se_nw_sw"),
    "0,1,4": _o("fork_se_nw_sw", True),
    "0,3,5": _o("fork_se_nw_s"),
    "1,4,5": _o("fork_se_nw_s", True),
    "0,2,4": _o("fork_se_n_sw"),
    "1,3,5": _o("fork_ne_nw_s"),
    "1,2,3": _o("fork_ne_n_nw"),
    "0,1,2": _o("fork_se_ne_n"),
    "2,3,4": _o("fork_se_ne_n", True),
    "0,1,5": _o("fork_se_ne_s"),
    "3,4,5": _o("fork_se_ne_s", True),
    "0,4,5": _o("fork_se_sw_s"),
}


def oriented_river_tile(edges: Sequence[int]) -> OrientedTile | None:
    """``riverTiles.ts orientedRiverTile``; ``None`` = unhandled shape (generic 00)."""
    key = ",".join(str(e) for e in edges)
    table = {1: ORIENTED_SINGLES, 2: ORIENTED_PAIRS, 3: ORIENTED_TRIPLES}.get(
        len(edges)
    )
    return None if table is None else table.get(key)


def _is_road_like(h: Hex | None) -> bool:
    return h is not None and (h.road or h.terrain == "bridge")


def road_connected_edges(q: int, r: int, hex_at: HexProbe) -> list[int]:
    """``roadTiles.ts roadConnectedEdges``: directions to road-carrying hexes."""
    return [
        i
        for i, (dq, dr) in enumerate(AXIAL_DIRECTIONS)
        if _is_road_like(hex_at(q + dq, r + dr))
    ]


@dataclass(frozen=True)
class RoadTileRef:
    """``roadTiles.ts RoadTileRef``: byOriented lookup + mirror."""

    lookup: str
    mirror: bool


def road_tile_refs(
    hexes: Iterable[Hex], road_course: dict[str, list[int]]
) -> dict[str, RoadTileRef]:
    """``roadTiles.ts roadTileRefs``: baked-road refs for every road hex whose
    course-graph mask (``road_course``, from ``road_course_edges`` over the same
    map) is a handled shape."""
    refs: dict[str, RoadTileRef] = {}
    for h in hexes:
        if not h.road or h.terrain in RIVER_LIKE:
            continue
        oriented = oriented_river_tile(road_course[_hex_key(h.q, h.r)])
        if oriented is None:
            continue
        refs[_hex_key(h.q, h.r)] = RoadTileRef(
            f"road/{h.terrain}_{oriented.name}", oriented.mirror
        )
    return refs


def shape_label(edges: Sequence[int]) -> str:
    """Human label for an unhandled mask in the report."""
    if not edges:
        return "isolated"
    if len(edges) == 2:
        return f"sharp turn {{{edges[0]},{edges[1]}}}"
    return f"{len(edges)}-edge mask {{{','.join(map(str, edges))}}}"


# ==============================================================================
# Tile sets
# ==============================================================================


@dataclass(frozen=True)
class TileSet:
    """``tiles.ts loadTerrainTextures`` over a directory instead of a Vite glob."""

    label: str
    root: Path
    by_terrain: dict[str, list[Path]]
    by_oriented: dict[str, list[Path]]

    @classmethod
    def discover(cls, root: Path, label: str) -> TileSet:
        """Index ``<root>/<terrain>/<variant>.png`` like the frontend glob does."""
        if not root.is_dir():
            raise TileSetError(f"{label} tiles directory not found: {root}")
        nested = list(root.glob("*/*/**/*.png"))
        if nested:
            raise TileSetError(
                f"{label} tiles: PNGs nested deeper than <terrain>/<file>.png are "
                f"mis-keyed by the frontend glob; move them: {nested[:3]}"
            )
        by_terrain: dict[str, list[Path]] = {}
        by_oriented: dict[str, list[Path]] = {}
        for terrain_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            names = locale_sorted(p.name for p in terrain_dir.glob("*.png"))
            for name in names:
                path = terrain_dir / name
                stem = name[: -len(".png")]
                by_terrain.setdefault(terrain_dir.name, []).append(path)
                key = f"{terrain_dir.name}/{oriented_key(stem)}"
                by_oriented.setdefault(key, []).append(path)
        if not by_terrain:
            raise TileSetError(f"{label} tiles directory has no tiles: {root}")
        return cls(label, root, by_terrain, by_oriented)

    def rel(self, path: Path) -> str:
        return f"{self.label}:{path.relative_to(self.root)}"


@dataclass(frozen=True)
class Group:
    """A resolved variant group: which tile set served it and its files."""

    tile_set: TileSet
    key: str
    files: list[Path]


class TileLibrary:
    """Primary tile set with an optional per-group fallback set.

    A group (``plains``, ``river/straight_v``, ``road/plains_turn_s_nw``) is taken
    whole from the first set that has it; groups are never merged, so a partial
    candidate set replaces exactly the shapes it ships.
    """

    def __init__(self, primary: TileSet, fallback: TileSet | None) -> None:
        self.sets = [primary] if fallback is None else [primary, fallback]
        self._widths: dict[Path, int] = {}

    def oriented(self, key: str) -> Group | None:
        for tile_set in self.sets:
            files = tile_set.by_oriented.get(key)
            if files:
                return Group(tile_set, key, files)
        return None

    def terrain(self, terrain: str) -> Group | None:
        for tile_set in self.sets:
            files = tile_set.by_terrain.get(terrain)
            if files:
                return Group(tile_set, terrain, files)
        return None

    def has_lake_masks(self) -> bool:
        """True when any set has mask-painted lake tiles (``lake/mNN_*.png``)."""
        return any(
            LAKE_MASK_KEY.match(key)
            for tile_set in self.sets
            for key in tile_set.by_oriented
        )

    def cosmetic_lake(self) -> Group | None:
        """The first set's round-1 style lake variants (``lake/NN.png``, digits
        only); mask-painted files are never drawn as cosmetic variants."""
        for tile_set in self.sets:
            files = [p for p in tile_set.by_terrain.get(LAKE, []) if p.stem.isdigit()]
            if files:
                return Group(tile_set, LAKE, files)
        return None

    def width(self, path: Path) -> int:
        if path not in self._widths:
            with Image.open(path) as im:
                self._widths[path] = im.width
        return self._widths[path]


@dataclass(frozen=True)
class SiteSet:
    """Opt-in site tiles: ``<root>/<kind>/l<level>_<nn>.png`` per (kind, level)."""

    root: Path
    groups: dict[tuple[str, int], list[Path]]
    # base stage -> variants, each {axial offset: tile path}
    stages: dict[int, list[dict[tuple[int, int], Path]]] = field(default_factory=dict)

    @classmethod
    def discover(cls, root: Path) -> SiteSet:
        if not root.is_dir():
            raise TileSetError(f"sites directory not found: {root}")
        groups: dict[tuple[str, int], list[Path]] = {}
        stages: dict[int, list[dict[tuple[int, int], Path]]] = {}
        for stage, footprint in BASE_STAGES.items():
            for d in sorted((root / "base").glob(f"stage{stage}_*")):
                pos_file = d / "positions.json"
                if not pos_file.is_file():
                    raise TileSetError(f"{d}: missing positions.json")
                positions = json.loads(pos_file.read_text())["positions"]
                members = {
                    (int(o[0]), int(o[1])): d / f"{n}.png" for n, o in positions.items()
                }
                if set(members) != set(footprint):
                    raise TileSetError(
                        f"{d}: positions {sorted(members)} != stage {stage} footprint "
                        f"{sorted(footprint)}"
                    )
                stages.setdefault(stage, []).append(members)
        for kind in SITE_KINDS:
            if kind == "base" and stages:
                continue
            for level in SITE_LEVELS:
                names = locale_sorted(
                    p.name for p in (root / kind).glob(f"l{level}_*.png")
                )
                if names:
                    groups[(kind, level)] = [root / kind / n for n in names]
        if not groups and not stages:
            raise TileSetError(f"sites directory has no <kind>/l<level>_*.png: {root}")
        return cls(root, groups, stages)

    def footprint_tiles(
        self, h: Hex, kind: str, level: int
    ) -> dict[tuple[int, int], Path]:
        """{axial offset: tile} for the site anchored at ``h`` (one entry unless a
        staged base); the variant is picked by the anchor's coordinate hash."""
        if kind == "base" and self.stages:
            variants = self.stages.get(level)
            if not variants:
                raise TileSetError(
                    f"no base/stage{level}_* under {self.root} (needed at col={h.col}, "
                    f"row={h.row})"
                )
            return variants[variant_index(h.q, h.r, len(variants))]
        return {(0, 0): self.choose(h, kind, level)}

    def choose(self, h: Hex, kind: str, level: int) -> Path:
        """The variant for a site hex, by the same coordinate hash as terrain."""
        files = self.groups.get((kind, level))
        if files is None:
            raise TileSetError(
                f"no {kind}/l{level}_*.png under {self.root} (needed at col={h.col}, "
                f"row={h.row})"
            )
        return files[variant_index(h.q, h.r, len(files))]


# ==============================================================================
# Variety (opt-in, candidates only; not in the frontend yet)
# ==============================================================================
#
# Purely visual decoration of open country, chosen per hex from static map data
# only (terrain, roads, sites): never units or ownership, and nothing the engine
# sees. Written to port line for line to frontend/src/board/hexTile.ts:
#
#   1. Excluded hexes: any hex covered by a site (``SiteSet`` footprints, bases
#      included) is never decorated.
#   2. Homestead candidates: undecorated plains hexes (no road, no site) next to
#      a road-carrying hex or a town/city hex, not next to a base hex, with
#      ``roll(HOME) < home_town`` (next to a town/city) or ``< home_road``.
#   3. Nature candidates: the other plains hexes (no road, no site) with
#      ``roll(NATURE) < nature``.
#   4. Roadside candidates: plains road hexes whose baked road shape has
#      roadside variants, with ``roll(ROAD) < road_town`` (a town/city within
#      2 hexes) or ``< road_open``.
#   5. No clusters: a homestead candidate is kept only if it outranks every
#      adjacent homestead candidate (``rank``: ``roll(RANK)``, ties by q then
#      r). Nature and roadside candidates rank as one pool the same way, and are
#      also dropped next to a kept homestead. So no two decorated hexes touch.
#   6. Variant: ``salted_index(q, r, PICK, count)`` over the kind's files,
#      stepped on (mod count) past any file already used by a higher-ranked
#      decorated hex within ``VARIETY_REPEAT_RADIUS`` (picks are assigned in
#      descending rank). A roadside pick keeps the road hex's mirror flag.
#   7. Size (only with a medium-footprint set): a decorated hex draws from the
#      medium set when ``roll(SIZE) < medium_share`` (``VARIETY_MIXES``), else
#      from the small set; a roadside hex whose shape only one set has uses
#      that set. Which hexes are decorated does not depend on the mix.
#
# Probabilities are per mille (``roll`` is ``salted_index(q, r, salt, 1000)``).

VARIETY_SALT_NATURE = 11
VARIETY_SALT_HOME = 12
VARIETY_SALT_ROAD = 13
VARIETY_SALT_RANK = 14
VARIETY_SALT_PICK = 15
VARIETY_SALT_SIZE = 16
VARIETY_REPEAT_RADIUS = 4  # no two decorated hexes this close share a tile file
# medium-footprint share per mille of decorated hexes (step 7)
VARIETY_MIXES: dict[str, int] = {
    "small": 0,
    "mostly-small": 250,
    "even": 500,
    "mostly-medium": 750,
    "medium": 1000,
}
VARIETY_KINDS = ("nature", "home", "road")
SETTLEMENTS = ("town", "city")


@dataclass(frozen=True)
class VarietyDensity:
    """Per-mille candidate probabilities (before the no-cluster rule)."""

    nature: int
    home_road: int
    home_town: int
    road_open: int
    road_town: int


VARIETY_DENSITIES: dict[str, VarietyDensity] = {
    "low": VarietyDensity(
        nature=70, home_road=90, home_town=200, road_open=60, road_town=160
    ),
    "medium": VarietyDensity(
        nature=140, home_road=160, home_town=350, road_open=120, road_town=300
    ),
    "high": VarietyDensity(
        nature=240, home_road=260, home_town=550, road_open=220, road_town=480
    ),
}


def salted_index(q: int, r: int, salt: int, count: int) -> int:
    """``variant_index`` with a third hash term ``salt`` (as ``join_variant_index``):
    an independent stable value in ``[0, count)`` per (hex, salt)."""
    if count <= 1:
        return 0
    h_float = (
        float(_to_int32(q)) * 0x1F1F1F1F
        + float(_to_int32(r)) * 0x85EBCA6B
        + float(salt) * 0x27D4EB2F
    )
    h = _to_int32(_to_uint32(h_float) ^ (_to_uint32(h_float) >> 15))
    h = _imul(h, 0x2C1B3C6D)
    h = _to_int32(h ^ ((h % (1 << 32)) >> 12))
    return abs(h) % count


@dataclass(frozen=True)
class VarietySet:
    """Variety tiles: ``<root>/nature/*.png``, ``<root>/home/*.png`` and
    ``<root>/road/road-<shape>-<label>.png`` (shape e.g. ``straight_v``)."""

    root: Path
    nature: list[Path]
    home: list[Path]
    road: dict[str, list[Path]]  # oriented shape -> variants

    @classmethod
    def discover(cls, root: Path) -> VarietySet:
        if not root.is_dir():
            raise TileSetError(f"variety directory not found: {root}")
        nature = [
            root / "nature" / n
            for n in locale_sorted(p.name for p in (root / "nature").glob("*.png"))
        ]
        home = [
            root / "home" / n
            for n in locale_sorted(p.name for p in (root / "home").glob("*.png"))
        ]
        road: dict[str, list[Path]] = {}
        for name in locale_sorted(p.name for p in (root / "road").glob("*.png")):
            m = re.match(r"^road-([a-z_]+)-[A-Za-z0-9]+\.png$", name)
            if m is None:
                raise TileSetError(
                    f"{root}/road/{name}: expected road-<shape>-<label>.png"
                )
            road.setdefault(m.group(1), []).append(root / "road" / name)
        if not nature or not home:
            raise TileSetError(
                f"variety directory needs nature/*.png and home/*.png: {root}"
            )
        return cls(root, nature, home, road)


@dataclass(frozen=True)
class VarietyPick:
    kind: str  # one of VARIETY_KINDS
    path: Path
    size: str = "small"  # "small" or "medium" (see VARIETY_MIXES)


def site_hexes(game_map: GameMap) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """(every hex covered by a ``[sites]`` site, the hexes covered by bases)."""
    covered: set[tuple[int, int]] = set()
    bases: set[tuple[int, int]] = set()
    for (aq, ar), (kind, level) in game_map.sites.items():
        footprint = BASE_STAGES[level] if kind == "base" else ((0, 0),)
        for dq, dr in footprint:
            covered.add((aq + dq, ar + dr))
            if kind == "base":
                bases.add((aq + dq, ar + dr))
    return covered, bases


def _neighbours(q: int, r: int) -> list[tuple[int, int]]:
    return [(q + dq, r + dr) for dq, dr in AXIAL_DIRECTIONS]


def _within(q: int, r: int, radius: int) -> list[tuple[int, int]]:
    """Every hex within ``radius`` of (q, r), excluding (q, r)."""
    return [
        (q + dq, r + dr)
        for dq in range(-radius, radius + 1)
        for dr in range(max(-radius, -dq - radius), min(radius, -dq + radius) + 1)
        if (dq, dr) != (0, 0)
    ]


def _rank(q: int, r: int) -> tuple[int, int, int]:
    return salted_index(q, r, VARIETY_SALT_RANK, 1000), q, r


def _settlement_within(game_map: GameMap, q: int, r: int, radius: int) -> bool:
    for dq in range(-radius, radius + 1):
        for dr in range(max(-radius, -dq - radius), min(radius, -dq + radius) + 1):
            if game_map.terrain_at(q + dq, r + dr) in SETTLEMENTS:
                return True
    return False


def variety_picks(
    game_map: GameMap,
    road_tiles: dict[str, RoadTileRef],
    variety: VarietySet | None,
    density: VarietyDensity,
    medium: VarietySet | None = None,
    medium_share: int = 0,
) -> dict[tuple[int, int], VarietyPick]:
    """The decorated hexes of the whole map (see the rule above).

    ``variety`` is the small-feature set, ``medium`` the medium-footprint set;
    each decorated hex is medium when ``roll(SIZE) < medium_share`` (per mille),
    else small (step 7). With ``medium`` None the result is exactly the
    small-only rule.
    """
    sizes = {"small": variety, "medium": medium}
    if variety is None and medium is None:
        raise ValueError("variety_picks needs a small or a medium variety set")
    road_shapes = {
        shape for vs in sizes.values() if vs is not None for shape in vs.road
    }
    covered, bases = site_hexes(game_map)
    roll = salted_index
    home_c: set[tuple[int, int]] = set()
    nature_c: set[tuple[int, int]] = set()
    road_c: dict[tuple[int, int], str] = {}  # hex -> oriented shape
    for h in game_map.hexes:
        at = (h.q, h.r)
        if at in covered or h.terrain != "plains":
            continue
        nbrs = [game_map.hex_at(*n) for n in _neighbours(h.q, h.r)]
        if h.road:
            ref = road_tiles.get(_hex_key(h.q, h.r))
            if ref is None:
                continue
            shape = ref.lookup.removeprefix("road/plains_")
            if shape not in road_shapes:
                continue
            p = (
                density.road_town
                if _settlement_within(game_map, h.q, h.r, 2)
                else density.road_open
            )
            if roll(h.q, h.r, VARIETY_SALT_ROAD, 1000) < p:
                road_c[at] = shape
            continue
        near_town = any(n is not None and n.terrain in SETTLEMENTS for n in nbrs)
        near_road = any(_is_road_like(n) for n in nbrs)
        near_base = any(n in bases for n in _neighbours(h.q, h.r))
        if (near_town or near_road) and not near_base:
            p = density.home_town if near_town else density.home_road
            if roll(h.q, h.r, VARIETY_SALT_HOME, 1000) < p:
                home_c.add(at)
                continue
        if roll(h.q, h.r, VARIETY_SALT_NATURE, 1000) < density.nature:
            nature_c.add(at)

    def outranks(at: tuple[int, int], rivals: set[tuple[int, int]] | dict) -> bool:
        return all(n not in rivals or _rank(*at) > _rank(*n) for n in _neighbours(*at))

    def size_of(at: tuple[int, int], has: Callable[[VarietySet], bool]) -> str:
        """The hex's size by roll, or the other size when that set lacks it."""
        want = (
            "medium"
            if medium is not None and roll(*at, VARIETY_SALT_SIZE, 1000) < medium_share
            else "small"
        )
        chosen = sizes[want]
        if chosen is None or not has(chosen):
            want = "small" if want == "medium" else "medium"
        return want

    def files_for(at: tuple[int, int], kind: str, shape: str) -> tuple[str, list[Path]]:
        if kind == "road":
            size = size_of(at, lambda vs: shape in vs.road)
        else:
            size = size_of(at, lambda vs: True)
        vs = sizes[size]
        assert vs is not None
        return size, (vs.road[shape] if kind == "road" else getattr(vs, kind))

    homes = {at for at in home_c if outranks(at, home_c)}
    others = nature_c | set(road_c)  # nature and roadside rank as one pool
    chosen: dict[tuple[int, int], tuple[str, str]] = {at: ("home", "") for at in homes}
    for at in others:
        if not outranks(at, others) or any(n in homes for n in _neighbours(*at)):
            continue
        chosen[at] = ("road", road_c[at]) if at in road_c else ("nature", "")
    # Variant: the hashed index, stepped past any file a higher-ranked decorated
    # hex within VARIETY_REPEAT_RADIUS already uses (when the set has another).
    picks: dict[tuple[int, int], VarietyPick] = {}
    for at in sorted(chosen, key=lambda h: _rank(*h), reverse=True):
        kind, shape = chosen[at]
        size, files = files_for(at, kind, shape)
        near = {
            picks[n].path
            for n in _within(at[0], at[1], VARIETY_REPEAT_RADIUS)
            if n in picks
        }
        start = roll(*at, VARIETY_SALT_PICK, len(files))
        index = next(
            (
                (start + k) % len(files)
                for k in range(len(files))
                if files[(start + k) % len(files)] not in near
            ),
            start,
        )
        picks[at] = VarietyPick(kind, files[index], size)
    return picks


def variety_shares(
    game_map: GameMap, picks: dict[tuple[int, int], VarietyPick]
) -> dict[str, float]:
    """Decorated share of each eligible pool: open plains (nature), plains next
    to a road or town (home), plains road hexes (road), and of all plains."""
    covered, _ = site_hexes(game_map)
    plains = [
        h for h in game_map.hexes if h.terrain == "plains" and (h.q, h.r) not in covered
    ]
    open_plains = [h for h in plains if not h.road]
    beside = [
        h
        for h in open_plains
        if any(
            (n := game_map.hex_at(*at)) is not None
            and (n.terrain in SETTLEMENTS or _is_road_like(n))
            for at in _neighbours(h.q, h.r)
        )
    ]
    roads = [h for h in plains if h.road]
    count = Counter(p.kind for p in picks.values())
    by_town = [
        h
        for h in beside
        if any(game_map.terrain_at(*at) in SETTLEMENTS for at in _neighbours(h.q, h.r))
    ]
    town_homes = sum(
        1
        for h in by_town
        if (p := picks.get((h.q, h.r))) is not None and p.kind == "home"
    )

    def share(n: int, pool: list[Hex]) -> float:
        return round(n / len(pool), 4) if pool else 0.0

    return {
        "plains_hexes": len(plains),
        "nature_of_open_plains": share(count["nature"], open_plains),
        "home_of_plains_beside_road_or_town": share(count["home"], beside),
        "home_of_plains_beside_town": share(town_homes, by_town),
        "road_of_plains_road_hexes": share(count["road"], roads),
        "decorated_of_all_plains": share(sum(count.values()), plains),
    }


@dataclass(frozen=True)
class TileChoice:
    """What a hex draws and why (``hexTile.ts hexTileRef`` plus provenance)."""

    path: Path
    mirror: bool
    group: Group
    kind: str  # "river", "road", "terrain"
    shape: str  # wanted shape name ("straight_v", "plains", "00", ...)
    notes: tuple[str, ...]


def choose_tile(
    h: Hex,
    library: TileLibrary,
    river_course: dict[str, list[int]],
    road_tiles: dict[str, RoadTileRef],
    road_course: dict[str, list[int]],
    lake_mask_by_key: dict[str, int] | None = None,
) -> TileChoice | None:
    """``hexTile.ts hexTileRef``: river/bridge oriented art, baked road, lake by
    mask, cosmetic.

    ``road_course`` (``road_course_edges``) is only used to label an unhandled
    road mask in the report. ``lake_mask_by_key`` (``lake_masks``) is needed for
    lake hexes when the library has mask-painted lake tiles.
    ``None`` means the hex draws no tile, only its flat base fill: its terrain
    is in ``FILL_ONLY_OK_TERRAINS`` and no tile set has any art for it.
    """
    key = _hex_key(h.q, h.r)
    primary = library.sets[0]
    if h.terrain == LAKE:
        return _choose_lake_tile(h, library, lake_mask_by_key)
    if h.terrain in RIVER_LIKE:
        edges = river_course.get(key, [])
        oriented = oriented_river_tile(edges)
        notes: list[str] = []
        group = None
        shape = "00"
        if oriented is not None:
            shape = oriented.name
            group = library.oriented(f"{h.terrain}/{oriented.name}")
            if group is None:
                notes.append(f"no {h.terrain}/{oriented.name} art -> generic 00")
        else:
            notes.append(f"unhandled {shape_label(edges)} -> generic 00")
        if group is None:
            group = library.oriented(f"{h.terrain}/00")
            if group is None:
                raise TileSetError(
                    f"hex col={h.col}, row={h.row} needs {h.terrain}/00.png (the "
                    f"generic fallback) but no tile set has it; {notes[0]}"
                )
        if group.tile_set is not primary:
            notes.append(f"{group.key} from {group.tile_set.label}")
        path = group.files[variant_index(h.q, h.r, len(group.files))]
        mirror = oriented.mirror if oriented is not None else False
        return TileChoice(path, mirror, group, "river", shape, tuple(notes))

    ref = road_tiles.get(key) if h.road else None
    road_notes: list[str] = []
    if ref is not None:
        road_group = library.oriented(ref.lookup)
        if road_group is not None:
            path = road_group.files[variant_index(h.q, h.r, len(road_group.files))]
            if road_group.tile_set is not primary:
                road_notes.append(f"{ref.lookup} from {road_group.tile_set.label}")
            shape = ref.lookup.split("/", 1)[1]
            return TileChoice(
                path, ref.mirror, road_group, "road", shape, tuple(road_notes)
            )
        road_notes.append(f"no {ref.lookup} art -> base tile + vector road")
    elif h.road:
        edges = road_course[key]
        road_notes.append(f"unhandled road {shape_label(edges)} -> vector road")

    group = library.terrain(h.terrain)
    if group is None and h.terrain in FILL_ONLY_OK_TERRAINS:
        return None
    if group is None:
        raise TileSetError(
            f"no tile set has any {h.terrain}/*.png (needed at col={h.col}, "
            f"row={h.row}); the frontend would crash here too"
        )
    if group.tile_set is not primary:
        road_notes.append(f"{h.terrain} from {group.tile_set.label}")
    path = group.files[variant_index(h.q, h.r, len(group.files))]
    return TileChoice(path, False, group, "terrain", h.terrain, tuple(road_notes))


def _choose_lake_tile(
    h: Hex, library: TileLibrary, lake_mask_by_key: dict[str, int] | None
) -> TileChoice | None:
    """Lake: the mask's own tiles when the library has any mask set, else (or
    when this mask has no tile, with a note) the cosmetic ``lake/NN`` variants,
    else ``None`` (flat fill)."""
    primary = library.sets[0]
    notes: list[str] = []
    if library.has_lake_masks():
        if lake_mask_by_key is None:
            raise ValueError("lake masks are needed to choose mask-painted lake tiles")
        mask = lake_mask_by_key[_hex_key(h.q, h.r)]
        group = library.oriented(lake_tile_key(mask))
        if group is not None:
            if group.tile_set is not primary:
                notes.append(f"{group.key} from {group.tile_set.label}")
            path = group.files[variant_index(h.q, h.r, len(group.files))]
            return TileChoice(path, False, group, "lake", f"m{mask:02d}", tuple(notes))
        notes.append(f"no {lake_tile_key(mask)} art")
    group = library.cosmetic_lake()
    if group is None:
        return None
    if notes:
        notes[-1] += " -> cosmetic lake variant"
    if group.tile_set is not primary:
        notes.append(f"lake from {group.tile_set.label}")
    path = group.files[variant_index(h.q, h.r, len(group.files))]
    return TileChoice(path, False, group, "terrain", LAKE, tuple(notes))


def draws_vector_road(
    h: Hex,
    library: TileLibrary,
    river_course: dict[str, list[int]],
    road_tiles: dict[str, RoadTileRef],
) -> bool:
    """Does this hex get centre-to-edge road stubs (retired Board.tsx ``drawTerrainOverlay``)?"""
    if not h.road and h.terrain != "bridge":
        return False
    key = _hex_key(h.q, h.r)
    if h.terrain == "bridge":
        oriented = oriented_river_tile(river_course.get(key, []))
        group = library.oriented(f"bridge/{oriented.name}") if oriented else None
        if group is not None and library.width(group.files[0]) > LEGACY_TILE_PX:
            return False
    if h.road:
        ref = road_tiles.get(key)
        if ref is not None and library.oriented(ref.lookup) is not None:
            return False
    return True


# ==============================================================================
# Rendering
# ==============================================================================


def _rgb(color: int) -> np.ndarray:
    return np.array(
        [(color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF], dtype=np.float32
    ) / np.float32(255)


def _premultiplied(im: Image.Image) -> np.ndarray:
    arr = np.asarray(im.convert("RGBA"), dtype=np.float32) / np.float32(255)
    arr[..., :3] *= arr[..., 3:4]
    return arr


class Canvas:
    """Premultiplied float RGBA canvas in screen pixels, world -> screen mapped."""

    def __init__(
        self, width: int, height: int, origin: tuple[float, float], zoom: float
    ):
        self.px = np.zeros((height, width, 4), dtype=np.float32)
        self.px[..., :3] = _rgb(BACKGROUND)
        self.px[..., 3] = 1.0
        self.origin = origin
        self.zoom = zoom
        # Brightness gain for terrain layers (--drift); None = untouched.
        self.terrain_gain: np.ndarray | None = None

    def to_screen(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.origin[0]) * self.zoom, (y - self.origin[1]) * self.zoom

    def composite(
        self, sprite: np.ndarray, cx: float, cy: float, terrain: bool = False
    ) -> None:
        """Draw a premultiplied sprite centred at screen ``(cx, cy)`` (anchor 0.5).

        Sub-pixel positions are honoured with a bilinear shift, as a linearly
        filtered Pixi sprite at a fractional position would be, so seams between
        neighbours are not artefacts of integer rounding.
        """
        h, w = sprite.shape[:2]
        x0 = cx - w / 2
        y0 = cy - h / 2
        ix, iy = math.floor(x0), math.floor(y0)
        fx, fy = x0 - ix, y0 - iy
        # Four-tap bilinear: out[i, j] samples the sprite at (i - fy, j - fx).
        s00 = np.pad(sprite, ((0, 1), (0, 1), (0, 0)))  # sprite[i, j]
        s01 = np.pad(sprite, ((0, 1), (1, 0), (0, 0)))  # sprite[i, j-1]
        s10 = np.pad(sprite, ((1, 0), (0, 1), (0, 0)))  # sprite[i-1, j]
        s11 = np.pad(sprite, ((1, 0), (1, 0), (0, 0)))  # sprite[i-1, j-1]
        shifted = (
            (1 - fx) * (1 - fy) * s00
            + fx * (1 - fy) * s01
            + (1 - fx) * fy * s10
            + fx * fy * s11
        )
        self._over(shifted, ix, iy, terrain)

    def _over(self, src: np.ndarray, ix: int, iy: int, terrain: bool) -> None:
        h, w = src.shape[:2]
        H, W = self.px.shape[:2]
        x0, y0 = max(ix, 0), max(iy, 0)
        x1, y1 = min(ix + w, W), min(iy + h, H)
        if x0 >= x1 or y0 >= y1:
            return
        piece = src[y0 - iy : y1 - iy, x0 - ix : x1 - ix]
        if terrain and self.terrain_gain is not None:
            # "over" is linear in the source, so scaling every terrain layer's
            # contribution scales the terrain composite and nothing above it.
            piece = piece.copy()
            piece[..., :3] *= self.terrain_gain[y0:y1, x0:x1, None]
        dst = self.px[y0:y1, x0:x1]
        dst *= 1 - piece[..., 3:4]
        dst += piece

    def stroke(
        self,
        segments: Sequence[Segment],
        width: float,
        color: int,
        extend: bool,
        alpha: float = 1.0,
        feather: float | None = None,
    ) -> None:
        """Antialiased opaque strokes of world-space segments in one colour.

        All segments form one coverage mask before compositing, like a single Pixi
        ``g.stroke`` over a multi-subpath path. ``extend`` pushes each end out by
        half the width (closes the grid's polygon corners, standing in for Pixi's
        miter joins); otherwise ends are butt caps, Pixi's default. ``feather``
        (world px, at least one screen px) softens the edges beyond the default
        one-pixel antialiasing; ``alpha`` makes the stroke translucent.
        """
        soft = None if feather is None else max(feather * self.zoom, 1.0)
        H, W = self.px.shape[:2]
        mask = np.zeros((H, W), dtype=np.float32)
        half = width * self.zoom / 2
        for (wx0, wy0), (wx1, wy1) in segments:
            ax, ay = self.to_screen(wx0, wy0)
            bx, by = self.to_screen(wx1, wy1)
            length = math.hypot(bx - ax, by - ay)
            if length == 0:
                continue
            ux, uy = (bx - ax) / length, (by - ay) / length
            ext = half if extend else 0.0
            pad = half + ext + 1.5 + (0.0 if soft is None else soft)
            gx0 = max(int(math.floor(min(ax, bx) - pad)), 0)
            gy0 = max(int(math.floor(min(ay, by) - pad)), 0)
            gx1 = min(int(math.ceil(max(ax, bx) + pad)), W)
            gy1 = min(int(math.ceil(max(ay, by) + pad)), H)
            if gx0 >= gx1 or gy0 >= gy1:
                continue
            ys, xs = np.mgrid[gy0:gy1, gx0:gx1].astype(np.float32)
            dx, dy = xs + 0.5 - ax, ys + 0.5 - ay
            along = dx * ux + dy * uy
            perp = np.abs(dx * -uy + dy * ux)
            if soft is None:
                cover = np.clip(half + 0.5 - perp, 0, 1) * np.clip(
                    np.minimum(along + ext, length + ext - along) + 0.5, 0, 1
                )
            else:
                cover = np.clip((half - perp) / soft + 0.5, 0, 1) * np.clip(
                    (np.minimum(along + ext, length + ext - along)) / soft + 0.5,
                    0,
                    1,
                )
            region = mask[gy0:gy1, gx0:gx1]
            np.maximum(region, cover, out=region)
        cov = mask[..., None] if alpha == 1.0 else mask[..., None] * alpha
        self.px[..., :3] = self.px[..., :3] * (1 - cov) + _rgb(color) * cov
        self.px[..., 3:4] = self.px[..., 3:4] * (1 - cov) + cov

    def image(self) -> Image.Image:
        alpha = np.clip(self.px[..., 3:4], 1e-6, 1)
        rgb = np.clip(self.px[..., :3] / alpha, 0, 1)
        return Image.fromarray(np.round(rgb * 255).astype(np.uint8), "RGB")


class SpriteCache:
    """Tiles resampled to the output scale, premultiplied, mirrored on demand."""

    def __init__(self, hex_px: int) -> None:
        self.hex_px = hex_px
        self._cache: dict[tuple[Path, bool], np.ndarray] = {}
        self._fills: dict[str, np.ndarray] = {}

    def tile(self, path: Path, mirror: bool) -> np.ndarray:
        key = (path, mirror)
        if key not in self._cache:
            with Image.open(path) as raw:
                im = raw.convert("RGBA")
            # WorldRenderer.ts scales uniformly by the texture *width* to 2*HEX_SIZE.
            scale = self.hex_px / im.width
            size = (self.hex_px, max(1, round(im.height * scale)))
            if size != im.size:
                if scale < 1:
                    resample = Image.Resampling.LANCZOS
                elif im.width <= LEGACY_TILE_PX:
                    resample = Image.Resampling.NEAREST  # tiles.ts: legacy = nearest
                else:
                    resample = Image.Resampling.BILINEAR
                im = im.resize(size, resample)
            arr = _premultiplied(im)
            self._cache[key] = arr[:, ::-1].copy() if mirror else arr
        return self._cache[key]

    def join(self, path: Path, native_hex_width: int) -> np.ndarray:
        """A join sprite at the output scale (its canvas scales like a tile)."""
        key = (path, False)
        if key not in self._cache:
            with Image.open(path) as raw:
                im = raw.convert("RGBA")
            scale = self.hex_px / native_hex_width
            size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
            if size != im.size:
                resample = (
                    Image.Resampling.LANCZOS if scale < 1 else Image.Resampling.BILINEAR
                )
                im = im.resize(size, resample)
            self._cache[key] = _premultiplied(im)
        return self._cache[key]

    def fill(self, terrain: str) -> np.ndarray:
        """The flat hex under each sprite (retired Board.tsx ``drawTerrainBase``)."""
        if terrain not in self._fills:
            alpha = hex_mask(self.hex_px, self.hex_px).astype(np.float32) / 255
            arr = np.zeros((self.hex_px, self.hex_px, 4), dtype=np.float32)
            arr[..., :3] = _rgb(TERRAIN_FILL[terrain]) * alpha[..., None]
            arr[..., 3] = alpha
            self._fills[terrain] = arr
        return self._fills[terrain]


def _lattice_values(i: np.ndarray, j: np.ndarray, octave: int) -> np.ndarray:
    """Deterministic pseudo-random values in [-1, 1] at integer lattice points."""
    h = (
        i.astype(np.int64) * 0x1F1F1F1F
        + j.astype(np.int64) * 0x85EBCA6B
        + octave * 0x27D4EB2F
    ) & 0xFFFFFFFF
    h = h.astype(np.uint64)
    h ^= h >> np.uint64(15)
    h = (h * np.uint64(0x2C1B3C6D)) & np.uint64(0xFFFFFFFF)
    h ^= h >> np.uint64(12)
    return h.astype(np.float64) / 2**31 - 1.0


def drift_noise(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Smooth world-space noise in [-1, 1] for ``--drift`` (x, y in world px).

    Value noise, two octaves, quintic-interpolated, on lattices rotated off the
    hex axes with spacings unrelated to the grid's periods, so the drift never
    lines up with hex rows. Anchored in world space: a crop sees the same drift.
    """
    total = np.zeros(np.broadcast(x, y).shape, dtype=np.float64)
    for octave, (spacing_hexes, angle, weight) in enumerate(DRIFT_OCTAVES):
        spacing = spacing_hexes * 2 * HEX_SIZE
        u = (x * math.cos(angle) + y * math.sin(angle)) / spacing
        v = (-x * math.sin(angle) + y * math.cos(angle)) / spacing
        i0, j0 = np.floor(u), np.floor(v)
        fu, fv = u - i0, v - j0
        su = fu * fu * fu * (fu * (fu * 6 - 15) + 10)
        sv = fv * fv * fv * (fv * (fv * 6 - 15) + 10)
        v00 = _lattice_values(i0, j0, octave)
        v10 = _lattice_values(i0 + 1, j0, octave)
        v01 = _lattice_values(i0, j0 + 1, octave)
        v11 = _lattice_values(i0 + 1, j0 + 1, octave)
        top = v00 + (v10 - v00) * su
        bottom = v01 + (v11 - v01) * su
        total += weight * (top + (bottom - top) * sv)
    weights = sum(w for _, _, w in DRIFT_OCTAVES)
    noise: np.ndarray = np.clip(total / weights * DRIFT_NORMALISE, -1, 1)
    return noise


def drift_gain(canvas: Canvas, amount: float) -> np.ndarray:
    """Per-pixel brightness gain ``1 + amount * noise`` over the canvas."""
    height, width = canvas.px.shape[:2]
    xs = (np.arange(width) + 0.5) / canvas.zoom + canvas.origin[0]
    ys = (np.arange(height) + 0.5) / canvas.zoom + canvas.origin[1]
    gain: np.ndarray = (1 + amount * drift_noise(xs[None, :], ys[:, None])).astype(
        np.float32
    )
    return gain


# Join kind -> code: edges by direction from A, vertices 3 + i (triangle A,
# A + d_i, A + d_(i+1); the vertex is A's corner at VERTEX_ANGLES[i] degrees),
# river mouths 5 + d (d = direction from the river hex R to its lake; the sprite
# is anchored on that edge's midpoint and hashed with R's coordinates).
JOIN_KINDS = {
    "edge_se": 0,
    "edge_ne": 1,
    "edge_n": 2,
    "vert_e": 3,
    "vert_ne": 4,
    "mouth_se": 5,
    "mouth_ne": 6,
    "mouth_n": 7,
    "mouth_nw": 8,
    "mouth_sw": 9,
    "mouth_s": 10,
}
MOUTH_CODE_BASE = 5
AXIAL_DIRECTION_NAMES = ("se", "ne", "n", "nw", "sw", "s")
VERTEX_ANGLES = (0.0, 300.0)


@dataclass(frozen=True)
class JoinSet:
    """Same-terrain join sprites (scripts/tile-foundry/joins.py output)."""

    root: Path
    hex_width: int
    groups: dict[tuple[str, int], list[Path]]  # (terrain, code) -> variants

    @classmethod
    def load(cls, root: Path) -> JoinSet:
        """Read ``joins.json`` + ``<terrain>/<kind>_<nn>.png``."""
        manifest_path = root / "joins.json"
        if not manifest_path.is_file():
            raise TileSetError(f"--joins {root} has no joins.json (run joins.py)")
        manifest = json.loads(manifest_path.read_text())
        hex_width = manifest.get("hex_width")
        if not isinstance(hex_width, int) or hex_width <= 0:
            raise TileSetError(f"{manifest_path}: bad hex_width {hex_width!r}")
        groups: dict[tuple[str, int], list[Path]] = {}
        for terrain_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for name in locale_sorted(p.name for p in terrain_dir.glob("*.png")):
                kind = oriented_key(name[: -len(".png")])
                if kind not in JOIN_KINDS:
                    raise TileSetError(
                        f"join {terrain_dir.name}/{name}: kind must be one of "
                        f"{sorted(JOIN_KINDS)}"
                    )
                key = (terrain_dir.name, JOIN_KINDS[kind])
                groups.setdefault(key, []).append(terrain_dir / name)
        if not groups:
            raise TileSetError(f"--joins {root} has no join sprites")
        return cls(root, hex_width, groups)


@dataclass(frozen=True)
class JoinChoice:
    """One join sprite: its anchor (world px), the hexes it joins, its file."""

    hexes: tuple[Hex, ...]
    code: int
    anchor: tuple[float, float]
    path: Path


def _same_terrain_group(
    game_map: GameMap, a: Hex, directions: Sequence[int]
) -> tuple[Hex, ...] | None:
    group = [a]
    for direction in directions:
        dq, dr = AXIAL_DIRECTIONS[direction]
        other = game_map.hex_at(a.q + dq, a.r + dr)
        if other is None or other.terrain != a.terrain:
            return None
        group.append(other)
    return tuple(group)


def choose_joins(
    game_map: GameMap,
    shown: Sequence[Hex],
    joins: JoinSet,
    mouths: dict[str, int] | None = None,
    skip_terrains: frozenset[str] = frozenset(),
) -> list[JoinChoice]:
    """Every same-terrain edge (pair) and vertex (triangle) touching a shown hex
    whose terrain has join art, in draw order: edges by (A.r, A.q, dir), then
    vertices by (A.r, A.q, i), then river mouths (``lake_mouths``, river hex R ->
    direction d) by (R.r, R.q, d) when the set has ``lake/mouth_*`` art.
    ``skip_terrains`` get no edge or vertex joins (lakes drawn from mask-painted
    tiles: their water already runs edge to edge); mouths are still drawn."""
    shown_keys = {(h.q, h.r) for h in shown}
    ordered = sorted(game_map.hexes, key=lambda h: (h.r, h.q))
    edges: list[JoinChoice] = []
    vertices: list[JoinChoice] = []
    for a in ordered:
        for code in range(5):
            if code < 3:
                group = _same_terrain_group(game_map, a, (code,))
            else:
                group = _same_terrain_group(game_map, a, (code - 3, code - 2))
            if group is None or not any((h.q, h.r) in shown_keys for h in group):
                continue
            if a.terrain in skip_terrains:
                continue
            files = joins.groups.get((a.terrain, code))
            if files is None:
                continue
            ax, ay = axial_to_pixel(a.q, a.r, HEX_SIZE)
            if code < 3:
                bx, by = axial_to_pixel(group[1].q, group[1].r, HEX_SIZE)
                anchor = ((ax + bx) / 2, (ay + by) / 2)
            else:
                angle = math.radians(VERTEX_ANGLES[code - 3])
                anchor = (
                    ax + HEX_SIZE * math.cos(angle),
                    ay + HEX_SIZE * math.sin(angle),
                )
            path = files[join_variant_index(a.q, a.r, code, len(files))]
            (edges if code < 3 else vertices).append(
                JoinChoice(group, code, anchor, path)
            )
    mouth_joins: list[JoinChoice] = []
    for a in ordered:
        d = (mouths or {}).get(_hex_key(a.q, a.r))
        if d is None:
            continue
        dq, dr = AXIAL_DIRECTIONS[d]
        lake = game_map.by_axial[(a.q + dq, a.r + dr)]
        if not any((h.q, h.r) in shown_keys for h in (a, lake)):
            continue
        code = MOUTH_CODE_BASE + d
        files = joins.groups.get((LAKE, code))
        if files is None:
            continue
        ax, ay = axial_to_pixel(a.q, a.r, HEX_SIZE)
        bx, by = axial_to_pixel(lake.q, lake.r, HEX_SIZE)
        path = files[join_variant_index(a.q, a.r, code, len(files))]
        mouth_joins.append(
            JoinChoice((a, lake), code, ((ax + bx) / 2, (ay + by) / 2), path)
        )
    return edges + vertices + mouth_joins


def parse_crop(spec: str) -> tuple[int, int, int, int]:
    """``c0,r0,c1,r1`` (authored offset cols/rows, inclusive) for ``--crop``."""
    parts = spec.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(f"--crop wants c0,r0,c1,r1, got {spec!r}")
    try:
        c0, r0, c1, r1 = (int(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--crop values must be ints: {spec!r}"
        ) from exc
    if c1 < c0 or r1 < r0:
        raise argparse.ArgumentTypeError(f"--crop range is empty: {spec!r}")
    return c0, r0, c1, r1


@dataclass
class RenderReport:
    """Per-hex tile choices plus the hexes drawn with a vector road."""

    choices: list[tuple[Hex, TileChoice]]
    vector_roads: list[Hex]
    joins: list[JoinChoice] = field(default_factory=list)
    sites: list[tuple[Hex, Path]] = field(default_factory=list)
    fill_only: list[Hex] = field(default_factory=list)  # no art: flat base fill
    mouths: dict[str, int] = field(default_factory=dict)  # mouth rule results
    edge_outlets: dict[str, int] = field(default_factory=dict)  # edge rule results
    # lake hexes whose mask has no tile in a mask-painted set (hex, mask)
    lake_missing: list[tuple[Hex, int]] = field(default_factory=list)
    # hexes drawn with a variety tile (only with --variety)
    variety: list[tuple[Hex, VarietyPick]] = field(default_factory=list)


def render(
    game_map: GameMap,
    library: TileLibrary,
    hex_px: int,
    grid: bool,
    crop: tuple[int, int, int, int] | None,
    joins: JoinSet | None = None,
    drift: float = 0.0,
    grid_style: str = "dark",
    draw_vector_roads: bool = True,
    sites: SiteSet | None = None,
    variety: VarietySet | None = None,
    variety_density: str = "medium",
    variety_medium: VarietySet | None = None,
    variety_mix: str = "small",
) -> tuple[Image.Image, RenderReport]:
    """Render the map; tile selection always sees the whole map, even when cropped.

    The three keyword options are opt-in renderer overlays: same-terrain ``joins``
    (the frontend draws these too, frontend/src/board/joins.ts), and two the
    frontend does not have: terrain brightness ``drift`` (fraction, e.g. 0.05 =
    +-5%) and ``grid_style`` "concept" (the concept art's line).
    ``draw_vector_roads=False`` skips the vector road fallback entirely, as the world
    renderer does: a road hex without a baked road tile shows its base tile.
    ``sites`` (opt-in) draws the map's ``[sites]`` hexes from that set in place of
    their plains tile; without it the sites section is ignored.
    ``variety`` (opt-in, candidates) decorates open country by ``variety_picks``
    at ``variety_density`` (a ``VARIETY_DENSITIES`` key), drawing small or
    ``variety_medium`` tiles by ``variety_mix`` (a ``VARIETY_MIXES`` key); site
    hexes need
    ``sites`` to be excluded, so the map's ``[sites]`` count either way.
    """
    if grid_style not in GRID_STYLES:
        raise ValueError(f"grid_style must be one of {GRID_STYLES}, got {grid_style!r}")
    if crop is None:
        shown = list(game_map.hexes)
    else:
        c0, r0, c1, r1 = crop
        shown = [h for h in game_map.hexes if c0 <= h.col <= c1 and r0 <= h.row <= r1]
        if not shown:
            raise MapError(
                f"--crop {crop} selects no hexes of the {game_map.width}x"
                f"{game_map.height} map"
            )

    coords = [(h.q, h.r) for h in game_map.hexes]
    mouths = lake_mouths(coords, game_map.terrain_at)
    edge_outlets = map_edge_outlets(coords, game_map.terrain_at, mouths)
    river_course = river_course_edges(coords, game_map.terrain_at)
    road_course = road_course_edges(game_map.hexes, game_map.hex_at)
    road_tiles = road_tile_refs(game_map.hexes, road_course)
    masks = lake_masks(coords, game_map.terrain_at)
    mask_set = library.has_lake_masks()

    # hex.ts mapPixelBounds: centre bbox padded by one hex radius.
    centres = [axial_to_pixel(h.q, h.r, HEX_SIZE) for h in shown]
    min_x = min(x for x, _ in centres) - HEX_SIZE
    min_y = min(y for _, y in centres) - HEX_SIZE
    max_x = max(x for x, _ in centres) + HEX_SIZE
    max_y = max(y for _, y in centres) + HEX_SIZE
    zoom = hex_px / (2 * HEX_SIZE)
    canvas = Canvas(
        math.ceil((max_x - min_x) * zoom),
        math.ceil((max_y - min_y) * zoom),
        (min_x, min_y),
        zoom,
    )
    sprites = SpriteCache(hex_px)
    if drift != 0.0:
        canvas.terrain_gain = drift_gain(canvas, drift)

    for h, (x, y) in zip(shown, centres):
        canvas.composite(sprites.fill(h.terrain), *canvas.to_screen(x, y), terrain=True)

    choices: list[tuple[Hex, TileChoice]] = []
    fill_only: list[Hex] = []
    lake_missing: list[tuple[Hex, int]] = []
    site_choices: list[tuple[Hex, Path]] = []
    site_tiles: dict[tuple[int, int], Path] = {}
    if sites is not None:
        for (aq, ar), (kind, level) in game_map.sites.items():
            anchor = game_map.by_axial[(aq, ar)]
            for (dq, dr), tile in sites.footprint_tiles(anchor, kind, level).items():
                site_tiles[(aq + dq, ar + dr)] = tile
    picks: dict[tuple[int, int], VarietyPick] = {}
    if variety is not None or variety_medium is not None:
        picks = variety_picks(
            game_map,
            road_tiles,
            variety,
            VARIETY_DENSITIES[variety_density],
            medium=variety_medium,
            medium_share=VARIETY_MIXES[variety_mix],
        )
    variety_used: list[tuple[Hex, VarietyPick]] = []
    for h, (x, y) in zip(shown, centres):
        choice = choose_tile(h, library, river_course, road_tiles, road_course, masks)
        if choice is None:
            fill_only.append(h)
            if mask_set and h.terrain == LAKE:
                lake_missing.append((h, masks[_hex_key(h.q, h.r)]))
            continue
        if mask_set and h.terrain == LAKE and choice.kind != "lake":
            lake_missing.append((h, masks[_hex_key(h.q, h.r)]))
        choices.append((h, choice))
        path, mirror = choice.path, choice.mirror
        if (h.q, h.r) in site_tiles:
            path, mirror = site_tiles[(h.q, h.r)], False
            site_choices.append((h, path))
        elif (h.q, h.r) in picks:
            pick = picks[(h.q, h.r)]
            if pick.kind == "road" and choice.kind != "road":
                raise TileSetError(
                    f"roadside variety at col={h.col}, row={h.row} but the hex drew "
                    f"no baked road tile ({choice.group.key})"
                )
            path, mirror = pick.path, mirror if pick.kind == "road" else False
            variety_used.append((h, pick))
        canvas.composite(
            sprites.tile(path, mirror),
            *canvas.to_screen(x, y),
            terrain=True,
        )

    join_choices: list[JoinChoice] = []
    if joins is not None:
        skip = frozenset({LAKE}) if mask_set else frozenset()
        join_choices = choose_joins(game_map, shown, joins, mouths, skip)
        for join in join_choices:
            canvas.composite(
                sprites.join(join.path, joins.hex_width),
                *canvas.to_screen(*join.anchor),
                terrain=True,
            )

    if grid:
        segments: list[Segment] = []
        for x, y in centres:
            pts = corner_points(x, y, HEX_SIZE)
            segments.extend((pts[i], pts[(i + 1) % 6]) for i in range(6))
        if grid_style == "dark":
            canvas.stroke(segments, GRID_WIDTH, HEX_STROKE, extend=True)
        else:
            canvas.stroke(
                segments,
                CONCEPT_HIGHLIGHT_WIDTH,
                CONCEPT_HIGHLIGHT,
                extend=True,
                alpha=CONCEPT_HIGHLIGHT_ALPHA,
                feather=CONCEPT_FEATHER,
            )
            canvas.stroke(
                segments,
                CONCEPT_CORE_WIDTH,
                CONCEPT_CORE,
                extend=True,
                alpha=CONCEPT_CORE_ALPHA,
                feather=CONCEPT_FEATHER,
            )

    road_segments: list[Segment] = []
    vector_roads: list[Hex] = []
    for h, (x, y) in zip(shown, centres):
        if not draw_vector_roads or not draws_vector_road(
            h, library, river_course, road_tiles
        ):
            continue
        vector_roads.append(h)
        # stubs follow the road course graph, pruned like the baked tiles
        for direction in road_course[_hex_key(h.q, h.r)]:
            dq, dr = AXIAL_DIRECTIONS[direction]
            nx, ny = axial_to_pixel(h.q + dq, h.r + dr, HEX_SIZE)
            road_segments.append(((x, y), ((x + nx) / 2, (y + ny) / 2)))
    canvas.stroke(road_segments, ROAD_WIDTH, ROAD_COLOR, extend=False)

    return canvas.image(), RenderReport(
        choices,
        vector_roads,
        join_choices,
        site_choices,
        fill_only,
        mouths,
        edge_outlets,
        lake_missing,
        variety_used,
    )


# ==============================================================================
# Report + CLI
# ==============================================================================


def _where(h: Hex) -> str:
    return f"col={h.col:>2} row={h.row:>2} (q={h.q},r={h.r})"


def format_report(report: RenderReport) -> str:
    """Tiles used per (kind, terrain, shape) group, then every degraded hex."""
    usage: dict[tuple[str, str], Counter[str]] = {}
    for _, choice in report.choices:
        group_key = (choice.kind, choice.group.key)
        if choice.kind == "river" and choice.shape != choice.group.key.split("/")[1]:
            group_key = (choice.kind, f"{choice.group.key} (wanted {choice.shape})")
        label = choice.group.tile_set.rel(choice.path)
        if choice.mirror:
            label += " [mirrored]"
        usage.setdefault(group_key, Counter())[label] += 1

    lines = ["tiles used (group -> file x hexes):"]
    for kind, key in sorted(usage, key=lambda k: (k[0], k[1])):
        files = usage[(kind, key)]
        listed = ", ".join(f"{f} x{n}" for f, n in sorted(files.items()))
        lines.append(f"  {kind:<7} {key:<32} {listed}")

    if report.joins:
        join_usage: Counter[str] = Counter()
        for join in report.joins:
            join_usage[f"{join.path.parent.name}/{join.path.name}"] += 1
        listed = ", ".join(f"{f} x{n}" for f, n in sorted(join_usage.items()))
        lines.append(f"  joins   {len(report.joins)} sprites: {listed}")

    if report.sites:
        site_usage: Counter[str] = Counter(
            f"{path.parent.name}/{path.name}" for _, path in report.sites
        )  # a staged base lists one entry per member hex
        listed = ", ".join(f"{f} x{n}" for f, n in sorted(site_usage.items()))
        lines.append(f"  sites   {len(report.sites)} hexes: {listed}")

    if report.variety:
        kinds = Counter(p.kind for _, p in report.variety)
        listed = ", ".join(f"{k} x{kinds[k]}" for k in VARIETY_KINDS if kinds[k])
        sizes = Counter(p.size for _, p in report.variety)
        sized = ", ".join(f"{k} x{n}" for k, n in sorted(sizes.items()))
        lines.append(f"  variety {len(report.variety)} hexes: {listed} ({sized})")

    if report.mouths:
        listed = ", ".join(
            f"({key}) -> {AXIAL_DIRECTION_NAMES[d]}"
            for key, d in sorted(report.mouths.items())
        )
        lines.append(
            f"  mouths  {len(report.mouths)} river hexes run into a lake: {listed}"
        )
    if report.edge_outlets:
        listed = ", ".join(
            f"({key}) -> {AXIAL_DIRECTION_NAMES[d]}"
            for key, d in sorted(report.edge_outlets.items())
        )
        lines.append(
            f"  exits   {len(report.edge_outlets)} river hexes run off the map: {listed}"
        )

    degraded = [(h, c) for h, c in report.choices if c.notes]
    vector = {(h.q, h.r) for h in report.vector_roads}
    lines.append("")
    if report.lake_missing:
        missing = Counter(m for _, m in report.lake_missing)
        listed = ", ".join(f"m{m:02d} x{n}" for m, n in sorted(missing.items()))
        lines.append(
            f"LAKE MASKS WITHOUT TILES ({len(report.lake_missing)} hexes): {listed}"
        )
        for h, m in report.lake_missing:
            lines.append(f"  {_where(h)} lake    no lake/m{m:02d} tile")
    if report.fill_only:
        terrains = sorted({h.terrain for h in report.fill_only})
        lines.append(
            f"flat fill only (no {'/'.join(terrains)} art in any tile set): "
            f"{len(report.fill_only)} hexes"
        )
    if not degraded and not vector:
        lines.append(
            "fallbacks: none (every hex drew its own art from the primary set)"
        )
        return "\n".join(lines)
    lines.append(f"fallbacks ({len(degraded)} hexes with notes):")
    for h, c in degraded:
        lines.append(f"  {_where(h)} {h.terrain:<7} " + "; ".join(c.notes))
    bridge_vector = [h for h in report.vector_roads if h.terrain == "bridge"]
    for h in bridge_vector:
        lines.append(f"  {_where(h)} bridge  legacy/missing bridge art -> vector road")
    lines.append(f"vector road stubs drawn on {len(vector)} hexes")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render an offline board mock-up from a tiles directory.",
    )
    parser.add_argument("--tiles", type=Path, default=DEFAULT_TILES)
    parser.add_argument("--fallback", type=Path, default=None)
    parser.add_argument("--map", type=Path, required=True, dest="map_path")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--hex-px", type=int, default=64)
    parser.add_argument("--grid", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--crop", type=parse_crop, default=None)
    parser.add_argument(
        "--joins",
        type=Path,
        default=None,
        help="same-terrain join sprites dir (scripts/tile-foundry/joins.py output)",
    )
    parser.add_argument(
        "--drift",
        type=float,
        nargs="?",
        const=DRIFT_DEFAULT,
        default=0.0,
        help=f"terrain brightness drift amount (bare flag = {DRIFT_DEFAULT})",
    )
    parser.add_argument("--grid-style", choices=GRID_STYLES, default="dark")
    parser.add_argument(
        "--sites",
        type=Path,
        default=None,
        help="site tiles dir (<kind>/l<level>_<nn>.png) for the map's [sites] section",
    )
    parser.add_argument(
        "--no-vector-roads",
        action="store_true",
        help="skip the vector road fallback (the world renderer draws none)",
    )
    parser.add_argument(
        "--variety",
        type=Path,
        default=None,
        help="variety tiles dir (nature/, home/, road/road-<shape>-*.png): opt-in "
        "decoration of open country (candidates; see variety_picks)",
    )
    parser.add_argument(
        "--variety-density", choices=tuple(VARIETY_DENSITIES), default="medium"
    )
    parser.add_argument(
        "--variety-medium",
        type=Path,
        default=None,
        help="medium-footprint variety tiles dir, same layout as --variety",
    )
    parser.add_argument(
        "--variety-mix",
        choices=tuple(VARIETY_MIXES),
        default="small",
        help="share of decorated hexes drawn from --variety-medium",
    )
    args = parser.parse_args(argv)
    share = VARIETY_MIXES[args.variety_mix]
    if share < 1000 and args.variety_medium is not None and args.variety is None:
        parser.error(f"--variety-mix {args.variety_mix} needs --variety (small tiles)")
    if share > 0 and args.variety_medium is None:
        parser.error(f"--variety-mix {args.variety_mix} needs --variety-medium")
    if not 0.0 <= args.drift < 1.0:
        parser.error(f"--drift must be in [0, 1), got {args.drift}")
    if args.hex_px < 8:
        parser.error(f"--hex-px must be >= 8, got {args.hex_px}")

    game_map = load_map(args.map_path)
    primary = TileSet.discover(args.tiles, "tiles")
    fallback = (
        None if args.fallback is None else TileSet.discover(args.fallback, "fallback")
    )
    library = TileLibrary(primary, fallback)
    joins = None if args.joins is None else JoinSet.load(args.joins)
    image, report = render(
        game_map,
        library,
        args.hex_px,
        args.grid,
        args.crop,
        joins=joins,
        drift=args.drift,
        grid_style=args.grid_style,
        draw_vector_roads=not args.no_vector_roads,
        sites=None if args.sites is None else SiteSet.discover(args.sites),
        variety=None if args.variety is None else VarietySet.discover(args.variety),
        variety_density=args.variety_density,
        variety_medium=(
            None
            if args.variety_medium is None
            else VarietySet.discover(args.variety_medium)
        ),
        variety_mix=args.variety_mix,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out)
    print(
        f"{args.out}  {image.width}x{image.height}  map={game_map.name} "
        f"{game_map.width}x{game_map.height} hex_px={args.hex_px}"
    )
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
