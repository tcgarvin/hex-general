# Lake tiles, round 2: painted whole per neighbour mask (RATIFIED)

Ratified by the user 2026-11-10 and applied: `lake/*.png` ship as byte copies in
`frontend/src/board/tiles/lake/` (mouths from `../lake2-joins/lake/` in
`frontend/src/board/joins/lake/`); `scripts/tests/test_lake_tiles_sync.py` checks
the copies. `w0.png` is not shipped. Changing these tiles is hex art under the
sign-off rule in CLAUDE.md.

## Files

- `lake/m<MM>_<VV>.png`: 128 px flat-top RGBA tiles (asset spec as every terrain
  tile). `MM` = the 6-bit neighbour **mask** in two digits, `VV` = variant.
  86 tiles: every mask 00-63 once, a second variant of the 17 most frequent shore
  masks (03 06 07 14 15 24 28 30 32 33 35 39 48 49 56 57 60), six variants of 63.
- `w0.png`: the shared water tile W0 every water edge is pinned to (opaque 128
  px square, exactly periodic on the hex lattice). Not drawn by the board.
- `picks.json`: per tile its source sheet, cell, raw, Codex thread, checks and
  sha256 (written by `paintin_lake.py apply`).
- River mouths: `../lake2-joins/lake/mouth_<dir>_<nn>.png` (`joins.py --lake-water`).
  No lake `edge_*`/`vert_*` joins with this set.

## Selection rule (`scripts/board-mock.py`; frontend port `frontend/src/board/lakeTiles.ts`)

```
mask = sum(1 << d for d in 0..5 if neighbour(hex, AXIAL_DIRECTIONS[d]) is lake)   # off-map = land
key  = "lake/m%02d" % mask          # lake_tile_key
tile = files[key][variantIndex(q, r, len(files[key]))]   # same hash as terrain variants
```

`lake_mask` / `lake_tile_key` in board-mock; directions as `hex.ts`
AXIAL_DIRECTIONS (0 SE, 1 NE, 2 N, 3 NW, 4 SW, 5 S), the same indices the river
edges use. Never mirrored. A lake hex whose mask has no file falls back to a
cosmetic `lake/NN.png` (digits only) or the flat fill, and the report lists it
under `LAKE MASKS WITHOUT TILES`. With a mask set, lake-lake edge and vertex
joins are not drawn; river mouth sprites are (`mouth_<dir>`, dir = direction
from the river hex to the lake, anchored on the shared edge midpoint).

## What every tile guarantees

- **Water edge** (bit set): rim exactly W0 (pinned 1.5 px, cross-fade over 11
  px), so any two tiles meet in continuous water.
- **Land edge**: plains rim (r1 bases), shore 11 px in (water at least 7 px from
  any land edge), bank in between.
- **Crossing**: where a land edge meets a water edge at corner V, the shore
  crosses the water edge 11 px from V, square to it (an arc of radius 11 round
  the land hex's corner); its neighbour has the same arc, so shorelines meet.

Rebuild: `art/codex/terrain/lake/README.md`, "Round 2".
