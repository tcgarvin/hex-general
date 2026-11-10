#!/usr/bin/env python3
"""Write the roads-over-hills paint-in jobs (follow-up to round 1).

Every `road/hills_<shape>` name the frontend can request (`roadTiles.ts`: any
handled oriented shape on a road-flagged hills hex: 4 ends, 2 straights, 4
turns, 12 forks), one variant each and two for the straights: 24 cells on two
4x4 sheets. Same road profile, colour anchors and gates as the plains roads
(`../road/profile.json`).

Differences from the plains road jobs, all because the ground is relief:
- bases are the ratified tan-peak hills (`candidates/review1/hills/00..03`),
  made opaque here (transparent corners filled from their neighbours);
- the route is searched: for each cell, every base x seed is scored by how much
  rock and shadow the track would cross (|L - grass L| under the band), with a
  usage penalty so the four ridges rotate; the cheapest line runs over a saddle
  or along the foot of the ridge;
- the guide is pale cream, not road tan: the tan rock *is* road colour, so a
  tan guide would vanish into it;
- the pin keeps the hill pixel-for-pixel outside the track region
  (`paint_region_px`), so the ridge cannot be flattened or repainted.

    python3 art/codex/terrain/road-hills/make_jobs.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
TERRAIN = HERE.parent
REPO = TERRAIN.parents[2]
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
sys.path.insert(0, str(TERRAIN))

from make_r1_jobs import FORKS_SHIPPED, ROAD, ROAD_ANCHORS, ROAD_STYLE, SHAPES  # noqa: E402
from paintin_geom import Guide, render_guide_mask, tile_distance  # noqa: E402

HILLS = [TERRAIN / "candidates" / "review1" / "hills" / f"{i:02d}.png" for i in range(4)]
GUIDE_CREAM = [222, 208, 168]  # stands apart from both grass (L ~98) and lit rock (L ~143)
HILL_MEANDER = 5.0  # a little more winding than plains (3): picks its way round rock
SEEDS = range(10)
USAGE_PENALTY = 0.02  # per earlier use of the same base, in cost units
GRASS_L = 98.0
PIN = {"restore": False, "feature_strength": 0.8, "feature_rgb": ROAD, "tone_sigma": 6.0,
       "paint_region_px": 5.0}


def opaque_base(src: Path) -> Path:
    """The hills tile with its transparent corners filled by repeated
    neighbour averaging (identical to the tile wherever alpha is 255)."""
    rgba = np.asarray(Image.open(src).convert("RGBA"), dtype=np.float64)
    rgb = rgba[..., :3].copy()
    known = rgba[..., 3] >= 255
    while not known.all():
        acc = np.zeros_like(rgb)
        cnt = np.zeros(known.shape)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                k = np.roll(np.roll(known, dy, 0), dx, 1)
                acc += np.roll(np.roll(rgb, dy, 0), dx, 1) * k[..., None]
                cnt += k
        grow = ~known & (cnt > 0)
        rgb[grow] = acc[grow] / cnt[grow][:, None]
        known = known | grow
    out = HERE / "bases" / f"h{src.stem}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.round(rgb).astype(np.uint8)).save(out)
    return out


def rock_cost(base: Path) -> np.ndarray:
    a = np.asarray(Image.open(base).convert("RGB"), dtype=np.float64)
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    return np.clip(np.abs(lum - GRASS_L) / 45.0, 0.0, 1.0)


def guide_dict(shape: str, seed: int) -> dict:
    return {"kind": "road", "edges": list(SHAPES[shape]), "color": GUIDE_CREAM,
            **{**ROAD_STYLE, "meander": HILL_MEANDER}, "seed": seed,
            **({"end_taper": 0.8} if shape.startswith("end_") else {})}


def route(
    shape: str, bases: list[Path], costs: list[np.ndarray], usage: list[int], skip: int = -1
) -> tuple[int, int, float]:
    """(base index, seed, rock cost) of the cheapest line for `shape` (never on
    base `skip`, so a second straight lands on a different ridge)."""
    inside = tile_distance() > 0
    best = None
    for bi, cost in enumerate(costs):
        if bi == skip:
            continue
        for seed in SEEDS:
            g = guide_dict(shape, seed)
            guide = Guide("road", tuple(g["edges"]), g["width"], tuple(GUIDE_CREAM), None,
                          meander=g["meander"], ragged=g["ragged"], round_px=g["round_px"],
                          seed=seed, end_taper=g.get("end_taper", 0.55))
            band = render_guide_mask(guide, 128, 128.0) * inside
            c = float((band * cost).sum() / band.sum()) + USAGE_PENALTY * usage[bi]
            if best is None or c < best[2]:
                best = (bi, seed, c)
    assert best is not None
    return best


def job(name: str, cells: list[dict]) -> dict:
    return {"name": name, "layout": "4x4", "cells": cells,
            "profile": {"path": "../../road/profile.json"}, "pin": PIN, "gates": {},
            "anchors": ROAD_ANCHORS}


REROLL = ["hills_fork_se_n_sw_00", "hills_fork_se_nw_sw_00"]  # narrow at one hand-over in b
REROLL_WIDTH = 11.5  # Codex paints the track ~25% narrower than the cream line


def write_reroll(routes: dict) -> None:
    """Sheet c: the cells that painted a narrow track at a hand-over, same
    routes, a wider guide, two takes each."""
    cells = []
    for name in REROLL:
        shape = name[len("hills_"):-3]
        r = routes[name]
        for take in range(2):
            g = guide_dict(shape, r["seed"])
            g["width"] = REROLL_WIDTH
            cells.append({"name": f"{name}_t{take}", "slot": len(cells),
                          "base": f"../bases/{r['base']}", "guide": g})
    d = HERE / "c"
    d.mkdir(parents=True, exist_ok=True)
    (d / "job.json").write_text(json.dumps(job("road-hills-c", cells), indent=1) + "\n")
    (d / "prompt.txt").write_text(
        (HERE / "prompt.template.txt").read_text().format(count="four", rows=1)
    )
    print(f"wrote c/job.json ({len(cells)} cells)")


def write_reroll_d(bases: list[Path], costs: list[np.ndarray]) -> None:
    """Sheet d: fork_se_nw_sw on another ridge (on h01 Codex's SW arm stayed
    ~6 px at the hand-over in four takes), wider guide, two takes."""
    skip = [b.name for b in bases].index("h01.png")
    bi, seed, c = route("fork_se_nw_sw", bases, costs, [0, 0, 0, 0], skip=skip)
    cells = []
    for take in range(2):
        g = guide_dict("fork_se_nw_sw", seed)
        g["width"] = REROLL_WIDTH
        cells.append({"name": f"hills_fork_se_nw_sw_00_d{take}", "slot": take,
                      "base": f"../bases/{bases[bi].name}", "guide": g})
    d = HERE / "d"
    d.mkdir(parents=True, exist_ok=True)
    (d / "job.json").write_text(json.dumps(job("road-hills-d", cells), indent=1) + "\n")
    (d / "prompt.txt").write_text(
        (HERE / "prompt.template.txt").read_text().format(count="two", rows=1)
    )
    print(f"wrote d/job.json: {bases[bi].name} seed {seed} rock {c:.3f}")


def write_reroll_e() -> None:
    """Sheet e: fork_se_nw_sw again on both routes (c's h01, d's h02) with a
    13 px guide; Codex kept painting its rock-crossing arm at ~60-70% of the
    line width, so the line is drawn wider than the track should end up."""
    cells = []
    for sub, src in (("c", "hills_fork_se_nw_sw_00_t0"), ("d", "hills_fork_se_nw_sw_00_d0")):
        prev = json.loads((HERE / sub / "job.json").read_text())
        cell = next(x for x in prev["cells"] if x["name"] == src)
        for _ in range(2):
            g = dict(cell["guide"])
            g["width"] = 13.0
            cells.append({"name": f"hills_fork_se_nw_sw_00_e{len(cells)}", "slot": len(cells),
                          "base": cell["base"], "guide": g})
    d = HERE / "e"
    d.mkdir(parents=True, exist_ok=True)
    (d / "job.json").write_text(json.dumps(job("road-hills-e", cells), indent=1) + "\n")
    (d / "prompt.txt").write_text(
        (HERE / "prompt.template.txt").read_text().format(count="four", rows=1)
    )
    print(f"wrote e/job.json ({len(cells)} cells)")


def main() -> None:
    bases = [opaque_base(p) for p in HILLS]
    costs = [rock_cost(b) for b in bases]
    plan = [("straight_v", 2), ("straight_d", 2), ("turn_n_se", 1), ("turn_s_nw", 1),
            ("turn_se_sw", 1), ("turn_ne_nw", 1), ("end_n", 1), ("end_s", 1),
            ("end_se", 1), ("end_ne", 1)] + [(f, 1) for f, _ in FORKS_SHIPPED]
    usage = [0, 0, 0, 0]
    cells = []
    routes = {}
    for shape, n in plan:
        first = -1
        for v in range(n):
            bi, seed, c = route(shape, bases, costs, usage, skip=first)
            first = bi
            usage[bi] += 1
            name = f"hills_{shape}_{v:02d}"
            routes[name] = {"base": bases[bi].name, "seed": seed, "rock_cost": round(c, 3)}
            cells.append({"name": name, "base": f"../bases/{bases[bi].name}",
                          "guide": guide_dict(shape, seed)})
    sheets = [cells[:16], cells[16:]]
    for i, sub in enumerate(sheets):
        for slot, c in enumerate(sub):
            c["slot"] = slot
        d = HERE / "abc"[i]
        d.mkdir(parents=True, exist_ok=True)
        (d / "job.json").write_text(json.dumps(job(f"road-hills-{'abc'[i]}", sub), indent=1) + "\n")
        text = (HERE / "prompt.template.txt").read_text().format(
            count={16: "sixteen", 8: "eight"}[len(sub)],
            rows=4 if len(sub) == 16 else 2)
        (d / "prompt.txt").write_text(text)
        print(f"wrote {d.name}/job.json ({len(sub)} cells)")
    (HERE / "routes.json").write_text(json.dumps(routes, indent=1) + "\n")
    write_reroll(routes)
    write_reroll_d(bases, costs)
    write_reroll_e()
    for k, v in routes.items():
        print(f"  {k:<26} {v['base']} seed {v['seed']:2d} rock {v['rock_cost']}")


if __name__ == "__main__":
    main()
