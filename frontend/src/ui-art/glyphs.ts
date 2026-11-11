// The UI glyph set (ratified 2026-11-11; provenance in art/codex/ui/APPLIED.md):
// single-colour 48 px masks, white with the ink in alpha, built by
// art/codex/ui/apply_ui_art.py. `components/Glyph.tsx` draws them as CSS masks
// filled with currentColor, so one file serves ivory on wood, ink on parchment and
// any state colour. Names are the game's own keys where one exists (resources,
// buildings, unit types, site kinds).

export const GLYPH_NAMES = [
  "manpower",
  "fuel",
  "steel",
  "barracks",
  "depot",
  "workshop",
  "infantry",
  "armor",
  "artillery",
  "army",
  "turn",
  "column",
  "garrison",
  "base",
  "city",
  "farm",
  "oil_field",
  "steel_mill",
  "factory",
  "travel",
  "deploy",
  "disband",
] as const;

export type GlyphName = (typeof GLYPH_NAMES)[number];

const GLYPH_MODULES = import.meta.glob<string>("./glyphs/*.png", {
  eager: true,
  query: "?url",
  import: "default",
});

/** The glyph file's URL (a data: URI once built: each is about 1 KB). Throws if the art is missing. */
export function glyphUrl(name: GlyphName): string {
  const url = GLYPH_MODULES[`./glyphs/${name}.png`];
  if (url === undefined) throw new Error(`missing glyph ${name}`);
  return url;
}

/** The file names present, for the sync test. */
export const GLYPH_FILES: readonly string[] = Object.keys(GLYPH_MODULES)
  .map((path) => path.slice("./glyphs/".length, -".png".length))
  .sort();
