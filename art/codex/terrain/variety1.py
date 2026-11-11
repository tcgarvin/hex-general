#!/usr/bin/env python3
"""Variety round 1 (candidates, unratified): plains feature, homestead and
roadside-building variants painted in with the paint-in toolchain.

Work lives in `candidates/variety-r1/`:

    bases/road/<name>.png      opaque road bases: the shipped road tile over the
                               G2 ground crop it was painted on (`road/a/job.json`),
                               so the rim pin and the road hold use the shipped
                               pixels exactly
    nature/<job>/, home/<job>/, road/<job>/
                               paint-in work dirs (job.json, prompt.txt, refs.txt,
                               raw/, manifest.json, report.json, run.log)
    road/<job>/hold.json       road hold report (below)
    kept/<group>/<label>.png   kept candidates (sha256 in candidates.json)
    candidates.json            every cell: source, gates, kept or why dropped

Subcommands:

    bases      write bases/road/*.png
    hold WORK  road sheets only: after `paintin.py run`, put the shipped road back
               pixel for pixel (`held/<cell>.png`) and gate it:
                 road_exact  max |held - base| over the road and 3 px around it
                             (must be 0)
                 rim_exact   max |held - base| over the 1.5 px rim (must be 0)
                 road_clash  salient painted px in the 4..7 px ring around the
                             road (a building cut by the hold), limit 12
                 rim_touch   salient painted px within 4 px of the boundary,
                             away from the road (limit 2)
               (paintin's own rim gates count Codex's re-rendered road at the
               crossings, so for road cells they are advisory.)
    candidates write candidates.json and kept/ from SELECTION below

    python3 art/codex/terrain/variety1.py bases
    python3 scripts/tile-foundry/paintin.py run --job <work>/job.json \\
        --prompt <work>/prompt.txt $(cat <work>/refs.txt) --out-dir <work>
    python3 art/codex/terrain/variety1.py hold <work>       # road sheets
    python3 art/codex/terrain/variety1.py candidates
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))

from paintin_geom import (
    gaussian_blur,
    max_filter,
    min_filter,
    tile_distance,
)  # noqa: E402
from paintin_pin import diff_mask  # noqa: E402

ROUND = HERE / "candidates" / "variety-r1"
SHIPPED = REPO / "frontend" / "src" / "board" / "tiles"
APPLIED = HERE / "APPLIED.md"  # shipped tile -> work dir and cell

ROAD_HOLD_EXACT_PX = 3  # held exactly this far around the road mask
ROAD_HOLD_FEATHER = 1.2  # Gaussian feather beyond it (px)
CLASH_RING = (4, 7)  # ring around the road checked for cut features
CLASH_MAX = 12
RIM_EXACT_PX = 1.5
RIM_TOUCH_PX = 4.0
RIM_TOUCH_MAX = 2


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rel(p: Path) -> str:
    return str(p.relative_to(REPO))


def rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)


# ------------------------------------------------------------------ bases ----


def road_source(name: str) -> tuple[Path, str]:
    """(work dir, cell) behind shipped road tile `name`, from APPLIED.md."""
    for line in APPLIED.read_text().splitlines():
        cols = [c.strip().strip("`") for c in line.split("|")]
        if len(cols) > 4 and cols[1] == f"road/{name}.png":
            return REPO / cols[2], cols[4]
    raise KeyError(f"road/{name}.png is not in {rel(APPLIED)}")


def road_ground(name: str) -> Path:
    """The G2 ground crop the shipped road tile `name` was painted on."""
    work, cell_name = road_source(name)
    job = json.loads((work / "job.json").read_text())
    for cell in job["cells"]:
        if cell["name"] == cell_name:
            return (work / cell["base"]).resolve()
    raise KeyError(f"{cell_name} is not a cell of {rel(work / 'job.json')}")


def road_base(name: str) -> np.ndarray:
    """Shipped road tile `name` composited over its own ground crop (opaque)."""
    tile = np.asarray(
        Image.open(SHIPPED / "road" / f"{name}.png").convert("RGBA"), dtype=np.float64
    )
    ground = rgb(road_ground(name))
    a = tile[..., 3:4] / 255.0
    return tile[..., :3] * a + ground * (1.0 - a)


def road_mask(name: str) -> np.ndarray:
    """Road pixels of shipped tile `name`: painted vs its ground, closed by 1 px."""
    m = (diff_mask(road_base(name), rgb(road_ground(name))) > 0.5).astype(np.float64)
    return min_filter(max_filter(m, 1), 1) > 0.5


def cmd_bases(_: argparse.Namespace) -> int:
    out = ROUND / "bases" / "road"
    out.mkdir(parents=True, exist_ok=True)
    # straights and turns only (all from road/a); forks and ends are left out
    names = sorted(
        p.stem
        for p in (SHIPPED / "road").glob("plains_*.png")
        if "_straight_" in p.stem or "_turn_" in p.stem
    )
    for name in names:
        Image.fromarray(np.round(road_base(name)).astype(np.uint8)).save(
            out / f"{name}.png"
        )
    print(f"wrote {len(names)} road bases to {rel(out)}")
    return 0


# ------------------------------------------------------------------- hold ----


def hold_weight(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(weight of the base, exact core): 1 on the road and ROAD_HOLD_EXACT_PX
    around it, feathered to 0 beyond."""
    core = max_filter(mask.astype(np.float64), ROAD_HOLD_EXACT_PX) > 0.5
    soft = gaussian_blur(
        max_filter(mask.astype(np.float64), ROAD_HOLD_EXACT_PX + 1), ROAD_HOLD_FEATHER
    )
    w = np.where(core, 1.0, np.clip(soft, 0.0, 1.0))
    return w, core


def ring(mask: np.ndarray, lo: int, hi: int) -> np.ndarray:
    m = mask.astype(np.float64)
    return (max_filter(m, hi) > 0.5) & ~(max_filter(m, lo) > 0.5)


def cmd_hold(args: argparse.Namespace) -> int:
    work = Path(args.work).resolve()
    job = json.loads((work / "job.json").read_text())
    out_dir = work / "held"
    out_dir.mkdir(exist_ok=True)
    d = tile_distance()
    report: dict[str, dict] = {}
    failed = 0
    for cell in job["cells"]:
        name = cell["name"]
        shipped = Path(cell["base"]).stem
        base = rgb((work / cell["base"]).resolve())
        mask = road_mask(shipped)
        tile = np.asarray(
            Image.open(work / "tiles" / f"{name}.png").convert("RGBA"), dtype=np.float64
        )
        w, core = hold_weight(mask)
        held_rgb = base * w[..., None] + tile[..., :3] * (1.0 - w[..., None])
        held = np.dstack([np.clip(np.round(held_rgb), 0, 255), tile[..., 3]]).astype(
            np.uint8
        )
        Image.fromarray(held, "RGBA").save(out_dir / f"{name}.png")
        inside = held[..., 3] > 0
        diff = np.abs(held[..., :3].astype(np.float64) - np.round(base)).max(axis=2)
        road_exact = float(diff[core & inside].max())
        rim = inside & (d < RIM_EXACT_PX)
        rim_exact = float(diff[rim].max())
        salient = diff_mask(held[..., :3].astype(np.float64), base) > 0.5
        clash = int((salient & ring(mask, *CLASH_RING) & inside).sum())
        away = ~(max_filter(mask.astype(np.float64), 6) > 0.5)
        touch = int((salient & away & (d >= 0) & (d < RIM_TOUCH_PX)).sum())
        painted = float(
            (salient & inside & away).mean() / max(float((inside & away).mean()), 1e-9)
        )
        checks = {
            "road_exact": road_exact,
            "rim_exact": rim_exact,
            "road_clash": clash,
            "rim_touch": touch,
        }
        passed = (
            road_exact == 0
            and rim_exact == 0
            and clash <= CLASH_MAX
            and touch <= RIM_TOUCH_MAX
        )
        failed += not passed
        report[name] = {
            "shipped": shipped,
            "passed": passed,
            "checks": checks,
            "painted_share": round(painted, 3),
            "road_px": int(mask.sum()),
        }
        print(
            f"{'PASS' if passed else 'FAIL'} {name} ({shipped}) {checks} painted {painted:.3f}"
        )
    (work / "hold.json").write_text(
        json.dumps(
            {
                "limits": {
                    "road_hold_exact_px": ROAD_HOLD_EXACT_PX,
                    "road_hold_feather": ROAD_HOLD_FEATHER,
                    "clash_ring": CLASH_RING,
                    "clash_max": CLASH_MAX,
                    "rim_touch_px": RIM_TOUCH_PX,
                    "rim_touch_max": RIM_TOUCH_MAX,
                },
                "tiles": report,
            },
            indent=1,
        )
        + "\n"
    )
    print(f"wrote {work / 'hold.json'}: {len(report) - failed}/{len(report)} pass")
    return 0


# ------------------------------------------------------------- candidates ----

# group -> work dirs (relative to ROUND)
WORK: dict[str, list[str]] = {
    "nature": ["nature/n1-a", "nature/n1-b"],
    "home": ["home/h1-a", "home/h1-b"],
    "road": ["road/r1-a", "road/r1-b"],
}
# Filled in after looking at every tile at 3x and at board scale: kept cells in
# label order, and every other gate-passing cell with the reason it was dropped.
SELECTION: dict[str, dict[str, object]] = {
    "nature": {
        "kept": [
            "nature/n1-a:n_a1",  # clump of three trees
            "nature/n1-a:n_a2",  # lone tree
            "nature/n1-b:n_b1",  # lone broad oak
            "nature/n1-a:n_a8",  # two trees and a bush
            "nature/n1-b:n_b2",  # two trees and a fallen log
            "nature/n1-b:n_b9",  # small tree and a boulder
            "nature/n1-a:n_a3",  # scrub patches
            "nature/n1-b:n_b7",  # low bushes, rough grass
            "nature/n1-a:n_a4",  # boulder and stones
            "nature/n1-b:n_b4",  # three boulders
            "nature/n1-a:n_a9",  # rock outcrop
            "nature/n1-a:n_a5",  # hedgerow fragment
            "nature/n1-b:n_b5",  # broken dry-stone wall
            "nature/n1-b:n_b6",  # tiny pond with reeds
            "nature/n1-b:n_b8",  # old fence posts
        ],
        "dropped": {
            "nature/n1-a:n_a6": "the damp hollow came out as a few px of blue glint; reads as a blot, not a feature",
            "nature/n1-a:n_a7": "wildflower patch is a few pale specks, invisible at board scale",
        },
    },
    "home": {
        "kept": [
            "home/h1-b:h_b1",  # thatched cottage among trees
            "home/h1-b:h_b2",  # cottage with a well
            "home/h1-b:h_b3",  # two houses under trees
            "home/h1-b:h_b4",  # long cottage with lean-to
            "home/h1-b:h_b6",  # house with a curved hedge
            "home/h1-b:h_b8",  # two houses, slate and thatch
            "home/h1-b:h_b9",  # L-shaped farmhouse, walled yard
            "home/h1-a:h_a2",  # two houses sharing a yard
            "home/h1-a:h_a4",  # farmhouse, tree, fence
            "home/h1-a:h_a7",  # house with three fruit trees
        ],
        "dropped": {
            "home/h1-a:h_a1": "kitchen garden painted as tidy crop rows: a tiny field, edges toward the farm site",
            "home/h1-a:h_a3": "a lone barn with a yard is the farm L1 motif without its field",
            "home/h1-a:h_a5": "fine, but pale walls and red roof are brighter than the h1-b houses; ten kept is enough",
            "home/h1-a:h_a6": "fine, but pale walls and red roofs; close to h_a2",
            "home/h1-a:h_a8": "walled garden with crop rows reads as a small field",
            "home/h1-a:h_a9": "house plus a large barn round a yard reads as a farmstead, close to the farm site",
            "home/h1-b:h_b5": "stone barn and shed, no house: farm-like",
            "home/h1-b:h_b7": "timber hut and log stack are tiny and read as clutter at 64 px",
        },
    },
    "road": {
        "kept": [
            "road/r1-a:r_a1",  # straight_v: cottage and tree west
            "road/r1-a:r_a2",  # straight_v: two houses east
            "road/r1-a:r_a3",  # straight_v: three trees and a bush east
            "road/r1-a:r_a4",  # straight_d: cottage and shed north-east
            "road/r1-a:r_a5",  # straight_d: two houses south-west
            "road/r1-a:r_a6",  # straight_d: row of three trees
            "road/r1-b:r_b6",  # turn_n_se: two houses east
            "road/r1-a:r_a7",  # turn_n_se: cottage and garden west
            "road/r1-a:r_a8",  # turn_s_nw: barn east
            "road/r1-b:r_b7",  # turn_s_nw: thatched cottage and tree west
            "road/r1-a:r_a9",  # turn_se_sw: cottage and tree north
            "road/r1-b:r_b8",  # turn_se_sw: stone barn and shed north
            "road/r1-b:r_b3",  # turn_ne_nw: two houses south
            "road/r1-b:r_b9",  # turn_ne_nw: cottage and two trees south
        ],
        "dropped": {
            "road/r1-b:r_b1": "fine (lone barn west); three straight_v kept already",
            "road/r1-b:r_b2": "tree and a tiny boulder; the boulder reads as a speck",
            "road/r1-b:r_b4": "fine (hedge and tree); three straight_d kept already",
            "road/r1-b:r_b5": "fine (cottage one side, tree the other); three straight_d kept already",
        },
    },
}


def nature_label(i: int) -> str:
    return f"nature-{i + 1:02d}"


def home_label(i: int) -> str:
    return f"home-{i + 1:02d}"


def road_shape(shipped: str) -> str:
    """`plains_straight_v_01` -> `straight_v`."""
    return shipped.removeprefix("plains_").rsplit("_", 1)[0]


def gather(group: str) -> dict[str, dict]:
    """cell -> record for every cell of the group's work dirs."""
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
        for name, t in report["tiles"].items():
            gates = {k["name"]: k["value"] for k in t["checks"] if k["hard"]}
            rec = {
                "work": rel(work),
                "cell": name,
                "base": bases[name],
                "raw": rel(work / "raw" / f"{run['name']}.png"),
                "codex_thread": (
                    run["codex"]["codex_thread"] if run.get("codex") else None
                ),
                "paintin_passed": t["passed"],
                "paintin_gates": gates,
            }
            if group == "road":
                h = hold[name]
                rec["tile"] = work / "held" / f"{name}.png"
                rec["passed"] = h["passed"]
                rec["hold_gates"] = h["checks"]
                rec["painted_share"] = h["painted_share"]
            else:
                rec["tile"] = work / "tiles" / f"{name}.png"
                rec["passed"] = t["passed"]
            out[f"{w}:{name}"] = rec
    return out


def cmd_candidates(_: argparse.Namespace) -> int:
    spec_path = ROUND / "candidates.json"
    spec = json.loads(spec_path.read_text())
    kept_dir = ROUND / "kept"
    if kept_dir.exists():
        shutil.rmtree(kept_dir)
    candidates = []
    counts: dict[str, dict[str, int]] = {}
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
            if group == "nature":
                label = nature_label(i)
            elif group == "home":
                label = home_label(i)
            else:
                shape = road_shape(c["base"])
                n = per_shape.get(shape, 0)
                per_shape[shape] = n + 1
                label = f"road-{shape}-{chr(ord('A') + n)}"
            dst = kept_dir / group / f"{label}.png"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(c["tile"], dst)
            candidates.append(
                {
                    "label": label,
                    "group": group,
                    "kept": True,
                    "tile": str(dst.relative_to(ROUND)),
                    "sha256": sha(dst),
                    "note": sel.get("notes", {}).get(key, ""),  # type: ignore[union-attr]
                    **{k: v for k, v in c.items() if k != "tile"},
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
                    **{k: v for k, v in c.items() if k != "tile"},
                }
            )
        counts[group] = {
            "cells": len(cells),
            "passed": len(passing),
            "kept": len(kept),
            "failed_gates": sorted(n for n, c in cells.items() if not c["passed"]),  # type: ignore[dict-item]
        }
    spec["counts"] = counts
    spec["candidates"] = candidates
    spec_path.write_text(json.dumps(spec, indent=1) + "\n")
    print(f"wrote {rel(spec_path)}: {sum(c['kept'] for c in candidates)} kept")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bases").set_defaults(fn=cmd_bases)
    h = sub.add_parser("hold")
    h.add_argument("work")
    h.set_defaults(fn=cmd_hold)
    sub.add_parser("candidates").set_defaults(fn=cmd_candidates)
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
