"""frontend lake tiles and river-mouth sprites must match the ratified candidates.

Round 2 lake art was ratified 2026-11-10: the 86 mask tiles in
``art/codex/terrain/candidates/lake2/lake/`` and the 18 mouth sprites in
``art/codex/terrain/candidates/lake2-joins/lake/`` are copied byte for byte into
the frontend (provenance: ``art/codex/terrain/APPLIED.md``).
"""

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "art/codex/terrain/candidates"
# (ratified source dir, frontend copy dir)
PAIRS = [
    (CANDIDATES / "lake2/lake", ROOT / "frontend/src/board/tiles/lake"),
    (CANDIDATES / "lake2-joins/lake", ROOT / "frontend/src/board/joins/lake"),
]
PICKS = CANDIDATES / "lake2/picks.json"


def _names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def _cases() -> list[tuple[Path, Path]]:
    return [(src / name, dst / name) for src, dst in PAIRS for name in _names(src)]


@pytest.mark.parametrize(("source", "copy"), PAIRS, ids=["tiles", "mouths"])
def test_copy_has_exactly_the_ratified_files(source: Path, copy: Path) -> None:
    assert _names(copy) == _names(source)


@pytest.mark.parametrize(
    ("source", "copy"), _cases(), ids=lambda p: str(p.relative_to(ROOT))
)
def test_file_is_byte_identical(source: Path, copy: Path) -> None:
    assert copy.read_bytes() == source.read_bytes()


def test_every_mask_ships_and_matches_its_pick() -> None:
    picks = json.loads(PICKS.read_text())["picks"]
    tiles = ROOT / "frontend/src/board/tiles"
    assert sorted(p["file"] for p in picks) == sorted(
        f"lake/{name}" for name in _names(tiles / "lake")
    )
    assert {p["mask"] for p in picks} == set(range(64))
    for pick in picks:
        digest = hashlib.sha256((tiles / pick["file"]).read_bytes()).hexdigest()
        assert digest == pick["sha256"], pick["file"]
        assert pick["file"].startswith(f"lake/m{pick['mask']:02d}_")


def test_no_round_one_or_water_tile_shipped() -> None:
    tiles = ROOT / "frontend/src/board/tiles/lake"
    assert all(name.startswith("m") for name in _names(tiles))
    assert not (tiles / "w0.png").exists()
