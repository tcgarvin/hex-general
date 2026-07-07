// Field-HQ palette tokens (docs/UI-STYLE.md §2) — the single source for the
// Pixi chrome. Values are 0xRRGGBB ints (what Pixi fill/tint want), sampled
// from the ratified concept (docs/concept-art/ui-concept-a.png). Panels are
// migrated onto these tokens panel-by-panel during the pilot rollout; until a
// panel is migrated it keeps its local placeholder constants.

export const THEME = {
  // Materials
  wood900: 0x321f14, // darkest wood — header bar, panel outer rails
  wood700: 0x3d2d1b, // primary wood frame tone
  wood500: 0x665036, // wood highlight grain
  brass600: 0xa1793e, // brass base
  brass400: 0xd9b060, // brass highlight / lit edges
  parchment200: 0xe4cda2, // parchment face (light panel fill)
  parchment400: 0xaa9675, // parchment shadow / aged edge
  canvas800: 0x4c2e21, // leather/canvas dark panel fill
  canvas600: 0x7f5c37, // leather mid tone

  // Text
  ink900: 0x2b2118, // text on parchment (warm near-black ink)
  ivory100: 0xf0e6d2, // text on dark wood/canvas
  ivory400: 0xa89880, // secondary text on dark

  // Status
  warning: 0xf5b342, // amber
  invalid: 0xc03b2e, // red
  valid: 0x7fa65a, // green

  // Factions — unchanged from the board palette for consistency
  factionBlue: 0x4f8cff,
  factionRed: 0xff5d5d,
} as const;

export { FONT_DISPLAY, FONT_LABEL, FONT_DATA } from "./fonts";
