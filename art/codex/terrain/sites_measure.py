"""Measure site tiles: occupied share, whole-hex luminance, gate results.

occupied = share of the hex (inset 1 px) covered by the ground-restore matte,
i.e. where the painting differs from the plain base ground (closed and
smoothed exactly as `paintin_pin.ground_restore` does with the job's pin
settings). This counts field strips, yards and stained ground as occupied,
which a "non-grass colour" test misses for green fields.
built = share of non-grass-coloured pixels (r1_terrain_measure.grass_like).

usage: python3 art/codex/terrain/sites_measure.py <work_dir> [...] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts" / "tile-foundry"))
from paintin_geom import tile_distance  # noqa: E402
from paintin_pin import PinConfig, ground_restore  # noqa: E402
from paintin_sheet import load_base, load_sidecar, repo_path  # noqa: E402
from r1_terrain_measure import grass_like, lum  # noqa: E402


def measure_work(work: Path) -> dict[str, dict]:
    sidecar = load_sidecar(next(work.glob("*.sheet.json")))
    cfg = PinConfig.from_json(sidecar["pin"])
    report = json.loads((work / "report.json").read_text())
    hexm = tile_distance() >= 1.0
    out = {}
    for cell in sidecar["cells"]:
        name = cell["name"]
        base = load_base(repo_path(cell["base"])).tile
        cut = np.asarray(Image.open(work / "cut" / cell["output"]).convert("RGB"), dtype=float)
        final = np.asarray(Image.open(work / "tiles" / cell["output"]).convert("RGB"), dtype=float)
        matte = ground_restore(cut, base, cfg)[1]
        t = report["tiles"][name]
        out[name] = {
            "occupied": round(float((matte[hexm] > 0.5).mean()), 3),
            "built": round(float((~grass_like(final))[hexm].mean()), 3),
            "L": round(float(np.median(lum(final)[hexm])), 1),
            "gates_passed": t["passed"],
            "failed": [c["detail"] for c in t["checks"] if not c["passed"] and c["hard"]],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("work", nargs="+", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    allr = {}
    for w in args.work:
        r = measure_work(w)
        allr[str(w)] = r
        for n, v in r.items():
            print(f"{w.parent.name}/{w.name}/{n}: occ {v['occupied']:.2f} built {v['built']:.2f} L {v['L']:.0f} {'PASS' if v['gates_passed'] else 'FAIL ' + '; '.join(v['failed'])}")
    if args.json:
        args.json.write_text(json.dumps(allr, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
