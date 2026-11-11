"""The shared lake fixture (frontend/src/board/__fixtures__/lakes.json) must be
what board-mock.py chooses today: the frontend's lake tests check their port
against it, so a stale fixture would let the two drift apart."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "lake-fixture.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lake_fixture", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["lake_fixture"] = module
    spec.loader.exec_module(module)
    return module


def test_committed_fixture_matches_board_mock() -> None:
    lake_fixture = _load()
    assert (
        lake_fixture.OUT.read_text() == lake_fixture.build()
    ), "stale: run python3 scripts/lake-fixture.py"


def test_fixture_covers_every_mouth_direction() -> None:
    lake_fixture = _load()
    data = json.loads(lake_fixture.OUT.read_text())
    directions = {
        m["direction"] for game_map in data["maps"] for m in game_map["mouths"]
    }
    assert directions == set(range(6))
