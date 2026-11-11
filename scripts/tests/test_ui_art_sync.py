"""Shipped UI art must match its recorded provenance (art/codex/ui/applied.json).

`art/codex/ui/apply_ui_art.py` builds every shipped glyph, emblem, favicon and key
art file from a ratified source and records both hashes. A changed source or a
hand-edited shipped file fails here until the script is re-run (and the change
ratified).
"""

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RECORDS = json.loads((ROOT / "art/codex/ui/applied.json").read_text())
GLYPH_DIR = ROOT / "frontend/src/ui-art/glyphs"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["output"])
def test_output_matches_record(record: dict[str, str]) -> None:
    assert _sha(ROOT / record["output"]) == record["output_sha256"]


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["output"])
def test_source_matches_record(record: dict[str, str]) -> None:
    assert _sha(ROOT / record["source"]) == record["source_sha256"]


def test_every_shipped_glyph_is_recorded() -> None:
    shipped = sorted(p.relative_to(ROOT).as_posix() for p in GLYPH_DIR.glob("*.png"))
    recorded = sorted(r["output"] for r in RECORDS if r["output"].startswith("frontend/src/ui-art/glyphs/"))
    assert shipped == recorded
