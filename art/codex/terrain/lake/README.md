# Lake tiles, round 1 (2026-11-10): SUPERSEDED

**Superseded by round 2** (below), which the user ratified and which ships in the
game. Round 1 was rejected and never applied to `frontend/`; it is kept as the
record only. Do not copy `../candidates/lake1/` or `../candidates/lake1-joins/`
into the frontend.

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

# Lake tiles, round 2 (2026-11-10): painted whole per neighbour mask

Round 1 was rejected (scalloped shoreline; shore fragments and stitching seams
inside big lakes, from the lens/vertex overlays). Round 2 paints every variation
of the tile whole, chosen by the 6-bit neighbour mask.

**Ratified by the user 2026-11-10 and applied.** The 86 tiles ship as byte copies
in `frontend/src/board/tiles/lake/` and the 18 river-mouth sprites in
`frontend/src/board/joins/lake/` (`scripts/tests/test_lake_tiles_sync.py` checks
the copies; per-tile provenance in `../APPLIED.md`). The frontend selects them in
`frontend/src/board/lakeTiles.ts` (`lakeMask` / `lakeTileKey`, ports of
board-mock's `lake_mask` / `lake_tile_key`) and draws mouths from
`frontend/src/board/joins.ts` (`mouthPlacement`, codes 5 + d); the shared fixture
`frontend/src/board/__fixtures__/lakes.json` (`scripts/lake-fixture.py`) pins the
two to board-mock. No lake edge or vertex joins. `w0.png` is not shipped.

- Candidates: `../candidates/lake2/` (`lake/m<MM>_<VV>.png`, `w0.png`,
  `picks.json`, README with the selection rule); river mouths
  `../candidates/lake2-joins/`.
- Work: `r2/` (`w0/`, `water/`, `pilot/`, `a/`-`d/`: job, lake.json, bases,
  prompt, raw + run.json, cut, norm, tiles, report, manifest), `r2/picks.json`,
  `r2/world_lakes.py` (mask counts and map dumps from generated worlds).
- Tool: `scripts/tile-foundry/paintin_lake.py` (`w0`, `job`, `run`, `apply`);
  tests `scripts/tests/test_paintin_lake.py`, lake-mask tests in
  `test_board_mock.py`, r2 mouth tests in `test_joins.py`.
- Review renders (git-ignored): `review/r2-*.png`.

## Recipe

1. **Geometry** (`ShoreGeometry`): signed shore field = soft minimum of the
   distances to the land edges' *segments* minus 11 px. Where a land edge meets a
   water edge at corner V that is the arc of radius 11 round V: the shore leaves
   the tile across the water edge 11 px from V, square to it, and the lake hex on
   the other side has the same arc. Seeded wobble (up to about 4 px, 2.4 px per
   unit noise) and a broken bank lip only in free shore runs; both fade out within
   11-26 px of every water edge, and the soft minimum shrinks there (8 -> 1.5 px),
   so the cross-section at a crossing is fixed. Tested: on every shared water edge
   the fields of the two tiles agree exactly, whatever their other neighbours.
2. **W0**: a water tile exactly periodic on the hex lattice. v0 (`w0/w0-v0.png`,
   sheet water for the water sheet only): random-phase synthesis at the
   reciprocal-lattice frequencies, with the spectrum and colour distribution of
   round 1's sheet-b water, 40% isotropised. v2 (shipped, `w0/w0-v2.png` =
   `candidates/lake2/w0.png`): `coset_blend` of three Codex-painted all-water
   hexes (water sheet cells 01, 05, 09): the hex centres and the two corner kinds
   are three lattice cosets, each sampled from its own source with a soft window
   and a variance-preserving blend, after removing each source's slow tone drift
   (otherwise window borders showed as faint diagonal lines); brightest flecks
   compressed above +1.5 std (they made the repeat readable); contrast set to a
   luminance high-pass std of 6.0.
3. **Bases** (`paintin_lake.py job`): r1 plains on the land, W0 (a seeded patch of
   it per cell, so Codex does not see one texture 16 times) on the water, a bank
   from colours measured on round 1 (dark undercut at the water, khaki lip 3.6 px
   in, water shaded within 6 px of the shore). Per-hexagon prompt lines name the
   open edges and the shore sides, plus a shore detail and a tone-neutral ripple
   description.
4. **Codex**: the paint-in recipe, 4x4 sheets (256 px cells), refs as round 1
   (`concept1.png`, `ref_river_lake_3x.png`, `refs/river-tiles-256.png`). The
   prompt's key line: water touching a hexagon edge carries on into the next
   hexagon, no bank or outline there.
5. **Pin** (`pin_lake_tile`): water colour pulled all the way (strength 1.0) to
   (65, 80, 77) over the guide's water, and water ripple contrast scaled to
   high-pass std 6.0 (W0 has the same), so no tile is lighter, darker, calmer or
   busier than the water at its edges. Rim: exact to the per-mask base (land
   edges 1.5 px + 6 px ramp, water edges 1.5 px + 11 px ramp). Land interior: the
   paint-in ground restore. Water interior: Codex's, its low frequencies pulled
   to W0's within 26 px of water edges, then a variance-preserving cross-fade from
   W0 into it (correlation estimated on open water, capped at 0.6).
6. **Checks** (report.json): rim exact, water reaching each water edge in the
   cut (share of water in the open part of the edge band >= 0.85), no water on
   the base's grass (<= 3%), and an advisory ring check (water near water edges
   vs deeper: mean L and texture std).

Rebuild (from the repo root; replays need no Codex call):

```bash
L=scripts/tile-foundry/paintin_lake.py; W=art/codex/terrain/lake/r2
for x in water pilot a b c d; do
  python3 $L run --work-dir $W/$x --w0 $W/w0/w0-v2.png --from-raw $W/$x/raw/lake2-$x.png
done
python3 $L w0 --method coset --sources $W/water/norm/m63_0{1,5,9}.png --radius 30 \
    --sharpness 3 --glint-knee 1.5 --hp-std 6.0 --out $W/w0/w0-v2.png
python3 $L apply --picks $W/picks.json --w0 $W/w0/w0-v2.png --out art/codex/terrain/candidates/lake2
python3 scripts/tile-foundry/joins.py --tiles art/codex/terrain/candidates/lake2 \
    --out art/codex/terrain/candidates/lake2-joins --terrains lake \
    --lake-water art/codex/terrain/candidates/lake2/w0.png
```

(W0 v2 is built from the water sheet's `norm/` cells, so after changing the
normalisation replay `water` first, rebuild W0, then replay every sheet.)

## Codex calls (6 of the 10 budgeted, no re-rolls)

| sheet | cells | thread | wall | result |
|---|---|---|---|---|
| `water/` lake2-water | 16 x mask 63 | 01a1c81b-eb44-7e00-80bc-db45b6b64476 | 41 s | 16/16; calm flecked water, no wind streaks; cells 01/05/09 -> W0, 00/03/04/07/08/12 -> m63_00-05; 02/06/10/14 ("very calm") too flat |
| `pilot/` lake2-pilot | big + small showcase lakes (1 4 6 7 12 16 31 33 43 46 48 55 56 58 59 61) | 01a1c81e-ac6e-74e1-8a8b-362fe398ce81 | 69 s | 16/16; water to the edge 0.99-1.0 on every water edge |
| `a/` lake2-a | 12 masks + 4 second variants | 01a1c821-f95f-7013-bf05-02b31506854c | 57 s | 16/16 |
| `b/` lake2-b | 12 + 4 | 01a1c821-f94f-7012-a5d9-be64ffb9730a | 73 s | 16/16 |
| `c/` lake2-c | 12 + 4 | 01a1c824-3f1e-7191-b95d-d827a72cea65 | 67 s | 16/16 |
| `d/` lake2-d | 11 + 5 | 01a1c824-3f2a-71b2-a694-3a8a762e6327 | 73 s | 16/16 |

Registration: max residual 0.16-0.32 tile px, min IoU 0.989-0.993. Every cell
passed every hard check; 22 of the 86 picks carry the advisory ring warning.

Mask frequencies (3 generated worlds, seeds 1-3, 200 slots, 3254 lake hexes,
`r2/world_lakes.py counts`): 63 4.3%, 49 3.7%, 14 3.4%, 56 3.3%, 39 3.2%, 7 3.1%,
35 3.0%, 48, 28, 24, 3, 60, 33, 15, 30, 32, 6, 57 ...; mask 0 never occurs
(lakes are >= 3 hexes), 42 and 43 0.1%. The 17 most frequent shore masks got a
second variant; 63 has six.

## Measurements

- W0 v2 against itself across each edge pair (step across the edge vs the 1 px
  step inside, ratio 1 = no seam): SE/NW 0.98, NE/SW 1.02, N/S 0.90. Exact
  periodicity is also tested in continuous coordinates.
- Pinned water edges: on every water-edge rim (depth < 1 px) every tile equals
  W0 exactly (max |tile - W0| = 0 over all 86). So any pair across a water edge
  has exactly W0's own seam: over all 6120 compatible pairs the ratio equals
  W0's for that edge pair.
- Ring (mean over the 85 tiles with water edges, open water by depth from the
  nearest water edge): L 74.9 / 74.6 / 74.8 / 74.6 / 75.1 at 0-2 / 2-4 / 4-7 /
  7-11 / 11-16 px vs 75.7 deeper; texture std 5.5 / 5.4 / 5.5 / 5.3 / 5.7 vs 6.3.
  Before contrast normalisation sheets a-d reached 6.6-7.3 deep vs W0's 5.0, a
  calmer band with ratio down to 0.33.
- Water: median RGB about (63, 78, 75), luminance std about 7 (rivers 11-13,
  round 1 11-12), gradient y/x about 2 (round 1 3.4: the horizontal streaks
  are gone; rivers 0.7-1.5).
- Guides keep water at least 7.0 px from any land edge (wobble peak); the shore
  sits at 11 px on straight runs.

## Known weaknesses

1. The water is calmer and lower in contrast than the rivers (luminance std ~7
   vs 11-13): it reads flatter than the river beside it at 64 px. Deliberate
   per the "calmer" direction, but it no longer matches the river water exactly.
2. Codex's ripple pattern differs by sheet (pilot: soft scale-like ripples; a/c:
   finer horizontal flecks; water sheet: sparse flecks). Mean colour and contrast
   are normalised, the character is not; adjacent tiles from different sheets
   can show a slight change of texture across an edge (not seen in the big-lake
   or world renders at 128 px, but look for it).
3. Water within ~11 px of every water edge is about 12% calmer in texture than
   deeper water (W0 band + cross-fade). Not visible to me in the renders; 22
   picks carry the advisory ring warning (bands outside 0.75-1.3 or |dL| > 3).
4. The shore follows the hex: land hexes poking into a lake read as rounded
   hexagons, and a lone lake hex is a lobe. Inherent in a fixed 11 px inset.
5. W0 repeats every hex by construction; it only shows in the rim bands, and
   with its flecks softened I could not see the lattice in a 5x5 W0-only block,
   but a 64 px look at a huge all-water area is untested.
6. Mouth sprites are derived (river profile extruded, W0 water): the channel's
   water is a touch lighter and more uniform than the lake's.
