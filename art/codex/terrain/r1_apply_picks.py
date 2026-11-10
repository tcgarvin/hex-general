"""Copy r1 picks into the candidate tile layout and record where each came from.

usage: python3 art/codex/terrain/r1_apply_picks.py <dest_root> <class> <work_dir>:<cell>[:<tiles_subdir>] [...]
(tiles_subdir defaults to "tiles"; r2 graded tiles live in "tiles_graded", with a .grade.json beside each.)
Writes <dest_root>/<class>/00.png.. in the order given, plus picks.json beside them.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

dest_root, cls, *specs = sys.argv[1:]
out = Path(dest_root) / cls
out.mkdir(parents=True, exist_ok=True)
for old in out.glob("*.png"):
    old.unlink()
record = []
for i, spec in enumerate(specs):
    work, cell, *sub = spec.split(":")
    src = Path(work) / (sub[0] if sub else "tiles") / f"{cell}.png"
    dst = out / f"{i:02d}.png"
    shutil.copyfile(src, dst)
    report = json.loads((Path(work) / "report.json").read_text())
    tile = report["tiles"][cell]
    record.append(
        {
            "file": dst.name,
            "source": str(src),
            "sheet_job": str(Path(work) / "job.json"),
            "raw": json.loads((Path(work) / "registration.json").read_text())["raw"],
            "gate_passed": tile["passed"],
            "failed_checks": [c["detail"] for c in tile["checks"] if not c["passed"] and c["hard"]],
            "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
            "grade": (
                json.loads(Path(f"{src}.grade.json").read_text())["params"]
                if Path(f"{src}.grade.json").exists()
                else None
            ),
        }
    )
(out / "picks.json").write_text(json.dumps(record, indent=1) + "\n")
print(f"{out}: {len(record)} tiles")
