#!/usr/bin/env python3
"""Split column source crops into base + tint-mask pairs with the unit foundry.

Runs ``scripts/tile-foundry/unit.py``'s own ``build_unit`` (classify, split,
premultiplied LANCZOS scale, rim bake, gates) unmodified: this script only
assembles an in-memory manifest section of the same shape as
``art/foundry/manifest.json -> units``, taking ``classify`` and ``rim`` from
that section verbatim so the column pairs are cut exactly like the shipped
deployed sprites.

Differences from the shipped section, all per candidate:
  - ``mirror`` is false: Codex was asked for west-facing art directly.
  - ``target_bbox_w``: 90 px for vehicles (as armor/artillery), 81 px for the
    on-foot marching files (as deployed infantry).
  - ``coverage_band`` is wide open ([0.02, 0.98]); the coverage is reported
    for review instead, since there is no ratified band for columns yet.

Per-candidate overrides: if ``<sheet-dir>/targets.json`` exists (written by
register_edit.py for edits of a shipped sprite), its ``target_bbox_w`` and
``mirror`` replace the defaults above for the candidates it names.

A candidate that fails a gate (cobalt residue in the base, canvas overflow,
...) is reported with the gate message and gets no PNGs; the run still exits
non-zero so a failure is never silent.

Usage: build_pairs.py <sheet-dir> [<sheet-dir> ...]
  reads <sheet-dir>/src/*.png, writes <sheet-dir>/<name>_{base,mask}.png and
  <sheet-dir>/build.json (per-candidate bbox, coverage or gate failure).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
from unit import UnitBuildError, build_unit  # noqa: E402

FOUNDRY_MANIFEST = REPO / "art" / "foundry" / "manifest.json"
VEHICLE_W = 90
ON_FOOT_W = 81
OPEN_BAND = [0.02, 0.98]


def target_width(name: str) -> int:
    return ON_FOOT_W if "march" in name else VEHICLE_W


def build_sheet(sheet_dir: Path, shipped: dict) -> bool:
    crops = sorted((sheet_dir / "src").glob("*.png"))
    if not crops:
        raise SystemExit(f"no source crops in {sheet_dir / 'src'}")
    targets_path = sheet_dir / "targets.json"
    overrides = json.loads(targets_path.read_text()) if targets_path.exists() else {}
    section = {
        "classify": shipped["classify"],
        "rim": shipped["rim"],
        "units": {
            c.stem: {
                "source": str(c.relative_to(sheet_dir)),
                "mirror": overrides.get(c.stem, {}).get("mirror", False),
                "target_bbox_w": overrides.get(c.stem, {}).get("target_bbox_w", target_width(c.stem)),
                "coverage_band": OPEN_BAND,
            }
            for c in crops
        },
    }
    results: dict[str, dict] = {}
    ok = True
    for name in section["units"]:
        try:
            base_png, mask_png, report = build_unit(sheet_dir, section, name)
        except UnitBuildError as exc:
            results[name] = {"gate_failure": str(exc)}
            print(f"{sheet_dir.name}/{name}: GATE FAIL {exc}")
            ok = False
            continue
        Image.fromarray(base_png, "RGBA").save(sheet_dir / f"{name}_base.png")
        Image.fromarray(mask_png, "RGBA").save(sheet_dir / f"{name}_mask.png")
        results[name] = {
            "target_bbox_w": section["units"][name]["target_bbox_w"],
            "bbox_wh": [report["width"], report["height"]],
            "coverage": round(report["coverage"], 3),
        }
        print(
            f"{sheet_dir.name}/{name}: {report['width']}x{report['height']} "
            f"coverage={report['coverage']:.3f}"
        )
    (sheet_dir / "build.json").write_text(json.dumps(results, indent=1) + "\n")
    return ok


def main(argv: list[str]) -> int:
    shipped = json.loads(FOUNDRY_MANIFEST.read_text())["units"]
    ok = all([build_sheet(Path(d).resolve(), shipped) for d in argv])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
