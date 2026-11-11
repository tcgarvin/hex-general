# TODO

Running list of follow-ups and tech debt that don't belong to a specific roadmap
phase. Newest section on top. See [`ROADMAP.md`](./ROADMAP.md) for planned feature
work.

**When an item is finished, delete it from this file — don't check it off.** This
list tracks only outstanding work; completed work lives in git history.

## Strategic view in the staff-map style (decided 2026-11-11, not started)

The owner picked the light "staff-map" style for the far-zoom strategic view
(`docs/VISION.md` §10), with "staff-map-night" kept as a possible theme, the grease
layer (territory edges and fronts) staying on over the tiles, and a short cross-fade at
the switch. Nothing is built yet: the world view still draws today's flat overview below
camera scale 0.4 (`frontend/src/world/WorldRenderer.ts`). The mocks are
`scripts/staff-map-mock.py` (`scripts/staff_map/`), notes in
`.dev/staff-map/notes-r1.json`. Gaps the mocks left open:

- **Owner colours.** Hashed hues collide among 100 players (several pinks and purples);
  needs a neighbour-aware or alliance palette.
- **Towns at near zoom** read as small dark rings, not building clusters.
- **Crayon hatching** is drawn in screen space, so it would swim during a continuous
  zoom; move it to world space.
- **Fog.** The mocks are omniscient; the player's view must show only what they know,
  with stale intel marked.
- **Texture size.** A whole-world texture at 10 to 22 px per hex is 3,500 to 7,700 px
  wide: bake chunks on demand (in a worker, cached like the tile sprite pool) or draw it
  in a WebGL pass from per-hex data textures with rivers and roads as a line mesh.

## After the world-only cutover (2026-11-10)

The 2-player match was deleted in code on 2026-11-10 (not yet deployed). Follow-ups it
left, each checked against the tree:

- **Deploy ordering.** Migration `20261110200000_retire_matches.sql` deletes non-world
  games and drops `games.invite_token`, and `supabase.yml` and `api.yml` deploy
  independently. The currently deployed API (the 2-player build) reads `invite_token`,
  so the migration and the new API need to go out together, or the old API will fail
  in between. Decide the order at the cutover.
- **Unreferenced UI art.** The world screen and pages now use part of the Field HQ kit
  in `frontend/src/board/panels/chrome/` (list in `docs/UI-STYLE.md`'s note). Still
  unreferenced: `btn-{up,hover,pressed,disabled,active}.png`, `frame-leather.png`,
  `leather.png`, `canvas.png`, `sheet.png`, `sheet-tall.png`, `sheet-worn.png`,
  `stitch-{canvas,leather}{,-tall}.png`, and the transport glyphs
  `icon-{back,forward,pause,play,rewind}.png` (the transport keys draw SVG glyphs).
  They are still produced by `scripts/ui-kit-prep.py` and `scripts/ui-icon-cut.py`.
  Needs the owner's call: find them a use, or delete them.
- **Engine scenario loader is test-only.** `load_scenario_file` in
  `api/app/engine/scenario.py` and `api/app/scenarios/twin_rivers.json` are now used
  only by engine tests and `scripts/board-mock.py` (and its tests). Keep as fixtures,
  or move them under `tests/` and `scripts/`.
- **v0.1 muster path.** The engine still has the v0.1 muster/recruitment path, chosen
  when `GameConfig.economy` is unset (`execution.py`, `recruitment.py`, `fog.py`).
  Worlds always set an economy, so only engine tests exercise the old path.
- **Leftover columns.** `games.scenario` is always `"world"` and `games.name` is set
  to the same string. `games.status` still passes through `"lobby"` on creation
  (`SupabaseGameRepo.create_game`) and is set `"active"` later in the
  same `create_world` call, so a world is only briefly a lobby. A later migration could
  drop `scenario` and the `lobby` default.
- **No way to leave a world.** The API has no leave or resign route for a player, and
  the world UI has no button for it. A seated player can only be removed by a dev
  deleting the world (`DELETE /dev/worlds/{id}`).

