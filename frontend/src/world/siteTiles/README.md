# Site and base tiles

Painted 128px flat-top RGBA hex tiles for economic sites and the player base.

- Source: `art/codex/terrain/candidates/sites-ratified/`, ratified by the user on 2026-11-10;
  factory L1 and L3, oil field L3 and base stage 1 replaced from sites round 5 on 2026-11-11.
- Naming: `farm|oil_field|steel_mill|factory/l<level>_<nn>.png` (level 1-3, variants `nn`), and
  `base/stage<N>_<v>/h<i>.png` (stage N = 1..4, variants `a`/`b`, one file per footprint hex).
- These files are byte-for-byte copies. To regenerate, re-copy from the ratified set (provenance
  for every tile is in `art/codex/terrain/SITES.md`). `positions.json` and `picks.json` are not
  copied: the base footprint is the constant `BASE_FOOTPRINT` in `../siteTiles.ts`.
- `scripts/tests/test_site_tiles_sync.py` asserts the copies match the source and the footprint.
- Changing these files is hex art and is subject to the user's sign-off rule in the root
  `CLAUDE.md` (montage Artifact first, user picks). Never auto-ratify.
- Selection logic lives in `../siteTiles.ts` and must match `scripts/board-mock.py`.
