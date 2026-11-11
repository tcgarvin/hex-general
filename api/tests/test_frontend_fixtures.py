"""The engine-recorded fixtures the frontend tests run on must be current.

The generators live beside the fixtures in ``frontend/src/world/__fixtures__``
and import the engine. A stale fixture lets the frontend tests pass on data
the server no longer produces (the playback fixture kept event fields the
replay privacy change had nulled), so each test reruns a generator in process
and compares its output with the committed JSON. On a failure, rerun the
generator from ``api/`` as the message says; the playback generator also fails
when its cases stop covering a feature the playback tests need, and then
needs new ``CASES``. ``api/scripts/route_fixture.py`` is left out: it plays a
40-slot world and takes several seconds.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

FIXTURES = Path(__file__).resolve().parents[2] / "frontend/src/world/__fixtures__"


def _load(name: str) -> ModuleType:
    path = FIXTURES / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"frontend_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_playback_fixture_is_current() -> None:
    playback = _load("playback_fixture")
    text, _ = playback.build()
    # Compare to a bool first: pytest's diff of two large strings takes a minute.
    current = playback.OUTPUT.read_text() == text
    assert current, (
        "stale: run `uv run python ../frontend/src/world/__fixtures__/"
        "playback_fixture.py` and update the playback tests"
    )


def test_combat_fixture_is_current() -> None:
    combat = _load("combat_fixture")
    current = combat.OUT.read_text() == combat.build()
    assert current, (
        "stale: run `uv run python ../frontend/src/world/__fixtures__/"
        "combat_fixture.py`"
    )
