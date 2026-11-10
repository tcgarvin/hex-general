#!/usr/bin/env python3
"""Copy the picked roads-over-hills tiles to candidates/hills-roads/road/ under
frontend names (`picks.json`: name -> work dir + cell), with provenance.

    python3 art/codex/terrain/road-hills/assemble.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "candidates" / "hills-roads" / "road"


def main() -> None:
    picks = json.loads((HERE / "picks.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    prov = {}
    for dest, src in picks.items():
        work = HERE / src["work"]
        report = json.loads((work / "report.json").read_text())
        if not report["tiles"][src["cell"]]["passed"]:
            raise RuntimeError(f"{dest}: {src['work']}/{src['cell']} failed its hard gates")
        side = json.loads(next(work.glob("*.sheet.json")).read_text())
        cell = next(c for c in side["cells"] if c["name"] == src["cell"])
        shutil.copyfile(work / "tiles" / f"{src['cell']}.png", OUT / dest)
        prov[dest] = {"work": f"road-hills/{src['work']}", "cell": src["cell"],
                      "hills_base": cell["base"], "sheet": report["sheet"],
                      "sha256": hashlib.sha256((OUT / dest).read_bytes()).hexdigest()}
    for stale in OUT.glob("*.png"):
        if stale.name not in picks:
            raise RuntimeError(f"{stale} is not in picks.json")
    (OUT / "provenance.json").write_text(json.dumps(prov, indent=1, sort_keys=True))
    print(f"{len(picks)} tiles -> {OUT}")


if __name__ == "__main__":
    main()
