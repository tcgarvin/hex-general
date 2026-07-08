// Field-HQ chrome sprites (docs/UI-STYLE.md §4) for the Pixi panels: the
// blank-face button states, the portrait inset well, the brass nameplate, and
// the canvas fill. Produced by scripts/ui-kit-prep.py from the ratified kit
// cuts — regenerate there, never hand-edit the PNGs.

import { Assets, type Texture } from "pixi.js";

// Eagerly resolve every chrome PNG's URL at build time, like units.ts does.
// Keys look like "./chrome/btn-up.png".
const CHROME_MODULES = import.meta.glob<string>("./chrome/*.png", {
  eager: true,
  query: "?url",
  import: "default",
});

/** Interaction states with dedicated button art (UI-STYLE §4). */
export const BUTTON_STATES = ["up", "hover", "pressed", "disabled", "active"] as const;
export type ButtonState = (typeof BUTTON_STATES)[number];

export interface LoadedChrome {
  /** Order-button plate per interaction state (blank face; icon/label composited). */
  readonly btn: Record<ButtonState, Texture>;
  /** Brass-lipped inset well framing the unit portrait (NineSlice). */
  readonly well: Texture;
  /** Engraved brass nameplate strip behind panel/unit titles (NineSlice). */
  readonly plate: Texture;
  /** Tileable worn-canvas panel fill. */
  readonly canvas: Texture;
}

function url(name: string): string {
  const key = `./chrome/${name}.png`;
  const mod = CHROME_MODULES[key];
  if (mod === undefined) throw new Error(`chrome sprite missing from bundle: ${key}`);
  return mod;
}

/**
 * Load the chrome sprite set for the selected-unit dock pilot. Rejects if any
 * sprite fails: callers keep the flat placeholder chrome and log, mirroring
 * the terrain/unit texture loaders (degrade whole, never mixed).
 */
export async function loadChromeTextures(): Promise<LoadedChrome> {
  const [up, hover, pressed, disabled, active, well, plate, canvas] = await Promise.all([
    Assets.load<Texture>(url("btn-up")),
    Assets.load<Texture>(url("btn-hover")),
    Assets.load<Texture>(url("btn-pressed")),
    Assets.load<Texture>(url("btn-disabled")),
    Assets.load<Texture>(url("btn-active")),
    Assets.load<Texture>(url("well")),
    Assets.load<Texture>(url("plate")),
    Assets.load<Texture>(url("canvas")),
  ]);
  return { btn: { up, hover, pressed, disabled, active }, well, plate, canvas };
}
