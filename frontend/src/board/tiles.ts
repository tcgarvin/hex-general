// AI-generated terrain tile textures (Phase 8 Slice 2). One flat-top hex sprite
// per terrain, generated with PixelLab's native `hex` tile mode at 32×32 — which
// is exactly our render geometry (radius `HEX_SIZE` = 16 → 32px point-to-point),
// so they blit 1:1 with no resampling. These replace the solid `TERRAIN_FILL`
// colours in `palette.ts`, which now serve only as the pre-load fallback.

import { Assets, type Texture } from "pixi.js";

import type { components } from "@/lib/api/schema";

import bridgeUrl from "./tiles/bridge.png";
import cityUrl from "./tiles/city.png";
import hillsUrl from "./tiles/hills.png";
import plainsUrl from "./tiles/plains.png";
import riverUrl from "./tiles/river.png";
import townUrl from "./tiles/town.png";
import woodsUrl from "./tiles/woods.png";

type Terrain = components["schemas"]["Terrain"];

const TILE_URLS: Record<Terrain, string> = {
  plains: plainsUrl,
  woods: woodsUrl,
  hills: hillsUrl,
  river: riverUrl,
  bridge: bridgeUrl,
  town: townUrl,
  city: cityUrl,
};

/**
 * Load every terrain texture, forcing nearest-neighbour scaling so the pixel art
 * stays crisp when the board zooms in. Resolves to a complete terrain→texture map;
 * a load failure rejects (the board then keeps the flat-colour fallback).
 */
export async function loadTerrainTextures(): Promise<Record<Terrain, Texture>> {
  const entries = await Promise.all(
    (Object.entries(TILE_URLS) as [Terrain, string][]).map(async ([terrain, url]) => {
      const texture: Texture = await Assets.load(url);
      texture.source.scaleMode = "nearest";
      return [terrain, texture] as const;
    }),
  );
  return Object.fromEntries(entries) as Record<Terrain, Texture>;
}
