"""Write APPLIED.md: every shipped terrain tile and join sprite -> its source.

Reads the provenance the candidate sets already record (candidates/review1/*/
picks.json and provenance.json, ground/G2/set.json for plains,
candidates/review1-joins/joins.json) and checks each entry against the files
actually shipped in frontend/src/board/ (sha256), so the table cannot drift
from the app. Fails loudly on a shipped file with no provenance, a digest
mismatch, or a source raw that is missing.

Run from the repo root: python3 art/codex/terrain/applied_table.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TERRAIN = REPO / "art" / "codex" / "terrain"
CANDIDATES = TERRAIN / "candidates" / "review1"
JOINS = TERRAIN / "candidates" / "review1-joins"
SHIPPED_TILES = REPO / "frontend" / "src" / "board" / "tiles"
SHIPPED_JOINS = REPO / "frontend" / "src" / "board" / "joins"
OUT = TERRAIN / "APPLIED.md"


class ProvenanceError(RuntimeError):
    """A shipped file cannot be traced to a recorded source."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: str | Path) -> str:
    """Repo-relative path text for a recorded path (absolute or repo-relative)."""
    p = Path(path)
    return str(p.relative_to(REPO)) if p.is_absolute() else str(p)


def require(path: str, what: str) -> str:
    if not (REPO / path).is_file():
        raise ProvenanceError(f"{what}: {path} is missing")
    return path


def raw_for_sheet(sheet: str) -> str:
    """`<work>/<name>.sheet.png` -> `<work>/raw/<name>.png` (paintin.py layout)."""
    p = Path(sheet)
    name = p.name.removesuffix(".sheet.png")
    return str(p.parent / "raw" / f"{name}.png")


def picked_rows(terrain: str) -> list[tuple[str, str, str, str, str]]:
    """Rows from a paint-in class's picks.json (woods, hills, town, city)."""
    rows = []
    for e in json.loads((CANDIDATES / terrain / "picks.json").read_text()):
        shipped = SHIPPED_TILES / terrain / e["file"]
        if sha256(shipped) != e["sha256"]:
            raise ProvenanceError(f"{terrain}/{e['file']}: shipped file != picks.json sha256")
        raw = require(rel(e["raw"]), f"{terrain}/{e['file']} raw")
        work = str(Path(rel(e["sheet_job"])).parent)
        cell = Path(e["source"]).stem
        grade = "" if e["grade"] is None else ", ".join(f"{k} {v}" for k, v in e["grade"].items())
        rows.append((f"{terrain}/{e['file']}", work, raw, cell, grade))
    return rows


def oriented_rows(terrain: str) -> list[tuple[str, str, str, str, str]]:
    """Rows from an oriented class's provenance.json (river, road, bridge)."""
    rows = []
    for name, e in sorted(json.loads((CANDIDATES / terrain / "provenance.json").read_text()).items()):
        shipped = SHIPPED_TILES / terrain / name
        if sha256(shipped) != e["sha256"]:
            raise ProvenanceError(f"{terrain}/{name}: shipped file != provenance.json sha256")
        sheet = rel(e["sheet"])
        raw = require(raw_for_sheet(sheet), f"{terrain}/{name} raw")
        rows.append((f"{terrain}/{name}", str(Path(sheet).parent), raw, e["cell"], ""))
    return rows


def hills_road_rows() -> list[tuple[str, str, str, str, str]]:
    """Roads over hills: candidates/hills-roads/road/provenance.json, shipped in tiles/road/.

    The work dir (road-hills/) also holds manifest.json and picks.json; each cell
    must be listed in picks.json so the pick trail is complete.
    """
    base = TERRAIN / "candidates" / "hills-roads" / "road"
    picks = json.loads((TERRAIN / "road-hills" / "picks.json").read_text())
    require("art/codex/terrain/road-hills/manifest.json", "hills-road manifest")
    rows = []
    for name, e in sorted(json.loads((base / "provenance.json").read_text()).items()):
        shipped = SHIPPED_TILES / "road" / name
        if sha256(shipped) != e["sha256"]:
            raise ProvenanceError(f"road/{name}: shipped file != hills-roads provenance.json sha256")
        if picks[name]["cell"] != e["cell"]:
            raise ProvenanceError(f"road/{name}: picks.json cell != provenance.json cell")
        sheet = rel(e["sheet"])
        raw = require(raw_for_sheet(sheet), f"road/{name} raw")
        rows.append((f"road/{name}", e["work"] and f"art/codex/terrain/{e['work']}", raw, e["cell"], ""))
    return rows


def plains_rows() -> list[tuple[str, str, str, str, str]]:
    """Plains are 1:1 cuts of the recoloured G2 meadow (ground/G2/set.json)."""
    s = json.loads((TERRAIN / "ground" / "G2" / "set.json").read_text())
    raw = require(rel(TERRAIN / s["raw"]), "plains raw")
    rows = []
    for t in s["tiles"]:
        name = Path(t["file"]).name
        if sha256(SHIPPED_TILES / "plains" / name) != sha256(CANDIDATES / "plains" / name):
            raise ProvenanceError(f"plains/{name}: shipped file != candidates/review1 copy")
        x, y = t["source_offset_px"]
        rows.append((f"plains/{name}", "art/codex/terrain/ground/G2", raw, f"offset ({x}, {y})", ""))
    return rows


def join_rows() -> list[tuple[str, str, str]]:
    manifest = json.loads((JOINS / "joins.json").read_text())
    rows = []
    for e in manifest["sprites"]:
        if sha256(SHIPPED_JOINS / e["file"]) != sha256(JOINS / e["file"]):
            raise ProvenanceError(f"joins/{e['file']}: shipped file != candidates copy")
        x, y = e["offset"]
        rows.append((f"joins/{e['file']}", rel(e["source"]), f"{e['route']}, offset ({x}, {y})"))
    return rows


def main() -> int:
    rows = plains_rows()
    for terrain in ("woods", "hills", "town", "city"):
        rows += picked_rows(terrain)
    for terrain in ("river", "road", "bridge"):
        rows += oriented_rows(terrain)
    rows += hills_road_rows()
    shipped = {str(p.relative_to(SHIPPED_TILES)) for p in SHIPPED_TILES.glob("*/*.png")}
    traced = {r[0] for r in rows}
    if shipped != traced:
        raise ProvenanceError(
            f"untraced shipped tiles {sorted(shipped - traced)}; "
            f"traced but not shipped {sorted(traced - shipped)}"
        )
    joins = join_rows()
    shipped_joins = {str(p.relative_to(SHIPPED_JOINS)) for p in SHIPPED_JOINS.glob("*/*.png")}
    if shipped_joins != {r[0].removeprefix("joins/") for r in joins}:
        raise ProvenanceError("shipped join sprites do not match joins.json")

    lines = [
        "# Applied terrain tiles: provenance",
        "",
        "Generated by `applied_table.py` from the recorded picks and provenance; do not",
        "hand-edit. Every row's sha256 was checked against the shipped file.",
        "",
        "Tiles live in `frontend/src/board/tiles/<file>`. To rebuild a paint-in tile,",
        "replay its sheet: `python3 scripts/tile-foundry/paintin.py run --job <work>/job.json",
        "--prompt <work>/prompt.txt --out-dir <work> --from-raw <raw>` plus the `--ref`",
        "arguments in `<work>/refs.txt` (if any), then apply the grade listed (via",
        "`r2_grade.py`; parameters also in `<work>/tiles_graded/*.grade.json`). Plains",
        "are 1:1 cuts of the recoloured G2 meadow; the recolour and cut parameters are",
        "in `ground/G2/set.json`.",
        "",
        "| Shipped tile | Work dir | Raw Codex sheet | Cell | Grade |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [f"| `{a}` | `{b}` | `{c}` | {d} | {e} |" for a, b, c, d, e in rows]
    lines += [
        "",
        "## Join sprites",
        "",
        "`frontend/src/board/joins/<file>`, built by `scripts/tile-foundry/joins.py`",
        "(`--tiles art/codex/terrain/candidates/review1 --out <dir>`) from a patch of",
        "the shipped tile named here, shifted by the offset (tile px).",
        "",
        "| Join sprite | Source tile | Route, offset |",
        "| --- | --- | --- |",
    ]
    lines += [f"| `{a}` | `{b}` | {c} |" for a, b, c in joins]
    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT.relative_to(REPO)}: {len(rows)} tiles, {len(joins)} joins")
    return 0


if __name__ == "__main__":
    sys.exit(main())
