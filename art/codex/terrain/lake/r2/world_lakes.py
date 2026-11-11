#!/usr/bin/env python3
"""Lake neighbour-mask statistics and a board-mock map dump from generated worlds.

Run from api/ (it needs the API package):

    cd api && uv run python ../art/codex/terrain/lake/r2/world_lakes.py counts --seeds 1 2 3
    cd api && uv run python ../art/codex/terrain/lake/r2/world_lakes.py dump --seed 1 \
        --out ../scripts/board-mock-maps/world-lake-s1.txt

`counts` prints, over every lake hex, how often each 6-bit mask occurs (bit d set
when the neighbour in AXIAL_DIRECTIONS[d] is lake; off-map counts as land).
`dump` writes the region around the largest lake (plus a margin) in the
board-mock ASCII format, roads included.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from app.engine.hex_coords import AXIAL_DIRECTIONS, Axial, axial_to_offset
from app.engine.stats import Terrain
from app.world.generate import WorldParams, generate

CHARS = {
    Terrain.PLAINS: ".",
    Terrain.WOODS: "w",
    Terrain.HILLS: "h",
    Terrain.RIVER: "~",
    Terrain.BRIDGE: "=",
    Terrain.TOWN: "t",
    Terrain.CITY: "c",
    Terrain.LAKE: "o",
}


def lake_masks(terrain: dict[Axial, Terrain]) -> dict[Axial, int]:
    """Mask per lake hex."""
    out = {}
    for a, t in terrain.items():
        if t is not Terrain.LAKE:
            continue
        mask = 0
        for d, step in enumerate(AXIAL_DIRECTIONS):
            n = Axial(q=a.q + step.q, r=a.r + step.r)
            if terrain.get(n) is Terrain.LAKE:
                mask |= 1 << d
        out[a] = mask
    return out


def world_terrain(
    seed: int, slots: int
) -> tuple[dict[Axial, Terrain], dict[Axial, bool]]:
    world = generate(WorldParams.for_slots(slots, seed))
    terrain = {h.coord: h.terrain for h in world.game_map.hexes}
    roads = {h.coord: h.road for h in world.game_map.hexes}
    return terrain, roads


def lakes(terrain: dict[Axial, Terrain]) -> list[set[Axial]]:
    """Connected lake components, largest first."""
    left = {a for a, t in terrain.items() if t is Terrain.LAKE}
    comps = []
    while left:
        stack = [left.pop()]
        comp = set(stack)
        while stack:
            a = stack.pop()
            for step in AXIAL_DIRECTIONS:
                n = Axial(q=a.q + step.q, r=a.r + step.r)
                if n in left:
                    left.remove(n)
                    comp.add(n)
                    stack.append(n)
        comps.append(comp)
    return sorted(comps, key=len, reverse=True)


def run_counts(args: argparse.Namespace) -> None:
    total: Counter[int] = Counter()
    for seed in args.seeds:
        terrain, _ = world_terrain(seed, args.slots)
        masks = lake_masks(terrain)
        sizes = [len(c) for c in lakes(terrain)]
        print(f"seed {seed}: {len(masks)} lake hexes in {len(sizes)} lakes {sizes}")
        total.update(masks.values())
    n = sum(total.values())
    print(f"{n} lake hexes, {len(total)} distinct masks")
    for mask, count in total.most_common():
        print(f"  m{mask:02d} {mask:06b}  {count:4d}  {100 * count / n:5.1f}%")


def run_dump(args: argparse.Namespace) -> None:
    terrain, roads = world_terrain(args.seed, args.slots)
    comp = lakes(terrain)[args.lake]
    cells = [axial_to_offset(a) for a in comp]
    c0 = max(0, min(c for c, _ in cells) - args.margin)
    c1 = max(c for c, _ in cells) + args.margin
    r0 = max(0, min(r for _, r in cells) - args.margin)
    r1 = max(r for _, r in cells) + args.margin
    # keep the odd-q parity of the authored grid: start on an even column
    c0 -= c0 & 1
    by_offset = {axial_to_offset(a): a for a in terrain}
    rows_t, rows_r = [], []
    for row in range(r0, r1 + 1):
        line_t, line_r = [], []
        for col in range(c0, c1 + 1):
            a = by_offset.get((col, row))
            if a is None:
                raise SystemExit(f"region runs off the world at col={col} row={row}")
            line_t.append(CHARS[terrain[a]])
            line_r.append("#" if roads[a] else ".")
        rows_t.append("".join(line_t))
        rows_r.append("".join(line_r))
    text = (
        f"; generated world seed {args.seed} ({args.slots} slots), lake #{args.lake} "
        f"({len(comp)} hexes), cols {c0}-{c1} rows {r0}-{r1}\n"
        "[terrain]\n" + "\n".join(rows_t) + "\n[roads]\n" + "\n".join(rows_r) + "\n"
    )
    args.out.write_text(text)
    print(f"wrote {args.out} ({c1 - c0 + 1}x{r1 - r0 + 1})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("counts")
    c.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    c.add_argument("--slots", type=int, default=200)
    d = sub.add_parser("dump")
    d.add_argument("--seed", type=int, default=1)
    d.add_argument("--slots", type=int, default=200)
    d.add_argument("--lake", type=int, default=0, help="0 = largest lake")
    d.add_argument("--margin", type=int, default=3)
    d.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    {"counts": run_counts, "dump": run_dump}[args.cmd](args)


if __name__ == "__main__":
    main()
