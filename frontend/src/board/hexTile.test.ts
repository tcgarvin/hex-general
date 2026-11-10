import type { Texture } from "pixi.js";
import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import { hexTileRef } from "./hexTile";
import { variantIndex, type LoadedTiles } from "./tiles";

type Hex = components["schemas"]["Hex"];

/** Stand-in textures: hexTileRef only picks one, it never reads it. */
const tex = (name: string) => ({ name }) as unknown as Texture;

const hex = (q: number, r: number, terrain: Hex["terrain"]): Hex => ({ coord: { q, r }, terrain, road: false });

const NO_COURSE = new Map<string, number[]>();
const NO_ROADS = new Map();

describe("hexTileRef for a terrain without tile art", () => {
  const woods = [tex("woods/00"), tex("woods/01"), tex("woods/02")];

  it("returns null (draw the flat fill) when the terrain has no tiles loaded", () => {
    const tiles: LoadedTiles = { byTerrain: { woods }, byOriented: {} };
    expect(hexTileRef(hex(3, 4, "lake"), tiles, NO_COURSE, NO_ROADS)).toBeNull();
  });

  it("picks lake tiles up as coord-hashed cosmetic variants once they exist, like woods", () => {
    const lake = [tex("lake/00"), tex("lake/01"), tex("lake/02")];
    const tiles: LoadedTiles = { byTerrain: { woods, lake }, byOriented: {} };
    for (const [q, r] of [
      [0, 0],
      [3, 4],
      [-7, 11],
    ]) {
      const i = variantIndex(q, r, 3);
      expect(hexTileRef(hex(q, r, "lake"), tiles, NO_COURSE, NO_ROADS)).toEqual({ tex: lake[i], mirror: false });
      expect(hexTileRef(hex(q, r, "woods"), tiles, NO_COURSE, NO_ROADS)).toEqual({ tex: woods[i], mirror: false });
    }
  });
});
