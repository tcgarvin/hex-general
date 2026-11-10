# Lake tiles, round 1 (2026-11-10)

Candidates only; nothing applied to `frontend/`. Hex art is the user's call.

- Candidate tiles: `../candidates/lake1/lake/00-05.png` (a tiles root holding only
  `lake/`), provenance in `../candidates/lake1/picks.json`.
- Joins: `../candidates/lake1-joins/` (`lake/edge_*`, `vert_*`, `mouth_*`, with
  `joins.json` + README), built by `scripts/tile-foundry/joins.py`.
- Review: `review/lake-review.html` (self-contained, data-URI images) and the PNGs
  beside it. `review/` is git-ignored (`.gitignore` here), like
  `art/codex/terrain/review/`.
- Showcase map: `scripts/board-mock-maps/lakes.txt`.

## Recipe

Paint-in (`docs/ART_PIPELINE.md`, "Paint-in recipe") with a new guide shape:
`scripts/tile-foundry/paintin_geom.py` `_lake_strokes`, switched on by the guide
field `lake_margin` on a river guide with no edges. The outline is a blend of a
circle and the hex inset by the margin (so water reaches further toward the
corners than toward the edge midpoints, without reading as a hexagon), plus seeded
harmonics 2-9 (no first order: the body stays centred so join sprites line up;
no sixth: the shore never echoes the hex), soft-capped `lake_margin - 1` px inside
every edge. `lake_margin: 10` keeps the leak gate's 8 px band clear. Each cell has
its own seed, so every guide has its own shoreline; Codex follows the guide.

Pin: rim pin, ground restore on, per-sheet water colour normalisation toward the
river's water (`feature_strength` 0.8 to (67, 81, 77), as the river jobs), anchors
from the river pond job. Gates: the standard paint-in gates; the only warnings are
the advisory whole-tile `lum` (a tile that is half water is darker than plains).

Refs (in `refs.txt` of each work dir): `docs/concept-art/concept1.png`, the
concept's lake close-up `direction/refs/ref_river_lake_3x.png`, and
`refs/river-tiles-<cell>.png` = three shipped river tiles at the sheet's cell
scale on magenta (`make_refs.py`), so the water and banks copy the rivers.

## Codex calls (2 of the 4 budgeted)

| sheet | layout | thread | wall | result |
|---|---|---|---|---|
| `a/` lake-a | 3x3 (340 px) | 01a1c7aa-66a8-7392-8666-9582fb5fe47b | 48 s | 9/9 pass; not used |
| `b/` lake-b | 4x4 (256 px) | 01a1c7ad-048a-7290-9bb6-bfe26d3bff21 | 44 s | 16/16 pass; all six picks |

Registration: a residual 0.08 tile px / IoU 0.991; b 0.09 / 0.994.

**lake-a** (3x3, the features scale) followed the guides and the per-hexagon shore
details, but its water was flatter than the rivers' (water L std 8.5-9.0 against
13.1-13.4 for river tiles; median L 77 vs 75) and its banks were a thin dark line
with almost no khaki lip; one cell painted a bright green tuft, stones were large
and pale. Concrete defects, so one re-roll.

**lake-b** re-rolled at 4x4, the scale the river tiles were painted at (2:1), with
the river-tile ref at that scale, wording asking for the rivers' ripple contrast
and bank lip, and "tiny, dull, half-sunk" stones. Water L std 12.6-13.5 (river
13.1-13.4), median RGB (61, 76, 72) vs river (64, 78, 74), S .19 vs .18; banks have
the river's dark undercut and broken khaki lip. No further re-roll: the remaining
weaknesses (below) are not ones another call is likely to fix.

## Picks (`apply_picks.py`)

| candidate | sheet cell | guide seed | details |
|---|---|---|---|
| lake/00 | b lake_00 | 31 | reeds NW, wide body |
| lake/01 | b lake_02 | 33 | gravel W, reeds E |
| lake/02 | b lake_07 | 38 | bay W with reeds |
| lake/03 | b lake_09 | 40 | reeds NE and SW |
| lake/04 | b lake_12 | 43 | reeds SW, round |
| lake/05 | b lake_15 | 46 | gravel NE, reeds S |

All pass every hard gate. Water: 45-48% of the hex area, reaching 0.80-0.88 of
the inradius toward the corners and 0.76-0.79 toward the edge midpoints (the rim
pin plus the bank need about 12 px there), 10-12.5 px clear of every edge.

## Joins and river mouths (`scripts/tile-foundry/joins.py --terrains lake`)

```bash
python3 scripts/tile-foundry/joins.py --tiles art/codex/terrain/candidates/lake1 \
    --out art/codex/terrain/candidates/lake1-joins --terrains lake
```

- Edge lenses and vertex fills: the patch route, with windows ranked against
  anything that is not open water (reeds, stones, banks) and bigger, crisper
  shapes than woods (lens half-width 25, half-length 30, feather 3; vertex radius
  29, feather 4) because the shore sits 10-12.5 px inside each edge. Lens tips
  carry a bank (dark undercut + broken khaki lip, colours measured on the
  candidates) where the lens edge lies over the grass rim, so where two lakes meet
  beside land the neck has a shore.
- `mouth_<dir>` (dir = direction from the river hex to the lake): derived, no
  Codex. The river crossing profile (`river/profile.json`, the colours every river
  tile is pinned to at an edge) is extruded into the lake hex, flaring to 1.6x by
  18 px, banks fading out at 6-11 px (where the lake shore takes over), water
  blending into lake water patches and fading out at 15-23 px. On the edge it equals
  the profile, so it continues the river tile without a seam.
- The shipped woods/city joins still rebuild byte-identical from
  `candidates/review1`.

## board-mock

`scripts/board-mock.py`: map char `o`, `TERRAIN_FILL["lake"]` = the river fill,
lake is a cosmetic-variant terrain (same hash as woods) and draws as its flat fill
when no tile set has lake art (the live frontend today), no roads on lakes. Two
outlet rules, pure functions sharing `calm_outlet`: the **mouth rule**
(`mouth_direction`, `lake_mouths`) and, below it, the **edge rule**
(`edge_direction`, `map_edge_outlets`) for river hexes on the map border;
`with_outlets` adds the chosen edge to the river course. `--joins` draws lake joins
like woods and the mouth sprite on each (river, lake) mouth edge.

Renders (from the repo root):

```bash
M="--fallback art/codex/terrain/candidates/lake1 --map scripts/board-mock-maps/lakes.txt"
J="--joins art/codex/terrain/candidates/lake1-joins"
python3 scripts/board-mock.py $M $J --hex-px 64  --out art/codex/terrain/lake/review/showcase-64-joins.png
python3 scripts/board-mock.py $M $J --hex-px 128 --out art/codex/terrain/lake/review/showcase-128-joins.png
python3 scripts/board-mock.py $M    --hex-px 64  --out art/codex/terrain/lake/review/showcase-64-nojoins.png
python3 scripts/board-mock.py $M $J --hex-px 128 --crop 8,4,19,11 --out art/codex/terrain/lake/review/bigLake-128-joins.png
```

## Known weaknesses (first pass)

1. Ripples are soft horizontal wind-streak bands, coarser and more regular than
   the rivers' broken current streaks; a big lake shows a faint striped rhythm.
2. Inside merged lakes a few short bank fragments survive between lens and vertex
   fills, and neighbouring patches show slight ripple seams at 128 px.
3. The shore inset follows the hex (every tile keeps its grass rim), so lone lake
   hexes and lake edges read slightly hexagonal; two lakes meeting beside land
   make a narrow neck.
4. Mouth sprites are derived: the flared channel's neck is a little lighter than
   the lake water at 128 px.
5. At 64 px only the reed clumps read; stones and gravel vanish.

Tests: `scripts/tests/test_board_mock.py` (mouth and edge rules, lake parsing, flat
fill, lake variants, mouth joins), `test_joins.py` (mouth sprite equals the profile
on the edge, every lake kind built), `test_paintin.py` (lake guide clearance, reach,
centring, per-seed variety).
