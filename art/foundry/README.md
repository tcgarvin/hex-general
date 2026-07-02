# Art foundry — painterly tileset provenance

Everything needed to **regenerate, tweak, or extend** the painterly terrain
tiles in `frontend/src/board/tiles/`. The deployed PNGs are build artifacts of
what's in this directory plus the tools in `scripts/tile-foundry/`.
`manifest.json` records every parameter; this file explains the shape of the
thing.

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
4. **Settlement vignettes** (town/city): whole-hex compositions on a
   full-bleed grass rim (not yet migrated).

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
- **New texture terrain**: follow the plains recipe with a new prompt +
  concept-art measurement for its luminance band.

`review/` folders hold the montages the user ratified, as the visual baseline
to compare any tweak against.
