# Concept art

## `ui-concept-a.png` — ratified UI chrome direction (2026-07-07)

Concept for the **UI chrome** — panels, buttons, type, decoration — in the
ratified "Field HQ" register (dark wood, brass fittings, parchment, canvas).
The layout follows `docs/UI-SPEC.md`; the materials and component treatments
are specified in [`../UI-STYLE.md`](../UI-STYLE.md). Like `concept1.png`, it
directs *feel*, not pixels — and its warm/sepia cast over the **map** is
explicitly not ratified (chrome only; the board keeps the naturalistic
tileset). Prompt provenance: `art/foundry/ui/ui-concept-a.prompt.txt`.

## What `concept1.png` is

`concept1.png` is concept art for the **map, hex terrain art, and general
look/feel** of the game. It captures the *feel* of the experience: the mood,
palette, and overall visual register we're aiming for.

## What this is NOT

- **Not a pixel-for-pixel reference image.** Don't treat it as art to reproduce
  exactly. It sets a target feel, not a spec.
- **Not a unit look/feel reference.** The units shown are illustrative only; this
  art does not direct how our units should look.
- **Not a specific UI direction.** The HUD, panels, and chrome in the image are
  not a layout or component spec for our UI.

It exists to answer "what should this *feel* like?" — for terrain/hex art and the
overall board mood — not "what should this *be*, exactly."

## What it tells us for the tileset (see `docs/ROADMAP.md` Phase 8)

- **Plains** read as calm, open ground that recedes and doesn't fight the units
  for attention — but note *how* they're calm: the grass is a **saturated, warm
  mid-green** (healthy daylight grass), made restful by **smoothness and lack of
  busy detail**, not by being pale. "Calm" here is low *internal detail*, not low
  *saturation*. (A pale/desaturated "washed-out" plains was tried and rejected —
  it looks faded, and it makes every other terrain look muddy by comparison.)
- **Forests** are dense, dark canopy masses with no internal features (no
  clearings) — consistent with our "variants are mild texture variety, not
  distinct features" discipline.
- **Hills** are muted rocky ridges with gentle shading, sparse against the grass.
- **Rivers** wind across hex edges with bends and **forks/confluences**, bridged
  where roads cross — i.e. edge-connected river features (ROADMAP Slice T3), not
  standalone "river hexes".
- Overall: a muted, naturalistic palette where quiet terrain lets the units and
  overlays pop.

## What the tileset now does (Codex paint-in, ratified 2026-11-09)

The shipped terrain tiles were redone from this concept with Codex paint-in
(`art/codex/terrain/`, recipe in `docs/ART_PIPELINE.md`). Measured direction is in
`art/codex/terrain/direction/concept-read.md`. Where it departs from earlier rulings:

- **Hills are tan peaks**: olive slopes, warm umber east faces, ochre-tan rock along
  the crest. Ratified by the user on 2026-11-09, superseding the June ruling that
  hills be "green and grassy, not rocky". The green-ridge alternative is kept in
  `art/codex/terrain/candidates/review1-hills-green/` but is not shipped.
- **City is a street grid**: a rectilinear grid of closed blocks, filled close to the
  hex edge so adjacent city hexes read as one footprint (with join sprites over the
  seams). **Town is a loose village**: an organic cluster that dissolves into
  gardens and single trees. Both replace the earlier six-spoke radial plan.
- **Woods and city hexes merge** with their same-terrain neighbours through join
  sprites, so a forest or city reads as one mass, not a grid of stamps.
- **Palette anchors** (concept medians the tiles were measured against): grass RGB
  (104,105,47), L 98; forest canopy (52,61,29); hill rock (162,141,100) with umber
  shade about (55,50,28); water (67,81,77); bridge deck (125,111,79); road
  (161,140,96); town whole-hex (100,94,49); city whole-hex about (84,72,52), L 70-77.
