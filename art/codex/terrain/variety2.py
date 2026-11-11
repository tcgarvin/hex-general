#!/usr/bin/env python3
"""Variety round 2 (candidates, unratified): MEDIUM-footprint plains features,
homesteads and roadside/junction buildings, at the same world scale as round 1
but covering about a quarter to a third of the hex so they read at zoom 1.

Same workflow and tools as round 1 (`variety1.py`): paint-in on 3x3 sheets,
restore on, rim pin, gates; road cells sit on the opaque shipped road tile and
get `variety1.py hold`. Work lives in `candidates/variety-r2/`.

    python3 art/codex/terrain/variety2.py bases      # bases/road/*.png (straights, turns, forks)
    python3 art/codex/terrain/variety2.py jobs       # <group>/<job>/job.json + refs.txt
    python3 scripts/tile-foundry/paintin.py run --job <work>/job.json \\
        --prompt <work>/prompt.txt $(cat <work>/refs.txt) --out-dir <work>
    python3 art/codex/terrain/variety1.py hold <work>   # road sheets
    python3 art/codex/terrain/variety2.py candidates
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import variety1 as v1  # noqa: E402
from sites_measure import measure_work  # noqa: E402

ROUND = HERE / "candidates" / "variety-r2"
REFS_NATURE = (
    "--ref docs/concept-art/concept1.png --ref art/codex/terrain/refs/r1/town_2x.png "
    "--ref art/codex/terrain/refs/r1/forest_cluster_2x.png "
    "--ref art/codex/terrain/refs/r1/hills_tan_ridge_2x.png\n"
)
REFS = (
    "--ref docs/concept-art/concept1.png --ref art/codex/terrain/refs/r1/town_2x.png "
    "--ref art/codex/terrain/refs/r1/forest_cluster_2x.png\n"
)
REFS_HOME_SCALE = (
    REFS.rstrip("\n")
    + " --ref art/codex/terrain/candidates/variety-r2/refs/house-scale-ref.png\n"
)
PLAINS = "../../../../r1-bases/{:02d}.png"
ROAD = "../../bases/road/plains_{}.png"

ROAD_A = [
    "straight_v_00", "straight_v_01", "straight_v_02",
    "straight_d_00", "straight_d_01", "straight_d_02",
    "turn_n_se_00", "turn_s_nw_00", "turn_se_sw_00",
]  # fmt: skip
ROAD_B = [
    "turn_ne_nw_00", "turn_n_se_01", "turn_s_nw_01",
    "turn_se_sw_01", "turn_ne_nw_01", "straight_v_01",
    "straight_d_01", "straight_v_00", "straight_d_02",
]  # fmt: skip
# the eight forks that occur on generated worlds (seed 1: 67-148 hexes each),
# plus a second Y fork
ROAD_C = [
    "fork_ne_nw_s_00", "fork_se_n_sw_00", "fork_se_nw_sw_00",
    "fork_se_n_nw_00", "fork_ne_n_s_00", "fork_se_ne_nw_00",
    "fork_se_nw_s_00", "fork_se_n_s_00", "fork_se_n_sw_00",
]  # fmt: skip

JOBS: dict[str, tuple[list[tuple[str, str]], str]] = {
    "nature/m-a": (
        [(f"nm_a{i + 1}", PLAINS.format(i % 6)) for i in range(9)],
        REFS_NATURE,
    ),
    "nature/m-b": (
        [(f"nm_b{i + 1}", PLAINS.format((i + 3) % 6)) for i in range(9)],
        REFS_NATURE,
    ),
    "home/m-a": (
        [(f"hm_a{i + 1}", PLAINS.format((i + 1) % 6)) for i in range(9)],
        REFS,
    ),
    "home/m-b": (
        [(f"hm_b{i + 1}", PLAINS.format((i + 4) % 6)) for i in range(9)],
        REFS,
    ),
    # re-rolls of m-a / m-b: their houses came out 1.5-2x the round-1 and town
    # house size; these attach round-1 homesteads at sheet scale as a yardstick
    "home/m-c": (
        [(f"hm_c{i + 1}", PLAINS.format((i + 2) % 6)) for i in range(9)],
        REFS_HOME_SCALE,
    ),
    "home/m-d": (
        [(f"hm_d{i + 1}", PLAINS.format((i + 5) % 6)) for i in range(9)],
        REFS_HOME_SCALE,
    ),
    "road/m-a": (
        [(f"rm_a{i + 1}", ROAD.format(n)) for i, n in enumerate(ROAD_A)],
        REFS,
    ),
    "road/m-b": (
        [(f"rm_b{i + 1}", ROAD.format(n)) for i, n in enumerate(ROAD_B)],
        REFS,
    ),
    "road/m-c": (
        [(f"rm_c{i + 1}", ROAD.format(n)) for i, n in enumerate(ROAD_C)],
        REFS,
    ),
}


def cmd_bases(_: argparse.Namespace) -> int:
    out = ROUND / "bases" / "road"
    out.mkdir(parents=True, exist_ok=True)
    names = sorted({f"plains_{n}" for n in ROAD_A + ROAD_B + ROAD_C})
    for name in names:
        Image.fromarray(np.round(v1.road_base(name)).astype(np.uint8)).save(
            out / f"{name}.png"
        )
    print(f"wrote {len(names)} road bases to {v1.rel(out)}")
    return 0


def cmd_jobs(_: argparse.Namespace) -> int:
    for rel, (cells, refs) in JOBS.items():
        work = ROUND / rel
        work.mkdir(parents=True, exist_ok=True)
        job = {
            "name": f"variety2-{rel.replace('/', '-')}",
            "layout": "3x3",
            "cells": [{"name": n, "base": b} for n, b in cells],
            "pin": {"restore": True},
            "gates": {},
            "anchors": {},
        }
        (work / "job.json").write_text(json.dumps(job, indent=1) + "\n")
        (work / "refs.txt").write_text(refs)
        print(f"wrote {v1.rel(work)}/job.json")
    return 0


# ------------------------------------------------------------- candidates ----

WORK: dict[str, list[str]] = {
    "nature": ["nature/m-a", "nature/m-b"],
    "home": ["home/m-a", "home/m-b", "home/m-c", "home/m-d"],
    "road": ["road/m-a", "road/m-b", "road/m-c"],
}
# Kept cells in label order, and every other gate-passing cell with the reason
# it was dropped (judged at 3x and on the board renders).
SELECTION: dict[str, dict[str, object]] = {
    "nature": {
        "kept": [
            "nature/m-a:nm_a1",  # copse
            "nature/m-b:nm_b2",  # hedgerow with two trees
            "nature/m-a:nm_a9",  # tree line
            "nature/m-a:nm_a3",  # thicket with saplings
            "nature/m-b:nm_b3",  # thicket with birch saplings
            "nature/m-b:nm_b4",  # rock outcrop, boulders
            "nature/m-b:nm_b9",  # rocky knoll with trees
            "nature/m-a:nm_a5",  # pond with willows
            "nature/m-a:nm_a6",  # orchard remnant
            "nature/m-b:nm_b6",  # orchard remnant with a stump
            "nature/m-a:nm_a7",  # gorse heath
            "nature/m-a:nm_a8",  # marsh with willows
        ],
        "dropped": {
            "nature/m-a:nm_a4": "outcrop rock is brighter than the hills tiles (p95 L 164 vs 142-143): the nature-11 problem again",
            "nature/m-b:nm_b7": "heather painted saturated purple on pale sand; the most colourful thing on the board",
            "nature/m-b:nm_b8": "fine, but close to nm_a8 and twelve kept already",
        },
    },
    "home": {
        "kept": [
            "home/m-c:hm_c1",  # farmstead with fenced paddock
            "home/m-d:hm_d1",  # farmstead with fenced paddock
            "home/m-c:hm_c2",  # hamlet of three round a green
            "home/m-d:hm_d7",  # hamlet of three in a row
            "home/m-c:hm_c3",  # inn with stable
            "home/m-c:hm_c4",  # chapel, churchyard wall, two yews
            "home/m-c:hm_c8",  # chapel with priest's cottage
            "home/m-c:hm_c5",  # water mill with pond
        ],
        "dropped": {
            "home/m-a:hm_a1": "houses 1.5-2x the round-1 and town house size; the sheet was re-rolled with a scale yardstick (m-c, m-d)",
            "home/m-a:hm_a4": "houses 1.5-2x the round-1 and town house size; the sheet was re-rolled with a scale yardstick (m-c, m-d)",
            "home/m-a:hm_a6": "houses 1.5-2x the round-1 and town house size; the sheet was re-rolled with a scale yardstick (m-c, m-d)",
            "home/m-a:hm_a7": "houses 1.5-2x the round-1 and town house size; the sheet was re-rolled with a scale yardstick (m-c, m-d)",
            "home/m-a:hm_a9": "houses 1.5-2x the round-1 and town house size; the sheet was re-rolled with a scale yardstick (m-c, m-d)",
            "home/m-b:hm_b2": "houses 1.5-2x the round-1 and town house size; the sheet was re-rolled with a scale yardstick (m-c, m-d)",
            "home/m-b:hm_b3": "houses 1.5-2x the round-1 and town house size; the sheet was re-rolled with a scale yardstick (m-c, m-d)",
            "home/m-b:hm_b5": "houses 1.5-2x the round-1 and town house size; the sheet was re-rolled with a scale yardstick (m-c, m-d)",
            "home/m-b:hm_b6": "houses 1.5-2x the round-1 and town house size; the sheet was re-rolled with a scale yardstick (m-c, m-d)",
            "home/m-b:hm_b8": "houses 1.5-2x the round-1 and town house size; the sheet was re-rolled with a scale yardstick (m-c, m-d)",
            "home/m-b:hm_b9": "houses 1.5-2x the round-1 and town house size; the sheet was re-rolled with a scale yardstick (m-c, m-d)",
            "home/m-c:hm_c6": "fine, but a third farmstead; two kept",
            "home/m-c:hm_c7": "fine, but a third hamlet; the lane reads as a track",
            "home/m-c:hm_c9": "vegetable plots read as small crop beds, toward the farm site",
            "home/m-d:hm_d2": "hedged garden plots read as small fields",
            "home/m-d:hm_d3": "fine, but a second inn; one kept",
            "home/m-d:hm_d4": "pale churchyard fence is the brightest line on the tile",
            "home/m-d:hm_d5": "willows painted bright yellow-green",
            "home/m-d:hm_d6": "fine, but a third farmstead",
            "home/m-d:hm_d8": "smithy reads as a small house and shed, close to round 1",
            "home/m-d:hm_d9": "fine (cottage under trees), close to round 1 home-01",
        },
    },
    "road": {
        "kept": [
            "road/m-a:rm_a1",  # straight_v: house pair west
            "road/m-a:rm_a3",  # straight_v: poplars west
            "road/m-a:rm_a4",  # straight_d: row of three cottages
            "road/m-b:rm_b9",  # straight_d: poplars north-east
            "road/m-a:rm_a7",  # turn_n_se: house pair and shed
            "road/m-b:rm_b2",  # turn_n_se: wayside farm
            "road/m-a:rm_a8",  # turn_s_nw: poplars
            "road/m-b:rm_b4",  # turn_se_sw: shrine and trees
            "road/m-b:rm_b1",  # turn_ne_nw: hamlet of three
            "road/m-c:rm_c1",  # fork_ne_nw_s: house pair
            "road/m-c:rm_c2",  # fork_se_n_sw: inn
            "road/m-c:rm_c9",  # fork_se_n_sw: shrine and a house
            "road/m-c:rm_c3",  # fork_se_nw_sw: farm
            "road/m-c:rm_c4",  # fork_se_n_nw: house pair and shed
            "road/m-c:rm_c5",  # fork_ne_n_s: chapel
            "road/m-c:rm_c6",  # fork_se_ne_nw: hamlet
            "road/m-c:rm_c7",  # fork_se_nw_s: farm
            "road/m-c:rm_c8",  # fork_se_n_s: house pair
        ],
        "dropped": {
            "road/m-a:rm_a5": "fine (shrine), but two straight_d kept",
            "road/m-b:rm_b6": "fine (shrine and milestone), but two straight_v kept",
            "road/m-b:rm_b7": "fine, but close to rm_a4 (row of three)",
        },
    },
}


def gather(group: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for w in WORK[group]:
        work = ROUND / w
        if not (work / "report.json").is_file():
            continue
        report = json.loads((work / "report.json").read_text())
        run = json.loads((work / "manifest.json").read_text())["runs"][-1]
        job = json.loads((work / "job.json").read_text())
        bases = {c["name"]: Path(c["base"]).stem for c in job["cells"]}
        hold = (
            json.loads((work / "hold.json").read_text())["tiles"]
            if group == "road"
            else {}
        )
        measured = measure_work(work) if group != "road" else {}
        for name, t in report["tiles"].items():
            rec = {
                "work": v1.rel(work),
                "cell": name,
                "base": bases[name],
                "raw": v1.rel(work / "raw" / f"{run['name']}.png"),
                "codex_thread": run["codex"]["codex_thread"],
                "paintin_passed": t["passed"],
                "paintin_gates": {
                    k["name"]: k["value"] for k in t["checks"] if k["hard"]
                },
            }
            if group == "road":
                h = hold[name]
                rec.update(
                    tile=work / "held" / f"{name}.png",
                    passed=h["passed"],
                    hold_gates=h["checks"],
                    painted_share=h["painted_share"],
                )
            else:
                rec.update(
                    tile=work / "tiles" / f"{name}.png",
                    passed=t["passed"],
                    occupied=measured[name]["occupied"],
                )
            out[f"{w}:{name}"] = rec
    return out


def cmd_candidates(_: argparse.Namespace) -> int:
    spec_path = ROUND / "candidates.json"
    spec = json.loads(spec_path.read_text())
    kept_dir = ROUND / "kept"
    if kept_dir.exists():
        shutil.rmtree(kept_dir)
    candidates = []
    counts: dict[str, dict[str, object]] = {}
    for group, sel in SELECTION.items():
        cells = gather(group)
        kept: list[str] = list(sel["kept"])  # type: ignore[call-overload]
        dropped: dict[str, str] = sel["dropped"]  # type: ignore[assignment]
        passing = {n for n, c in cells.items() if c["passed"]}
        missing = passing - set(kept) - set(dropped)
        if missing:
            raise SystemExit(
                f"{group}: passing cells with no decision: {sorted(missing)}"
            )
        per_shape: dict[str, int] = {}
        for i, key in enumerate(kept):
            c = cells[key]
            if not c["passed"]:
                raise SystemExit(f"{group}: kept {key} did not pass the gates")
            if group in ("nature", "home"):
                label = f"{group}-m{i + 1:02d}"
            else:
                shape = v1.road_shape(c["base"])
                n = per_shape.get(shape, 0)
                per_shape[shape] = n + 1
                label = f"road-{shape}-m{chr(ord('A') + n)}"
            dst = kept_dir / group / f"{label}.png"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(c["tile"], dst)
            candidates.append(
                {
                    "label": label,
                    "group": group,
                    "kept": True,
                    "tile": str(dst.relative_to(ROUND)),
                    "sha256": v1.sha(dst),
                    **{k: val for k, val in c.items() if k != "tile"},
                }
            )
        for key in sorted(dropped):
            c = cells[key]
            candidates.append(
                {
                    "label": None,
                    "group": group,
                    "kept": False,
                    "why_dropped": dropped[key],
                    **{k: val for k, val in c.items() if k != "tile"},
                }
            )
        counts[group] = {
            "cells": len(cells),
            "passed": len(passing),
            "kept": len(kept),
            "failed_gates": sorted(n for n, c in cells.items() if not c["passed"]),
        }
    spec["counts"] = counts
    spec["candidates"] = candidates
    spec_path.write_text(json.dumps(spec, indent=1) + "\n")
    print(f"wrote {v1.rel(spec_path)}: {sum(c['kept'] for c in candidates)} kept")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in (
        ("bases", cmd_bases),
        ("jobs", cmd_jobs),
        ("candidates", cmd_candidates),
    ):
        sub.add_parser(name).set_defaults(fn=fn)
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
