"""frontend/src/world/siteTiles copies must match the ratified art source."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "art/codex/terrain/candidates/sites-ratified"
COPY = ROOT / "frontend/src/world/siteTiles"
# Mirrors BASE_FOOTPRINT in frontend/src/world/siteTiles.ts.
BASE_FOOTPRINT = [[0, 0], [1, 0], [0, 1], [1, 1]]


def _source_pngs() -> list[Path]:
    return sorted(p.relative_to(SOURCE) for p in SOURCE.rglob("*.png"))


def test_copy_has_exactly_the_ratified_pngs() -> None:
    assert sorted(p.relative_to(COPY) for p in COPY.rglob("*.png")) == _source_pngs()


@pytest.mark.parametrize("rel", _source_pngs(), ids=str)
def test_png_is_byte_identical(rel: Path) -> None:
    assert (COPY / rel).read_bytes() == (SOURCE / rel).read_bytes()


@pytest.mark.parametrize("stage", [1, 2, 3, 4])
@pytest.mark.parametrize("variant", ["a", "b"])
def test_positions_match_footprint_prefix(stage: int, variant: str) -> None:
    positions = json.loads(
        (SOURCE / f"base/stage{stage}_{variant}/positions.json").read_text()
    )["positions"]
    assert [positions[f"h{i}"] for i in range(stage)] == BASE_FOOTPRINT[:stage]
    assert len(positions) == stage
