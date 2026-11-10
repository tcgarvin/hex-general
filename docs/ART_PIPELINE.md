# Terrain art pipeline — inputs & outputs

> **Status (2026-11-09):** new art generation moves to **Codex** (the `codex` CLI's
> built-in image generation; see `docs/VISION.md` §11). The shipped terrain tiles
> now come from the Codex paint-in toolchain (see "Paint-in recipe" below; ratified
> 2026-11-09). The NBP sections further down remain the contract for the unit
> sprites and the record of the superseded NBP terrain tiles (kept, and still
> byte-reproduced by `selftest.py`, under `art/foundry/applied-tiles/`). The asset
> spec and the user-ratification gate apply to Codex-made art unchanged.

## Codex recipe (2026-11-10)

How to drive Codex's built-in image generation non-interactively. First proven on
the site-kind icon candidates; prompts, raw sheets, cuts and provenance are in
`art/codex/sites/` (`manifest.json`, `README.md`).

**Prerequisites.** `~/.local/bin/codex` (v0.165.0), logged in with ChatGPT
(`codex login status` should print `Logged in using ChatGPT`). No API key needed.
The `image_generation` feature is on by default (`codex features list`).

**Invocation.** Run from an empty scratch directory outside the repo, read-only
sandbox, prompt on stdin, JSONL events on stdout:

```bash
wd=$(mktemp -d)          # scratch dir: Codex never sees the repo
cd "$wd"
codex exec --skip-git-repo-check -s read-only -C "$wd" --json - \
  < art/codex/<set>/prompts/<name>.txt > run.jsonl 2> run.err
tid=$(head -1 run.jsonl | python3 -c 'import json,sys;print(json.load(sys.stdin)["thread_id"])')
cp ~/.codex/generated_images/$tid/*.png <dest>.png
```

- `-s read-only` is enough: image generation is a built-in tool, not a shell
  command, so the sandbox doesn't block it, and Codex can't write files anywhere.
  `--skip-git-repo-check` is needed because the scratch dir isn't a git repo.
- **Output location:** `~/.codex/generated_images/<thread_id>/exec-<uuid>.png`,
  one file per generated image. The thread id is in the first JSONL event
  (`{"type":"thread.started","thread_id":...}`). Codex does not copy the image
  anywhere else, so copy it yourself, and record the thread id in the manifest.
- **Format:** 8-bit **RGB PNG, no alpha.** Square requests come back
  1254x1254; earlier landscape/portrait requests came back 1536x1024 / 1024x1536.
  You can't get transparency from Codex directly, so key the background yourself
  (below).
- **Time:** 48-76 s wall per call (one image) end to end, including Codex
  start-up. Several calls can run in parallel (three at once worked fine).
- `run.err` shows harmless `failed to refresh available models: request timed
  out` lines; the generation still succeeds (exit 0, final agent message "Done.").

**Prompt tips.**
- Open with an explicit instruction so Codex makes the image and nothing else:
  *"Use your built-in image generation tool to create ONE image. Do not write
  code, do not run shell commands, do not edit any files. Just generate the
  image and then reply with one line saying it is done."*
- For a set, ask for **one sheet in a strict grid** (e.g. 4 columns x 3 rows, one
  subject per cell, generous margins, no grid lines, no text). That gives a much
  more consistent set (scale, lighting, palette) than separate calls, and 12
  icons cost one ~60 s call. Expect uneven cell spacing; cut at the widest gaps,
  not on a fixed grid.
- **Backgrounds to key:** painted art on flat pure magenta `#FF00FF` (comes back
  about (251,2,251)); ink/line art on flat pure white. Say "no gradient, no
  texture, no shadows on the background".
- Palette: Codex drifts warm and golden by default. To match the board, say
  "soft overcast cool daylight, neutral colour balance, NOT sepia, golden,
  warm-yellow or orange-tinted; greens stay cool olive". Sheet 3 of the sites set
  (`art/codex/sites/prompts/A-painted-sheet-3.txt`) is the reference wording.
- Spell out each subject's silhouette, and say which motifs must not be reused
  (a "city" variant came back as a gear and clashed with the factory until the
  prompt said "do not use a gear on anything except the tank factory").

**Cut and key.** `art/codex/sites/cut_sheet.py <raw.png> --key magenta|white
--out-dir <dir>` → 12 x 128x128 RGBA PNGs, subject fitted to 120 px and centred.
Magenta keying: alpha from `min(r,b) - g`, colour unmixed against the sampled
background, then despilled. White keying: alpha = `1 - min(rgb)/255`, unmixed
from white. Clean edges, no visible fringe. Rendering the review images:
`art/codex/sites/review_renders.py`, then `scripts/review-page.py`.

## Paint-in recipe (terrain tiles, ratified 2026-11-09)

How the shipped terrain set was made. Tools: `scripts/tile-foundry/paintin.py`
(+ `paintin_sheet.py`, `paintin_geom.py`, `paintin_pin.py`, `paintin_gate.py`),
`scripts/tile-foundry/joins.py`, `scripts/board-mock.py`. Work dirs, prompts, raws,
picks and measurements are in `art/codex/terrain/` (its `README.md` has the round
by round findings; `APPLIED.md` maps every shipped tile to its sheet and cell).

1. **Sheet.** `job.json` lays out separated flat-top hexagons on flat `#FF00FF`,
   each one of our own opaque ground tiles (crops of the ratified G2 meadow), plus
   for oriented tiles a deterministic guide (river band or road ribbon through the
   named edge midpoints, bridge-deck marker).
2. **Codex.** One call per sheet (Codex recipe above), with concept close-up refs.
   Codex keeps the magenta and the hexagon silhouettes to about a pixel and paints
   the feature in register, but re-renders the ground it was given.
3. **Cut.** Fit scale and translation from the silhouettes (fails loudly on a missing
   cell or a residual, width or IoU over its limit) and resample each cell to 128 px.
4. **Pin.** Pin the rim back to the base ground exactly, so any tile abuts any other;
   optionally restore the interior ground with a difference matte (on for every
   class: Codex's repainted grass otherwise leaves a light hex outline). Oriented
   tiles pin each edge crossing to one shared edge profile (`river/profile.json`,
   20 px water; `road/profile.json`, 9.25 px road), so crossings line up across
   every shape and variant.
5. **Gates.** Per-tile checks with measured values in `report.json`: registration,
   rim painted over (`rim_clear`), features touching the rim, salient pixels near
   the boundary, water or road leaking outside its band, crossing width and centre,
   advisory feature L and S against concept anchors. The montage and board preview
   decide; a marginal gate failure can still be picked, and the pick records it.

**Scale.** Codex paints elements at a roughly constant size in output pixels
whatever the hexagon size, so the sheet density sets the feature scale. Woods,
hills, town and city use 3x3 sheets (340 px hexagons, 2.66:1), which gives the
concept's crown and house scale and the best registration; denser sheets (6x6,
1:1) make features cartoonishly large. Rivers, roads and bridges use 4x4 sheets
(256 px, 2:1); 6x6 came back softer.

**Per-hexagon prompts.** Codex reuses one layout across a sheet unless told not to.
Numbering the nine hexagons and describing each one's layout ("densest mass toward
the north-west, a bay on its south-east side"), then saying every hexagon must look
different, gives nine distinct variants from one call.

**Joins.** Every tile keeps a plain-ground rim, so same-terrain neighbours show a
grass moat. `joins.py` cuts lens sprites (shared edge) and disc sprites (corner of
three) from the terrain's own tiles; the renderers draw them after the base tiles
and before the grid and overlays (`frontend/src/board/joins.ts`). Woods and city
have joins.

**Offline preview.** `scripts/board-mock.py` renders any map with the frontend's
exact tile, join and road selection (`--tiles`, `--joins`, `--no-vector-roads`)
and reports any hex that falls back to a generic tile. Use it to judge candidates
at board scale before anything is applied; the user still ratifies.

**Replay.** `paintin.py run --from-raw <work>/raw/<name>.png` (with the job, prompt
and `refs.txt` refs) rebuilds a sheet's tiles without a Codex call; the raws of the
sheets behind shipped tiles are committed, derived files are not.

## NBP foundry (superseded for terrain; unit sprites)

The review artifact for how terrain hex art gets made, now that the loop is
proven on plains, woods, hills, and city. It defines the **contract**: what
art direction goes in, what assets come out, and where human judgment sits.
Provenance and per-terrain parameters live in `art/foundry/` (see its
README + `manifest.json`); tooling lives in `scripts/tile-foundry/`.

## The loop in one paragraph

Style direction comes from concept art. Nano Banana Pro (Vertex AI, keyless)
generates either a continuous swatch or a white-background cluster sheet,
steered by concept-art crops as style references — plus, when geometry
matters, a **layout-diagram reference** (proven by the city's six-spoke
street guide). Deterministic tools cut/segment/composite tiles; automated
gates (seam, truncation, luminance) filter candidates; the **user ratifies
from hosted montages + adjacency previews** (hard gate — nothing is applied
without a pick); the applied tiles are verified on the live board and made
**byte-reproducible** from `manifest.json` (`selftest.py` enforces this).

## Inputs (how art direction reaches the pipeline)

| Input | Where it lives | Who owns it |
|---|---|---|
| Concept art (register, palette, mood) | `docs/concept-art/` (+ its README for what it does/doesn't direct) | user |
| Per-terrain art direction (durable prose: "trees end before the hex edge", "calm via smoothness not paleness", "six streets toward edge midpoints") | scattered today: `docs/ROADMAP.md` Phase 8, `docs/concept-art/README.md`, CLAUDE.md — **decision point 1 below** | user |
| Prompt templates (the operational encoding of direction) | `art/foundry/<terrain>/*-prompt.txt` | agent drafts, user's feedback shapes |
| Style-ref crops | `art/foundry/refs/` (crop coords recorded in `manifest.json`) | agent selects from concept art |
| Layout guides (geometric control: street directions, future river/road anchors) | `art/foundry/<terrain>/` (e.g. `city/street-guide.png`) | agent builds from game geometry |
| Numeric bands (luminance etc.) | `manifest.json → concept_anchors` — **measured from the concept art**, not chosen by taste | derived |
| The ratification | hosted montage + adjacency preview (full + board scale), inline images when remote | **user, always** |

## Outputs (the asset spec)

- **128×128 RGBA PNG**, flat-top hex, point-to-point width 128, centred.
  (Display 64 world px; crisp at `MAX_ZOOM = 2`.)
- **Alpha** = `hexgeom.hex_mask` dilated 1px ("bleed") so abutting tiles
  overlap real pixels — kills AA hairline seams.
- **No baked outline/grid/vignette**; the grid is a render-time overlay.
- **Naming**: `frontend/src/board/tiles/{terrain}/{nn}.png`, discovered by
  glob; a coord hash picks the variant per hex (stable, cosmetic).
- **Variants**: 3–4 per terrain, "same thing, a little different" — mild
  texture variety, never distinct features.
- **Rendering**: linear filtering for >64px tiles (`tiles.ts` switches on
  `pixelWidth`); legacy 64px pixel-art terrains keep nearest until migrated.
- **Reproducibility**: every applied tile regenerates byte-for-byte from
  `art/foundry/manifest.json` + committed sheets via `compose.py` (woods) /
  `vignette.py` (hills, city); `scripts/tile-foundry/selftest.py` checks this.

## Terrain classes (which recipe a terrain gets)

> Superseded for terrain by the paint-in recipe above (2026-11-09): every terrain is
> now painted in context on the shared ground. Kept as the record of the NBP tiles
> and of why each class existed.

1. **Texture** (plains): swatch-cut. Requires genuinely uniform art — cuts
   must be safe anywhere. Variants = seam-compatible cut offsets
   (`gate.py clique`).
2. **Object field** (woods): sprites segmented from a white sheet, placed
   whole by a seeded compositor. Nothing is ever truncated at a hex edge.
3. **Whole-hex vignette** (city, hills, town): one feature cluster per
   hex, segmented from a white sheet and composited onto the ratified plains
   base. The grass rim **is** plains, so seam compatibility is inherited.
   Optional grass palette-match toward plains medians (hills).
4. **Oriented** (river/bridge/roads): edge-anchored per-tile art keyed by the
   connected-edge mask (`riverTiles.ts`), with a render-time L↔R mirror. **Rivers
   and bridges migrated** to the painterly register earlier (swatch-cut water +
   deck-composite crossings). **Roads (T5, 2026-07-04) use a distinct NBP-finish
   route** (`scripts/tile-foundry/roadfinish.py`): a deterministic flat road ribbon
   is baked onto the ratified plains/hills bases (`road.py`), an NBP image-to-image
   pass wears/grades the road into the ground, then a deterministic post-chain
   (road-mask recolour to the ribbon palette + a b08 rim blend, `rimblend.py`) pins
   the width, colour, grassy rim, and edge crossings back to the deterministic
   input so tiles still abut invisibly. Auto-gated on drift/width/rim/lum (+phantom
   for end caps); 24 tiles shipped (plains full, hills partial), the rest keep the
   vector-overlay fallback. **Reproducibility:** the 2K raw NBP gens are *not*
   committed; the committed source is the 128px post-resize intermediate
   (`art/foundry/road/gens128/`), from which the post-chain byte-replays
   (`roadfinish.py build --verify`, in `selftest.py`; regenerate via
   `export-sources`) — same "commit the cheap deterministic-replay seed, not the
   heavy raw" rule the river/bridge foundries follow.

Learned rulings that constrain the classes: hills-as-texture is dead (relief
contrast breaks seams — best clique 2/49 vs plains' 23); "calm" = low detail,
never low saturation; processing to hit a number degrades good art — prefer
prompt fixes and pure curation, with deterministic recolour only for palette
cohesion (match hue *and* sat, sweep value gently).

## Decision points for this review

1. **Where should durable per-terrain art direction live?** It's currently
   split across ROADMAP Phase 8, concept-art README, CLAUDE.md, and session
   memory. Proposal: one `art/DIRECTION.md` with a short section per terrain
   (the user-voiced rules only — parameters stay in `manifest.json`), and the
   other homes point at it.
2. **Are the output specs above final?** 128px / 1px bleed / 3–4 variants /
   no baked grid — anything you want different before rivers/roads/town lock
   more art onto this spec?
3. **Luminance bands: hard gate or advisory?** Woods taught us board-scale
   legibility trumps the number. Today bands are advisory (gates filter
   candidates, the montage decides). Formalize that?
4. **Oriented-terrain migration** (river/bridge, then roads): same painterly
   register, but geometry is exact (edge-anchored crossings). Plan is
   per-tile generation + layout-diagram refs (the street-guide trick) +
   the existing edge classifiers. Green-light to design when we pick it up?
5. **Holistic regional renders** (the "later phase" from the pivot): paint
   multi-hex regions over the proven tile style and cut with the same cutter.
   Stays parked until the per-hex set is complete?

## How to run the loop (operational quick-ref)

```
# generate (keyless Vertex; refs repeatable)
python3 scripts/tile-foundry/gen.py --prompt-file <p.txt> --ref <crop.png> --image-size 2K --out <swatch|sheet>.png

# texture route
python3 scripts/tile-foundry/cut.py <swatch> --out-dir <cands> --stride 320 --bleed 1
python3 scripts/tile-foundry/gate.py "<cands>/*.png" --lum-min <lo> --lum-max <hi> --seam-mode texture --summary
python3 scripts/tile-foundry/gate.py clique "<cands>/*.png" --seam-mode texture --pick 4 --out-dir <picks>

# vignette / object-field route (after a manifest section exists)
python3 scripts/tile-foundry/vignette.py --manifest art/foundry/manifest.json --section <terrain> --out-dir <tiles>

# review artifacts (the user picks; hard gate)
python3 scripts/tile-foundry/patch.py --tiles <tiles> --context art/foundry/plains/picks --layout mass --out <prefix>
python3 scripts/tile-montage.py <tiles> --out .dev/montages/<name>.png   # hosted by ./scripts/dev.sh montage up

# after ratification
#   apply → verify-board skill → provenance into art/foundry/ + manifest section → selftest.py → commit
```
