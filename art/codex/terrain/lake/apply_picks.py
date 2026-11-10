#!/usr/bin/env python3
"""Copy the picked lake tiles into the candidate set with provenance.

Writes art/codex/terrain/candidates/lake1/lake/NN.png (a tiles root holding only
the lake terrain) and picks.json beside them: source sheet, cell, raw, gate
result and sha256 of every pick. Run from the repo root:

    python3 art/codex/terrain/lake/apply_picks.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
OUT = REPO / "art" / "codex" / "terrain" / "candidates" / "lake1" / "lake"
# (work dir, cell) in candidate order
PICKS: tuple[tuple[str, str], ...] = (
    ("b", "lake_00"),
    ("b", "lake_02"),
    ("b", "lake_07"),
    ("b", "lake_09"),
    ("b", "lake_12"),
    ("b", "lake_15"),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    records = []
    for i, (work, cell) in enumerate(PICKS):
        work_dir = HERE / work
        report = json.loads((work_dir / "report.json").read_text())
        tile = report["tiles"][cell]
        if not tile["passed"]:
            raise SystemExit(f"{work}/{cell} failed its hard gates; not a candidate")
        src = work_dir / "tiles" / f"{cell}.png"
        dest = OUT / f"{i:02d}.png"
        shutil.copyfile(src, dest)
        job = json.loads((work_dir / "job.json").read_text())
        records.append(
            {
                "file": f"lake/{dest.name}",
                "source_tile": str(src.relative_to(REPO)),
                "sheet": job["name"],
                "cell": cell,
                "raw": str((work_dir / "raw" / f"{job['name']}.png").relative_to(REPO)),
                "guide_seed": next(c for c in job["cells"] if c["name"] == cell)[
                    "guide"
                ]["seed"],
                "gates_passed": True,
                "warnings": [c["detail"] for c in tile["checks"] if not c["passed"]],
                "sha256": sha256(dest),
            }
        )
    (OUT.parent / "picks.json").write_text(
        json.dumps({"picks": records}, indent=1) + "\n"
    )
    print(f"{len(records)} lake candidates -> {OUT}")


if __name__ == "__main__":
    main()
