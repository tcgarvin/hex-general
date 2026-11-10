"""Build the ratified site set and its provenance table.

Collects the site tiles the user approved (2026-11-10) from the round 3 and
round 4 candidate dirs into `candidates/sites-ratified/`, byte-for-byte, writes a
`picks.json` per kind carrying the recorded provenance (source work dir, raw
sheet, cell, gates, sha256), verifies every digest against the copied file, and
writes `SITES.md` (every ratified file -> work dir, raw sheet, cell).

Re-runnable: copies are overwritten with identical bytes. Fails loudly on a
missing source or raw, a digest that differs from the recorded one, a source dir
holding tiles that are not in its picks, or a ratified dir holding files this
script did not place.

Run from anywhere: python3 art/codex/terrain/sites_ratified_build.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
TERRAIN = REPO / "art" / "codex" / "terrain"
CANDIDATES = TERRAIN / "candidates"
RATIFIED = CANDIDATES / "sites-ratified"
SITES_MD = TERRAIN / "SITES.md"
RATIFIED_ON = "2026-11-10"

# kind -> candidate round dir that holds the approved tiles
SINGLE_HEX_KINDS: dict[str, str] = {
    "farm": "sites3",
    "steel_mill": "sites3",
    "oil_field": "sites4",
    "factory": "sites4",
}
# ratified base dir -> (candidate round, candidate dir). The user chose the
# complete bank for stages 1-2; the *_building_* dirs are not approved.
BASE_DIRS: dict[str, tuple[str, str]] = {
    "stage1_a": ("sites4", "stage1_full_a"),
    "stage1_b": ("sites4", "stage1_full_b"),
    "stage2_a": ("sites4", "stage2_full_a"),
    "stage2_b": ("sites4", "stage2_full_b"),
    "stage3_a": ("sites4", "stage3_a"),
    "stage3_b": ("sites4", "stage3_b"),
    "stage4_a": ("sites4", "stage4_a"),
    "stage4_b": ("sites4", "stage4_b"),
}
# Footprint in axial offsets from the anchor; assumed, never explicitly confirmed.
EXPECTED_FOOTPRINT: dict[int, list[list[int]]] = {
    1: [[0, 0]],
    2: [[0, 0], [1, 0]],
    3: [[0, 0], [1, 0], [0, 1]],
    4: [[0, 0], [1, 0], [0, 1], [1, 1]],
}


class RatifyError(RuntimeError):
    """The approved set cannot be assembled or does not match its provenance."""


def sha256(path: Path) -> str:
    """Hex sha256 of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: str | Path) -> str:
    """Repo-relative text for a recorded path (absolute or already relative)."""
    p = Path(path)
    return str(p.relative_to(REPO)) if p.is_absolute() else str(p)


def require_file(path: str | Path, what: str) -> Path:
    """Return the repo path of a recorded file, raising if it is missing."""
    full = REPO / rel(path)
    if not full.is_file():
        raise RatifyError(f"{what}: {rel(path)} is missing")
    return full


def copy_verified(src: Path, dst: Path, expected: str) -> str:
    """Copy src to dst and check both ends against the recorded digest."""
    if sha256(src) != expected:
        raise RatifyError(f"{rel(src)}: sha256 differs from the recorded {expected}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    actual = sha256(dst)
    if actual != expected:
        raise RatifyError(f"{rel(dst)}: copy has sha256 {actual}, expected {expected}")
    return actual


def check_no_strays(directory: Path, placed: set[Path]) -> None:
    """Fail if a ratified dir holds a file this script did not place."""
    stray = sorted(p for p in directory.rglob("*") if p.is_file() and p not in placed)
    if stray:
        raise RatifyError(
            f"unexpected files in {rel(directory)}: {[rel(p) for p in stray]}"
        )


def cell_of(source: str) -> str:
    """Cell name of a work-dir tile path (`.../tiles/l2_13.png` -> `l2_13`)."""
    return Path(source).stem


def single_record(entry: dict[str, Any], round_dir: str, kind: str) -> dict[str, Any]:
    """Normalise one candidate pick record and add the ratified-set fields."""
    raw = require_file(entry["raw"], f"{kind}/{entry['file']} raw")
    record: dict[str, Any] = {
        "file": entry["file"],
        "level": entry["level"],
        "candidate": f"art/codex/terrain/candidates/{round_dir}/{kind}/{entry['file']}",
        "work_dir": rel(Path(entry["sheet_job"]).parent),
        "raw": rel(entry["raw"]),
        "raw_sha256": sha256(raw),
        "cell": cell_of(entry["source"]),
        "source": rel(entry["source"]),
        "gate_passed": entry["gate_passed"],
        "failed_checks": entry["failed_checks"],
        "occupied": entry["occupied"],
        "grade": entry["grade"],
        "sha256": entry["sha256"],
    }
    if "tank_repaint" in entry:
        before = entry["before"]
        require_file(before["raw"], f"{kind}/{entry['file']} original raw")
        record["tank_repaint"] = entry["tank_repaint"]
        record["original_paint"] = {
            "work_dir": rel(Path(before["sheet_job"]).parent),
            "raw": rel(before["raw"]),
            "raw_sha256": sha256(REPO / rel(before["raw"])),
            "cell": cell_of(before["source"]),
            "source": rel(before["source"]),
            "grade": before["grade"],
            "sha256": before["sha256"],
        }
    return record


def build_single(kind: str, round_dir: str) -> list[dict[str, Any]]:
    """Copy one single-hex kind into the ratified set; return its records."""
    src_dir = CANDIDATES / round_dir / kind
    picks = json.loads((src_dir / "picks.json").read_text())["picks"]
    on_disk = {p.name for p in src_dir.glob("*.png")}
    listed = {e["file"] for e in picks}
    if on_disk != listed:
        raise RatifyError(
            f"{rel(src_dir)}: tiles on disk and in picks.json differ: "
            f"{sorted(on_disk ^ listed)}"
        )
    out_dir = RATIFIED / kind
    placed: set[Path] = {out_dir / "picks.json"}
    records = []
    for entry in sorted(picks, key=lambda e: e["file"]):
        copy_verified(src_dir / entry["file"], out_dir / entry["file"], entry["sha256"])
        placed.add(out_dir / entry["file"])
        records.append(single_record(entry, round_dir, kind))
    payload = {"kind": kind, "ratified": RATIFIED_ON, "tiles": records}
    (out_dir / "picks.json").write_text(json.dumps(payload, indent=1) + "\n")
    check_no_strays(out_dir, placed)
    return records


def base_stage_record(
    entry: dict[str, Any], round_dir: str, cand_name: str, name: str
) -> dict[str, Any]:
    """Normalise one base stage pick record."""
    raw = require_file(entry["raw"], f"base/{name} raw")
    record: dict[str, Any] = {
        "dir": name,
        "stage": entry["stage"],
        "candidate": f"art/codex/terrain/candidates/{round_dir}/base/{cand_name}",
        "work_dir": rel(Path(entry["raw"]).parent.parent),
        "work_cluster": entry["work_cluster"],
        "raw": rel(entry["raw"]),
        "raw_sha256": sha256(raw),
        "registration": entry["registration"],
        "gate_failures": entry["gate_failures"],
        "seams_mean": entry["seams_mean"],
        "seam_failures": entry["seam_failures"],
        "guide_arc": entry["guide_arc"],
        "files": entry["sha256"],
    }
    if "reused_from" in entry:
        record["reused_from"] = entry["reused_from"]
    return record


def build_base() -> list[dict[str, Any]]:
    """Copy the base stage dirs into the ratified set; return their records."""
    picks = {
        e["dir"]: e
        for e in json.loads((CANDIDATES / "sites4/base/picks.json").read_text())
    }
    out_dir = RATIFIED / "base"
    placed: set[Path] = {out_dir / "picks.json"}
    records = []
    for name, (round_dir, cand_name) in BASE_DIRS.items():
        entry = picks[cand_name]
        src_dir = CANDIDATES / round_dir / "base" / cand_name
        hexes = sorted(p.name for p in src_dir.glob("h*.png"))
        if hexes != sorted(entry["sha256"]):
            raise RatifyError(f"{rel(src_dir)}: tiles {hexes} differ from picks.json")
        for tile, digest in entry["sha256"].items():
            copy_verified(src_dir / tile, out_dir / name / tile, digest)
            placed.add(out_dir / name / tile)
        positions = json.loads((src_dir / "positions.json").read_text())
        footprint = [positions["positions"][f"h{i}"] for i in range(len(hexes))]
        if footprint != EXPECTED_FOOTPRINT[entry["stage"]]:
            raise RatifyError(
                f"{rel(src_dir)}: footprint {footprint} is not the assumed one"
            )
        shutil.copyfile(src_dir / "positions.json", out_dir / name / "positions.json")
        placed.add(out_dir / name / "positions.json")
        record = base_stage_record(entry, round_dir, cand_name, name)
        record["footprint"] = footprint
        records.append(record)
    payload = {
        "kind": "base",
        "ratified": RATIFIED_ON,
        "footprint_assumed": "axial offsets from the anchor, not explicitly confirmed by the user",
        "stages": records,
    }
    (out_dir / "picks.json").write_text(json.dumps(payload, indent=1) + "\n")
    check_no_strays(out_dir, placed)
    return records


def verify_ratified() -> int:
    """Re-hash every ratified PNG against its picks.json; return the file count."""
    count = 0
    for kind in [*SINGLE_HEX_KINDS, "base"]:
        data = json.loads((RATIFIED / kind / "picks.json").read_text())
        if kind == "base":
            expected = {
                f"{s['dir']}/{f}": h
                for s in data["stages"]
                for f, h in s["files"].items()
            }
        else:
            expected = {t["file"]: t["sha256"] for t in data["tiles"]}
        for name, digest in expected.items():
            if sha256(RATIFIED / kind / name) != digest:
                raise RatifyError(f"{kind}/{name}: sha256 mismatch after copy")
            count += 1
    return count


def md_single(kind: str, records: list[dict[str, Any]]) -> list[str]:
    """SITES.md section for a single-hex kind."""
    lines = [f"## {kind}", ""]
    if kind == "oil_field":
        lines += [
            "Two-step provenance: the original paint is a round 4 count-ladder sheet",
            "(`sites/oil_field/s4-l1..l3d`); the tank repaint then repainted only the",
            "storage tanks on the picked tiles (`sites/oil_field/s4-tanks-l1..l3`, the",
            "paint-in base is the original pick in `orig/`) and `sites4_oilfac_tanks.py",
            "composite` keeps the original byte-identical outside a feathered box around",
            "the tanks. The `raw`/`cell` columns are the repaint; the original is in the",
            "last column.",
            "",
        ]
        lines += [
            "| file | level | work dir | raw sheet | cell | sha256 | original paint (work dir, cell) |"
        ]
        lines += ["|---|---|---|---|---|---|---|"]
        for r in records:
            o = r["original_paint"]
            lines.append(
                f"| {r['file']} | {r['level']} | `{r['work_dir']}` | `{r['raw']}` | "
                f"{r['cell']} | `{r['sha256'][:12]}` | `{o['work_dir']}` {o['cell']} |"
            )
    else:
        lines += [
            "| file | level | work dir | raw sheet | cell | sha256 |",
            "|---|---|---|---|---|---|",
        ]
        for r in records:
            lines.append(
                f"| {r['file']} | {r['level']} | `{r['work_dir']}` | `{r['raw']}` | "
                f"{r['cell']} | `{r['sha256'][:12]}` |"
            )
    return lines + [""]


def md_base(records: list[dict[str, Any]]) -> list[str]:
    """SITES.md section for the base stages."""
    lines = [
        "## base",
        "",
        "Each stage dir holds `h<i>.png` per hex and `positions.json`; cluster = the",
        "composition in the work dir's `job.json` the hexes were cut from.",
        "",
        "| dir | stage | work dir | cluster | raw sheet | files (sha256) | seam failures |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in records:
        files = ", ".join(f"{f[:-4]} `{h[:12]}`" for f, h in r["files"].items())
        seams = "; ".join(r["seam_failures"]) or "none"
        lines.append(
            f"| {r['dir']} | {r['stage']} | `{r['work_dir']}` | {r['work_cluster']} | "
            f"`{r['raw']}` | {files} | {seams} |"
        )
    return lines + [""]


def write_table(all_records: dict[str, list[dict[str, Any]]]) -> None:
    """Write SITES.md from the records."""
    lines = [
        "# Ratified site tiles: provenance",
        "",
        f"Generated by `sites_ratified_build.py` (ratified {RATIFIED_ON}). Every file under",
        "`candidates/sites-ratified/` -> work dir, raw Codex sheet and cell. Digests are",
        "sha256 prefixes; full digests in each kind's `picks.json`. Paths are repo-relative.",
        "",
    ]
    for kind in SINGLE_HEX_KINDS:
        lines += md_single(kind, all_records[kind])
    lines += md_base(all_records["base"])
    SITES_MD.write_text("\n".join(lines))


def main() -> None:
    """Build, verify and document the ratified set."""
    all_records: dict[str, list[dict[str, Any]]] = {}
    for kind, round_dir in SINGLE_HEX_KINDS.items():
        all_records[kind] = build_single(kind, round_dir)
    all_records["base"] = build_base()
    checked = verify_ratified()
    write_table(all_records)
    per_kind = {k: len(v) for k, v in all_records.items()}
    print(
        f"ratified set built: {per_kind}; {checked} PNGs verified; wrote {rel(SITES_MD)}"
    )


if __name__ == "__main__":
    main()
