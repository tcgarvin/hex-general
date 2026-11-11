"""The shared variety fixture (frontend/src/board/__fixtures__/variety.json) must be
what board-mock.py chooses today: the frontend's varietyTiles.test.ts checks its
port against it, so a stale fixture would let the two drift apart."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "variety-fixture.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("variety_fixture", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["variety_fixture"] = module
    spec.loader.exec_module(module)
    return module


def test_committed_fixture_matches_board_mock() -> None:
    variety_fixture = _load()
    assert (
        variety_fixture.OUT.read_text() == variety_fixture.build()
    ), "stale: run python3 scripts/variety-fixture.py"


def test_fixture_exercises_every_kind_size_and_a_mirror() -> None:
    data = json.loads(_load().OUT.read_text())
    picks = [p for m in data["maps"] for p in m["picks"]]
    assert {(p["kind"], p["size"]) for p in picks} == {
        (k, s) for k in ("nature", "home", "road") for s in ("small", "medium")
    }
    assert any(p["mirror"] for p in picks)
    assert any(p["key"].startswith("road_variety/fork_") for p in picks)
