import type { Texture } from "pixi.js";
import { describe, expect, it } from "vitest";

import type { Hex } from "./hex";
import { hexTileRef } from "./hexTile";
import { variantIndex, type LoadedTiles } from "./tiles";

/** Stand-in textures: hexTileRef only picks one, it never reads it. */
const tex = (name: string) => ({ name }) as unknown as Texture;

const hex = (q: number, r: number, terrain: Hex["terrain"]): Hex => ({ coord: { q, r }, terrain, road: false });

const NO_COURSE = new Map<string, number[]>();
const NO_ROADS = new Map();
const NO_LAKES = new Map<string, number>();

describe("hexTileRef for a terrain without tile art", () => {
  const woods = [tex("woods/00"), tex("woods/01"), tex("woods/02")];

  it("returns null (draw the flat fill) when the terrain has no tiles loaded", () => {
    const tiles: LoadedTiles = { byTerrain: { woods }, byOriented: {} };
    expect(hexTileRef(hex(3, 4, "hills"), tiles, NO_COURSE, NO_ROADS, NO_LAKES)).toBeNull();
  });

  it("picks a terrain's tiles up as coord-hashed cosmetic variants once they exist, like woods", () => {
    const hills = [tex("hills/00"), tex("hills/01"), tex("hills/02")];
    const tiles: LoadedTiles = { byTerrain: { woods, hills }, byOriented: {} };
    for (const [q, r] of [
      [0, 0],
      [3, 4],
      [-7, 11],
    ]) {
      const i = variantIndex(q, r, 3);
      expect(hexTileRef(hex(q, r, "hills"), tiles, NO_COURSE, NO_ROADS, NO_LAKES)).toEqual({ tex: hills[i], mirror: false });
      expect(hexTileRef(hex(q, r, "woods"), tiles, NO_COURSE, NO_ROADS, NO_LAKES)).toEqual({ tex: woods[i], mirror: false });
    }
  });
});

describe("hexTileRef for a lake", () => {
  it("draws its mask's tile group, coord-hashed, never the cosmetic lake variants", () => {
    const m05 = [tex("lake/m05_00"), tex("lake/m05_01")];
    const tiles: LoadedTiles = {
      byTerrain: { lake: [...m05, tex("lake/m63_00")] },
      byOriented: { "lake/m05": m05, "lake/m63": [tex("lake/m63_00")] },
    };
    for (const [q, r] of [
      [0, 0],
      [3, 4],
      [-7, 11],
    ]) {
      const masks = new Map([[`${q},${r}`, 5]]);
      const ref = hexTileRef(hex(q, r, "lake"), tiles, NO_COURSE, NO_ROADS, masks);
      expect(ref).toEqual({ tex: m05[variantIndex(q, r, 2)], mirror: false });
    }
  });
});
