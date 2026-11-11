# Ratified site and base tiles (2026-11-10, round 5 replacements 2026-11-11)

The set the user approved after two review rounds (sites round 3 and round 4, see
`art/codex/terrain/README.md`), with factory L1 and L3, oil field L3 and base stage 1
replaced from sites round 5 on 2026-11-11 (see "Round 5 replacements" below). **Used by the game:** byte copies live in
`frontend/src/world/siteTiles/` and the world renderer draws them via
`frontend/src/world/siteTiles.ts` (`scripts/tests/test_site_tiles_sync.py` keeps the
copies in sync with this dir). Changing a tile here is hex art under the sign-off rule
in the root `CLAUDE.md`. Provenance for every file: `picks.json` in each kind dir and the
generated table `art/codex/terrain/SITES.md` (`sites_ratified_build.py` rebuilds
both and fails on a missing source or a sha256 mismatch).

## Layout and naming

```
farm/ oil_field/ steel_mill/ factory/   l<level>_<nn>.png   (variants nn = 00, 01, ...)
base/stage<N>_<v>/                      h<i>.png per hex + positions.json
```

| kind | L1 | L2 | L3 | total |
|---|---|---|---|---|
| farm | 2 | 3 | 3 | 8 |
| steel_mill | 3 | 3 | 3 | 9 |
| oil_field | 4 | 4 | 4 | 12 |
| factory | 4 | 4 | 4 | 12 |
| base (20 hex tiles) | stage 1: 2 variants x 1 hex | stage 2: 2 x 2 | stage 3: 2 x 3, stage 4: 2 x 4 | 8 sets |

Levels 1-3 grow the site: more of the hex built, heavier materials (L1 makeshift,
L2 brick and fenced yard, L3 major works). Oil shows N derricks at level N and the
factory N sawtooth halls at level N. Variants are interchangeable picks within a
level (the mock chooses by coordinate hash). Base stage dirs are variants `a` and
`b` of the same stage; the user chose the complete earth bank for stages 1-2 (the
"building" flavour was not approved and is not here).

## Base footprint

Stage N covers N hexes, in axial offsets from the anchor (`hex.ts` convention):
stage 1 `(0,0)`; stage 2 adds `(1,0)`; stage 3 adds `(0,1)`; stage 4 adds `(1,1)`.
`h<i>.png` is the hex at `positions.json` entry `h<i>`. The user asked for four
hexes kept clear per base (2026-11-10); this particular shape and fill order were
proposed by the art pass and built without objection, not explicitly chosen. The
engine keeps all four hexes of every base plot clear (`BASE_FOOTPRINT` in
`api/app/engine/models.py`, mirrored in `frontend/src/world/siteTiles.ts`).

## Tile format

128x128 RGBA flat-top hex, transparent outside the hex. The outer rim is pinned to
plains ground (G2), so a site abuts any terrain. Base internal edges (between the
hexes of one stage) are painted continuous and **must be drawn together**; they
are not rim-pinned and do not match plains.

## Preview

```
python3 scripts/board-mock.py --map scripts/board-mock-maps/sites1.txt \
  --sites art/codex/terrain/candidates/sites-ratified \
  --joins frontend/src/board/joins --hex-px 64 --out <out.png>
```

`scripts/board-mock-maps/sites4-base.txt` draws the bases only. Map `[sites]` lines
are `<col>,<row> <kind> <level>` (base level = stage 1-4). The mock raises if the
sites dir lacks a tile a map asks for.

## How the game uses these

1. Farm, oil field, steel mill and factory sites stand on their own plains hexes;
   the world renderer draws `l<MapSite.level>_*` in place of the terrain tile.
2. A founded base (one with a settlement) draws the anchor's `stage1_*` tile; a vacant
   base slot keeps its plains tile. Base growth is deferred, so stages 2-4 are not
   drawn yet; when they are, all hexes of one `stage<N>_*` dir are drawn together.
3. City sites keep their city terrain tile and round emblem.

## Round 5 replacements (ratified 2026-11-11)

The owner signed off on sites round 5 ("proceed with site round 5, use your best
judgment"), which fixed three shortfalls of the 2026-11-10 set: the factory L1 hall
was as large as an L2 hall and L3 filled only about three-quarters of the hex; oil L3
was light on industry (read like a bigger L2); the one-hex base (stage 1) was tight.
Files replaced, from `candidates/sites-r5/kept/` (sources and gates in
`candidates/sites-r5/candidates.json`, the replaced tiles under `replaced` in
`picks.json`):

| files | round 5 picks | what changed |
|---|---|---|
| `factory/l1_00..03` | `factory-l1-A`, `B`, `C`, `D` | one small hall, about half an L2 hall (occupied .26-.27, was .31-.37) |
| `factory/l3_00..03` | `factory-l3-A`, `B`, `C`, `D` | three staggered halls of different lengths over most of the hex, ragged edge (occupied .75-.82) |
| `oil_field/l3_00..03` | `oil-l3-A`, `C`, `D`, `E` | refinery block (columns, stack, tank farm, pipe runs, wagons) beside three derricks |
| `base/stage1_a`, `stage1_b` | `base-s1-B`, `base-s1-D` | complete bank with a gate, open trodden centre, few things inside |

Not used: `oil-l3-B` (its bund is squared to the hex rim) and `base-s1-A`, `C`, `E`
(stage 1 keeps two flavours). Stage 1 is still one hex at `(0,0)`; its
`positions.json` now comes from the round 5 cluster. Farm, steel mill, factory L2, oil
L1-L2 and base stages 2-4 are byte-identical to 2026-11-10.

Caveats recorded with round 5: the new factory L1 hall has brick-red walls rather than
timber; the oil L3 tanks are larger than L2's (and C sits in a walled bund); the base
trucks stay a little large, and the stage 1 gun pits are a motif stages 2-4 lack.

## Known shortfalls

- `stage4_b` has one internal seam at 4.86 against the 4.0 limit
  (`seam_failures` in `base/picks.json`); no visible line at 3x.
- Sites have no road connection of their own (no track to the rim by design).
