# River-by-inpainting experiment

Goal: stop fighting PixelLab for river geometry. Instead of generating whole
river hexes and snapping/warping them, **overlay a river onto a plains hex** by
painting water anchors at the canonical edge crossings and letting PixelLab
*inpaint* the channel between them. Only the river segment is generated; the
plains tile underneath is reused.

## Mechanism (what actually works)

- Tool: PixelLab **`map-objects`** inpainting (`POST /v2/map-objects`, exposed in
  the MCP as `create_map_object`). Payload: a 64x64 `background_image` (plains +
  painted water anchors) + an `inpainting.mask_image` (WHITE = model fills,
  BLACK = frozen context). This is real init-image + mask inpainting — the thing
  the terrain-tiles note said PixelLab lacked (that was only true of
  `create_tiles_pro`).
- The return is a **transparent-background object** = just the generated region.
  So this is naturally an **overlay** (composite over any plains variant), not a
  baked tile. We clip to the plains alpha on composite.
- `make_corridor.py` builds the bg + mask for a straight or curved corridor of a
  given width, pinned to the top/bottom edge centers so tiles self-tile.

## Key findings

1. **Concept works.** Inpainting reliably bridges painted anchors into a
   continuous river that blends with the plains (run 1, `out1`).
2. **The white mask *is* the river.** Fill the whole hex -> you get a pond, not a
   river (run 2, `out2`). Width is controlled by the fillable corridor, full stop.
3. **Mask adherence is only ~2-4px.** The inpaint bleeds past the mask edge, so
   **small mask features are ignored** — a 11x4px "anchor notch" got steamrolled
   (`diag_montage.png`, 274px opaque-in-frozen). Control width with *chunky*
   frozen grass, not fine features.
4. **Width rule of thumb:** final river width ~= corridor width + ~8px (the model
   adds ~4px spread per side). 9px corridor -> ~17px river (run 3, `out3`), clean
   and uniform, and it **self-tiles** (same width + centered crossings).
5. **Bends are authored, not generated.** A curved corridor centerline (endpoints
   pinned at the crossings) yields a natural winding river that still tiles
   (run 5, `out5`). PixelLab does not need to invent the meander.
6. **Color:** tint the anchor to steer water color (muted grey-teal achieved,
   run 2/3). `outline: "lineless"` softens the rust-colored bank rim of the
   default `single color outline`.

## Open / next

- **Pin a `seed`.** Runs were unseeded, so RNG variance (esp. water color:
  run 5 rolled pale cyan+lime) rode along on every change and confounded A/Bs.
  Seed first, then vary one thing at a time.
- Narrow-corridor + lineless together dissolved the channel (run 4, `out4`) —
  re-test seeded, one variable at a time.
- Then: reuse the corridor trick for diagonal / turn / fork masks.

## Run log (object ids auto-delete after 8h; PNGs are the record)

| run | inputs        | mask            | return      | notes |
|-----|---------------|-----------------|-------------|-------|
| 1   | bg_straight_v | mask_straight_v | out1_river  | open interior; bridges anchors; fat |
| 2   | bg3           | mask3 (edited)  | out2_river  | whole-hex fill -> pond; muted teal; `diag_*` = adherence check |
| 3   | bg4           | mask4 (9px)     | out3_river  | **clean uniform ~17px straight river, self-tiles** |
| 4   | bg5           | mask5 (5px S)   | out4_river  | 3 vars at once (5px+curve+lineless) -> broke |
| 5   | bg6           | mask6 (9px S)   | out5_river  | **curve works**; color rolled bad (no seed) |

`pipelineN_montage.png` = source->input->mask->return->composite->self-tile for
each run. `inline`/`overlay` images are working views.
