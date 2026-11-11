#!/usr/bin/env python3
"""Render style mocks of the far-zoom ("strategic") world map from a season record.

The world view swaps its painted hex tiles for a prerendered overview below
camera scale 0.4 (``frontend/src/world/WorldRenderer.ts``). VISION §10 proposes
replacing today's flat-colour overview with "a printed staff map with
grease-pencil front lines and pins". This tool draws the same world state in
several candidate styles at the zoom levels the game uses, so the pick can be
made from pictures. It is offline mock work: nothing here is wired into the app.

Styles (``scripts/staff_map/styles.py``):

    current          today's overview, reproduced (flat hex fills, dot roads, dots)
    staff-map        printed topographic staff map + grease pencil, pins, counters
    staff-map-night  the same linework on dark "ops room" paper
    painted-far      the painted tile palette seen from high up, clean overlays
    hybrid           painted ground with the staff map's ink, crayon and pins

Input is a season record from ``uv run python -m app.world.season`` (run from
``api/``). Every style is deterministic and drawn only from map data.

Usage:
    python3 scripts/staff-map-mock.py render SEASON.json --style staff-map \\
        --view region --out out.png [--center COL,ROW] [--hex-px 10] \\
        [--size 1400x900] [--turn N] [--viewer b012] [--grid km|hex|none]
    python3 scripts/staff-map-mock.py batch SEASON.json --out-dir .dev/staff-map \\
        [--styles current,staff-map,...] [--no-board]
    python3 scripts/staff-map-mock.py page .dev/staff-map/r1 --notes notes.json \
        --out .dev/review-pages/staff-map-r1.html [--max-bytes 12000000]

Views: ``world`` fits the whole map to ``--size`` width (a 349-column world is
~4 px per column, 5.3 px per hex point to point); ``region`` is 10 px per hex
(point-to-point width) at 1400x900; ``near`` is 22 px per hex at 1400x900;
``strip`` is 22 px per hex at 1400x500. Without ``--center`` the region, near and
strip views centre on the most contested window (fronts and fights). ``batch``
renders every style at every view plus a strip of the real painted board at
26 px per hex (``scripts/board-mock.py``: the frontend's tile rules and tiles)
and writes ``views.json`` with the centres and viewer it chose.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

from staff_map import page, styles  # noqa: E402
from staff_map.raster import SQRT3, View  # noqa: E402
from staff_map.world import (  # noqa: E402
    NOBODY,
    PLAINS,
    TERRAIN_CHARS,
    VACANT,
    Derived,
    Snapshot,
    board_mock,
    derive,
    load_record,
    owner_palette,
    snapshot,
)

REPO = Path(__file__).resolve().parents[1]
TILES = REPO / "frontend" / "src" / "board" / "tiles"
JOINS = REPO / "frontend" / "src" / "board" / "joins"
# The frontend copies of the site tiles lack board-mock's base positions.json; the
# ratified originals are byte-identical (scripts/tests/test_site_tiles_sync.py).
SITE_TILES = REPO / "art" / "codex" / "terrain" / "candidates" / "sites-ratified"

VIEWS = ("world", "region", "near", "strip")
DEFAULT_HEX_PX = {"region": 10.0, "near": 22.0, "strip": 22.0}
DEFAULT_SIZE = {
    "world": (1400, 0),
    "region": (1400, 900),
    "near": (1400, 900),
    "strip": (1400, 500),
}
BOARD_HEX_PX = 26  # just above the tile switch: 0.4 x 64 = 25.6 px per hex


@dataclass(frozen=True)
class Choice:
    """The windows a batch run centred on, and whose colour is the viewer's."""

    region: tuple[int, int]
    near: tuple[int, int]
    viewer: int


def world_view(snap: Snapshot, width: int) -> View:
    x0, y0, x1, y1 = (
        -1.0,
        -SQRT3 / 2,
        1.5 * (snap.width - 1) + 1.0,
        SQRT3 * (snap.height - 0.5) + SQRT3 / 2,
    )
    radius = width / (x1 - x0)
    return View(radius, x0, y0, width, int(math.ceil((y1 - y0) * radius)))


def centred_view(center: tuple[int, int], hex_px: float, size: tuple[int, int]) -> View:
    col, row = center
    cx, cy = 1.5 * col, SQRT3 * (row + 0.5 * (col & 1))
    radius = hex_px / 2.0
    w, h = size
    return View(radius, cx - w / 2 / radius, cy - h / 2 / radius, w, h)


def make_view(
    snap: Snapshot,
    kind: str,
    center: tuple[int, int] | None,
    hex_px: float | None,
    size: tuple[int, int] | None,
) -> View:
    if kind not in VIEWS:
        raise ValueError(f"unknown view {kind!r}; use one of {VIEWS}")
    w, h = size or DEFAULT_SIZE[kind]
    if kind == "world":
        return world_view(snap, w)
    if center is None:
        raise ValueError(f"the {kind} view needs a centre")
    return centred_view(center, hex_px or DEFAULT_HEX_PX[kind], (w, h))


def _contest(snap: Snapshot, derived: Derived) -> np.ndarray:
    """Per hex: 1 on a front (a player hex beside another player's), +3 per fight."""
    g = derived.grid
    owner = derived.owner
    nb = g.pad(owner, 0)[g.neighbors]
    front = (owner > 0) & ((nb > 0) & (nb != owner[:, None])).any(axis=1)
    score = front.astype(np.float64)
    for hex_ in snap.fights:
        score[hex_] += 3.0
    return score.reshape(snap.height, snap.width)


def _densest(
    score: np.ndarray,
    half_cols: int,
    half_rows: int,
    within: tuple[int, int, int, int] | None = None,
) -> tuple[int, int]:
    """Centre (col, row) of the window with the highest summed score."""
    h, w = score.shape
    sat = np.zeros((h + 1, w + 1))
    sat[1:, 1:] = score.cumsum(0).cumsum(1)
    c_lo, r_lo, c_hi, r_hi = within or (0, 0, w - 1, h - 1)
    c_lo, c_hi = max(c_lo, half_cols), min(c_hi, w - 1 - half_cols)
    r_lo, r_hi = max(r_lo, half_rows), min(r_hi, h - 1 - half_rows)
    best, at = -1.0, (w // 2, h // 2)
    for row in range(r_lo, r_hi + 1, 2):
        for col in range(c_lo, c_hi + 1, 2):
            a, b = row - half_rows, row + half_rows + 1
            c, d = col - half_cols, col + half_cols + 1
            total = sat[b, d] - sat[a, d] - sat[b, c] + sat[a, c]
            if total > best:
                best, at = total, (col, row)
    return at


def _half_extent(hex_px: float, size: tuple[int, int]) -> tuple[int, int]:
    r = hex_px / 2.0
    return int(size[0] / 2 / (1.5 * r)), int(size[1] / 2 / (SQRT3 * r))


def choose(snap: Snapshot, derived: Derived) -> Choice:
    score = _contest(snap, derived)
    hc, hr = _half_extent(DEFAULT_HEX_PX["region"], DEFAULT_SIZE["region"])
    region = _densest(score, hc, hr)
    nc, nr = _half_extent(DEFAULT_HEX_PX["near"], DEFAULT_SIZE["near"])
    near = _densest(
        score,
        nc,
        nr,
        (
            region[0] - hc + nc,
            region[1] - hr + nr,
            region[0] + hc - nc,
            region[1] + hr - nr,
        ),
    )
    owner = derived.owner.reshape(snap.height, snap.width)
    window = owner[
        max(0, near[1] - nr) : near[1] + nr + 1, max(0, near[0] - nc) : near[0] + nc + 1
    ]
    counts = np.bincount(window.ravel(), minlength=len(snap.players) + 1)
    counts[0] = 0
    # The viewer (drawn in the game's own-player blue) is whoever holds most of
    # the near window; the first player when nobody holds any of it.
    viewer = int(np.argmax(counts)) if counts.any() else 1
    return Choice(region, near, viewer)


# --- the real painted board (board-mock) --------------------------------------------------


def board_strip(
    snap: Snapshot, center: tuple[int, int], size: tuple[int, int], viewer: int
) -> Image.Image:
    """The painted board around ``center`` at ``BOARD_HEX_PX``, as the game draws it
    in tile mode: tiles, joins, site tiles, owner rings on held sites and stand-in
    unit discs (the game draws unit sprites there)."""
    bm = board_mock()
    radius = BOARD_HEX_PX / 2.0
    hc, hr = _half_extent(BOARD_HEX_PX, size)
    margin = 6
    c0 = max(0, center[0] - hc - margin)
    c0 -= c0 % 2  # keep odd-q column parity
    r0 = max(0, center[1] - hr - margin)
    c1 = min(snap.width - 1, center[0] + hc + margin)
    r1 = min(snap.height - 1, center[1] + hr + margin)
    rows, roads = [], []
    for row in range(r0, r1 + 1):
        base = row * snap.width
        rows.append(
            "".join(TERRAIN_CHARS[t] for t in snap.terrain[base + c0 : base + c1 + 1])
        )
        roads.append(
            "".join("#" if v else "." for v in snap.road[base + c0 : base + c1 + 1])
        )
    sites = []
    for k, site in enumerate(snap.sites):
        col, row = site.hex % snap.width, site.hex // snap.width
        if not (c0 <= col <= c1 and r0 <= row <= r1) or site.kind == "city":
            continue
        holder = int(snap.holders[k])
        if site.kind == "base" and holder == VACANT:
            continue  # a vacant slot keeps its terrain tile
        if snap.terrain[site.hex] != PLAINS or snap.road[site.hex]:
            continue
        level = 1 if site.kind == "base" else site.level
        sites.append(f"{col - c0},{row - r0} {site.kind} {level}")
    text = "[terrain]\n" + "\n".join(rows) + "\n[roads]\n" + "\n".join(roads)
    text += "\n[sites]\n" + "\n".join(sites) + "\n"
    game_map = bm.parse_ascii(text, "strip")
    library = bm.TileLibrary(bm.TileSet.discover(TILES, "tiles"), None)
    crop = (
        center[0] - hc - 2 - c0,
        center[1] - hr - 2 - r0,
        center[0] + hc + 2 - c0,
        center[1] + hr + 2 - r0,
    )
    crop = (
        max(0, crop[0]),
        max(0, crop[1]),
        min(c1 - c0, crop[2]),
        min(r1 - r0, crop[3]),
    )
    image, _ = bm.render(
        game_map,
        library,
        BOARD_HEX_PX,
        False,
        crop,
        joins=bm.JoinSet.load(JOINS),
        draw_vector_roads=False,
        sites=bm.SiteSet.discover(SITE_TILES),
    )
    # Board-mock's canvas origin: the shown centres' bbox minus one radius.
    hexes = [
        h
        for h in game_map.hexes
        if crop[0] <= h.col <= crop[2] and crop[1] <= h.row <= crop[3]
    ]
    min_x = min(1.5 * h.q for h in hexes) - 1.0
    min_y = min(SQRT3 * (h.r + h.q / 2.0) for h in hexes) - 1.0
    world_x0 = min_x + 1.5 * c0
    world_y0 = min_y + SQRT3 * r0
    view = centred_view(center, BOARD_HEX_PX, size)
    left = int(round((view.x0 - world_x0) * radius))
    top = int(round((view.y0 - world_y0) * radius))
    out = Image.new("RGB", size, (10, 10, 11))
    out.paste(image.convert("RGB"), (-left, -top))
    _board_overlay(out, snap, view, viewer)
    return out


def _board_overlay(image: Image.Image, snap: Snapshot, view: View, viewer: int) -> None:
    """Tile-mode markers: owner hex rings on sites (``drawSites``) and unit discs."""
    colors = owner_palette(snap, snap.players[viewer - 1])
    ss = 3
    layer = Image.new("RGBA", (image.width * ss, image.height * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    k_px = view.radius * ss

    def xy(hex_: int) -> tuple[float, float]:
        col, row = hex_ % snap.width, hex_ // snap.width
        return (
            (1.5 * col - view.x0) * k_px,
            (SQRT3 * (row + 0.5 * (col & 1)) - view.y0) * k_px,
        )

    for k, site in enumerate(snap.sites):
        holder = int(snap.holders[k])
        if holder == VACANT:
            continue
        x, y = xy(site.hex)
        color = 0xB8B2A2 if holder == NOBODY else colors[holder]
        r = 0.92 * k_px
        pts = [
            (x + r * math.cos(math.pi / 3 * i), y + r * math.sin(math.pi / 3 * i))
            for i in range(7)
        ]
        width = (4 if holder != NOBODY else 2) * view.radius / 32 * ss
        d.line(pts, fill=_rgba(color, 0.95), width=max(1, int(round(width))))
    by_hex: dict[int, int] = {}
    for u in snap.units:
        by_hex.setdefault(u.hex, u.owner)
    for hex_, owner in by_hex.items():
        x, y = xy(hex_)
        r = 0.42 * k_px
        d.ellipse(
            (x - r, y - r, x + r, y + r),
            fill=_rgba(colors[owner]),
            outline=(16, 17, 20, 255),
            width=max(1, int(r * 0.15)),
        )
    small = layer.resize(image.size, Image.LANCZOS)
    image.paste(small, (0, 0), small)


def _rgba(color: int, alpha: float = 1.0) -> tuple[int, int, int, int]:
    return ((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF, int(alpha * 255))


# --- CLI -----------------------------------------------------------------------------------


def _pair(text: str, sep: str) -> tuple[int, int]:
    a, b = text.split(sep)
    return int(a), int(b)


def _viewer_code(snap: Snapshot, viewer: str | None, fallback: int) -> int:
    if viewer is None:
        return fallback
    if viewer not in snap.players:
        raise SystemExit(f"--viewer {viewer!r} is not a player in the record")
    return snap.players.index(viewer) + 1


def cmd_render(args: argparse.Namespace) -> int:
    snap = snapshot(load_record(args.season), args.turn)
    derived = derive(snap)
    pick = choose(snap, derived)
    center = args.center or (pick.region if args.view == "region" else pick.near)
    view = make_view(snap, args.view, center, args.hex_px, args.size)
    scene = styles.make_scene(
        snap, derived, view, _viewer_code(snap, args.viewer, pick.viewer), args.grid
    )
    started = time.perf_counter()
    image = styles.render(args.style, scene)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out)
    print(
        f"{args.out}  {image.width}x{image.height}  {args.style} {args.view} "
        f"hex_px={view.hex_px:.2f} centre={center} ({time.perf_counter() - started:.1f} s)"
    )
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    snap = snapshot(load_record(args.season), args.turn)
    derived = derive(snap)
    pick = choose(snap, derived)
    viewer = _viewer_code(snap, args.viewer, pick.viewer)
    centres = {
        "world": None,
        "region": pick.region,
        "near": pick.near,
        "strip": pick.near,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    for style in args.styles:
        for kind in VIEWS:
            view = make_view(snap, kind, centres[kind], None, None)
            scene = styles.make_scene(snap, derived, view, viewer, args.grid)
            started = time.perf_counter()
            image = styles.render(style, scene)
            timings[f"{style}/{kind}"] = round(time.perf_counter() - started, 2)
            out = args.out_dir / f"{style}-{kind}.png"
            image.save(out)
            print(
                f"{out}  {image.width}x{image.height}  ({timings[f'{style}/{kind}']} s)"
            )
    if not args.no_board:
        board = board_strip(snap, pick.near, DEFAULT_SIZE["strip"], viewer)
        out = args.out_dir / "board-strip.png"
        board.save(out)
        print(f"{out}  {board.width}x{board.height}")
    meta = {
        "turn": snap.turn,
        "viewer": snap.players[viewer - 1],
        "region_centre": pick.region,
        "near_centre": pick.near,
        "render_seconds": timings,
    }
    (args.out_dir / "views.json").write_text(json.dumps(meta, indent=1))
    return 0


def cmd_page(args: argparse.Namespace) -> int:
    size = page.build(args.render_dir, args.out, args.notes, args.max_bytes)
    print(f"{args.out}  {size / 1e6:.2f} MB")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "season", type=Path, help="season record JSON (app.world.season)"
        )
        p.add_argument(
            "--turn", type=int, default=None, help="turn to draw (default: last)"
        )
        p.add_argument(
            "--viewer", default=None, help="player id drawn in the viewer's blue"
        )
        p.add_argument("--grid", choices=styles.GRID_MODES, default="km")

    render = sub.add_parser("render", help="render one style at one view")
    common(render)
    render.add_argument("--style", choices=styles.STYLES, required=True)
    render.add_argument("--view", choices=VIEWS, default="world")
    render.add_argument(
        "--center",
        type=lambda s: _pair(s, ","),
        default=None,
        help="COL,ROW offset cell to centre on",
    )
    render.add_argument(
        "--hex-px",
        type=float,
        default=None,
        help="point-to-point hex width in px (region/near/strip)",
    )
    render.add_argument(
        "--size", type=lambda s: _pair(s, "x"), default=None, help="WxH"
    )
    render.add_argument("--out", type=Path, required=True)
    render.set_defaults(func=cmd_render)

    batch = sub.add_parser(
        "batch", help="every style at every view, plus the board strip"
    )
    common(batch)
    batch.add_argument("--out-dir", type=Path, required=True)
    batch.add_argument(
        "--styles", type=lambda s: s.split(","), default=list(styles.STYLES)
    )
    batch.add_argument("--no-board", action="store_true")
    batch.set_defaults(func=cmd_batch)

    review = sub.add_parser("page", help="the review page for a batch directory")
    review.add_argument("render_dir", type=Path)
    review.add_argument(
        "--notes",
        type=Path,
        required=True,
        help='JSON: {"title", "intro", "sections": {name: [paragraph, ...]}}',
    )
    review.add_argument("--out", type=Path, required=True)
    review.add_argument("--max-bytes", type=int, default=12_000_000)
    review.set_defaults(func=cmd_page)

    args = parser.parse_args(argv)
    for style in getattr(args, "styles", []):
        if style not in styles.STYLES:
            parser.error(f"unknown style {style!r}; use {','.join(styles.STYLES)}")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
