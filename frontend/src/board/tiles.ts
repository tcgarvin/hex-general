// AI-generated terrain tile textures (Phase 8 Slice 2+). Flat-top hex sprites
// generated with PixelLab's native `hex` tile mode at 32×32 — which is exactly our
// render geometry (radius `HEX_SIZE` = 16 → 32px point-to-point), so they blit 1:1
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

/**
 * Build `terrain -> [url, …]` from the glob, with each terrain's variants sorted
 * by filename so the index↔file mapping is stable across machines. Crashes (via
 * the empty-array access in {@link loadTerrainTextures}) if a terrain has no
 * tiles, which is the boundary we want to fail loud at.
 */
function collectVariantUrls(): Record<string, string[]> {
  const byTerrain: Record<string, { variant: string; url: string }[]> = {};
  for (const [key, url] of Object.entries(TILE_MODULES)) {
    const { terrain, variant } = parseTileKey(key);
    (byTerrain[terrain] ??= []).push({ variant, url });
  }
  const result: Record<string, string[]> = {};
  for (const [terrain, entries] of Object.entries(byTerrain)) {
    entries.sort((a, b) => a.variant.localeCompare(b.variant));
    result[terrain] = entries.map((e) => e.url);
  }
  return result;
}

/**
 * Load every terrain's variant textures, forcing nearest-neighbour scaling so the
 * pixel art stays crisp when the board zooms in. Resolves to a complete
 * terrain→textures map; a load failure rejects (the board keeps the flat-colour
 * fallback).
 */
export async function loadTerrainTextures(): Promise<Record<Terrain, Texture[]>> {
  const variantUrls = collectVariantUrls();
  const entries = await Promise.all(
    (Object.entries(variantUrls) as [Terrain, string[]][]).map(async ([terrain, urls]) => {
      const textures = await Promise.all(
        urls.map(async (url) => {
          const texture: Texture = await Assets.load(url);
          texture.source.scaleMode = "nearest";
          return texture;
        }),
      );
      return [terrain, textures] as const;
    }),
  );
  return Object.fromEntries(entries) as Record<Terrain, Texture[]>;
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
