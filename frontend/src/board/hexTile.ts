// Which tile texture the world board draws a hex with.

import type { Texture } from "pixi.js";

import { hexKey, type Hex } from "./hex";
import { lakeTileKey } from "./lakeTiles";
import { orientedRiverTile } from "./riverTiles";
import type { RoadTileRef } from "./roadTiles";
import { variantIndex, type LoadedTiles } from "./tiles";
import type { VarietyPick } from "./varietyTiles";

/** A hex's terrain tile: the texture plus the oriented-art mirror flag. */
export interface HexTileRef {
  readonly tex: Texture;
  readonly mirror: boolean;
}

/**
 * The texture + mirror the board draws for a hex, in branch priority order:
 * lake art by neighbour mask (`lakeMasks`, from `lakeTiles.ts`), river/bridge
 * oriented art (chosen by edge mask so the water/deck connects across shared
 * edges, possibly L↔R mirrored, unhandled shapes falling back to the generic
 * "00" tile), baked road-on-terrain art (T5 — a missing texture falls through to
 * the base tile + vector road overlay), then the coord-hashed cosmetic variant
 * of the base terrain. A plains or plains-road hex in `variety` (`varietyPicks`)
 * draws its variety tile instead of the cosmetic or baked-road variant; a
 * roadside variety tile keeps the road shape's mirror flag.
 *
 * `null` when the hex has no tile art loaded: a terrain with no `tiles/<terrain>/`
 * files, or a lake whose mask has no `lake/m<MM>_*` tile (the shipped set covers
 * all 64 masks, pinned by `lakeTiles.test.ts`). The caller draws the hex as its
 * flat `TERRAIN_FILL` colour with no sprite. A lake hex missing from `lakeMasks`
 * is a caller bug and throws.
 */
export function hexTileRef(
  h: Hex,
  terrainTex: LoadedTiles,
  riverCourse: Map<string, number[]>,
  roadTiles: Map<string, RoadTileRef>,
  lakeMasks: ReadonlyMap<string, number>,
  variety: ReadonlyMap<string, VarietyPick> = new Map(),
): HexTileRef | null {
  if (h.terrain === "lake") {
    const key = hexKey(h.coord.q, h.coord.r);
    const mask = lakeMasks.get(key);
    if (mask === undefined) throw new Error(`lake hex ${key} has no lake mask`);
    const variants = terrainTex.byOriented[lakeTileKey(mask)];
    if (variants === undefined) return null;
    return { tex: variants[variantIndex(h.coord.q, h.coord.r, variants.length)], mirror: false };
  }
  if (h.terrain === "river" || h.terrain === "bridge") {
    const oriented = orientedRiverTile(
      riverCourse.get(hexKey(h.coord.q, h.coord.r)) ?? [],
    );
    const name = oriented ? `${h.terrain}/${oriented.name}` : `${h.terrain}/00`;
    const variants =
      terrainTex.byOriented[name] ?? terrainTex.byOriented[`${h.terrain}/00`];
    const tex = variants[variantIndex(h.coord.q, h.coord.r, variants.length)];
    return { tex, mirror: oriented?.mirror ?? false };
  }
  const roadRef = h.road ? roadTiles.get(hexKey(h.coord.q, h.coord.r)) : undefined;
  const roadVariants =
    roadRef === undefined ? undefined : terrainTex.byOriented[roadRef.lookup];
  const pick = variety.get(hexKey(h.coord.q, h.coord.r));
  if (roadRef !== undefined && roadVariants !== undefined) {
    if (pick !== undefined) return { tex: varietyTexture(terrainTex, pick), mirror: roadRef.mirror };
    const tex = roadVariants[variantIndex(h.coord.q, h.coord.r, roadVariants.length)];
    return { tex, mirror: roadRef.mirror };
  }
  if (pick !== undefined) {
    if (pick.kind === "road") throw new Error(`roadside variety ${pick.key} on a hex with no baked road tile`);
    return { tex: varietyTexture(terrainTex, pick), mirror: false };
  }
  const variants = terrainTex.byTerrain[h.terrain];
  if (variants === undefined) return null;
  return {
    tex: variants[variantIndex(h.coord.q, h.coord.r, variants.length)],
    mirror: false,
  };
}

/** The loaded texture of a variety pick; a pick naming an unloaded file is a bug. */
function varietyTexture(terrainTex: LoadedTiles, pick: VarietyPick): Texture {
  const tex = terrainTex.byOriented[pick.key]?.[0];
  if (tex === undefined) throw new Error(`variety tile ${pick.key} is not loaded`);
  return tex;
}
