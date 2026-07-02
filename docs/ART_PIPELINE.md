# Terrain art pipeline — inputs & outputs

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

1. **Texture** (plains): swatch-cut. Requires genuinely uniform art — cuts
   must be safe anywhere. Variants = seam-compatible cut offsets
   (`gate.py clique`).
2. **Object field** (woods): sprites segmented from a white sheet, placed
   whole by a seeded compositor. Nothing is ever truncated at a hex edge.
3. **Whole-hex vignette** (city, hills, town): one feature cluster per
   hex, segmented from a white sheet and composited onto the ratified plains
   base. The grass rim **is** plains, so seam compatibility is inherited.
   Optional grass palette-match toward plains medians (hills).
4. **Oriented** (river/bridge/roads): edge-anchored per-tile generation with
   guide images. **Not yet migrated** to the painterly register.

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
