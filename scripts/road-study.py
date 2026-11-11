#!/usr/bin/env python3
"""Compare the world generator's road styles (``direct`` vs ``natural``) on real seeds.

The generator (``api/app/world/generate.py``) can lay roads two ways
(``WorldParams.road_style``). This tool generates the same seed in both styles
and produces the review material for choosing between them:

* ``metrics``: a table of road shape and travel numbers per seed and style
  (``app.world.road_metrics``), plus a **board check** of how the frontend would
  draw every road hex (the same tile-selection port ``scripts/board-mock.py``
  uses): road hexes with no road art (unhandled masks, woods), and bridges whose
  road does not run along the bridge tile's baked ramps.
* ``images``: per seed and style, the whole-world road net (``app.world.preview``
  natural style), a regional crop, and a board-scale render of the same area with
  the real tiles (``board-mock.py``), on a window chosen to hold a river
  crossing, a road over hills and a town junction.
* ``page``: bundle the ``images`` output and the ``metrics`` JSON into one
  self-contained review page (``scripts/review-page.py``) with the metrics
  table and notes.
* ``export``: write a window of a generated world as a board-mock ASCII map
  (``[terrain]``, ``[roads]``, ``[sites]``), for ``scripts/board-mock-maps/``.

Runs on the system python3 (numpy and Pillow, plus pydantic for the api models)
with the api package on the path; it adds ``api/`` itself.

Usage:
    python3 scripts/road-study.py metrics --slots 200 --seeds 1,2,3 --out m.json
    python3 scripts/road-study.py images --slots 200 --seeds 1,2,3 --out-dir DIR
    python3 scripts/road-study.py export --slots 200 --seed 1 --style natural \\
        --window 100,40,130,60 --out scripts/board-mock-maps/roads-s1.txt
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from PIL import Image, ImageDraw

SCRIPT_DIR = Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO / "api"))

from app.engine.economy_rules import SiteKind  # noqa: E402
from app.engine.stats import Terrain  # noqa: E402
from app.world.generate import (  # noqa: E402
    ROAD_STYLES,
    GeneratedWorld,
    WorldParams,
    generate,
)
from app.world.grid import HexGrid  # noqa: E402
from app.world.preview import render as preview_render  # noqa: E402
from app.world.road_metrics import measure  # noqa: E402

TILES = REPO / "frontend" / "src" / "board" / "tiles"
JOINS = REPO / "frontend" / "src" / "board" / "joins"
SITE_TILES = REPO / "art" / "codex" / "terrain" / "candidates" / "sites-ratified"

TERRAIN_CHAR: dict[Terrain, str] = {
    Terrain.PLAINS: ".",
    Terrain.WOODS: "w",
    Terrain.HILLS: "h",
    Terrain.RIVER: "~",
    Terrain.BRIDGE: "=",
    Terrain.TOWN: "t",
    Terrain.CITY: "c",
    Terrain.LAKE: "o",
}

# The bridge tiles' baked road ramps, by the river course edges they are drawn
# for (frontend/src/board/tiles/bridge/: straight_v, turn_ne_nw, turn_se_sw; the
# mirrors are the same edge sets). A river shape not listed has no bridge tile
# and draws the generic 00 bridge, whose road meets no edge.
BRIDGE_RAMPS: dict[tuple[int, int], tuple[int, int]] = {
    (2, 5): (0, 4),
    (1, 3): (2, 5),
    (0, 4): (2, 5),
}

WINDOW = (34, 22)  # board window, hexes (cols x rows)
REGION = 110  # regional crop, hexes square
BOARD_HEX_PX = 64


def board_mock() -> ModuleType:
    """``scripts/board-mock.py`` as a module."""
    spec = importlib.util.spec_from_file_location("board_mock", SCRIPT_DIR / "board-mock.py")
    if spec is None or spec.loader is None:
        raise ImportError("cannot load scripts/board-mock.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["board_mock"] = module
    spec.loader.exec_module(module)
    return module


BM = board_mock()


# --- Board check -------------------------------------------------------------


def to_board_map(world: GeneratedWorld, window: tuple[int, int, int, int] | None = None,
                 sites: bool = False) -> object:
    """The world (or a ``window`` c0,r0,c1,r1 of it, inclusive) as a board-mock map."""
    return BM.parse_ascii(ascii_map(world, window, sites), "world")


def ascii_map(world: GeneratedWorld, window: tuple[int, int, int, int] | None = None,
              sites: bool = False, comment: str = "") -> str:
    """Board-mock ASCII map text for the world or a window of it.

    A window keeps odd-q column parity (its left column is even), so axial
    adjacency in the piece matches the world. ``sites`` adds a ``[sites]`` list:
    every site but cities (city tiles are terrain), bases at stage 1.
    """
    game_map = world.game_map
    width = game_map.width
    c0, r0, c1, r1 = window or (0, 0, width - 1, game_map.height - 1)
    if c0 % 2:
        raise ValueError(f"window must start on an even column, got {c0}")
    hexes = game_map.hexes
    rows, roads = [], []
    for row in range(r0, r1 + 1):
        line = hexes[row * width + c0 : row * width + c1 + 1]
        rows.append("".join(TERRAIN_CHAR[h.terrain] for h in line))
        roads.append("".join("#" if h.road else "." for h in line))
    text = ""
    if comment:
        text += "".join(f"; {c}\n" for c in comment.splitlines())
    text += "[terrain]\n" + "\n".join(rows) + "\n[roads]\n" + "\n".join(roads) + "\n"
    if sites:
        listed = []
        for site in game_map.sites:
            col = site.coord.q
            row = site.coord.r + (col - (col & 1)) // 2
            if not (c0 <= col <= c1 and r0 <= row <= r1) or site.kind is SiteKind.CITY:
                continue
            level = 1 if site.kind is SiteKind.BASE else site.level
            listed.append(f"{col - c0},{row - r0} {site.kind.value} {level}")
        text += "[sites]\n" + "\n".join(listed) + "\n"
    return text


@dataclass(frozen=True)
class BoardCheck:
    """How the frontend's tile rules would draw a world's roads."""

    road_tiles: int  # road hexes drawn with a baked road tile
    unhandled: int  # road hexes on plains/hills whose course mask has no tile
    on_woods: int  # road hexes on woods (no woods road art: the road vanishes)
    bridges: int
    bridges_untiled: int  # river shape without bridge art (generic 00 tile)
    bridges_off_ramps: int  # road meets the bridge away from its baked ramps

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


def board_check(world: GeneratedWorld) -> BoardCheck:
    """Run the frontend's road and river tile selection over the whole world."""
    bm_map = to_board_map(world)
    coords = [(h.q, h.r) for h in bm_map.hexes]
    road_course = BM.road_course_edges(bm_map.hexes, bm_map.hex_at)
    river_course = BM.river_course_edges(coords, bm_map.terrain_at)
    refs = BM.road_tile_refs(bm_map.hexes, road_course)
    tiles = unhandled = woods = bridges = untiled = off = 0
    for h in bm_map.hexes:
        key = f"{h.q},{h.r}"
        if h.terrain == "bridge":
            bridges += 1
            shape = tuple(river_course.get(key, []))
            ramps = BRIDGE_RAMPS.get(shape) if len(shape) == 2 else None
            if ramps is None:
                untiled += 1
                continue
            if tuple(BM.road_connected_edges(h.q, h.r, bm_map.hex_at)) != ramps:
                off += 1
            continue
        if not h.road or h.terrain in ("town", "city"):
            continue
        if h.terrain == "woods":
            woods += 1
        elif key in refs:
            tiles += 1
        else:
            unhandled += 1
    return BoardCheck(tiles, unhandled, woods, bridges, untiled, off)


# --- Metrics -----------------------------------------------------------------


def study(slots: int, seed: int, style: str) -> tuple[GeneratedWorld, dict[str, float]]:
    """Generate one world and measure it (metrics, board check, generation time)."""
    start = time.perf_counter()
    world = generate(WorldParams.for_slots(slots, seed, road_style=style))
    seconds = time.perf_counter() - start
    row: dict[str, float] = {"generate_s": round(seconds, 1)}
    row.update(measure(world).as_dict())
    row.update(board_check(world).as_dict())
    row["natural_road_fallbacks"] = world.stats.get("natural_road_fallbacks", 0)
    return world, row


def cmd_metrics(args: argparse.Namespace) -> int:
    table: dict[str, dict[str, dict[str, float]]] = {}
    for seed in _ints(args.seeds):
        for style in args.styles.split(","):
            _, row = study(args.slots, seed, style)
            table.setdefault(str(seed), {})[style] = row
            print(f"seed {seed} {style}: {json.dumps(row)}", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(table, indent=1))
    return 0


# --- Images ------------------------------------------------------------------


def choose_window(worlds: Sequence[GeneratedWorld]) -> tuple[int, int, int, int]:
    """A board window (even left column) that holds, in every one of ``worlds``
    (they share land and towns), a river crossing, road over hills and a town
    where three or more roads meet: the window maximising the product of the
    capped counts of the three, summed over the worlds."""
    first = worlds[0].game_map
    width, height = first.width, first.height
    grid = HexGrid(width, height)
    kinds = ("bridge", "hills", "junction")
    marks = {kind: [[0] * grid.size for _ in worlds] for kind in kinds}
    for w, world in enumerate(worlds):
        hexes = world.game_map.hexes
        for i, h in enumerate(hexes):
            if h.terrain is Terrain.BRIDGE:
                marks["bridge"][w][i] = 1
            elif h.road and h.terrain is Terrain.HILLS:
                marks["hills"][w][i] = 1
            elif h.terrain in (Terrain.TOWN, Terrain.CITY):
                if sum(hexes[n].road for n in grid.neighbors[i]) >= 3:
                    marks["junction"][w][i] = 1
    caps = {"bridge": 3, "hills": 10, "junction": 3}
    wc, wr = WINDOW
    best: tuple[float, int, int] = (-1.0, 0, 0)
    margin, inset = 8, 3
    for r0 in range(margin, height - wr - margin, 2):
        for c0 in range(margin, width - wc - margin, 2):
            score = 0.0
            for w in range(len(worlds)):
                product = 1.0
                for kind in kinds:
                    count = 0
                    for row in range(r0 + inset, r0 + wr - inset):
                        base = row * width
                        count += sum(marks[kind][w][base + c0 + inset : base + c0 + wc - inset])
                    product *= 1 + min(count, caps[kind])
                score += product
            if score > best[0]:
                best = (score, c0, r0)
    _, c0, r0 = best
    return c0, r0, c0 + wc - 1, r0 + wr - 1


def board_image(world: GeneratedWorld, window: tuple[int, int, int, int]) -> Image.Image:
    """The window at board scale with the real tiles, joins and site tiles (the
    world renderer's rules: no vector roads). Selection sees a margin around it."""
    c0, r0, c1, r1 = window
    pad = 6
    outer = (max(0, c0 - pad), max(0, r0 - pad),
             min(world.game_map.width - 1, c1 + pad),
             min(world.game_map.height - 1, r1 + pad))
    piece = BM.parse_ascii(ascii_map(world, outer, sites=True), "window")
    crop = (c0 - outer[0], r0 - outer[1], c1 - outer[0], r1 - outer[1])
    library = BM.TileLibrary(BM.TileSet.discover(TILES, "tiles"), None)
    image, _ = BM.render(
        piece, library, BOARD_HEX_PX, False, crop,
        joins=BM.JoinSet.load(JOINS), draw_vector_roads=False,
        sites=BM.SiteSet.discover(SITE_TILES),
    )
    return image.convert("RGB")


def world_image(world: GeneratedWorld, scale: int) -> Image.Image:
    return Image.open(io.BytesIO(preview_render(world, scale=scale, style="natural"))).convert("RGB")


def outline(image: Image.Image, box: tuple[int, int, int, int], scale: int) -> Image.Image:
    """``image`` (a preview at ``scale`` px per hex) with ``box`` (cols/rows) outlined."""
    out = image.copy()
    c0, r0, c1, r1 = box
    ImageDraw.Draw(out).rectangle(
        (c0 * scale, r0 * scale, (c1 + 1) * scale, (r1 + 1) * scale),
        outline=(230, 30, 30), width=max(2, scale),
    )
    return out


def cmd_images(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    styles = args.styles.split(",")
    windows: dict[str, list[int]] = {}
    for seed in _ints(args.seeds):
        worlds = [generate(WorldParams.for_slots(args.slots, seed, road_style=s)) for s in styles]
        window = choose_window(worlds)
        windows[str(seed)] = list(window)
        c0, r0, c1, r1 = window
        cx, cy = (c0 + c1) // 2, (r0 + r1) // 2
        width, height = worlds[0].game_map.width, worlds[0].game_map.height
        half = REGION // 2
        region = (max(0, cx - half), max(0, cy - half),
                  min(width - 1, cx + half), min(height - 1, cy + half))
        for style, world in zip(styles, worlds, strict=True):
            stem = out_dir / f"s{seed}-{style}"
            whole = world_image(world, 3)
            outline(whole, region, 3).save(f"{stem}-world.png")
            big = world_image(world, 8)
            rc0, rr0, rc1, rr1 = region
            crop = big.crop((rc0 * 8, rr0 * 8, (rc1 + 1) * 8, (rr1 + 1) * 8))
            outline(crop, (c0 - rc0, r0 - rr0, c1 - rc0, r1 - rr0), 8).save(f"{stem}-region.png")
            board_image(world, window).save(f"{stem}-board.png")
            (out_dir / f"s{seed}-{style}-window.txt").write_text(
                ascii_map(world, window, sites=True,
                          comment=f"seed {seed}, {args.slots} slots, road_style={style}, "
                          f"cols {c0}-{c1} rows {r0}-{r1}")
            )
            print(f"seed {seed} {style}: window {window}, region {region}", flush=True)
    (out_dir / "windows.json").write_text(json.dumps(windows))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    world = generate(WorldParams.for_slots(args.slots, args.seed, road_style=args.style))
    window = tuple(_ints(args.window))
    if len(window) != 4:
        raise SystemExit("--window takes c0,r0,c1,r1")
    text = ascii_map(world, window, sites=True,  # type: ignore[arg-type]
                     comment=f"generated world seed {args.seed} ({args.slots} slots), "
                     f"road_style={args.style}, cols {window[0]}-{window[2]} "
                     f"rows {window[1]}-{window[3]}")
    Path(args.out).write_text(text)
    print(f"wrote {args.out}")
    return 0


# Rows of the review page's metrics table: (metrics key, label, format).
TABLE_ROWS: tuple[tuple[str, str, str], ...] = (
    ("road_hexes", "road hexes", "{:.0f}"),
    ("bridges", "bridges", "{:.0f}"),
    ("long_run_share", "road hexes on straight axis runs over 8", "{:.1%}"),
    ("run_p90", "straight run length, 90th percentile", "{:.0f}"),
    ("run_max", "longest straight run", "{:.0f}"),
    ("turns_gentle", "two-way road hexes that turn 60 degrees", "{:.0%}"),
    ("detour_median", "town to nearby town by road / straight, median", "{:.2f}"),
    ("detour_p90", "same, 90th percentile", "{:.2f}"),
    ("road_on_woods", "road on woods (land: about 15%)", "{:.1%}"),
    ("road_on_hills", "road on hills (land: about 7%)", "{:.1%}"),
    ("base_to_base_median", "base to neighbouring base, pulses, median", "{:.0f}"),
    ("base_to_base_max", "base to neighbouring base, pulses, worst", "{:.0f}"),
    ("base_to_site_median", "base to own sites, pulses, median", "{:.0f}"),
    ("base_to_site_max", "base to own sites, pulses, worst", "{:.0f}"),
    ("unhandled", "road hexes with no road tile for their shape", "{:.0f}"),
    ("on_woods", "road hexes in woods (no woods road art)", "{:.0f}"),
    ("bridges_untiled", "bridges on a river shape with no bridge art", "{:.0f}"),
    ("bridges_off_ramps", "bridges whose road misses the baked ramps", "{:.0f}"),
    ("natural_road_fallbacks", "natural roads that fell back to looser rules", "{:.0f}"),
)


def metrics_table(table: dict[str, dict[str, dict[str, float]]]) -> str:
    """The metrics as an HTML table: one row per measure, before/after per seed."""
    seeds = list(table)
    head = "".join(
        f'<th colspan="2">seed {seed}</th>' for seed in seeds
    )
    sub = "".join("<th>direct</th><th>natural</th>" for _ in seeds)
    rows = []
    for key, label, fmt in TABLE_ROWS:
        cells = "".join(
            f"<td>{fmt.format(table[seed][style][key])}</td>"
            for seed in seeds
            for style in ("direct", "natural")
        )
        rows.append(f"<tr><th class=\"label\">{label}</th>{cells}</tr>")
    style = (
        "<style>.metrics{border-collapse:collapse;font-size:0.85rem;margin:1rem 0 2rem;"
        "width:100%}.metrics th,.metrics td{border:1px solid var(--rp-border);"
        "padding:0.25rem 0.5rem;text-align:right}.metrics th.label{text-align:left;"
        "font-weight:normal}.metrics-wrap{overflow-x:auto}</style>"
    )
    return (
        f'{style}<div class="metrics-wrap"><table class="metrics"><tr><th></th>{head}</tr>'
        f"<tr><th></th>{sub}</tr>{''.join(rows)}</table></div>"
    )


def side_by_side(left: Path, right: Path, out: Path) -> None:
    """Two images next to each other on a white gutter."""
    a, b = Image.open(left).convert("RGB"), Image.open(right).convert("RGB")
    canvas = Image.new("RGB", (a.width + b.width + 12, max(a.height, b.height)), (255, 255, 255))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width + 12, 0))
    canvas.save(out)


def cmd_page(args: argparse.Namespace) -> int:
    """Build the review page (scripts/review-page.py) from ``images`` output and
    ``metrics`` JSON, then add the metrics table and section notes."""
    import html
    import subprocess

    img = Path(args.img_dir)
    table = json.loads(Path(args.metrics).read_text())
    notes = json.loads(Path(args.notes).read_text())
    argv = [sys.executable, str(SCRIPT_DIR / "review-page.py"), "--title", args.title,
            "--out", args.out, "--intro", notes["intro"]]
    for seed in _ints(args.seeds):
        pair = img / f"s{seed}-region-pair.png"
        side_by_side(img / f"s{seed}-direct-region.png", img / f"s{seed}-natural-region.png", pair)
        argv += ["--section", f"Seed {seed}: whole world",
                 f"Before (direct)={img / f's{seed}-direct-world.png'}",
                 f"After (natural)={img / f's{seed}-natural-world.png'}",
                 "--section", f"Seed {seed}: region",
                 f"Before (direct) left, after (natural) right={pair}",
                 "--section", f"Seed {seed}: board scale, real tiles",
                 f"Before (direct)={img / f's{seed}-direct-board.png'}",
                 f"After (natural)={img / f's{seed}-natural-board.png'}"]
    subprocess.run(argv, check=True)
    page = Path(args.out).read_text()
    page = page.replace(" &mdash; shown at", ", shown at")
    marker = "</p>"
    at = page.index(marker) + len(marker)
    extra = f"<h2>Metrics (200 slots)</h2>{metrics_table(table)}"
    for heading, text in notes.get("before_images", []):
        extra += f"<h2>{html.escape(heading)}</h2>" + "".join(
            f"<p>{html.escape(par)}</p>" for par in text.split("\n\n")
        )
    page = page[:at] + extra + page[at:]
    for section, text in notes.get("sections", {}).items():
        head = f"<h2>{html.escape(section, quote=True)}</h2>"
        if page.count(head) != 1:
            raise SystemExit(f"section heading not found exactly once: {section!r}")
        page = page.replace(head, f'{head}\n<p class="intro">{html.escape(text)}</p>')
    Path(args.out).write_text(page)
    print(f"wrote {args.out} ({len(page.encode()) / 1e6:.1f} MB)")
    return 0


def _ints(text: str) -> list[int]:
    return [int(v) for v in text.split(",")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("metrics", help="metrics table per seed and style")
    m.add_argument("--slots", type=int, default=200)
    m.add_argument("--seeds", default="1,2,3")
    m.add_argument("--styles", default=",".join(ROAD_STYLES))
    m.add_argument("--out")
    m.set_defaults(func=cmd_metrics)
    i = sub.add_parser("images", help="world, region and board images per seed and style")
    i.add_argument("--slots", type=int, default=200)
    i.add_argument("--seeds", default="1,2,3")
    i.add_argument("--styles", default=",".join(ROAD_STYLES))
    i.add_argument("--out-dir", required=True)
    i.set_defaults(func=cmd_images)
    e = sub.add_parser("export", help="a window of a generated world as a board-mock map")
    e.add_argument("--slots", type=int, default=200)
    e.add_argument("--seed", type=int, default=1)
    e.add_argument("--style", choices=ROAD_STYLES, default="natural")
    e.add_argument("--window", required=True, help="c0,r0,c1,r1 (c0 even)")
    e.add_argument("--out", required=True)
    e.set_defaults(func=cmd_export)
    pg = sub.add_parser("page", help="the review page from images + metrics output")
    pg.add_argument("--img-dir", required=True)
    pg.add_argument("--metrics", required=True)
    pg.add_argument("--notes", required=True, help="JSON: intro, before_images, sections")
    pg.add_argument("--seeds", default="1,2,3")
    pg.add_argument("--title", default="Natural roads, round 1")
    pg.add_argument("--out", required=True)
    pg.set_defaults(func=cmd_page)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
