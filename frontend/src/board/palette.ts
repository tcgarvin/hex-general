// Flat-colour palette for the Phase 8 board (Slice 1). These stand in for the
// AI-generated sprite set that arrives in Slice 2 — terrain is a solid fill and
// units are tinted markers. Colours are 0xRRGGBB ints (what Pixi's fill/tint
// want), kept in one place so the sprite swap has a single source to replace.

import type { components } from "@/lib/api/schema";

type Terrain = components["schemas"]["Terrain"];
type UnitType = components["schemas"]["UnitType"];

/** Fill colour per terrain type. */
export const TERRAIN_FILL: Record<Terrain, number> = {
  plains: 0x6b8e4e,
  woods: 0x2f5d3a,
  hills: 0x8a7a52,
  river: 0x2c5a8c,
  bridge: 0x9c7b4a,
  town: 0xb0a48c,
  city: 0xd8d0bc,
};

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
