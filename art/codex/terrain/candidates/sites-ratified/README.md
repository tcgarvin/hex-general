# Ratified site and base tiles (2026-11-10)

The set the user approved after two review rounds (sites round 3 and round 4, see
`art/codex/terrain/README.md`). **Not yet used by the game:** nothing under
`frontend/` references it. It is kept here, with provenance, for the game work that
will consume it. Provenance for every file: `picks.json` in each kind dir and the
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

## Base footprint (assumed, not explicitly confirmed)

Stage N covers N hexes, in axial offsets from the anchor (`hex.ts` convention):
stage 1 `(0,0)`; stage 2 adds `(1,0)`; stage 3 adds `(0,1)`; stage 4 adds `(1,1)`.
`h<i>.png` is the hex at `positions.json` entry `h<i>`. This is the main agent's
assumption; the user did not object in two reviews but never confirmed it.

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

## What the game needs before it can use these

1. Sites move off town hexes onto their own hexes (today a site is a town).
2. A site level the renderer can read (1-3; base stage 1-4) so it picks `l<level>_*`.
3. A four-hex base footprint in the engine (the base grows over 1-4 hexes, drawn as
   one unit from the matching `stage<N>_*` dir).

## Known shortfalls

- Factory L1 hall is as large as an L2 hall; factory L3 fills about three-quarters
  of the hex.
- Oil L3 is light on industry (reads like a bigger L2).
- The one-hex base (stage 1) is tight.
- `stage4_b` has one internal seam at 4.86 against the 4.0 limit
  (`seam_failures` in `base/picks.json`); no visible line at 3x.
- Sites have no road connection of their own (no track to the rim by design).
