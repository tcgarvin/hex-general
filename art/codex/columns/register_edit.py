#!/usr/bin/env python3
"""Scale-match edited copies of a shipped unit to that unit's ratified footprint.

unit.py scales every sprite so its solid bbox hits ``target_bbox_w``. An edit
that adds riders can change the bbox (a rider overhanging the hull), so a
fixed 90 px would shrink or grow the hull relative to the deployed sprite.
Instead this measures how much bigger the edit is than the shipped source,
using the TRACK run (bottom 15 % of the solid bbox, which the edits must not
touch), and sets each edit's target so the tank ends up at exactly the shipped
scale:

    k        = edit_track_w / source_track_w
    target_w = round(edit_solid_w * shipped_target_w / (source_solid_w * k))

Writes <edit-dir>/targets.json ({name: {target_bbox_w, mirror, k}}), which
build_pairs.py applies as per-candidate overrides. ``mirror`` is copied from
the shipped unit (the edits keep the source's native east facing).

Usage: register_edit.py <edit-dir> --unit armor
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[3]
FOUNDRY = REPO / "art" / "foundry"
SOLID = 0.5
TRACK_BAND = (0.85, 1.0)


def measure(path: Path) -> tuple[int, int]:
    """(solid bbox width, track-run width) in source pixels."""
    a = np.asarray(Image.open(path).convert("RGBA"))[..., 3] / 255.0
    ys, xs = np.nonzero(a >= SOLID)
    y0, y1 = int(ys.min()), int(ys.max())
    h = y1 - y0 + 1
    band = a[y0 + int(TRACK_BAND[0] * h) : y0 + int(TRACK_BAND[1] * h)]
    tx = np.nonzero((band >= SOLID).any(axis=0))[0]
    return int(xs.max() - xs.min() + 1), int(tx.max() - tx.min() + 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("edit_dir", type=Path)
    ap.add_argument("--unit", required=True)
    args = ap.parse_args()
    spec = json.loads((FOUNDRY / "manifest.json").read_text())["units"]["units"][args.unit]
    src_solid, src_track = measure(FOUNDRY / spec["source"])
    targets = {}
    for crop in sorted((args.edit_dir / "src").glob("*.png")):
        solid, track = measure(crop)
        k = track / src_track
        targets[crop.stem] = {
            "target_bbox_w": round(solid * spec["target_bbox_w"] / (src_solid * k)),
            "mirror": spec["mirror"],
            "k": round(k, 4),
        }
        print(crop.stem, targets[crop.stem])
    (args.edit_dir / "targets.json").write_text(json.dumps(targets, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
