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
    frontend/src/board/riverTiles.ts  connectedEdges, riverCourseEdges, orientedRiverTile
    frontend/src/board/roadTiles.ts   roadConnectedEdges, roadTileRefs
    frontend/src/board/hexTile.ts     per-hex texture + mirror choice
    frontend/src/board/hex.ts         flat-top axial -> pixel (via tile-foundry/hexgeom.py)
    frontend/src/board/Board.tsx      draw order: base fill, tile sprite, grid, vector roads

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
    --grid/--no-grid draw the thin hex grid overlay (default on, as Board.tsx does)
    --crop c0,r0,c1,r1  render only authored (offset) cols c0..c1, rows r0..r1
                     inclusive; tile selection still sees the whole map

ASCII map format: one terrain character per hex in the scenario alphabet
(``.`` plains, ``w`` woods, ``h`` hills, ``~`` river, ``=`` bridge, ``t`` town,
``c`` city), rows top to bottom, authored in the scenario's flat-top odd-q offset
grid. Optionally a ``[roads]`` section follows with a same-sized grid of ``#``
(road) / ``.`` (none); the terrain section may be headed ``[terrain]``. Lines
starting with ``;`` and blank lines are ignored.

Prints the tile file used for each (terrain, shape) group and lists every hex
that fell back to ``--fallback``, to a generic ``00`` tile, or to a vector road,
so missing art is obvious. Raises on malformed maps or tile sets.
"""

from __future__ import annotations

import argparse
import json
import math
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
}
ROAD_CHAR = "#"
NO_ROAD_CHAR = "."
COSMETIC_TERRAINS = ("plains", "woods", "hills", "town", "city")
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

# --- render constants (frontend/src/board/{hex,palette}.ts, Board.tsx) --------

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
    """The loader's SPEC §3.4 rules: no road on river; a bridge is a road on a river."""
    for h in game_map.hexes:
        where = f"col={h.col}, row={h.row}"
        if h.terrain == "river" and h.road:
            raise MapError(f"road runs over river at {where}; use a bridge ('=')")
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
            raise MapError(f"{where}: unknown site kind {kind!r}; use one of {SITE_KINDS}")
        levels = tuple(BASE_STAGES) if kind == "base" else SITE_LEVELS
        if level not in levels:
            raise MapError(f"{where}: {kind} level must be one of {levels}")
        if not (0 <= col < game_map.width and 0 <= row < game_map.height):
            raise MapError(f"{where}: cell is off the {game_map.width}x{game_map.height} map")
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
                raise MapError(f"{where}: overlaps another site at col={h.col}, row={h.row}")
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
    3 + i for the vertex shared by ``(q, r)``, its d_i and d_(i+1) neighbours.
    The frontend port is ``joinVariantIndex`` in frontend/src/board/joins.ts.
    """
    if code not in range(5):
        raise ValueError(f"join code must be 0..4, got {code}")
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
    """``riverTiles.ts shapePenalty``: awkwardness of a connected-edge set."""
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


MAX_CHORD_CANDIDATES = 16


def _hex_key(q: int, r: int) -> str:
    return f"{q},{r}"


def river_course_edges(
    coords: Iterable[tuple[int, int]], terrain_at: TerrainProbe
) -> dict[str, list[int]]:
    """``riverTiles.ts riverCourseEdges``: raw river adjacency, junction chords pruned.

    Keys are ``hex.ts hexKey`` strings (``"q,r"``) because the TS breaks score ties
    by the lexicographic order of those strings; reproducing the tie-break needs
    the same keys.
    """
    nodes: dict[str, tuple[int, int, list[int]]] = {}
    for q, r in coords:
        if terrain_at(q, r) not in RIVER_LIKE:
            continue
        nodes[_hex_key(q, r)] = (q, r, connected_edges(q, r, terrain_at))

    def edge_id(a: str, b: str) -> str:
        return f"{a}|{b}" if a < b else f"{b}|{a}"

    def neighbor_key(key: str, direction: int) -> str:
        q, r, _ = nodes[key]
        dq, dr = AXIAL_DIRECTIONS[direction]
        return _hex_key(q + dq, r + dr)

    chord_ids: set[str] = set()
    for key in nodes:
        for i in range(6):
            b = neighbor_key(key, i)
            c = neighbor_key(key, (i + 1) % 6)
            if b in nodes and c in nodes:
                chord_ids.add(edge_id(key, b))
                chord_ids.add(edge_id(key, c))
                chord_ids.add(edge_id(b, c))

    def kept(key: str, dropped: set[str]) -> list[int]:
        return [
            d
            for d in nodes[key][2]
            if edge_id(key, neighbor_key(key, d)) not in dropped
        ]

    def result(dropped: set[str]) -> dict[str, list[int]]:
        return {key: kept(key, dropped) for key in nodes}

    if not chord_ids or len(chord_ids) > MAX_CHORD_CANDIDATES:
        return result(set())

    def component_signature(dropped: set[str]) -> str:
        seen: set[str] = set()
        labels: list[str] = []
        for start in nodes:
            if start in seen:
                continue
            member: list[str] = []
            stack = [start]
            seen.add(start)
            while stack:
                k = stack.pop()
                member.append(k)
                for d in nodes[k][2]:
                    nb = neighbor_key(k, d)
                    if nb not in seen and edge_id(k, nb) not in dropped:
                        seen.add(nb)
                        stack.append(nb)
            labels.append(",".join(sorted(member)))
        return ";".join(sorted(labels))

    # JS default sort / string `<` compare UTF-16 code units; for these ASCII keys
    # that is Python's ordinal str ordering.
    chords = sorted(chord_ids)
    affected: set[str] = set()
    for cid in chords:
        a, b = cid.split("|")
        affected.update((a, b))
    raw_signature = component_signature(set())

    best: set[str] = set()
    best_score = math.inf
    best_size = math.inf
    for mask in range(1 << len(chords)):
        dropped = {chords[i] for i in range(len(chords)) if mask & (1 << i)}
        if component_signature(dropped) != raw_signature:
            continue
        score = sum(shape_penalty(kept(key, dropped)) for key in affected)
        if score < best_score or (score == best_score and len(dropped) < best_size):
            best, best_score, best_size = dropped, score, len(dropped)
    return result(best)


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


def road_tile_refs(hexes: Iterable[Hex], hex_at: HexProbe) -> dict[str, RoadTileRef]:
    """``roadTiles.ts roadTileRefs``: baked-road refs for handled road masks."""
    refs: dict[str, RoadTileRef] = {}
    for h in hexes:
        if not h.road or h.terrain in RIVER_LIKE:
            continue
        oriented = oriented_river_tile(road_connected_edges(h.q, h.r, hex_at))
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
                members = {(int(o[0]), int(o[1])): d / f"{n}.png" for n, o in positions.items()}
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

    def footprint_tiles(self, h: Hex, kind: str, level: int) -> dict[tuple[int, int], Path]:
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
    hex_at: HexProbe,
) -> TileChoice:
    """``hexTile.ts hexTileRef``: river/bridge oriented art, baked road, cosmetic.

    ``hex_at`` is only used to label an unhandled road mask in the report.
    """
    key = _hex_key(h.q, h.r)
    primary = library.sets[0]
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
        edges = road_connected_edges(h.q, h.r, hex_at)
        road_notes.append(f"unhandled road {shape_label(edges)} -> vector road")

    group = library.terrain(h.terrain)
    if group is None:
        raise TileSetError(
            f"no tile set has any {h.terrain}/*.png (needed at col={h.col}, "
            f"row={h.row}); the frontend would crash here too"
        )
    if group.tile_set is not primary:
        road_notes.append(f"{h.terrain} from {group.tile_set.label}")
    path = group.files[variant_index(h.q, h.r, len(group.files))]
    return TileChoice(path, False, group, "terrain", h.terrain, tuple(road_notes))


def draws_vector_road(
    h: Hex,
    library: TileLibrary,
    river_course: dict[str, list[int]],
    road_tiles: dict[str, RoadTileRef],
) -> bool:
    """``Board.tsx drawTerrainOverlay``: does this hex get centre-to-edge road stubs?"""
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
            # Board.tsx scales uniformly by the texture *width* to 2*HEX_SIZE.
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
        """``Board.tsx drawTerrainBase``: the flat hex under each sprite."""
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
# A + d_i, A + d_(i+1); the vertex is A's corner at VERTEX_ANGLES[i] degrees).
JOIN_KINDS = {"edge_se": 0, "edge_ne": 1, "edge_n": 2, "vert_e": 3, "vert_ne": 4}
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
    game_map: GameMap, shown: Sequence[Hex], joins: JoinSet
) -> list[JoinChoice]:
    """Every same-terrain edge (pair) and vertex (triangle) touching a shown hex
    whose terrain has join art, in draw order: edges by (A.r, A.q, dir), then
    vertices by (A.r, A.q, i)."""
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
    return edges + vertices


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

    river_course = river_course_edges(
        ((h.q, h.r) for h in game_map.hexes), game_map.terrain_at
    )
    road_tiles = road_tile_refs(game_map.hexes, game_map.hex_at)

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
    site_choices: list[tuple[Hex, Path]] = []
    site_tiles: dict[tuple[int, int], Path] = {}
    if sites is not None:
        for (aq, ar), (kind, level) in game_map.sites.items():
            anchor = game_map.by_axial[(aq, ar)]
            for (dq, dr), tile in sites.footprint_tiles(anchor, kind, level).items():
                site_tiles[(aq + dq, ar + dr)] = tile
    for h, (x, y) in zip(shown, centres):
        choice = choose_tile(h, library, river_course, road_tiles, game_map.hex_at)
        choices.append((h, choice))
        path, mirror = choice.path, choice.mirror
        if (h.q, h.r) in site_tiles:
            path, mirror = site_tiles[(h.q, h.r)], False
            site_choices.append((h, path))
        canvas.composite(
            sprites.tile(path, mirror),
            *canvas.to_screen(x, y),
            terrain=True,
        )

    join_choices: list[JoinChoice] = []
    if joins is not None:
        join_choices = choose_joins(game_map, shown, joins)
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
        if not draw_vector_roads or not draws_vector_road(h, library, river_course, road_tiles):
            continue
        vector_roads.append(h)
        for dq, dr in AXIAL_DIRECTIONS:
            n = game_map.hex_at(h.q + dq, h.r + dr)
            if n is None or not _is_road_like(n):
                continue
            nx, ny = axial_to_pixel(n.q, n.r, HEX_SIZE)
            road_segments.append(((x, y), ((x + nx) / 2, (y + ny) / 2)))
    canvas.stroke(road_segments, ROAD_WIDTH, ROAD_COLOR, extend=False)

    return canvas.image(), RenderReport(choices, vector_roads, join_choices, site_choices)


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

    degraded = [(h, c) for h, c in report.choices if c.notes]
    vector = {(h.q, h.r) for h in report.vector_roads}
    lines.append("")
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
    args = parser.parse_args(argv)
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
