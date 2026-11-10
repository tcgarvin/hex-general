# Codex packed-column sprite candidates (2026-11-09)

Candidate sprites for units in **column** posture (WORLD_SPEC §4: packed for
road travel, fast, stack up to 6 per hex, can't attack, defend badly), one per
unit type (infantry, artillery, armor). They are meant to read as the same army
as the deployed sprites in `frontend/src/board/units/`, but clearly on the march.

**Status:** one pick per unit type is ratified and shipped (below); every other
cell here remains an unratified candidate, kept as provenance.

## Ratified

- **infantry** = troop truck (`infantry-sheet-1/inf-truck-2`), ratified 2026-11-09;
  shipped as `frontend/src/world/columns/infantry_{base,mask}.png`.
- **armor** = armoured-cab tank transporter carrying the shipped tank, compact
  trailer, 120 px footprint (`armor-transporter-1/arm-tt-c-w120`), ratified
  2026-11-09; shipped as `frontend/src/world/columns/armor_{base,mask}.png`.
- **artillery** = tracked tractor towing the field gun
  (`artillery-sheet-1/art-tractor-1`), ratified 2026-11-09; shipped as
  `frontend/src/world/columns/artillery_{base,mask}.png`.

- `prompts/` - the exact prompt text sent to `codex exec` for each sheet.
- `raw/` - the untouched 1254x1254 RGB sheets Codex returned (4x3 grid, magenta bg).
- `<sheet>/src/<name>.png` - hi-res RGBA crops, one per cell (the unit.py sources).
- `<sheet>/<name>_base.png`, `<name>_mask.png` - 128x128 two-layer pairs built by
  `scripts/tile-foundry/unit.py`'s own `build_unit` (same classifier, rim and
  gates as the shipped deployed sprites). `<sheet>/build.json` has each
  candidate's bbox and mask coverage, or the gate it failed.
- `review/` - full-size, tint-simulation, board-scale and stack review images.
- `manifest.json` - prompt -> raw sheet, Codex thread id and source path, wall
  time, cell names, gate failures, first-pass picks.

Sheets: `infantry-sheet-1`, `artillery-sheet-1`, `armor-sheet-1` (one idea family
per row), `mixed-sheet-2` (a chunkier, more stylised take on all three; drifts
toward cartoon), `armor-sheet-3` (fixes armor-sheet-1's facing problem: a reversed
turret reads as a tank facing east at board scale, so it adds tank riders, a big
tank on a transporter, and stronger front cues).

`armor-edit-1/` (added after user feedback: the fresh-generation tanks looked
like "a different tank all told"). These are image **edits** of the shipped
deployed tank, not new generations. The reference `armor-edit-1/ref.png` is the
ratified hi-res cobalt source (`art/foundry/units/gens/extracted/v3r1-tank-r2c0.png`)
on magenta, attached with `codex exec -i`. Each edit keeps the tank and adds an
open turret hatch with the commander's head out and two riders on the rear deck.
There are 4 separate calls (`prompts/armor-edit-1-{a,b,c,d}.txt`; c and d vary
the riders' poses). The edits keep the source's east facing and build with
`mirror=true` like the shipped armor. `register_edit.py` sets each edit's
target width so the tracks land at the shipped scale. `review_edit.py` renders a
side-by-side view, board scale, a silhouette overlay / |diff| against the deployed
base, and the tint check. Hull match numbers are in
`review/armor-edit-1-metrics.json`.

`armor-transporter-1/` (user direction, 2026-11-09): the **exact shipped tank on a
WW2 tank transporter**. These are image edits with the same `-i` approach as
`armor-edit-1`: hatches closed, no riders (variant d has the commander's head
out). There are 4 calls: a and b have a Diamond T 980-style bonneted tractor, c and d a
Dragon Wagon-style armoured cab; a and c have a compact trailer, b and d a longer one with a
ramp. Each rig is built at two footprints: `-w90` (whole rig 90 px, like every
unit) and `-w120` (whole rig 120 px of the 128 canvas). The `-w120` sources are
symlinks to the `-w90` crops. `match_tank.py` finds the tank inside each rig
(scale k, IoU against the shipped source) and records how wide the tank itself
ends up. `review_transporter.py` renders the review images.

Names: `inf-truck` canvas-covered troop truck, `inf-march` marching file,
`inf-open` open truck / half-track with riders; `art-truck` / `art-tractor` /
`art-halftrack` towing a field gun; `arm-reversed` turret to the rear in travel
lock, `arm-transporter` on a tank transporter, `arm-tarp` tarped with the gun
raised, `arm-riders` gun forward in a travel clamp with infantry riding on the deck.

Tools:
- `run_codex.sh <sheet> [ref.png ...]` - one Codex call (references attached with `-i`) (`docs/ART_PIPELINE.md`, "Codex recipe").
- `cut_sheet.py` - keys magenta (reusing `../sites/cut_sheet.py`'s keyer), splits
  at the widest gaps, drops keying specks, and stores the despilled colour
  re-composited over the sampled background so `unit.py`'s `load_crop` un-matte
  recovers it exactly (raw sheet RGB left magenta spill that tripped the
  cobalt-residue gate).
- `build_pairs.py` - builds the pairs; vehicles at 90 px solid-bbox width, marching
  files at 81 px (as deployed infantry), `mirror=false` (Codex drew them facing west).
- `register_edit.py` - for edits of a shipped sprite: writes `<dir>/targets.json`
  (per-edit `target_bbox_w` + `mirror`) so the edit lands at the shipped scale;
  `build_pairs.py` applies it as per-candidate overrides.
- `match_tank.py` - registers the shipped tank inside a transporter edit, writes
  `match.json` and the two-footprint `targets.json`.
- `review_transporter.py` - the transporter review (side by side, board scale at
  both footprints and a 1.35x canvas, tint, tank match).
- `review_edit.py` - the edit review (side by side, board, overlay/diff, tint, metrics).
- `review_renders.py` - the review images. Board scale matches `WorldRenderer`:
  tile 2*HEX_SIZE = 64 px, unit canvas 1.5*HEX_SIZE = 48 px.

Five candidates fail unit.py's cobalt-residue gate by one dark blue-grey pixel
each (tyre or track shadow). They are listed in `manifest.json` and left out of
the review. `unit.py` itself is unchanged.
