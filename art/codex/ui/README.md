# Codex UI art candidates, round 1 (2026-11-10)

Unratified candidates for three pieces of UI art, made overnight with Codex's
built-in image generation for the owner to review. Nothing here is applied to
the game; nothing under `frontend/` was touched. Review pages (built with
`scripts/review-page.py`, notes added with `add_notes.py`):
`.dev/review-pages/ui-glyphs-r1.html`, `emblem-r1.html`, `keyart-r1.html`.

14 Codex calls in all (one smoke test, five glyph sheets, two emblem sheets,
six key art paintings), 27 to 77 s each. `manifest.json` records each call:
prompt, raw, Codex thread id and source path, wall time, references attached,
cells and the cut command. `picks.json` holds the first-pass picks.

## Layout

- `run_codex.sh <set> <name> [ref.png ...]` - one Codex call (the "Codex recipe"
  in `docs/ART_PIPELINE.md`): reads `<set>/prompts/<name>.txt`, attaches the
  references with `codex exec -i`, writes `<set>/raw/<name>.png` and
  `<set>/raw/<name>.run.json`.
- `uikit.py` - shared review helpers: the header wood (`wood.png` under the
  `.fhq-wood` 18 % ink wash), parchment, palette tokens, the three UI fonts read
  from the repo's TTFs, tinting a mask.
- `add_notes.py` - inserts per-section notes into a review page.
- `build_manifest.py` - writes `manifest.json` from the run records.
- `refs/concept-crest.png` - the crest cropped from `docs/concept-art/ui-concept-a.png`.
- `smoke/` - the smoke-test call (a helmet silhouette).

## Set 1: glyphs (`glyphs/`)

Single-colour tintable glyphs for resources, buildings, unit types and world
actions. Prompts attach `art/foundry/ui/kit-icons.png` as a style reference.
Sheets were asked for as black ink on pure white rather than the grey of
kit-icons, so the alpha keys cleanly.

- `raw/core-{A,B}.png` (4x3): manpower, fuel, steel, barracks, depot, workshop,
  infantry, armor, artillery, army (army size), turn, column.
- `raw/world-{A,B}.png` (5x2): garrison, base, city, farm, oilfield, steelmill,
  factory, travel, deploy, disband. world-B came back 1983x793, not square.
- `raw/fix-1.png` (4x3): re-rolls for glyphs that blurred at 16 to 20 px, named
  per cell as takes C and D.
- `cut_glyphs.py` - cuts a sheet to 256x256 RGBA masks in `cut/<name>-<take>.png`:
  RGB is white, alpha is ink coverage, so the UI can tint it. Longest side 224 px;
  glyphs heavier than 30 % canvas coverage shrink by the square root of the
  excess (not below 0.82), to even out optical size.
- `review_glyphs.py` - `review/`: every glyph at 16, 20, 24 and 32 px in ivory on
  the header wood and in ink on parchment (shown at 2x), the world header strip
  and roster rows mocked with real fonts and layout, and 96 px sheets.

Labels are `<name>-<take>` (e.g. `fuel-B`). First-pass picks are in `picks.json`
and on the review page. Failures at 16 to 20 px: garrison-A (reads as an igloo),
garrison-B, garrison-C, factory-B, steelmill-A, steelmill-D (reads as a
microscope), oilfield-C (reads as a traffic cone). infantry-B uses a
German-pattern helmet, so the picks use round helmets.

## Set 2: brand emblem (`emblem/`)

Crests for the header, sign-in page and favicon, after the concept's winged
tank crest but with no eagle, no national insignia and no lettering. Prompts
attach the concept and the crest crop.

- `raw/sheet-{1,2}.png` (3x2, magenta): 12 crests.
- `cut_emblems.py` - keys the magenta (the sites round's keyer) and writes
  `cut/<name>.png`, each crest centred on a transparent square at sheet
  resolution (about 450 px, no resampling).
- `review_emblems.py` - `review/`: the 48 px header with each crest at 36 px,
  64/32/16 px on wood and parchment with a 4x view of 16 px, a browser-tab
  favicon mock, and 256 px boards.

Kept and labelled (8): emblem-1 `s1-winghex`, emblem-2 `s2-wingshield`,
emblem-3 `s2-compactwing`, emblem-4 `s1-tankhex`, emblem-5 `s2-medallion`,
emblem-6 `s2-laurel`, emblem-7 `s2-gunsight`, emblem-8 `s1-hexshield`. Dropped:
`s1-baton` (the baton reads as a pen), `s1-pin` (a modern map-app pin),
`s1-dividers` (a diamond, not a hex), `s2-chevron` (reads as a rank badge).

## Set 3: sign-in key art (`keyart/`)

Six paintings at 1536x1024, the largest size Codex returned: `centre-{1,2,3}`
leave a calm centre for a centred card, `right-{1,2,3}` a calm right third.
Prompts attach the concept for the map style and materials.

- `mock_signin.py` - `review/mock-<name>.jpg`: the art scaled to cover
  1440x900, a dark vignette, and a parchment card (the ratified kit
  `art/foundry/ui/cut/sheet.png`) with emblem-1, "HEX-GENERAL" in Allerta
  Stencil, two inputs and a drawn brass button. The fonts are the TTFs in
  `frontend/src/theme/fonts/` (no woff2 conversion needed). `review/calm.json`
  measures the card area: detail relative to the whole frame, and luminance.

Labels: keyart-1-centre to keyart-3-centre are `centre-1` to `centre-3`,
keyart-4-right to keyart-6-right are `right-1` to `right-3`. Known weaknesses:
centre-1 and centre-2 got their calm centre as a dark stain on the map, right-2
has a hard vertical edge where its dark third begins, and all six are warm and
golden.
