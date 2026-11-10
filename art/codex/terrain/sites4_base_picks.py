"""Apply the sites round 4 base picks to candidates/sites4/base and annotate picks.json.

usage: python3 art/codex/terrain/sites4_base_picks.py

Copies each pick with `sites_apply_base.py` (tiles, positions.json, registration,
outer-rim gate failures, seam means, sha256), then adds per pick: `stage`,
`flavour` ("full" / "building" for stages 1-2, "full" for 3-4), internal `seam_failures`,
the guide's solid share (`guide_arc`: 1.0 = closed bank guide; < 1 = the share
drawn as finished bank, the rest drawn as pale dashes for the marked-out trench)
and its start angle. Reused sites3 dirs must be byte-identical to the sites3 picks.
(A luminance classifier along the guide was tried and dropped: the bank's lit
crest is as light as the fresh trench, so it could not tell them apart.)
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WORK = HERE / "sites" / "base"
DEST = HERE / "candidates" / "sites4"
SITES3 = HERE / "candidates" / "sites3" / "base"

# dir -> (work dir, cluster, stage, flavour)
PICKS: dict[str, tuple[str, str, int, str]] = {
    "stage1_full_a": ("s4-mix-s1", "full_h", 1, "full"),
    "stage1_full_b": ("s4-full-a", "stage1_b", 1, "full"),
    "stage1_building_a": ("s4-mix-s1", "bld_h", 1, "building"),
    "stage1_building_b": ("s4-bld-a", "stage1_a", 1, "building"),
    "stage2_full_a": ("s4-mix-s2", "full_g", 2, "full"),
    "stage2_full_b": ("s4-mix-s2", "full_h", 2, "full"),
    "stage2_building_a": ("s4-mix-s2", "bld_g", 2, "building"),
    "stage2_building_b": ("s4-mix-s2", "bld_h", 2, "building"),
    "stage3_a": ("s3-a", "stage3_a", 3, "full"),
    "stage3_b": ("s4-late-a", "stage3_g", 3, "full"),
    "stage4_a": ("s3-a", "stage4_a", 4, "full"),
    "stage4_b": ("s4-late-a", "stage4_g", 4, "full"),
}
REUSED = {"stage3_a": "stage3_a", "stage4_a": "stage4_a"}  # sites4 dir -> sites3 dir

def main() -> int:
    specs = [f"{d}={WORK / w}:{c}" for d, (w, c, _, _) in PICKS.items()]
    subprocess.run([sys.executable, str(HERE / "sites_apply_base.py"), str(DEST), *specs], check=True)
    picks_path = DEST / "base" / "picks.json"
    record = json.loads(picks_path.read_text())
    for entry in record:
        work_name, cluster, stage, flavour = PICKS[entry["dir"]]
        work = WORK / work_name
        rep = json.loads((work / "report.json").read_text())["clusters"][cluster]
        side = json.loads(next(work.glob("*.sheet.json")).read_text())
        cl = next(c for c in side["clusters"] if c["name"] == cluster)
        entry["stage"] = stage
        entry["flavour"] = flavour
        entry["work_cluster"] = f"{work_name}:{cluster}"
        entry["seam_failures"] = [f"{s['a']}-{s['b']} mean {s['mean']}" for s in rep["seams"] if not s["passed"]]
        entry["guide_arc"] = cl["guide_arc"]["arc"] if "guide_arc" in cl else 1.0
        if "guide_arc" in cl:
            entry["guide_start_deg"] = cl["guide_arc"]["start_deg"]
        if entry["dir"] in REUSED:
            src = SITES3 / REUSED[entry["dir"]]
            for name, sha in entry["sha256"].items():
                if hashlib.sha256((src / name).read_bytes()).hexdigest() != sha:
                    raise RuntimeError(f"{entry['dir']}/{name} differs from the sites3 pick {src / name}")
            entry["reused_from"] = str(src.relative_to(REPO))
    picks_path.write_text(json.dumps(record, indent=1) + "\n")
    for e in record:
        print(
            f"{e['dir']:18s} {e['work_cluster']:20s} reg {e['registration']['residual_tile_px']:.2f} "
            f"IoU {e['registration']['iou']:.3f} rim {'pass' if not e['gate_failures'] else e['gate_failures']} "
            f"seams {e['seams_mean']} {e['seam_failures'] or ''} arc {e['guide_arc']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
