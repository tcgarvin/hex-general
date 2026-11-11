// Flat-colour palette for the board. Terrain fills show before the tile textures
// load and are the world view's zoomed-out overview, so they
// approximate the tile art: zooming between overview and tiles should not jump
// palette. Colours are 0xRRGGBB ints (what Pixi's fill/tint want).

import type { components } from "@/lib/api/schema";

type Terrain = components["schemas"]["Terrain"];

/**
 * Fill colour per terrain type. Plains is the alpha-weighted mean of its Codex
 * paint-in tiles (ratified 2026-11-09); every other terrain uses the art's
 * feature colour (canopy, rock, roofs, brick, water, deck), not the tile mean,
 * which is mostly grass and would make them indistinguishable from plains on
 * the strategy overview.
 */
export const TERRAIN_FILL: Record<Terrain, number> = {
  plains: 0x68692e,
  woods: 0x3a4420,
  hills: 0x9c8a5e,
  river: 0x41504c,
  bridge: 0x877353,
  lake: 0x41504c,
  town: 0x8f7150,
  city: 0x5a4636,
};

/** The painted roads' median colour (vector road fallback, overview road dots). */
export const ROAD_COLOR = 0xa78e62;

/** Per-player tint for units and settlement ownership. */
export const PLAYER_COLOR: Record<string, number> = {
  blue: 0x4f8cff,
  red: 0xff5d5d,
};

/** Fallback tint for any owner id not in {@link PLAYER_COLOR}. */
export const UNKNOWN_OWNER_COLOR = 0xcccccc;

export function playerColor(owner: string): number {
  return PLAYER_COLOR[owner] ?? UNKNOWN_OWNER_COLOR;
}
