#!/usr/bin/env python3
"""Write the round-1 oriented-terrain paint-in jobs (rivers, plains roads, bridges).

Every job is a `scripts/tile-foundry/paintin.py` job file; cell names are the
frontend tile names (`frontend/src/board/riverTiles.ts`, `roadTiles.ts`) plus a
`_NN` variant suffix, so `tiles/<cell>.png` drops straight into
`candidates/r1/<terrain>/`. Bases rotate through the G2 plains set
(`candidates/r1/plains/00..05.png`). Rerun after editing; jobs are plain JSON.

    python3 art/codex/terrain/make_r1_jobs.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
# opaque crops of the G2 plains (ground/G2/source.png at set.json offsets),
# identical to candidates/r1/plains inside the hex
PLAINS = [f"../../bases/g2/{i:02d}.png" for i in range(6)]

# frontend shape name -> connected edges (0=SE 1=NE 2=N 3=NW 4=SW 5=S)
SHAPES: dict[str, tuple[int, ...]] = {
    "straight_v": (2, 5),
    "straight_d": (0, 3),
    "turn_n_se": (0, 2),
    "turn_s_nw": (3, 5),
    "turn_se_sw": (0, 4),
    "turn_ne_nw": (1, 3),
    "end_n": (2,),
    "end_s": (5,),
    "end_se": (0,),
    "end_ne": (1,),
    "fork_se_n_s": (0, 2, 5),
    "fork_ne_n_s": (1, 2, 5),
    "fork_se_ne_nw": (0, 1, 3),
    "fork_se_n_nw": (0, 2, 3),
    "fork_se_nw_sw": (0, 3, 4),
    "fork_se_nw_s": (0, 3, 5),
    "fork_se_n_sw": (0, 2, 4),
    "fork_ne_nw_s": (1, 3, 5),
    "fork_ne_n_nw": (1, 2, 3),
    "fork_se_ne_n": (0, 1, 2),
    "fork_se_ne_s": (0, 1, 5),
    "fork_se_sw_s": (0, 4, 5),
}

WATER = [67, 81, 77]  # concept-read A4
ROAD = [161, 140, 96]  # concept-read A6
DECK = [125, 111, 79]  # concept-read A5

RIVER_STYLE = {"width": 23, "meander": 9, "breathe": 0.12, "ragged": 0.9, "round_px": 7}
ROAD_STYLE = {"width": 9.5, "meander": 3, "ragged": 0.4, "round_px": 3}

# pin: rim pin plus a low-frequency tone match of Codex's ground to the base
# (removes the faint hex outline its re-rendered ground leaves; ground restore
# would cut away bank tufts), feature colour pulled 80% of the way to the
# concept anchor per sheet so every sheet's water/road agrees
RIVER_PIN = {"restore": False, "feature_strength": 0.8, "feature_rgb": WATER, "tone_sigma": 6.0}
ROAD_PIN = {"restore": False, "feature_strength": 0.8, "feature_rgb": ROAD, "tone_sigma": 6.0}

GRASS_ANCHORS = {"lum": 96.7, "hue": 62.0}
RIVER_ANCHORS = {**GRASS_ANCHORS, "feature_lum": 76.0, "feature_sat": 0.17}
ROAD_ANCHORS = {**GRASS_ANCHORS, "feature_lum": 141.0, "feature_sat": 0.40}


def cell(name: str, slot: int, guide: dict, base: str | None = None) -> dict:
    return {"name": name, "slot": slot, "base": base or PLAINS[slot % 6], "guide": guide}


def river_guide(shape: str, seed: int, **extra) -> dict:
    return {"kind": "river", "edges": list(SHAPES[shape]), "color": WATER,
            **RIVER_STYLE, "seed": seed, **extra}


def road_guide(shape: str, seed: int) -> dict:
    return {"kind": "road", "edges": list(SHAPES[shape]), "color": ROAD,
            **ROAD_STYLE, "seed": seed}


def write(path: Path, job: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job, indent=1) + "\n")
    print(f"wrote {path.relative_to(HERE)} ({len(job['cells'])} cells)")


def river_a(layout: str = "4x4", name: str = "river-a-v2") -> dict:
    """Straights (3 each), the four turns (as shipped), the four ends."""
    plan = [("straight_v", 3), ("straight_d", 3), ("turn_n_se", 1), ("turn_s_nw", 2),
            ("turn_se_sw", 2), ("turn_ne_nw", 1), ("end_n", 1), ("end_s", 1),
            ("end_se", 1), ("end_ne", 1)]
    cells = []
    for shape, n in plan:
        for v in range(n):
            slot = len(cells)
            cells.append(cell(f"{shape}_{v:02d}", slot, river_guide(shape, 100 + slot)))
    return {
        "name": name,
        "layout": layout,
        "cells": cells,
        "profile": {"extract": {"cell": "straight_v_00", "edges": [2, 5]},
                    "bank_px": 3.0, "feather_px": 4.0},
        "pin": RIVER_PIN,
        "gates": {},
        "anchors": RIVER_ANCHORS,
    }


FORKS_SHIPPED = [("fork_ne_n_s", 2), ("fork_ne_nw_s", 1), ("fork_se_ne_nw", 1),
                 ("fork_se_ne_s", 1), ("fork_se_n_nw", 1), ("fork_se_n_s", 2),
                 ("fork_se_n_sw", 1), ("fork_se_nw_s", 1), ("fork_se_nw_sw", 2),
                 ("fork_se_sw_s", 2), ("fork_ne_n_nw", 1), ("fork_se_ne_n", 1)]
POND = {"kind": "river", "edges": [], "color": WATER, **RIVER_STYLE, "pond": 48, "seed": 7}


def river_job(name: str, layout: str, cells: list[dict]) -> dict:
    return {
        "name": name,
        "layout": layout,
        "cells": cells,
        "profile": {"path": "../profile.json"},
        "pin": RIVER_PIN,
        "gates": {},
        "anchors": RIVER_ANCHORS,
    }


def fork_cells(start: int, seed0: int) -> list[dict]:
    cells = []
    for shape, n in FORKS_SHIPPED:
        for v in range(n):
            slot = start + len(cells)
            cells.append(cell(f"{shape}_{v:02d}", slot, river_guide(shape, seed0 + slot)))
    return cells


def river_b() -> dict:
    """All twelve fork shapes, with as many variants as shipped (16 cells)."""
    return river_job("river-b", "4x4", fork_cells(0, 200))


def river_s6() -> dict:
    """Scale test at 1:1 (6x6, 128 px hexes): river-a's 16 cells (same guides),
    then the forks, the pond and three spare turn variants (36 cells)."""
    a = river_a()["cells"]
    cells = [dict(c) for c in a]
    forks = fork_cells(16, 200 - 16)  # same seeds as river-b
    cells += forks
    cells.append(cell("pond_00", 32, POND))
    for i, shape in enumerate(("turn_n_se", "turn_ne_nw", "end_s")):
        cells.append(cell(f"{shape}_alt", 33 + i, river_guide(shape, 300 + i)))
    job = river_job("river-s6", "6x6", cells)
    # at 1:1 one output px is one tile px; Codex's ~0.8 px drift is under the
    # rim ramp (1.5 + 6 px), so the 2:1 limits (0.75 px / IoU 0.97) are relaxed
    job["gates"] = {"max_residual_tile_px": 1.0, "min_iou": 0.96}
    return job


def road_job(name: str, cells: list[dict], profile: dict) -> dict:
    return {
        "name": name,
        "layout": "4x4",
        "cells": cells,
        "profile": profile,
        "pin": ROAD_PIN,
        "gates": {},
        "anchors": ROAD_ANCHORS,
    }


def road_cells(plan: list[tuple[str, int]], seed0: int, suffix_from: dict | None = None) -> list[dict]:
    cells = []
    for shape, n in plan:
        first = (suffix_from or {}).get(shape, 0)
        for v in range(first, first + n):
            slot = len(cells)
            cells.append(cell(f"plains_{shape}_{v:02d}", slot, road_guide(shape, seed0 + slot)))
    return cells


def road_a() -> dict:
    """Plains straights (3 each), turns (2 each), the N and S ends."""
    plan = [("straight_v", 3), ("straight_d", 3), ("turn_n_se", 2), ("turn_s_nw", 2),
            ("turn_se_sw", 2), ("turn_ne_nw", 2), ("end_n", 1), ("end_s", 1)]
    return road_job("road-a", road_cells(plan, 400),
                    {"extract": {"cell": "plains_straight_v_00", "edges": [2, 5]},
                     "bank_px": 2.0, "feather_px": 3.0})


def road_b() -> dict:
    """The other two ends, all twelve forks, and two spare variants (16 cells)."""
    plan = [("end_se", 1), ("end_ne", 1)] + [(f, 1) for f, _ in FORKS_SHIPPED] + [
        ("straight_v", 1), ("turn_se_sw", 1)]
    return road_job("road-b", road_cells(plan, 500, {"straight_v": 3, "turn_se_sw": 2}),
                    {"path": "../profile.json"})


def river_c() -> dict:
    """Re-rolls and missing names: the pond (river/00), fork_se_nw_sw_00 (failed
    its NW hand-over width in river-b), and second takes of the single-variant
    turns, the ends and the tight fork_se_sw_s."""
    plan = [("fork_se_nw_sw", "fork_se_nw_sw_00a"), ("fork_se_nw_sw", "fork_se_nw_sw_00b"),
            ("end_n", "end_n_alt"), ("end_s", "end_s_alt"), ("end_se", "end_se_alt"),
            ("end_ne", "end_ne_alt"), ("turn_n_se", "turn_n_se_alt"),
            ("turn_ne_nw", "turn_ne_nw_alt"), ("fork_se_sw_s", "fork_se_sw_s_alt0"),
            ("fork_se_sw_s", "fork_se_sw_s_alt1")]
    cells = [cell("pond_00", 0, POND)]
    for shape, name in plan:
        slot = len(cells)
        cells.append(cell(name, slot, river_guide(shape, 600 + slot)))
    return river_job("river-c", "4x4", cells)


def road_c() -> dict:
    """Road re-rolls: the four ends with the round dead-end geometry (road-a/b
    ends tapered to a spear point), two takes of each fork that failed in road-b,
    and second takes of six more forks."""
    plan = [("end_n", 2), ("end_s", 2), ("end_se", 1), ("end_ne", 1),
            ("fork_se_n_s", 2), ("fork_se_ne_n", 2), ("fork_ne_n_s", 1), ("fork_ne_nw_s", 1),
            ("fork_se_ne_nw", 1), ("fork_se_ne_s", 1), ("fork_se_n_nw", 1), ("fork_se_n_sw", 1)]
    cells = []
    for shape, n in plan:
        for v in range(n):
            slot = len(cells)
            guide = road_guide(shape, 700 + slot)
            if shape.startswith("end_"):
                guide["end_taper"] = 0.8
            cells.append(cell(f"plains_{shape}_c{v}", slot, guide))
    return road_job("road-c", cells, {"path": "../profile.json"})


DECK_LENGTH = 52.0  # tile px: about 40% of the hex width, 2.4x the river (concept ~48% of W)
DECK_WIDTH = 22.0  # tile px: deck + parapets + south face, ~2.3x the road (concept 2.5-3x)
RIVER_HOLD_HALF = 17.0  # river profile core half (~13) + feather (4)

# bridge name -> (river tile it is painted over, road edges, deck orientation)
BRIDGES = {
    "straight_v": ("straight_v_01", [0, 4], "across"),  # E-W deck, road SE->SW turn
    "turn_se_sw": ("turn_se_sw_01", [2, 5], "along"),  # N-S deck, road N->S
    "turn_ne_nw": ("turn_ne_nw_00", [2, 5], "along"),
    "00": ("straight_v_00", [], "across"),  # generic: deck only, short approaches
}


def bridge_base(river_name: str) -> str:
    """An opaque base for a bridge: the picked river tile over the G2 ground it
    was painted on (the river tile's alpha stops 1 px outside the hex)."""
    from PIL import Image

    picks = json.loads((HERE / "picks.json").read_text())
    src = picks[f"river/{river_name}.png"]
    work = HERE / src["work"]
    sidecar = json.loads(next(work.glob("*.sheet.json")).read_text())
    cell_rec = next(c for c in sidecar["cells"] if c["name"] == src["cell"])
    ground = Image.open(HERE.parents[2] / cell_rec["base"]).convert("RGBA")
    tile = Image.open(HERE / "candidates" / "r1" / "river" / f"{river_name}.png").convert("RGBA")
    ground.alpha_composite(tile)
    out = HERE / "bridge" / "bases" / f"{river_name}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    ground.convert("RGB").save(out)
    return f"../bases/{river_name}.png"


def deck_centre(river_name: str, road_edges: list[int], orientation: str) -> tuple[float, float]:
    """Where the deck goes: the middle of the water the road crosses, measured
    on the river tile (water classifier), along x = 64 for a N-S road or along
    the road centreline otherwise; y = 64 for the generic deck."""
    import sys

    import numpy as np
    from PIL import Image

    sys.path.insert(0, str(HERE.parents[2] / "scripts" / "tile-foundry"))
    from paintin_geom import Guide
    from paintin_pin import water_mask

    rgb = np.asarray(Image.open(HERE / "candidates" / "r1" / "river" / f"{river_name}.png").convert("RGB"), dtype=float)
    water = water_mask(rgb) > 0.5
    if not road_edges:
        xs = np.flatnonzero(water[64])
        return (float(xs.min() + xs.max() + 1) / 2.0, 64.0)
    if road_edges == [2, 5]:
        ys = np.flatnonzero(water[:, 64])
        return (64.0, float(ys.min() + ys.max() + 1) / 2.0)
    g = Guide("road", tuple(road_edges), ROAD_STYLE["width"], tuple(ROAD), None, meander=0.0, ragged=0.1)
    pts = g.courses()[0]
    inside = [p for p in pts if 0 <= p[0] < 128 and 0 <= p[1] < 128 and water[int(p[1]), int(p[0])]]
    if not inside:
        raise RuntimeError(f"road {road_edges} does not cross the water of {river_name}")
    mid = inside[len(inside) // 2]
    return (round(float(mid[0]), 2), round(float(mid[1]), 2))


def bridge_job() -> dict:
    cells = []
    for name, (river, road_edges, orient) in BRIDGES.items():
        base = bridge_base(river)
        centre = deck_centre(river, road_edges, orient)
        river_edges = [2, 5] if river.startswith("straight_v") else list(SHAPES[river[:-3]])
        for take in range(2):
            slot = len(cells)
            guide = {
                "kind": "road", "edges": road_edges, "color": ROAD, "width": ROAD_STYLE["width"],
                "meander": 0.0, "ragged": 0.3, "seed": 800 + slot,
                "hold_edges": river_edges, "hold_half": RIVER_HOLD_HALF,
                "marker": {"kind": "bridge", "axis_edges": [2, 5], "length": DECK_LENGTH,
                           "width": DECK_WIDTH, "color": DECK, "centre": list(centre),
                           "rotate_deg": 90.0 if orient == "along" else 0.0},
            }
            cells.append({"name": f"{name}_t{take}", "slot": slot, "base": base, "guide": guide})
    return {
        "name": "bridge-a-v2",
        "layout": "4x4",
        "cells": cells,
        "profile": {"path": "../../road/profile.json"},
        # no colour normalisation: the road classifier also sees the deck
        # Codex repaints the whole cell (lime grass, lighter water) when asked
        # for a bridge, so only the bridge + track region is taken from it
        "pin": {"restore": False, "tone_sigma": 6.0, "paint_region_px": 5.0},
        "gates": {},
        "anchors": GRASS_ANCHORS,
    }


NUMBER_WORDS = {9: "nine", 16: "sixteen", 25: "twenty-five", 36: "thirty-six"}
GRID = {"3x3": (3, 3), "4x4": (4, 4), "5x5": (5, 5), "6x6": (6, 6)}


def write_prompt(template: Path, out: Path, layout: str) -> None:
    """Fill a prompt template's {count}/{rows}/{cols} for the sheet layout."""
    rows, cols = GRID[layout]
    text = template.read_text().format(count=NUMBER_WORDS[rows * cols], rows=rows, cols=cols)
    out.write_text(text)
    print(f"wrote {out.relative_to(HERE)}")


def main() -> None:
    job_a = river_a()
    job_a["profile"] = {"path": "../profile.json"}  # extracted from river-a-v2 straight_v_00
    write(HERE / "river" / "a" / "job.json", job_a)
    write(HERE / "river" / "b" / "job.json", river_b())
    write(HERE / "river" / "s6" / "job.json", river_s6())
    write(HERE / "road" / "a" / "job.json", road_a())
    write(HERE / "road" / "b" / "job.json", road_b())
    write(HERE / "road" / "c" / "job.json", road_c())
    write(HERE / "river" / "c" / "job.json", river_c())
    for sub in ("a", "b", "c"):
        write_prompt(HERE / "road" / "prompt.template.txt", HERE / "road" / sub / "prompt.txt", "4x4")
    write(HERE / "bridge" / "a" / "job.json", bridge_job())
    rows, cols = GRID["4x4"]
    (HERE / "bridge" / "a" / "prompt.txt").write_text(
        (HERE / "bridge" / "prompt.template.txt").read_text().format(count="eight", rows=2, cols=4)
    )
    tpl = HERE / "river" / "prompt.template.txt"
    for sub, layout in (("a", "4x4"), ("b", "4x4"), ("c", "4x4"), ("s6", "6x6")):
        write_prompt(tpl, HERE / "river" / sub / "prompt.txt", layout)


if __name__ == "__main__":
    main()
