"""Apply the sites round 4 oil tank repaint to candidates/sites4/oil_field/.

Each current pick (kept in sites/oil_field/s4-tanks-l<lv>/pick_before/) is replaced by its
tank-region composite in sites/oil_field/s4-tanks-l<lv>/final/<cell>.png
(`sites4_oilfac_tanks.py composite`). picks.json keeps every field of the previous record
under "before" and adds the repaint provenance. `occupied` is carried over (the repaint
only touches the tank compound; it is measured on the paint-in cut, which this tile no
longer has); `occupied_final_tile`, built and L are measured on the new tile against the
cell's original ground base (`sites_measure.py` definitions, final tile in place of the
cut, which reads lower wherever ground was restored). Gates are inherited:
the composite leaves every pixel within the `rim` distance of the hex boundary
byte-identical to the gate-passing pick (checked here; fails loudly otherwise).

usage: python3 art/codex/terrain/sites4_oilfac_tanks_apply.py <file>=<cell> [...]
       e.g. l2_00.png=l2_130
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
from paintin_geom import tile_distance  # noqa: E402
from paintin_pin import PinConfig, ground_restore  # noqa: E402
from r1_terrain_measure import grass_like, lum  # noqa: E402

CAND = HERE / "candidates" / "sites4" / "oil_field"
WORK = HERE / "sites" / "oil_field"


def rel(p: Path) -> str:
    return str(p.resolve().relative_to(REPO))


def rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=float)


def main() -> int:
    picks = json.loads((CAND / "picks.json").read_text())
    by_file = {p["file"]: p for p in picks["picks"]}
    hexm = tile_distance() >= 1.0
    for spec in sys.argv[1:]:
        file, cell = spec.split("=")
        level = int(file[1])
        work = WORK / f"s4-tanks-l{level}"
        final = work / "final" / f"{cell}.png"
        before = work / "pick_before" / file
        record = json.loads(Path(f"{final}.tanks.json").read_text())
        if hashlib.sha256(before.read_bytes()).hexdigest() != record["orig_sha256"]:
            raise SystemExit(f"{final}: composite was not made from {before}")
        changed = np.abs(rgb(final) - rgb(before)).max(-1) > 0
        near_rim = tile_distance() < record["rim"]
        if (changed & near_rim).any():
            raise SystemExit(f"{final}: pixels within {record['rim']} px of the boundary changed")
        old = by_file[file]
        src_job = json.loads((REPO / old["sheet_job"]).read_text())
        src_cell = Path(old["source"]).stem
        base_rel = next(c["base"] for c in src_job["cells"] if c["name"] == src_cell)
        base = rgb((REPO / old["sheet_job"]).parent / base_rel)
        cfg = PinConfig.from_json(src_job["pin"])
        new = rgb(final)
        matte = ground_restore(new, base, cfg)[1]
        shutil.copyfile(final, CAND / file)
        by_file[file] = {
            "file": file,
            "level": level,
            "source": rel(final),
            "sheet_job": rel(work / "job.json"),
            "raw": json.loads((work / "registration.json").read_text())["raw"],
            "gate_passed": old["gate_passed"],
            "failed_checks": old["failed_checks"],
            "occupied": old["occupied"],
            "occupied_final_tile": round(float((matte[hexm] > 0.5).mean()), 3),
            "built": round(float((~grass_like(new))[hexm].mean()), 3),
            "L": round(float(np.median(lum(new)[hexm])), 1),
            "sha256": hashlib.sha256((CAND / file).read_bytes()).hexdigest(),
            "grade": "none",
            "tank_repaint": {
                "composite": rel(Path(f"{final}.tanks.json")),
                "repaint_cell": cell,
                "changed_px": int(changed.sum()),
                "gates": "inherited: no pixel within the rim distance changed",
            },
            "before": old,
        }
        print(f"{file} <- {rel(final)} occ(final tile) {by_file[file]['occupied_final_tile']}")
    picks["picks"] = [by_file[p["file"]] for p in picks["picks"]]
    (CAND / "picks.json").write_text(json.dumps(picks, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
