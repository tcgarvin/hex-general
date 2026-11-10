#!/usr/bin/env python3
"""Measure round-1 oriented tiles against the concept-read acceptance values.

Per tile: feature core (classifier mask > 0.5, >3 px inside the hex) median
luminance / saturation / RGB, the base ground's median luminance and the step
between them, and from a paintin report.json the final crossing centre/width at
every connected edge. Prints one line per tile and a summary.

    python3 art/codex/terrain/r1_measure.py --sidecar <sheet.json> --work <dir> [--tiles <dir>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))

from paintin_gate import feature_colour_stats  # noqa: E402
from paintin_sheet import cell_guide, load_base, load_sidecar, repo_path  # noqa: E402

ACCEPT = {
    "river": {"lum": (70, 82), "sat": (0.0, 0.22), "delta_lum_max": -15},
    "road": {"lum": (130, 150), "sat": (0.35, 0.45)},
}


def crossings(report: dict, name: str) -> list[str]:
    out = []
    for c in report["tiles"][name]["checks"]:
        if c["name"].startswith("cross_final"):
            d = c["detail"]
            width = d.split("width ")[1].split(" vs")[0]
            out.append(f"e{c['name'][-1]} {c['value']:+.2f}/{width}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sidecar", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--tiles", type=Path)
    ap.add_argument("--kind", help="override the classifier kind (bridges: river)")
    args = ap.parse_args()
    side = load_sidecar(args.sidecar)
    report = json.loads((args.work / "report.json").read_text())
    tiles = args.tiles or args.work / "tiles"
    rows = []
    for cell in side["cells"]:
        guide = cell_guide(cell)
        if guide is None:
            continue
        kind = args.kind or guide.kind
        rgba = np.asarray(Image.open(tiles / cell["output"]).convert("RGBA"))
        base = load_base(repo_path(cell["base"])).tile
        st = feature_colour_stats(rgba, base, kind)
        acc = ACCEPT.get(kind, {})
        ok = []
        if "lum" in acc:
            ok.append(acc["lum"][0] <= st["lum"] <= acc["lum"][1])
            ok.append(acc["sat"][0] <= st["sat"] <= acc["sat"][1])
        if "delta_lum_max" in acc:
            ok.append(st["delta_lum"] <= acc["delta_lum_max"])
        rows.append(st)
        print(
            f"{cell['name']:<18} {kind:<5} L {st['lum']:6.1f} S {st['sat']:.3f} "
            f"RGB {st['rgb']} dL {st['delta_lum']:+.1f} px {st['px']:5d} "
            f"{'ok' if all(ok) else 'OUT'}  cross " + " ".join(crossings(report, cell["name"]))
        )
    lums = [r["lum"] for r in rows]
    sats = [r["sat"] for r in rows]
    print(f"summary: L {min(lums):.1f}-{max(lums):.1f} (median {np.median(lums):.1f}), "
          f"S {min(sats):.3f}-{max(sats):.3f}, dL {min(r['delta_lum'] for r in rows):+.1f}.."
          f"{max(r['delta_lum'] for r in rows):+.1f}")


if __name__ == "__main__":
    main()
