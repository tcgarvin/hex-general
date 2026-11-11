"""World state for the staff-map mocks: a season record at one turn, plus what
the map styles derive from it (province ownership, river and road courses).

The input is a record written by ``uv run python -m app.world.season`` (schema
in ``api/app/world/season.py``): the static map once, then per-turn deltas. A
`Snapshot` replays those deltas up to one turn. `Derived` holds what every style
shares: per-hex owners (from province anchors, as VISION §6 has it), smoothed
river and road polylines from the frontend's course graph (ported in
``scripts/board-mock.py``), and the unit stacks.
"""

from __future__ import annotations

import heapq
import importlib.util
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from .raster import AXIAL_DIRECTIONS, HexGrid

SCRIPTS = Path(__file__).resolve().parents[1]

TERRAIN_CHARS = ".wh~=tco"
PLAINS, WOODS, HILLS, RIVER, BRIDGE, TOWN, CITY, LAKE = range(8)
TERRAIN_NAMES = ("plains", "woods", "hills", "river", "bridge", "town", "city", "lake")
VACANT = -1  # a site holder code: a base slot nobody has founded
NOBODY = (
    0  # a site holder code: unheld (garrisoned or empty); also the neutral unit owner
)

ROAD_STEP = 1  # province travel cost onto a road hex (COLUMN_RULES.road_step_pulses)
OFFROAD_STEP = 2  # onto any other passable hex (offroad_step_pulses)


class RecordError(ValueError):
    """The season record is not what `load_snapshot` expects."""


@dataclass(frozen=True)
class Site:
    hex: int
    kind: str
    level: int
    garrison: int


@dataclass(frozen=True)
class Unit:
    id: int
    owner: int  # 0 neutral garrison, k + 1 for players[k]
    type: str
    hp: int
    column: bool
    hex: int


@dataclass(frozen=True)
class Snapshot:
    """The world at the end of one recorded turn."""

    width: int
    height: int
    terrain: np.ndarray  # uint8 codes, row-major
    road: np.ndarray  # bool
    sites: tuple[Site, ...]
    holders: np.ndarray  # per site: VACANT, NOBODY or k + 1
    players: tuple[str, ...]
    units: tuple[Unit, ...]
    fights: tuple[int, ...]  # hexes fought over this turn
    turn: int


def load_record(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("v") != 1:
        raise RecordError(f"{path}: not a version-1 season record")
    return data


def snapshot(record: dict[str, Any], turn: int | None = None) -> Snapshot:
    """Replay ``record``'s frames up to ``turn`` (default: the last)."""
    frames = record["frames"]
    last = len(frames) - 1 if turn is None else turn
    if not 0 <= last < len(frames):
        raise RecordError(f"turn {turn} not in the record (0..{len(frames) - 1})")
    m = record["map"]
    width, height = int(m["w"]), int(m["h"])
    terrain_text, road_text = m["terrain"], m["roads"]
    if len(terrain_text) != width * height or len(road_text) != width * height:
        raise RecordError("map strings do not match w * h")
    lookup = np.zeros(256, dtype=np.uint8)
    for code, char in enumerate(TERRAIN_CHARS):
        lookup[ord(char)] = code
    raw = np.frombuffer(terrain_text.encode("ascii"), dtype=np.uint8)
    unknown = set(terrain_text) - set(TERRAIN_CHARS)
    if unknown:
        raise RecordError(f"unknown terrain chars {sorted(unknown)}")
    terrain = lookup[raw]
    road = np.frombuffer(road_text.encode("ascii"), dtype=np.uint8) == ord("#")
    kinds = record["site_kinds"]
    flat = m["sites"]
    sites = tuple(
        Site(flat[k], kinds[flat[k + 1]], flat[k + 2], flat[k + 3])
        for k in range(0, len(flat), 4)
    )
    holders = np.full(len(sites), VACANT, dtype=np.int64)
    units: dict[int, tuple[int, ...]] = {}
    types = record["unit_types"]
    for frame in frames[: last + 1]:
        u = frame["u"]
        for k in range(0, len(u), 6):
            units[u[k]] = tuple(u[k : k + 6])
        for uid in frame["x"]:
            del units[uid]
        s = frame["s"]
        for k in range(0, len(s), 2):
            holders[s[k]] = s[k + 1]
    fights = tuple(sorted({e[1] for e in frames[last]["e"] if e[0] == 0 or e[0] == 1}))
    return Snapshot(
        width=width,
        height=height,
        terrain=terrain,
        road=road,
        sites=sites,
        holders=holders,
        players=tuple(record["players"]),
        units=tuple(
            Unit(uid, row[1], types[row[2]], row[3], bool(row[4] & 1), row[5])
            for uid, row in sorted(units.items())
        ),
        fights=fights,
        turn=int(frames[last]["t"]),
    )


# --- derived ------------------------------------------------------------------------------


def _board_mock() -> ModuleType:
    """``scripts/board-mock.py`` (the frontend course-graph port), imported once."""
    name = "board_mock_for_staff_map"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "board-mock.py")
    if spec is None or spec.loader is None:
        raise ImportError("cannot load scripts/board-mock.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def board_mock() -> ModuleType:
    return _board_mock()


@dataclass(frozen=True)
class Stack:
    """The units on one hex, as one marker."""

    hex: int
    owner: int
    lead: str  # type of the first deployed unit (else the first column)
    count: int
    hp: int
    column: bool  # every unit here is in column


@dataclass(frozen=True)
class Army:
    """Stacks of one owner within a few hexes of each other (far-zoom markers)."""

    owner: int
    x: float
    y: float
    count: int
    lead: str


@dataclass(frozen=True)
class Derived:
    grid: HexGrid
    owner: np.ndarray  # per hex: 0 nobody, k + 1 player (water takes its banks' owner)
    rivers: tuple[np.ndarray, ...]  # smoothed polylines, world units
    roads: tuple[np.ndarray, ...]
    bridges: tuple[tuple[float, float, float], ...]  # x, y, road angle (radians)
    stacks: tuple[Stack, ...]  # player stacks
    garrisons: dict[int, int]  # hex -> neutral units there
    armies: tuple[Army, ...]
    site_at: dict[int, int]  # hex -> site position


def derive(snap: Snapshot) -> Derived:
    grid = HexGrid(snap.width, snap.height)
    rivers, roads, bridges = courses(snap, grid)
    stacks, garrisons = unit_stacks(snap)
    return Derived(
        grid=grid,
        owner=hex_owners(snap, grid),
        rivers=rivers,
        roads=roads,
        bridges=bridges,
        stacks=stacks,
        garrisons=garrisons,
        armies=armies(stacks, grid),
        site_at={s.hex: k for k, s in enumerate(snap.sites)},
    )


def hex_owners(snap: Snapshot, grid: HexGrid) -> np.ndarray:
    """Each hex's owner: the holder of its province's anchor site.

    Provinces are re-derived as the generator builds them (``generate.py``): the
    nearest site by summed entry cost over passable hexes, road 1, off road 2.
    The generator also seeds every base slot first; here the sites carry the base
    slots already, so only tie order may differ. A water hex takes the owner of
    its land neighbours when they agree, so a river inside one player's land
    does not cut their tint in two.
    """
    t = snap.terrain
    water = (t == RIVER) | (t == LAKE)
    passable = ~water | snap.road
    cost = np.where(snap.road, ROAD_STEP, OFFROAD_STEP)
    neighbors = grid.neighbors.tolist()
    size = grid.size
    province = [-1] * size
    best = [math.inf] * size
    heap: list[tuple[float, int, int]] = []
    for k, site in enumerate(snap.sites):
        best[site.hex] = 0
        heapq.heappush(heap, (0, k, site.hex))
    passable_l = passable.tolist()
    cost_l = cost.tolist()
    while heap:
        g, k, u = heapq.heappop(heap)
        if province[u] != -1:
            continue
        province[u] = k
        for v in neighbors[u]:
            if v == size or not passable_l[v] or province[v] != -1:
                continue
            ng = g + cost_l[v]
            if ng < best[v]:
                best[v] = ng
                heapq.heappush(heap, (ng, k, v))
    prov = np.array(province, dtype=np.int64)
    holder = np.maximum(snap.holders, 0)
    owner = np.where(prov >= 0, holder[np.maximum(prov, 0)], 0)
    padded = grid.pad(owner, -1)
    near = padded[grid.neighbors]
    land_near = np.where(grid.pad(water, True)[grid.neighbors], -1, near)
    for i in np.flatnonzero(water):
        values = {int(v) for v in land_near[i] if v >= 0}
        if len(values) == 1:
            owner[i] = values.pop()
    return owner


def _chains(adjacency: dict[Any, set[Any]]) -> list[list[Any]]:
    """Split a graph into maximal paths between nodes of degree other than 2
    (cycles come out as closed paths)."""
    seen: set[frozenset[Any]] = set()
    out: list[list[Any]] = []

    def walk(start: Any, nxt: Any) -> list[Any]:
        path = [start, nxt]
        seen.add(frozenset((start, nxt)))
        prev, cur = start, nxt
        while len(adjacency[cur]) == 2:
            a, b = sorted(adjacency[cur])
            step = b if a == prev else a
            edge = frozenset((cur, step))
            if edge in seen:
                break
            seen.add(edge)
            path.append(step)
            prev, cur = cur, step
        return path

    for node in sorted(adjacency):
        if len(adjacency[node]) != 2:
            for nb in sorted(adjacency[node]):
                if frozenset((node, nb)) not in seen:
                    out.append(walk(node, nb))
    for node in sorted(adjacency):
        for nb in sorted(adjacency[node]):
            if frozenset((node, nb)) not in seen:
                out.append(walk(node, nb))
    return out


def chaikin(points: np.ndarray, passes: int) -> np.ndarray:
    """Corner-cutting smoothing with both ends kept."""
    closed = len(points) > 2 and np.allclose(points[0], points[-1])
    for _ in range(passes):
        if len(points) < 3:
            return points
        a, b = points[:-1], points[1:]
        q = 0.75 * a + 0.25 * b
        r = 0.25 * a + 0.75 * b
        mid = np.empty((2 * len(a), 2))
        mid[0::2], mid[1::2] = q, r
        if closed:
            points = np.vstack([mid, mid[:1]])
        else:
            points = np.vstack([points[:1], mid[1:-1], points[-1:]])
    return points


def _centre(q: int, r: int) -> tuple[float, float]:
    return 1.5 * q, math.sqrt(3.0) * (r + q / 2.0)


def _course_polylines(
    course: dict[str, list[int]], passes: int
) -> tuple[np.ndarray, ...]:
    """Smoothed polylines through a course graph (``"q,r"`` -> directions).

    A direction to a hex outside the graph (a lake mouth or a map-edge exit) is
    an end node at that neighbour's centre, so the line runs into the lake or
    off the map.
    """
    adjacency: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for key, dirs in course.items():
        q, r = (int(v) for v in key.split(","))
        for d in dirs:
            dq, dr = AXIAL_DIRECTIONS[d]
            nb = (q + dq, r + dr)
            adjacency.setdefault((q, r), set()).add(nb)
            adjacency.setdefault(nb, set()).add((q, r))
    out = []
    for chain in _chains(adjacency):
        pts = np.array([_centre(q, r) for q, r in chain], dtype=np.float64)
        out.append(chaikin(pts, passes))
    return tuple(out)


def courses(snap: Snapshot, grid: HexGrid) -> tuple[
    tuple[np.ndarray, ...],
    tuple[np.ndarray, ...],
    tuple[tuple[float, float, float], ...],
]:
    """River and road polylines from the frontend's course graphs, plus bridges."""
    bm = board_mock()
    rows = [
        "".join(
            TERRAIN_CHARS[c]
            for c in snap.terrain[r * snap.width : (r + 1) * snap.width]
        )
        for r in range(snap.height)
    ]
    road_rows = [
        "".join(
            "#" if v else "." for v in snap.road[r * snap.width : (r + 1) * snap.width]
        )
        for r in range(snap.height)
    ]
    game_map = bm.build_map("world", rows, road_rows)
    coords = [(h.q, h.r) for h in game_map.hexes]
    river = bm.river_course_edges(coords, game_map.terrain_at)
    road = bm.road_course_edges(game_map.hexes, game_map.hex_at)
    bridges = []
    for i in np.flatnonzero(snap.terrain == BRIDGE):
        q, r = int(grid.q[i]), int(grid.r[i])
        dirs = road.get(f"{q},{r}", [])
        x, y = _centre(q, r)
        if dirs:
            dq, dr = AXIAL_DIRECTIONS[dirs[0]]
            nx, ny = _centre(q + dq, r + dr)
            angle = math.atan2(ny - y, nx - x)
        else:
            angle = 0.0
        bridges.append((x, y, angle))
    return _course_polylines(river, 3), _course_polylines(road, 2), tuple(bridges)


def unit_stacks(snap: Snapshot) -> tuple[tuple[Stack, ...], dict[int, int]]:
    by_hex: dict[int, list[Unit]] = {}
    garrisons: dict[int, int] = {}
    for u in snap.units:
        if u.owner == NOBODY:
            garrisons[u.hex] = garrisons.get(u.hex, 0) + 1
        else:
            by_hex.setdefault(u.hex, []).append(u)
    stacks = []
    for hex_, units in sorted(by_hex.items()):
        lead = next((u for u in units if not u.column), units[0])
        stacks.append(
            Stack(
                hex=hex_,
                owner=lead.owner,
                lead=lead.type,
                count=len(units),
                hp=sum(u.hp for u in units),
                column=all(u.column for u in units),
            )
        )
    return tuple(stacks), garrisons


ARMY_REACH = 3  # hexes: stacks this close (chained) form one far-zoom army marker


def armies(stacks: tuple[Stack, ...], grid: HexGrid) -> tuple[Army, ...]:
    """Group each owner's stacks into armies by chained hex distance."""
    by_owner: dict[int, list[Stack]] = {}
    for s in stacks:
        by_owner.setdefault(s.owner, []).append(s)
    out = []
    for owner, group in sorted(by_owner.items()):
        n = len(group)
        parent = list(range(n))

        def find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for a in range(n):
            qa, ra = int(grid.q[group[a].hex]), int(grid.r[group[a].hex])
            for b in range(a + 1, n):
                qb, rb = int(grid.q[group[b].hex]), int(grid.r[group[b].hex])
                dq, dr = qa - qb, ra - rb
                if (abs(dq) + abs(dr) + abs(dq + dr)) // 2 <= ARMY_REACH:
                    parent[find(a)] = find(b)
        clusters: dict[int, list[Stack]] = {}
        for a in range(n):
            clusters.setdefault(find(a), []).append(group[a])
        for members in clusters.values():
            count = sum(s.count for s in members)
            x = sum(grid.cx[s.hex] * s.count for s in members) / count
            y = sum(grid.cy[s.hex] * s.count for s in members) / count
            types: dict[str, int] = {}
            for s in members:
                types[s.lead] = types.get(s.lead, 0) + s.count
            lead = max(sorted(types), key=lambda t: types[t])
            out.append(Army(owner, float(x), float(y), count, lead))
    return tuple(out)


# --- owner colours (port of frontend/src/world/colors.ts) ----------------------------

VIEWER_COLOR = 0x3D7EFF
NEUTRAL_COLOR = 0x4A4F57
VACANT_COLOR = 0xB8B2A2


def _fnv(text: str) -> int:
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _hsl(h: float, s: float, light: float) -> int:
    def k(n: float) -> float:
        return (n + h / 30.0) % 12.0

    a = s * min(light, 1.0 - light)

    def f(n: float) -> float:
        return light - a * max(-1.0, min(k(n) - 3.0, min(9.0 - k(n), 1.0)))

    def to(x: float) -> int:
        return int(math.floor(x * 255.0 + 0.5))

    return (to(f(0)) << 16) | (to(f(8)) << 8) | to(f(4))


def owner_color(owner: str | None, viewer: str) -> int:
    """``colors.ts ownerColor``: the viewer blue, neutral slate, else a hashed hue."""
    if owner is None:
        return VACANT_COLOR
    if owner == viewer:
        return VIEWER_COLOR
    if owner == "neutral":
        return NEUTRAL_COLOR
    hue = _fnv(owner) % 300
    if hue >= 190:
        hue += 60
    lightness = 0.45 + ((_fnv(f"{owner}:l") % 3) - 1) * 0.07
    return _hsl(float(hue), 0.62, lightness)


def owner_palette(snap: Snapshot, viewer: str) -> dict[int, int]:
    """Colour per owner code (0 = neutral garrison)."""
    out = {NOBODY: NEUTRAL_COLOR}
    for k, player in enumerate(snap.players):
        out[k + 1] = owner_color(player, viewer)
    return out
