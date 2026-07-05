# Art foundry — painterly tileset provenance

Everything needed to **regenerate, tweak, or extend** the painterly terrain
tiles in `frontend/src/board/tiles/`. The deployed PNGs are build artifacts of
what's in this directory plus the tools in `scripts/tile-foundry/`.
`manifest.json` records every parameter; this file explains the shape of the
thing. The pipeline contract (inputs/outputs, terrain classes, review gates)
is `docs/ART_PIPELINE.md`.

## The pipeline in one paragraph

Style direction comes from `docs/concept-art/concept1.png` (see its README).
Images are generated with Nano Banana Pro (`gemini-3-pro-image-preview`) on
Vertex AI — keyless, same impersonated-SA setup as all GCP access — using
crops of the concept art as style references (`refs/`). Numeric quality bands
(luminance etc.) are anchored by *measuring the concept art*, not chosen by
taste. Every generated asset passes through `scripts/tile-foundry/` gates and
then a human montage review before touching `frontend/src/board/tiles/`.

## Terrain classes (this is the load-bearing design decision)

1. **Texture** (plains): generate a continuous 2K swatch, cut hex-masked
   128px tiles from it (`cut.py --bleed 1`). Art must be uniform/featureless —
   that's what makes arbitrary cuts safe. Variants = different cut offsets that
   form a mutually seam-compatible set (`gate.py`).
2. **Object fields** (woods): NEVER swatch-cut — cutting slices trees, and
   cut-off canopies are jarring (user direction). Instead: generate a sprite
   sheet of individual trees on white (`woods/tree-sprites-02.png`), segment by
   connected components, and deterministically composite complete trees onto
   the ratified plains base (`compose.py` in scripts/tile-foundry, or
   `woods/placements.json` for the exact ratified placements). Trees end before
   the hex edge; lower trees draw over higher ones; mix is ~65% deciduous /
   35% conifer by quota.
3. **Oriented** (river/bridge/roads): per-tile generation with edge-anchored
   prompts + guide images (not yet migrated to the painterly register).
4. **Whole-hex vignettes** (city, hills; town will reuse the recipe): a 2x2
   sheet of feature clusters on pure white is segmented (hole-filled — bright
   roofs/rock read as "white" but are interior), un-matted, and composited
   centred onto the ratified plains bases (`vignette.py`). The grass rim IS
   the plains texture, so seam compatibility is inherited rather than earned.
   Hills additionally get a grass palette-match toward the plains medians
   (rocky crests untouched) — swatch-cutting hills was tried and REJECTED:
   relief contrast breaks seams (best clique 2/49 cuts vs plains' 23).
   City towns carry exactly six streets radiating to the hex edge midpoints
   (generated against `city/street-guide.png`, a layout-diagram reference),
   where roads will connect later.
5. **Units** (infantry, armor, artillery): not terrain — two-layer sprites in
   `frontend/src/board/units/`, built by `unit.py` from the ratified crops in
   `units/gens/extracted/` (the raw 2K gen sheets are not committed, same
   ruling as the road gens). Team areas are painted in a keyable cobalt
   (#3B5FC0 family); a soft hue/saturation weight splits each crop into
   `{unit}_base.png` (team pixels desaturated to grey, 1px #14140f rim) and
   `{unit}_mask.png` (alpha = team coverage, RGB = the value channel), so the
   renderer multiplies the mask by any player colour live — colours are never
   baked and scale to 32+ players. All units face WEST (sources face east and
   are mirrored); footprints 90px bbox (armor/artillery) / 81px (infantry) on
   the 128 canvas, no ground shadow. Parameters in `manifest.json → units`;
   `unit.py build --verify` reproduces the shipped PNGs byte-for-byte.

## Asset spec (outputs)

- 128×128 RGBA PNG, flat-top hex, point-to-point width 128, centred.
- Alpha = `hexgeom.hex_mask` **dilated 1px** ("bleed") so adjacent tiles
  overlap real pixels instead of antialiased edges (kills hairline seams).
- Rendered at 64 world px (`HEX_SIZE*2`) with linear filtering (`tiles.ts`
  switches scaleMode on `pixelWidth > 64`); crisp at `MAX_ZOOM = 2`.
- Per terrain: 3–4 interchangeable variants, `tiles/{terrain}/{nn}.png`,
  discovered by glob; a coord hash picks the variant per hex.
- No baked hex outline of any kind — the grid is a render-time overlay.

## How to tweak

- **Plains texture**: edit `plains/plains-prompt.txt`, regenerate with
  `scripts/tile-foundry/gen.py`, re-cut + gate + review. Or pick different
  offsets from the existing swatch (`manifest.json` has the current four).
- **Woods density/mix/size**: parameters in `manifest.json → woods.compose`;
  re-run the compositor with different values (seeds are free variants).
  `woods/placements.json` is the exact ratified layout for byte-reproduction.
- **Woods tree look**: regenerate `tree-sprites-02.png` from
  `trees-prompt-v2.txt` (or edit the prompt), keeping the white background —
  segmentation and contrast-compression parameters are in `manifest.json`.
- **Hills look/green**: recolour targets in `manifest.json → hills.grass_match`
  (ratified option "a" of `hills/review/hills-recolor-compare.png`); re-run
  `vignette.py --section hills`. New formations = regenerate
  `hills/hill-clusters.png` from its prompt.
- **City layout/roofs**: edit `city/city-clusters-prompt.txt` (keep the
  six-street rule + `street-guide.png` as the second ref), regenerate the
  sheet, re-run `vignette.py --section city`.
- **New texture terrain**: follow the plains recipe with a new prompt +
  concept-art measurement for its luminance band.
- **New vignette terrain** (e.g. town): follow the city recipe — new sheet
  prompt + a manifest section; `vignette.py` needs no code change.

`review/` folders hold the montages the user ratified, as the visual baseline
to compare any tweak against.
