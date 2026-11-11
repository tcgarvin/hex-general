"""scripts/season-viewer.py builds one self-contained page from a season record."""

import base64
import gzip
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "season-viewer.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("season_viewer", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["season_viewer"] = module
    spec.loader.exec_module(module)
    return module


def _tiny_record() -> dict:
    """A hand-made two-turn season on a 3x2 map: two players, one fight."""
    return {
        "v": 1,
        "slots": 2,
        "bots": 2,
        "seed": 9,
        "turns": 1,
        "players": ["b000", "b001"],
        "bases": [0, 5],
        "unit_types": ["infantry", "armor", "artillery"],
        "site_kinds": ["base", "city", "farm", "oil_field", "steel_mill", "factory"],
        "buildings": ["barracks", "depot", "workshop"],
        "event_kinds": [
            "combat",
            "barrage",
            "capture",
            "destroyed",
            "disband",
            "eliminated",
            "recruit",
            "built",
        ],
        "player_fields": [
            "units",
            "holdings",
            "manpower",
            "fuel",
            "steel",
            "barracks",
            "depot",
            "workshop",
            "cap",
        ],
        "unit_fields": ["id", "owner", "type", "hp", "flags", "hex"],
        "map": {
            "w": 3,
            "h": 2,
            "terrain": "..w~.c",
            "roads": "#.#..#",
            "site_fields": ["hex", "kind", "level", "garrison"],
            "sites": [0, 0, 1, 0, 5, 0, 1, 0, 2, 2, 2, 1],
        },
        "eliminated": [0, 0],
        "frames": [
            {
                "t": 0,
                "u": [1, 1, 0, 10, 0, 1, 2, 2, 0, 10, 0, 4, 3, 0, 0, 5, 0, 2],
                "x": [],
                "s": [0, 1, 1, 2, 2, 0],
                "p": [1, 1, 30, 30, 30, 0, 0, 0, 4, 1, 1, 30, 30, 30, 0, 0, 0, 4],
                "e": [],
                "m": [],
                "alive": 2,
            },
            {
                "t": 1,
                "u": [1, 1, 0, 7, 0, 2, 2, 2, 0, 6, 0, 4],
                "x": [3],
                "s": [2, 1],
                "p": [1, 2, 28, 30, 31, 0, 0, 0, 4, 1, 1, 29, 30, 31, 0, 0, 0, 4],
                "e": [
                    [0, 2, [[1, 1, 0, 0, 3, 7, 0], [3, 0, 0, 1, 5, 0, 1]]],
                    [3, 2, 3, 0, 0, 0],
                    [2, 2, 2, 1, 0, 1],
                ],
                "m": [1, 1, 2],
                "alive": 2,
            },
        ],
    }


@pytest.fixture(scope="module")
def viewer() -> ModuleType:
    return _load()


def _embedded(page: str) -> dict:
    match = re.search(
        r'<script id="season-data" type="application/json" '
        r'data-encoding="(\w+)">(.*?)</script>',
        page,
        re.S,
    )
    assert match is not None, "no embedded season data"
    encoding, payload = match.groups()
    if encoding == "json":
        return json.loads(payload.replace("<\\/", "</"))
    assert encoding == "gzip"
    return json.loads(gzip.decompress(base64.b64decode(payload)))


@pytest.mark.parametrize("encoding", ["json", "gzip"])
def test_page_embeds_the_record(viewer: ModuleType, encoding: str) -> None:
    record = _tiny_record()
    page = viewer.build_page(record, "Season Viewer", False, encoding)
    assert _embedded(page) == record


@pytest.mark.parametrize("fragment", [False, True])
def test_page_makes_no_outside_requests(viewer: ModuleType, fragment: bool) -> None:
    page = viewer.build_page(_tiny_record(), "Season Viewer", fragment, "json")
    lowered = page.lower()
    for needle in (
        "http://",
        "https://",
        "//cdn",
        "@import",
        "url(",
        "<link",
        "fetch(",
    ):
        assert needle not in lowered, needle
    assert re.search(r"<script[^>]*\ssrc=", lowered) is None
    assert re.search(r"<img\b", lowered) is None
    for banned in ("alert(", "confirm(", "prompt("):
        assert banned not in page


def test_standalone_page_is_a_full_document(viewer: ModuleType) -> None:
    page = viewer.build_page(_tiny_record(), "Season Viewer", False, "json")
    assert page.startswith("<!DOCTYPE html>")
    assert "<title>Season Viewer</title>" in page[:2000]
    assert page.rstrip().endswith("</html>")


def test_fragment_follows_the_artifact_contract(viewer: ModuleType) -> None:
    page = viewer.build_page(_tiny_record(), "Season Viewer", True, "json")
    lines = page.splitlines()
    assert lines[0] == "<title>Season Viewer</title>"
    assert lines[1] == '<meta name="claude:viewport-fit-sides" content="cover">'
    assert lines[2] == "<style>"
    lowered = page.lower()
    for tag in ("<!doctype", "<html", "<head>", "<head ", "<body"):
        assert tag not in lowered, tag
    assert "padding-left: max(16px, env(safe-area-inset-left, 0px))" in page
    assert "@media (prefers-color-scheme: dark)" in page
    assert ':root[data-theme="dark"]' in page


def test_title_is_escaped(viewer: ModuleType) -> None:
    page = viewer.build_page(_tiny_record(), "A <b> & C", True, "json")
    assert page.startswith("<title>A &lt;b&gt; &amp; C</title>")


def test_cli_writes_the_page(viewer: ModuleType, tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "season.json"
    source.write_text(json.dumps(_tiny_record()))
    out = tmp_path / "viewer.html"
    monkeypatch.setattr(
        sys,
        "argv",
        ["season-viewer.py", str(source), "--out", str(out), "--title", "T"],
    )
    assert viewer.main() == 0
    assert _embedded(out.read_text()) == _tiny_record()


def test_wrong_record_version_is_refused(viewer: ModuleType, tmp_path: Path) -> None:
    record = _tiny_record()
    record["v"] = 99
    source = tmp_path / "season.json"
    source.write_text(json.dumps(record))
    with pytest.raises(viewer.ViewerError, match="version 99"):
        viewer.load_record(source)
