// Flat-colour palette for the board. Terrain fills show before the tile textures
// load (2-player board) and are the world view's zoomed-out overview, so they
// approximate the tile art: zooming between overview and tiles should not jump
// palette. Colours are 0xRRGGBB ints (what Pixi's fill/tint want).

import type { components } from "@/lib/api/schema";

type Terrain = components["schemas"]["Terrain"];
type UnitType = components["schemas"]["UnitType"];

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

/** Hex outline colour (between tiles). */
export const HEX_STROKE = 0x1b1b1f;

/** Per-player tint for units and settlement ownership. */
export const PLAYER_COLOR: Record<string, number> = {
  blue: 0x4f8cff,
  red: 0xff5d5d,
};

/** Colour for a neutral (unowned) settlement marker. */
export const NEUTRAL_COLOR = 0x9aa0a6;

/** Fallback tint for any owner id not in {@link PLAYER_COLOR}. */
export const UNKNOWN_OWNER_COLOR = 0xcccccc;

export function playerColor(owner: string): number {
  return PLAYER_COLOR[owner] ?? UNKNOWN_OWNER_COLOR;
}

/** Marker glyph per unit type, drawn over the tinted base in Slice 1. */
export const UNIT_GLYPH: Record<UnitType, string> = {
  infantry: "I",
  armor: "A",
  artillery: "R",
};
