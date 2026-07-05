// Painterly unit sprites (Phase 8 unit art). Each unit type ships as a
// two-layer pair produced by scripts/tile-foundry/unit.py: a neutral `base`
// (team areas desaturated to grey, 1px dark rim baked) and a `mask` holding
// only the team-area pixels as a white-to-grey luminance layer. The renderer
// draws base + mask with `tint = playerColor(owner)`, so any number of player
// colours works off the same two files — never bake a player colour into art.
//
// Assets are 128×128 RGBA, sprite centred, facing west, drawn at HEX_SIZE*2
// world px like the 128px painterly terrain tiles (crisp at MAX_ZOOM).

import { Assets, type Texture } from "pixi.js";

import type { components } from "@/lib/api/schema";

type UnitType = components["schemas"]["UnitType"];

const UNIT_TYPES: readonly UnitType[] = ["infantry", "armor", "artillery"];

// Eagerly resolve every unit PNG's URL at build time, like tiles.ts does for
// terrain. Keys look like "./units/armor_base.png".
const UNIT_MODULES = import.meta.glob<string>("./units/*.png", {
  eager: true,
  query: "?url",
  import: "default",
});

/** The two-layer sprite pair for one unit type. */
export interface UnitSpritePair {
  readonly base: Texture;
  readonly mask: Texture;
}

export type LoadedUnits = Record<UnitType, UnitSpritePair>;

/**
 * Load the six unit-layer textures. The full set is required: a missing or
 * unloadable layer rejects (the board keeps the vector-marker fallback and
 * logs, mirroring the flat-colour terrain fallback) rather than rendering a
 * mixed sprite/vector army.
 */
export async function loadUnitTextures(): Promise<LoadedUnits> {
  const loaded: Partial<Record<UnitType, UnitSpritePair>> = {};
  await Promise.all(
    UNIT_TYPES.map(async (type) => {
      const baseUrl = UNIT_MODULES[`./units/${type}_base.png`];
      const maskUrl = UNIT_MODULES[`./units/${type}_mask.png`];
      if (baseUrl === undefined || maskUrl === undefined) {
        throw new Error(`missing unit sprite layers for ${type}`);
      }
      const [base, mask] = await Promise.all([
        Assets.load<Texture>(baseUrl),
        Assets.load<Texture>(maskUrl),
      ]);
      base.source.scaleMode = "linear";
      mask.source.scaleMode = "linear";
      loaded[type] = { base, mask };
    }),
  );
  return loaded as LoadedUnits;
}
