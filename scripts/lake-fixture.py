#!/usr/bin/env python3
"""Write the shared lake fixture the frontend's lake tests check themselves against.

``board-mock.py`` is the reference port of the frontend's tile and join selection.
For a few maps (the round 2 lake review map and small hand-made edge cases) this
renders each map through board-mock with the shipped frontend tiles and joins and
records, per lake hex, its neighbour mask, tile key and chosen file, and per river
mouth its direction, join code and chosen sprite, in board-mock's draw order.

Writes ``frontend/src/board/__fixtures__/lakes.json``; ``lakeTiles.test.ts`` runs
the TypeScript port over the same hexes and must agree, and
``scripts/tests/test_lake_fixture.py`` fails if the committed fixture is stale.

    python3 scripts/lake-fixture.py          # rewrite the fixture
    python3 scripts/lake-fixture.py --check  # exit 1 if it is stale
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
JOINS = REPO_ROOT / "frontend" / "src" / "board" / "joins"
OUT = REPO_ROOT / "frontend" / "src" / "board" / "__fixtures__" / "lakes.json"

# (name, ASCII map or path under scripts/board-mock-maps)
MAPS: list[tuple[str, str]] = [
    ("world-lake-s1", "scripts/board-mock-maps/world-lake-s1.txt"),
    # a lake filling the map: off-map neighbours count as land
    ("border", "ooo\nooo\nooo\nooo\n"),
    # rivers ending at a lake from several sides, a lone river hex on the shore,
    # and a river running past a lake (two river neighbours: no mouth)
    ("mouths", ".~...~.\n..o.o..\n.ooooo.\n~ooo.o~\n..o~...\n.~...~.\n.~.o.~.\n"),
    # a river flowing south into a lake, and a lone river hex with a lake NE of it
    ("mouths-s-ne", ".~....\n.~...o\n.o..~.\n"),
]


def load_board_mock() -> ModuleType:
    spec = importlib.util.spec_from_file_location("board_mock", BOARD_MOCK)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BOARD_MOCK}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["board_mock"] = module
    spec.loader.exec_module(module)
    return module


def map_record(bm: ModuleType, name: str, source: str) -> dict[str, Any]:
    """board-mock's lake tile and mouth choices for one map."""
    if source.endswith(".txt"):
        game_map = bm.load_map(REPO_ROOT / source)
    else:
        game_map = bm.parse_ascii(source, name)
    library = bm.TileLibrary(bm.TileSet.discover(TILES, "tiles"), None)
    if not library.has_lake_masks():
        raise RuntimeError(f"{TILES} has no mask-painted lake tiles")
    joins = bm.JoinSet.load(JOINS)
    _, report = bm.render(game_map, library, 16, False, None, joins=joins)
    if report.lake_missing:
        raise RuntimeError(f"{name}: lake masks without tiles: {report.lake_missing}")
    masks = bm.lake_masks([(h.q, h.r) for h in game_map.hexes], game_map.terrain_at)
    lakes = []
    for h, choice in report.choices:
        if h.terrain != bm.LAKE:
            continue
        mask = masks[f"{h.q},{h.r}"]
        lakes.append(
            {
                "q": h.q,
                "r": h.r,
                "mask": mask,
                "key": bm.lake_tile_key(mask),
                "file": choice.path.relative_to(TILES).as_posix(),
            }
        )
    mouths = [
        {
            "q": join.hexes[0].q,
            "r": join.hexes[0].r,
            "direction": join.code - bm.MOUTH_CODE_BASE,
            "code": join.code,
            "file": join.path.relative_to(JOINS).as_posix(),
        }
        for join in report.joins
        if join.code >= bm.MOUTH_CODE_BASE
    ]
    return {
        "name": name,
        "hexes": [[h.q, h.r, h.terrain] for h in game_map.hexes],
        "lakes": lakes,
        "mouths": mouths,
    }


def build() -> str:
    bm = load_board_mock()
    fixture = {
        "generated_by": "scripts/lake-fixture.py (board-mock.py over the shipped tiles and joins)",
        "maps": [map_record(bm, name, source) for name, source in MAPS],
    }
    return json.dumps(fixture, indent=1) + "\n"


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
                f"{OUT.relative_to(REPO_ROOT)} is stale; run scripts/lake-fixture.py",
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
