#!/usr/bin/env python3
"""Write art/codex/ui/manifest.json from each raw's run.json plus the cell lists below.

Usage: build_manifest.py   (run from anywhere; edit SHEETS / PICKS when a round changes)
"""

from __future__ import annotations

import json
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))

GLYPH_CORE = "manpower,fuel,steel,barracks,depot,workshop,infantry,armor,artillery,army,turn,column"
GLYPH_WORLD = "garrison,base,city,farm,oilfield,steelmill,factory,travel,deploy,disband"

# (set, sheet, cells row-major as written by the cut script, cut command)
SHEETS = [
    ("smoke", "smoke-1", [], "none (smoke test of the Codex call)"),
    ("glyphs", "core-A", [f"{n}-A" for n in GLYPH_CORE.split(",")],
     f"python3 cut_glyphs.py raw/core-A.png --grid 4x3 --names {GLYPH_CORE} --take A --out-dir cut"),
    ("glyphs", "core-B", [f"{n}-B" for n in GLYPH_CORE.split(",")],
     f"python3 cut_glyphs.py raw/core-B.png --grid 4x3 --names {GLYPH_CORE} --take B --out-dir cut"),
    ("glyphs", "world-A", [f"{n}-A" for n in GLYPH_WORLD.split(",")],
     f"python3 cut_glyphs.py raw/world-A.png --grid 5x2 --names {GLYPH_WORLD} --take A --out-dir cut"),
    ("glyphs", "world-B", [f"{n}-B" for n in GLYPH_WORLD.split(",")],
     f"python3 cut_glyphs.py raw/world-B.png --grid 5x2 --names {GLYPH_WORLD} --take B --out-dir cut"),
    ("glyphs", "fix-1",
     "garrison-C,garrison-D,steelmill-C,steelmill-D,factory-C,city-C,infantry-C,infantry-D,artillery-C,army-C,army-D,oilfield-C".split(","),
     "python3 cut_glyphs.py raw/fix-1.png --grid 4x3 --names <cells> --out-dir cut"),
    ("emblem", "sheet-1", "s1-winghex,s1-baton,s1-tankhex,s1-pin,s1-hexshield,s1-dividers".split(","),
     "python3 cut_emblems.py raw/sheet-1.png --grid 3x2 --names <cells> --out-dir cut"),
    ("emblem", "sheet-2", "s2-wingshield,s2-medallion,s2-laurel,s2-compactwing,s2-gunsight,s2-chevron".split(","),
     "python3 cut_emblems.py raw/sheet-2.png --grid 3x2 --names <cells> --out-dir cut"),
] + [
    ("keyart", n, [], "none (single painting; review/mock-<name>.png by mock_signin.py)")
    for n in ("centre-1", "centre-2", "centre-3", "right-1", "right-2", "right-3")
]

LABELS = {
    "emblem": {
        "emblem-1": "s1-winghex", "emblem-2": "s2-wingshield", "emblem-3": "s2-compactwing",
        "emblem-4": "s1-tankhex", "emblem-5": "s2-medallion", "emblem-6": "s2-laurel",
        "emblem-7": "s2-gunsight", "emblem-8": "s1-hexshield",
    },
    "keyart": {
        "keyart-1-centre": "centre-1", "keyart-2-centre": "centre-2", "keyart-3-centre": "centre-3",
        "keyart-4-right": "right-1", "keyart-5-right": "right-2", "keyart-6-right": "right-3",
    },
}


def main() -> None:
    picks = json.load(open(os.path.join(HERE, "picks.json")))
    sheets = []
    for set_name, sheet, cells, cut in SHEETS:
        raw = os.path.join(HERE, set_name, "raw", f"{sheet}.png")
        run = json.load(open(os.path.join(HERE, set_name, "raw", f"{sheet}.run.json")))
        im = Image.open(raw)
        sheets.append(
            {
                "set": set_name,
                "sheet": sheet,
                "prompt": f"{set_name}/prompts/{sheet}.txt",
                "raw": f"{set_name}/raw/{sheet}.png",
                "raw_size": list(im.size),
                "raw_mode": im.mode,
                **run,
                "cells_row_major": cells,
                "cut": cut,
            }
        )
    manifest = {
        "generated": "2026-11-10",
        "generator": "codex-cli 0.165.0, codex exec, built-in image_generation tool (ChatGPT login)",
        "status": "candidates only, not ratified, not applied",
        "codex_calls": len(sheets),
        "labels": LABELS,
        "first_pass_picks": picks,
        "sheets": sheets,
    }
    with open(os.path.join(HERE, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")


if __name__ == "__main__":
    main()
