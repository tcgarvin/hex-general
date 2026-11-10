"""Copy picked base cluster stages into the candidate layout with provenance.

usage: python3 art/codex/terrain/sites_apply_base.py <dest_root> <dir>=<work_dir>:<cluster> [...]
e.g.   ... candidates/sites2 stage4_a=art/codex/terrain/sites/base/s2-b:stage4_b
Writes <dest_root>/base/<dir>/h<i>.png + positions.json, and <dest_root>/base/picks.json
(registration, outer-rim gate failures, internal seam means, sha256 per tile).
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

dest_root, *specs = sys.argv[1:]
base_root = Path(dest_root) / "base"
base_root.mkdir(parents=True, exist_ok=True)
record = []
for spec in specs:
    name, src_spec = spec.split("=")
    work_s, cluster = src_spec.split(":")
    work = Path(work_s)
    src = work / "tiles" / cluster
    dst = base_root / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    pos = json.loads((dst / "positions.json").read_text())
    pos["cluster"] = name
    pos["source"] = str(src)
    (dst / "positions.json").write_text(json.dumps(pos, indent=1) + "\n")
    report = json.loads((work / "report.json").read_text())
    rep = report["clusters"][cluster]
    fit = report["registration"]["clusters"][cluster]
    failures = [
        f"{m}:{c['detail']}"
        for m, v in rep["members"].items()
        for c in v["checks"]
        if not c["passed"] and c.get("hard", True)
    ]
    record.append(
        {
            "dir": name,
            "source": str(src),
            "raw": str(next((work / "raw").glob("*.png"))),
            "registration": {k: fit[k] for k in ("width_ratio", "residual_tile_px", "iou")},
            "gate_failures": failures,
            "seams_mean": [s["mean"] for s in rep["seams"]],
            "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dst.glob("h*.png"))},
        }
    )
    print(f"base/{name} <- {src} {'PASS' if not failures else 'FAIL ' + '; '.join(failures)}")
(base_root / "picks.json").write_text(json.dumps(record, indent=1) + "\n")
