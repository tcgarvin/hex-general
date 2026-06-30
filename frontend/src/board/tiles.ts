// AI-generated terrain tile textures (Phase 8 Slice 2+). Flat-top hex sprites
// generated with PixelLab's native `hex` tile mode at 64×64 — which is exactly our
// render geometry (radius `HEX_SIZE` = 32 → 64px point-to-point), so they blit 1:1
// with no resampling. These replace the solid `TERRAIN_FILL` colours in
// `palette.ts`, which now serve only as the pre-load fallback.
//
// Each terrain may have several interchangeable variants to break up the visible
// repetition of a tiled grid. They live in `tiles/{terrain}/{nn}.png` and are
// discovered by glob, so adding a variant is a drop-in (no edits here). A hex
// picks its variant deterministically from its axial coord (see `variantIndex`),
// so the choice is stable across re-renders and replays — purely cosmetic.

import { Assets, type Texture } from "pixi.js";

import type { components } from "@/lib/api/schema";

type Terrain = components["schemas"]["Terrain"];

// Eagerly resolve every tile PNG's URL at build time. Keys look like
// "./tiles/plains/00.png"; values are the hashed asset URLs Vite emits.
const TILE_MODULES = import.meta.glob<string>("./tiles/**/*.png", {
  eager: true,
  query: "?url",
  import: "default",
});

/** Parse a glob key into its `(terrain, variant-filename)` pair. */
function parseTileKey(key: string): { terrain: string; variant: string } {
  // "./tiles/<terrain>/<variant>.png"
  const parts = key.split("/");
  return { terrain: parts[2], variant: parts[3] };
}

/** The drop "<variant>.png" → "<variant>" basename used as the by-name key. */
function basename(variant: string): string {
  return variant.replace(/\.png$/, "");
}

export interface LoadedTiles {
  /**
   * `terrain → [texture, …]` sorted by filename, indexed by `variantIndex`. Used
   * by the cosmetic-variant terrains (plains/woods/hills/town/city). River/bridge
   * tiles also appear here but are selected by name instead (see {@link byName}).
   */
  readonly byTerrain: Record<Terrain, Texture[]>;
  /**
   * `"<terrain>/<basename>" → texture` (e.g. `"river/straight_v"`). River/bridge
   * hexes look up their oriented tile by name from the edge mask; see
   * `riverTiles.ts`.
   */
  readonly byName: Record<string, Texture>;
}

/**
 * Load every tile PNG, forcing nearest-neighbour scaling so the pixel art stays
 * crisp when the board zooms in. Returns both a terrain→variants map (for the
 * coord-hash variant terrains) and a name-keyed map (for mask-driven river/bridge
 * selection). A load failure rejects (the board keeps the flat-colour fallback).
 */
export async function loadTerrainTextures(): Promise<LoadedTiles> {
  // Sort by glob key so each terrain's variants land in a stable filename order,
  // keeping the `variantIndex` index↔file mapping identical across machines.
  const entries = Object.entries(TILE_MODULES).sort(([a], [b]) => a.localeCompare(b));
  const byName: Record<string, Texture> = {};
  await Promise.all(
    entries.map(async ([key, url]) => {
      const { terrain, variant } = parseTileKey(key);
      const texture: Texture = await Assets.load(url);
      texture.source.scaleMode = "nearest";
      byName[`${terrain}/${basename(variant)}`] = texture;
    }),
  );
  const byTerrain: Record<string, Texture[]> = {};
  for (const [key] of entries) {
    const { terrain, variant } = parseTileKey(key);
    (byTerrain[terrain] ??= []).push(byName[`${terrain}/${basename(variant)}`]);
  }
  return { byTerrain: byTerrain as Record<Terrain, Texture[]>, byName };
}

/**
 * Pick a variant index in `[0, count)` for the hex at `(q, r)`, deterministically.
 * A tiny integer hash spreads adjacent hexes across variants without storing
 * anything; the same coord always yields the same variant, so the board doesn't
 * shimmer on re-render and replays stay visually identical.
 */
export function variantIndex(q: number, r: number, count: number): number {
  if (count <= 1) return 0;
  // A cheap, well-mixing hash of two signed ints (no external deps). Constants are
  // the usual large odd primes; the xorshift breaks up linear coordinate streaks.
  let h = (q | 0) * 0x1f1f1f1f + (r | 0) * 0x85ebca6b;
  h ^= h >>> 15;
  h = Math.imul(h, 0x2c1b3c6d);
  h ^= h >>> 12;
  return Math.abs(h) % count;
}
