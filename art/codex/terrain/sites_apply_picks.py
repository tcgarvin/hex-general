"""Copy picked site tiles into the candidate layout and record their provenance.

usage: python3 art/codex/terrain/sites_apply_picks.py <dest_root> <kind> <work_dir>:<cell> [...]
Each cell name starts with its level ("l2_01"); picks are renumbered per level in
the order given: <dest_root>/<kind>/l<level>_00.png, l<level>_01.png, ...
Writes picks.json beside them (source, sheet job, raw, gates, measures, sha256).
"""

import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sites_measure import measure_work  # noqa: E402

dest_root, kind, *specs = sys.argv[1:]
out = Path(dest_root) / kind
out.mkdir(parents=True, exist_ok=True)
for old in out.glob("l*_*.png"):
    old.unlink()
count: Counter[str] = Counter()
measures: dict[str, dict] = {}
record = []
for spec in specs:
    work, cell = spec.split(":")
    level = cell.split("_")[0]
    if level not in ("l1", "l2", "l3"):
        raise SystemExit(f"cell {cell!r} does not start with a level")
    src = Path(work) / "tiles" / f"{cell}.png"
    dst = out / f"{level}_{count[level]:02d}.png"
    count[level] += 1
    shutil.copyfile(src, dst)
    if work not in measures:
        measures[work] = measure_work(Path(work))
    m = measures[work][cell]
    record.append(
        {
            "file": dst.name,
            "level": int(level[1]),
            "source": str(src),
            "sheet_job": str(Path(work) / "job.json"),
            "raw": json.loads((Path(work) / "registration.json").read_text())["raw"],
            "gate_passed": m["gates_passed"],
            "failed_checks": m["failed"],
            "occupied": m["occupied"],
            "built": m["built"],
            "L": m["L"],
            "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
        }
    )
(out / "picks.json").write_text(json.dumps({"kind": kind, "picks": record}, indent=1) + "\n")
for r in record:
    print(f"{kind}/{r['file']} <- {r['source']} occ {r['occupied']:.2f} L {r['L']:.0f} {'PASS' if r['gate_passed'] else 'FAIL'}")
