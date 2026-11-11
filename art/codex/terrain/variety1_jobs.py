#!/usr/bin/env python3
"""Write the variety round 1 paint-in jobs (`candidates/variety-r1/<group>/<job>/job.json`).

3x3 sheets (340 px hexagons): the house and crown scale of the town and woods
sheets. Nature and homestead cells sit on the G2 plains bases (`r1-bases/`,
the shipped plains tiles inside the hex); road cells sit on the opaque shipped
road tiles (`variety1.py bases`). Restore on everywhere, as for every class
since round 1. Prompts are hand-written per sheet (`prompt.txt` beside each job).

    python3 art/codex/terrain/variety1_jobs.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUND = HERE / "candidates" / "variety-r1"
REFS = (
    "--ref docs/concept-art/concept1.png --ref art/codex/terrain/refs/r1/town_2x.png "
    "--ref art/codex/terrain/refs/r1/forest_cluster_2x.png\n"
)

PLAINS = "../../../../r1-bases/{:02d}.png"
ROAD = "../../bases/road/{}.png"

JOBS: dict[str, list[tuple[str, str]]] = {
    # nature: nine cells each, bases rotated so no two neighbours share ground
    "nature/n1-a": [(f"n_a{i + 1}", PLAINS.format(i % 6)) for i in range(9)],
    "nature/n1-b": [(f"n_b{i + 1}", PLAINS.format((i + 3) % 6)) for i in range(9)],
    "home/h1-a": [(f"h_a{i + 1}", PLAINS.format((i + 1) % 6)) for i in range(9)],
    "home/h1-b": [(f"h_b{i + 1}", PLAINS.format((i + 4) % 6)) for i in range(9)],
    "road/r1-a": [
        ("r_a1", ROAD.format("plains_straight_v_00")),
        ("r_a2", ROAD.format("plains_straight_v_01")),
        ("r_a3", ROAD.format("plains_straight_v_02")),
        ("r_a4", ROAD.format("plains_straight_d_00")),
        ("r_a5", ROAD.format("plains_straight_d_01")),
        ("r_a6", ROAD.format("plains_straight_d_02")),
        ("r_a7", ROAD.format("plains_turn_n_se_00")),
        ("r_a8", ROAD.format("plains_turn_s_nw_00")),
        ("r_a9", ROAD.format("plains_turn_se_sw_00")),
    ],
    "road/r1-b": [
        ("r_b1", ROAD.format("plains_straight_v_01")),
        ("r_b2", ROAD.format("plains_straight_v_02")),
        ("r_b3", ROAD.format("plains_turn_ne_nw_00")),
        ("r_b4", ROAD.format("plains_straight_d_01")),
        ("r_b5", ROAD.format("plains_straight_d_02")),
        ("r_b6", ROAD.format("plains_turn_n_se_01")),
        ("r_b7", ROAD.format("plains_turn_s_nw_01")),
        ("r_b8", ROAD.format("plains_turn_se_sw_01")),
        ("r_b9", ROAD.format("plains_turn_ne_nw_01")),
    ],
}


def main() -> int:
    for rel, cells in JOBS.items():
        work = ROUND / rel
        work.mkdir(parents=True, exist_ok=True)
        job = {
            "name": f"variety-{rel.split('/')[1]}",
            "layout": "3x3",
            "cells": [{"name": n, "base": b} for n, b in cells],
            "pin": {"restore": True},
            "gates": {},
            "anchors": {},
        }
        (work / "job.json").write_text(json.dumps(job, indent=1) + "\n")
        (work / "refs.txt").write_text(REFS)
        print(f"wrote {work.relative_to(HERE)}/job.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
