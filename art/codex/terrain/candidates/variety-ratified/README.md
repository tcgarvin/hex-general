# Variety tiles, ratified 2026-11-11

Occasional decoration of open country: small natural features and homesteads on
plains hexes, and roadside buildings or trees on plains road hexes. Purely visual,
chosen per hex from static map data; the engine sees nothing.

Owner's direction on round 2: "I think the orchards and the rocky knolls are too
much, too close to hills and forests. I leave density and small/medium mix to you,
we can adjust later."

| folder | small (round 1, `s`) | medium (round 2, `m`) |
|---|---|---|
| `plains_nature/` | `s01`-`s11` (nature-01..07, 09, 10, 12, 14) | `m01`-`m08` (nature-m01..m05, m08, m11, m12) |
| `plains_home/` | `s01`-`s08` (home-01..06, 08, 10) | `m01`-`m08` (home-m01..m08) |
| `road_variety/` | `<shape>_s<N>`: straights x3, turn_n_se x1, other turns x2 | `<shape>_m<N>`: straights x2, turns x1-2, eight forks (fork_se_n_sw x2) |

Left out: nature-11 (bright pale outcrop), nature-m06 (outcrop reads as a rocky
mound), nature-m07 (rocky knoll), nature-m09 and nature-m10 (orchards), plus the
round 1 tiles that were not first-pass picks. `picks.json` maps every file to its
round, label, work dir, cell, raw Codex sheet and sha256, and lists every
exclusion with its reason.

Built by `art/codex/terrain/variety_ratified_build.py`, which also copies the set
byte for byte to `frontend/src/board/tiles/{plains_nature,plains_home,road_variety}/`
(`scripts/tests/test_variety_tiles_sync.py` checks the copies). The selection rule
is `frontend/src/board/varietyTiles.ts`, ported from `variety_picks` in
`scripts/board-mock.py`; density and size mix are in
`frontend/src/board/varietySettings.json` (medium, mostly-medium).
