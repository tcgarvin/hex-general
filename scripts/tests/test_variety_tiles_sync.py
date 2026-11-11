"""frontend/src/board/tiles variety folders must match the ratified art source,
and every ratified file must be its recorded round 1 / round 2 kept candidate."""

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "art/codex/terrain/candidates/variety-ratified"
COPY = ROOT / "frontend/src/board/tiles"
FOLDERS = ("plains_nature", "plains_home", "road_variety")
PICKS = json.loads((SOURCE / "picks.json").read_text())["picks"]


def _source_pngs() -> list[Path]:
    return sorted(p.relative_to(SOURCE) for p in SOURCE.rglob("*.png"))


def test_copy_has_exactly_the_ratified_pngs() -> None:
    copied = sorted(
        p.relative_to(COPY) for f in FOLDERS for p in (COPY / f).rglob("*.png")
    )
    assert copied == _source_pngs()


@pytest.mark.parametrize("rel", _source_pngs(), ids=str)
def test_png_is_byte_identical(rel: Path) -> None:
    assert (COPY / rel).read_bytes() == (SOURCE / rel).read_bytes()


def test_picks_cover_the_set() -> None:
    assert sorted(Path(p["file"]) for p in PICKS) == _source_pngs()


@pytest.mark.parametrize("pick", PICKS, ids=lambda p: p["file"])
def test_ratified_file_is_its_kept_candidate(pick: dict) -> None:
    data = (SOURCE / pick["file"]).read_bytes()
    assert data == (ROOT / pick["source"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == pick["sha256"]


def test_owner_exclusions_are_not_shipped() -> None:
    labels = {p["label"] for p in PICKS}
    for dropped in (
        "nature-11",
        "nature-m06",
        "nature-m07",
        "nature-m09",
        "nature-m10",
    ):
        assert dropped not in labels
