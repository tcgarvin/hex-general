#!/usr/bin/env python3
"""Copy picked round-1 tiles into candidates/r1/<terrain>/ under frontend names.

`picks.json` maps each output file (frontend name, e.g. "river/straight_v_00.png")
to the paint-in work dir + cell it comes from. The pinned tile is copied byte for
byte and a provenance record (source tile, sheet, raw, sha256) is written to
candidates/r1/<terrain>/provenance.json. Missing or failed sources raise.

    python3 art/codex/terrain/r1_assemble.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUT = HERE / "candidates" / "r1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    picks = json.loads((HERE / "picks.json").read_text())
    prov: dict[str, dict] = {}
    for dest, src in picks.items():
        work = HERE / src["work"]
        report = json.loads((work / "report.json").read_text())
        if not report["tiles"][src["cell"]]["passed"]:
            raise RuntimeError(f"{dest}: {src['work']}/{src['cell']} failed its hard gates")
        tile = work / "tiles" / f"{src['cell']}.png"
        target = OUT / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tile, target)
        terrain = dest.split("/")[0]
        prov.setdefault(terrain, {})[dest.split("/", 1)[1]] = {
            "source": str(tile.relative_to(REPO)),
            "sheet": report["sheet"],
            "cell": src["cell"],
            "sha256": sha(target),
            **({"note": src["note"]} if "note" in src else {}),
        }
    for terrain, rec in prov.items():
        keep = set(rec)
        for stale in (OUT / terrain).glob("*.png"):
            if stale.name not in keep:
                raise RuntimeError(f"{stale} is not in picks.json (remove it or pick it)")
        (OUT / terrain / "provenance.json").write_text(json.dumps(rec, indent=1, sort_keys=True))
        print(f"{terrain}: {len(rec)} tiles")


if __name__ == "__main__":
    main()
