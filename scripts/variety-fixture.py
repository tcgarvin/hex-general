#!/usr/bin/env python3
"""Write the shared variety fixture the frontend's variety tests check against.

``board-mock.py``'s ``variety_picks`` is the reference rule; the frontend port is
``frontend/src/board/varietyTiles.ts``. For a few maps (the two variety review
windows, a whole small generated world, and a hand-made edge case) this records
each map's grids and sites plus, per decorated hex, the variety file key, kind,
size and the mirror flag it is drawn with, using the shipped tiles
(``frontend/src/board/tiles``) at the game's density and mix
(``frontend/src/board/varietySettings.json``). It also records the variety
catalog it saw.

Writes ``frontend/src/board/__fixtures__/variety.json``; ``varietyTiles.test.ts``
runs the TypeScript port over the same maps and must agree exactly, and
``scripts/tests/test_variety_fixture.py`` fails if the committed fixture is stale.

    python3 scripts/variety-fixture.py          # rewrite the fixture
    python3 scripts/variety-fixture.py --check  # exit 1 if it is stale
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BOARD_MOCK = REPO_ROOT / "scripts" / "board-mock.py"
TILES = REPO_ROOT / "frontend" / "src" / "board" / "tiles"
OUT = REPO_ROOT / "frontend" / "src" / "board" / "__fixtures__" / "variety.json"

# (name, ASCII map or path under scripts/board-mock-maps)
MAPS: list[tuple[str, str]] = [
    ("variety-open", "scripts/board-mock-maps/variety-open.txt"),
    ("variety-town", "scripts/board-mock-maps/variety-town.txt"),
    ("world-s3", "scripts/board-mock-maps/variety-world-s3.txt"),
    # dense edge case: towns, a bridge, a base and its footprint, a farm, roads
    # on every side, the map border
    (
        "edges",
        "[terrain]\n........\n..t.....\n........\n...~=~..\n........\n.....t..\n"
        "........\n........\n"
        "[roads]\n#.......\n###.....\n..#.....\n....#...\n.....#..\n.....##.\n"
        "......##\n.......#\n"
        "[sites]\n1,5 base 1\n6,2 farm 2\n",
    ),
]


def load_board_mock() -> ModuleType:
    spec = importlib.util.spec_from_file_location("board_mock", BOARD_MOCK)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BOARD_MOCK}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["board_mock"] = module
    spec.loader.exec_module(module)
    return module


def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"terrain": [], "roads": [], "sites": []}
    current = "terrain"
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
            continue
        out[current].append(line)
    return out


def catalog(bm: ModuleType, size: str) -> dict[str, Any] | None:
    vs = bm.VarietySet.from_tiles(TILES, size)
    if vs is None:
        return None
    return {
        "nature": [bm.variety_key(p) for p in vs.nature],
        "home": [bm.variety_key(p) for p in vs.home],
        "road": {s: [bm.variety_key(p) for p in f] for s, f in sorted(vs.road.items())},
    }


def map_record(bm: ModuleType, name: str, source: str) -> dict[str, Any]:
    text = (REPO_ROOT / source).read_text() if source.endswith(".txt") else source
    game_map = bm.parse_ascii(text, name)
    grids = _sections(text)
    course = bm.road_course_edges(game_map.hexes, game_map.hex_at)
    refs = bm.road_tile_refs(game_map.hexes, course)
    picks = bm.variety_picks(
        game_map,
        refs,
        bm.VarietySet.from_tiles(TILES, "small"),
        bm.VARIETY_DENSITIES[bm.VARIETY_DEFAULT_DENSITY],
        medium=bm.VarietySet.from_tiles(TILES, "medium"),
        medium_share=bm.VARIETY_MIXES[bm.VARIETY_DEFAULT_MIX],
    )
    rows = []
    for (q, r), pick in sorted(picks.items()):
        ref = refs.get(f"{q},{r}")
        rows.append(
            {
                "q": q,
                "r": r,
                "key": bm.variety_key(pick.path),
                "kind": pick.kind,
                "size": pick.size,
                "mirror": ref.mirror if pick.kind == "road" else False,
            }
        )
    sites = []
    for line in grids["sites"]:
        cell, kind, level = line.split()
        col, row = (int(v) for v in cell.split(","))
        sites.append({"col": col, "row": row, "kind": kind, "level": int(level)})
    return {
        "name": name,
        "terrain": grids["terrain"],
        "roads": grids["roads"],
        "sites": sites,
        "picks": rows,
    }


def build() -> str:
    bm = load_board_mock()
    data = {
        "about": "written by scripts/variety-fixture.py; checked by varietyTiles.test.ts",
        "settings": {
            "density": bm.VARIETY_DEFAULT_DENSITY,
            "mix": bm.VARIETY_DEFAULT_MIX,
            "repeatRadius": bm.VARIETY_REPEAT_RADIUS,
        },
        "catalog": {"small": catalog(bm, "small"), "medium": catalog(bm, "medium")},
        # spot values of the salted hash, for a direct bit-for-bit check
        "hashes": {
            "zero": bm.salted_index(0, 0, 11, 1000),
            "far": bm.salted_index(348, -173, 16, 1000),
            "negative": bm.salted_index(-5, 7, 14, 1000),
        },
        "maps": [map_record(bm, name, source) for name, source in MAPS],
    }
    return json.dumps(data, indent=1) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if the fixture is stale"
    )
    args = parser.parse_args(argv)
    text = build()
    if args.check:
        if not OUT.is_file() or OUT.read_text() != text:
            print(
                f"{OUT.relative_to(REPO_ROOT)} is stale; run scripts/variety-fixture.py",
                file=sys.stderr,
            )
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
