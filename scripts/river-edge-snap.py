#!/usr/bin/env python3
"""Snap a generated river/bridge hex tile so its water crosses each connected edge
at a *canonical* position and width — the deterministic step that makes rivers
match across hex edges (ROADMAP Phase 8 T3/T4).

PixelLab generates the river *interior* (its meander, banks, colour), but can't be
steered to place the crossing exactly at an edge centre. This script forces each
connected edge to a fixed water band (centred on the edge midpoint, fixed width,
fixed colour), feathered into the generated interior a few px in. Because the band
geometry+colour are identical for a given edge across *all* tiles, two adjacent
river hexes present aligned, same-colour water at their shared edge — they connect.

Flat-top geometry (tile is a 64px flat-top hex; pixel space, y down): the 6 edge
midpoints sit at apothem = 32·cos30° ≈ 27.71 from centre (32,32), at the screen
angles below. Edges are named by their AXIAL_DIRECTIONS index (see
frontend/src/board/hex.ts), so `--edges 2,5` = the vertical (N/S) axis.

Usage:
  river-edge-snap.py snap IN.png OUT.png --edges 2,5
  river-edge-snap.py verify TILE.png --edges 2,5      # assert canonical edge profile
  river-edge-snap.py match A.png:2 B.png:5            # assert the two edges align
"""

from __future__ import annotations

import argparse
import math
import sys

from PIL import Image

# --- canonical constants (shared by every snapped tile, so seams match) ---
SIZE = 64
CENTER = SIZE / 2
APOTHEM = (SIZE / 2) * math.cos(math.radians(30))  # centre → edge-midpoint
WATER = (83, 130, 167)  # batch median water colour; the tiles' own interior water
BAND_HALF = 5.0  # half the river width at the edge (→ ~10px wide)
CLEAR_DEPTH = 3.0  # px from the edge that are hard-canonicalised (water|grass)
BAND_DEPTH = 18.0  # px the water band reaches inward before fully fading
TAN_FEATHER = 1.0  # px of softening on the band's long sides

# Screen angle (degrees, y-down) of each AXIAL_DIRECTIONS edge midpoint. Mirrors
# the frontend's axial→pixel layout: d0 ESE … see hex.ts.
EDGE_ANGLE_DEG = {0: 30, 1: 330, 2: 270, 3: 210, 4: 150, 5: 90}
# All six edge normals, for the hexagon point-membership test.
ALL_EDGE_DIRS = [
    (math.cos(math.radians(a)), math.sin(math.radians(a)))
    for a in (30, 90, 150, 210, 270, 330)
]


def _inside_hex(vx: float, vy: float) -> bool:
    """Is the offset-from-centre point inside the flat-top hexagon?"""
    return all(vx * nx + vy * ny <= APOTHEM + 0.5 for nx, ny in ALL_EDGE_DIRS)


def _grass_colour(im: Image.Image) -> tuple[int, int, int]:
    """Median colour of opaque, non-water pixels — the tile's own bank/grass."""
    px = im.load()
    rs, gs, bs = [], [], []
    for y in range(SIZE):
        for x in range(SIZE):
            r, g, b, a = px[x, y]
            if a > 128 and not (b > g and b > r - 10 and b > 70):
                rs.append(r)
                gs.append(g)
                bs.append(b)
    if not rs:  # pathological: no grass — fall back to a neutral green
        return (96, 140, 74)
    rs.sort()
    gs.sort()
    bs.sort()
    m = len(rs) // 2
    return (rs[m], gs[m], bs[m])


def _blend(
    a: tuple[int, int, int], b: tuple[int, int, int], w: float
) -> tuple[int, int, int]:
    return tuple(round(a[i] * (1 - w) + b[i] * w) for i in range(3))


def snap(in_path: str, out_path: str, edges: list[int]) -> None:
    im = Image.open(in_path).convert("RGBA")
    if im.size != (SIZE, SIZE):
        raise SystemExit(f"{in_path}: expected {SIZE}x{SIZE}, got {im.size}")
    grass = _grass_colour(im)
    src = im.load()
    out = im.copy()
    dst = out.load()
    dirs = [
        (
            d,
            math.cos(math.radians(EDGE_ANGLE_DEG[d])),
            math.sin(math.radians(EDGE_ANGLE_DEG[d])),
        )
        for d in edges
    ]

    for y in range(SIZE):
        for x in range(SIZE):
            vx, vy = x + 0.5 - CENTER, y + 0.5 - CENTER
            if not _inside_hex(vx, vy):
                continue
            best_w = 0.0  # water weight to apply (max over edges)
            hard_grass = False  # within an edge's clear-strip but outside its band
            for _d, nx, ny in dirs:
                rad = vx * nx + vy * ny
                tan = abs(vx * -ny + vy * nx)
                depth = APOTHEM - rad  # 0 at edge, grows inward
                if depth < -2 or depth > BAND_DEPTH:
                    continue
                if tan <= BAND_HALF:
                    tw = (
                        1.0
                        if tan <= BAND_HALF - TAN_FEATHER
                        else (BAND_HALF - tan) / TAN_FEATHER
                    )
                    if depth <= CLEAR_DEPTH:
                        w = tw  # hard canonical near the edge
                    else:
                        ramp = (BAND_DEPTH - depth) / (BAND_DEPTH - CLEAR_DEPTH)
                        w = tw * max(0.0, min(1.0, ramp))
                    best_w = max(best_w, w)
                elif depth <= CLEAR_DEPTH:
                    hard_grass = (
                        True  # narrow the river to exactly the band at the edge
                    )

            r, g, b, a = src[x, y]
            if best_w > 0:
                nr, ng, nb = _blend((r, g, b), WATER, best_w)
                dst[x, y] = (nr, ng, nb, max(a, round(best_w * 255)))
            elif hard_grass and (b > g and b > r - 10 and b > 70):
                dst[x, y] = (grass[0], grass[1], grass[2], 255)
    out.save(out_path)
    print(f"snapped {in_path} edges={edges} -> {out_path}")


def edge_profile(im: Image.Image, d: int) -> list[bool]:
    """Water presence sampled along edge `d`, at tangential offsets -16..16."""
    nx, ny = math.cos(math.radians(EDGE_ANGLE_DEG[d])), math.sin(
        math.radians(EDGE_ANGLE_DEG[d])
    )
    tx, ty = -ny, nx
    px = im.convert("RGBA").load()
    prof = []
    for t in range(-16, 17):
        # one pixel inside the edge line, offset t along the tangent
        x = CENTER + nx * (APOTHEM - 1.0) + tx * t
        y = CENTER + ny * (APOTHEM - 1.0) + ty * t
        ix, iy = int(round(x - 0.5)), int(round(y - 0.5))
        if not (0 <= ix < SIZE and 0 <= iy < SIZE):
            prof.append(False)
            continue
        r, g, b, a = px[ix, iy]
        prof.append(a > 128 and b > g and b > r - 10 and b > 70)
    return prof


def verify(path: str, edges: list[int]) -> bool:
    im = Image.open(path)
    ok = True
    for d in edges:
        prof = edge_profile(im, d)
        water = [t - 16 for t, v in enumerate(prof) if v]
        # canonical: a single centred run within ±BAND_HALF
        bad = [t for t in water if abs(t) > BAND_HALF + 0.5]
        centred = water and abs((min(water) + max(water)) / 2) <= 1.5
        if bad or not centred:
            ok = False
            print(f"  edge {d}: NON-CANONICAL water offsets={water}")
        else:
            print(f"  edge {d}: ok (water offsets={water})")
    print(f"verify {path}: {'PASS' if ok else 'FAIL'}")
    return ok


def match(spec_a: str, spec_b: str) -> bool:
    """Check edge A and edge B (opposite directions) align when placed adjacent.

    Adjacent tiles' shared edge flips tangent orientation, so A@t corresponds to
    B@(-t); a symmetric canonical profile makes them identical either way.
    """
    pa, da = spec_a.rsplit(":", 1)
    pb, db = spec_b.rsplit(":", 1)
    prof_a = edge_profile(Image.open(pa), int(da))
    prof_b = list(reversed(edge_profile(Image.open(pb), int(db))))
    # Diagonal (30°/210°) edges can't rasterise perfectly symmetrically on the
    # pixel grid, so allow a 1px tolerance — sub-pixel at board scale and hidden
    # under the hex-grid stroke. Vertical/N-S edges match exactly (tolerance 0).
    diff = sum(1 for x, y in zip(prof_a, prof_b) if x != y)
    ok = diff <= 1
    print(f"match {spec_a} vs {spec_b}: {'PASS' if ok else 'FAIL'} (diff={diff}px)")
    if not ok:
        print("  A:", "".join("#" if v else "." for v in prof_a))
        print("  B:", "".join("#" if v else "." for v in prof_b))
    return ok


def _parse_edges(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x != ""]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snap")
    s.add_argument("input")
    s.add_argument("output")
    s.add_argument("--edges", required=True, type=_parse_edges)

    v = sub.add_parser("verify")
    v.add_argument("tile")
    v.add_argument("--edges", required=True, type=_parse_edges)

    m = sub.add_parser("match")
    m.add_argument("a", help="TILE.png:EDGE")
    m.add_argument("b", help="TILE.png:EDGE")

    args = ap.parse_args()
    if args.cmd == "snap":
        snap(args.input, args.output, args.edges)
    elif args.cmd == "verify":
        sys.exit(0 if verify(args.tile, args.edges) else 1)
    elif args.cmd == "match":
        sys.exit(0 if match(args.a, args.b) else 1)


if __name__ == "__main__":
    main()
