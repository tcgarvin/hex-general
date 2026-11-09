# Codex site-kind icon candidates (2026-11-09)

Candidate icons for the six world site kinds (`SiteKind` in
`api/app/engine/economy_rules.py`: base, city, farm, oil_field, steel_mill,
factory).

**Ratified 2026-11-09:** the painted register (A), sheet 3, variant 1 of each kind,
copied to `frontend/src/world/sites/<kind>.png` and drawn on the world board at
tactical zoom and in the site panel. Everything else here is unused candidates.

- `prompts/` - the exact prompt text sent to `codex exec` for each sheet.
- `raw/` - the untouched 1254x1254 RGB sheets Codex returned (4x3 grid).
- `A-painted/sheet-N/`, `B-staffmap/sheet-N/` - cut 128x128 RGBA icons,
  `<kind>-<variant>.png`, cells read row-major as base-1, base-2, city-1, ...
- `manifest.json` - which prompt produced which raw sheet, Codex thread id and
  source path, wall time, keying mode, and the cut directory.
- `cut_sheet.py` - keys the background (magenta for painted sheets, white for
  ink sheets), splits the sheet at its widest gaps, fits each icon to 120 px in
  a 128 px canvas.
- `review_renders.py` - renders the full-size, 32/24 px and on-tile review images.

How to drive Codex image generation: `docs/ART_PIPELINE.md`, "Codex recipe".
