// The three UI font roles (docs/UI-STYLE.md §3). Pixi rasterizes whatever is
// loaded at draw time, so the app waits for `loadUiFonts()` before rendering
// (main.tsx) — otherwise panel text would bake in the system fallback.

/** Display role — panel titles, LOCK ORDERS, banners. */
export const FONT_DISPLAY = "Allerta Stencil";

/** Label role — buttons, tabs, header status chips. */
export const FONT_LABEL = "Archivo Narrow";

/**
 * Data role — roster rows, coords, HP, stats, event log.
 *
 * Deliberately NOT fixed-advance (typewriter register, ratified 2026-07-07):
 * align data columns by layout (one Text per column at a fixed x), never by
 * space-padding a single string.
 */
export const FONT_DATA = "Special Elite";

/**
 * Resolve when the three UI faces are ready. A failed face logs and falls
 * back to the system font — same contract as the terrain-texture loader
 * (degraded visuals, never a blank app).
 */
export async function loadUiFonts(): Promise<void> {
  const probes = [
    `400 16px "${FONT_DISPLAY}"`,
    `400 16px "${FONT_LABEL}"`,
    `700 16px "${FONT_LABEL}"`,
    `400 16px "${FONT_DATA}"`,
  ];
  const results = await Promise.allSettled(
    probes.map((probe) => document.fonts.load(probe)),
  );
  results.forEach((result, i) => {
    if (result.status === "rejected") {
      console.error(`UI font failed to load: ${probes[i]}`, result.reason);
    } else if (result.value.length === 0) {
      console.error(`UI font not found (falling back to system): ${probes[i]}`);
    }
  });
}
