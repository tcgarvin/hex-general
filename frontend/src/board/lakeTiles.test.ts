import type { Texture } from "pixi.js";
import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import fixture from "./__fixtures__/lakes.json";
import { AXIAL_DIRECTIONS, HEX_SIZE, hexKey, type Axial } from "./hex";
import { hexTileRef } from "./hexTile";
import { groupJoinFiles, isMouthJoin, JOIN_TERRAINS, joinPlacements, joinVariant } from "./joins";
import { LAKE_MASK_COUNT, lakeMask, lakeMasks, lakeTileKey } from "./lakeTiles";
import { riverMouths } from "./riverTiles";
import { groupTileFiles, type LoadedTiles } from "./tiles";

type Terrain = components["schemas"]["Terrain"];

/** `frontend/src/board/__fixtures__/lakes.json`, written by `scripts/lake-fixture.py`. */
interface LakeFixtureMap {
  name: string;
  hexes: [number, number, string][];
  lakes: { q: number; r: number; mask: number; key: string; file: string }[];
  mouths: { q: number; r: number; direction: number; code: number; file: string }[];
}

const MAPS = fixture.maps as LakeFixtureMap[];

// The shipped art, by glob key only (nothing is loaded).
const TILE_KEYS = Object.keys(import.meta.glob("./tiles/**/*.png"));
const JOIN_KEYS = Object.keys(import.meta.glob("./joins/**/*.png"));

/** Stand-in textures named by their file under `tiles/`: hexTileRef only picks one. */
const SHIPPED_TILES: LoadedTiles = groupTileFiles(
  TILE_KEYS.map((key) => [key, { name: key.replace("./tiles/", "") } as unknown as Texture] as const),
);
/** Join groups whose values are the file under `joins/`. */
const SHIPPED_JOINS = groupJoinFiles(JOIN_KEYS.map((key) => [key, key.replace("./joins/", "")] as const));

function probe(cells: Record<string, Terrain>): (q: number, r: number) => Terrain | undefined {
  return (q, r) => cells[hexKey(q, r)];
}

const at = (direction: number): string => {
  const d = AXIAL_DIRECTIONS[direction];
  return hexKey(d.q, d.r);
};

describe("lakeMask / lakeTileKey (hand cases mirrored from scripts/tests/test_board_mock.py)", () => {
  it("sets a bit per lake neighbour, in AXIAL_DIRECTIONS order", () => {
    const cells = { "0,0": "lake", [at(0)]: "lake", [at(2)]: "lake", [at(4)]: "plains" } as Record<string, Terrain>;
    expect(lakeMask({ q: 0, r: 0 }, probe(cells))).toBe(0b000101);
  });

  it("counts off-map neighbours as land", () => {
    expect(lakeMask({ q: 0, r: 0 }, () => undefined)).toBe(0);
    expect(lakeMask({ q: 0, r: 0 }, () => "lake")).toBe(63);
  });

  it("names the mask's tile group with two digits", () => {
    expect(lakeTileKey(7)).toBe("lake/m07");
    expect(lakeTileKey(63)).toBe("lake/m63");
    expect(() => lakeTileKey(64)).toThrow(/0\.\.63/);
    expect(() => lakeTileKey(-1)).toThrow();
  });

  it("collects masks for lake hexes only", () => {
    const cells = { "0,0": "lake", [at(5)]: "lake", [at(1)]: "woods" } as Record<string, Terrain>;
    const coords: Axial[] = [{ q: 0, r: 0 }, AXIAL_DIRECTIONS[5], AXIAL_DIRECTIONS[1]];
    expect(lakeMasks(coords, probe(cells))).toEqual(
      new Map([
        ["0,0", 1 << 5],
        [at(5), 1 << 2],
      ]),
    );
  });
});

describe("shipped lake art", () => {
  it("has a tile for every one of the 64 masks", () => {
    const missing = [...Array(LAKE_MASK_COUNT).keys()].filter((m) => SHIPPED_TILES.byOriented[lakeTileKey(m)] === undefined);
    expect(missing).toEqual([]);
  });

  it("has all six river-mouth kinds and no lake edge or vertex joins", () => {
    const lake = SHIPPED_JOINS.get("lake")!;
    expect(lake.slice(0, 5).every((files) => files.length === 0)).toBe(true);
    expect(lake.slice(5).every((files) => files.length > 0)).toBe(true);
    expect(JOIN_TERRAINS.has("lake")).toBe(false);
  });
});

describe.each(MAPS.map((m) => [m.name, m] as const))("board-mock agreement on %s (__fixtures__/lakes.json)", (_, map) => {
  const cells: Record<string, Terrain> = {};
  for (const [q, r, terrain] of map.hexes) cells[hexKey(q, r)] = terrain as Terrain;
  const terrainAt = probe(cells);
  const coords: Axial[] = map.hexes.map(([q, r]) => ({ q, r }));
  const masks = lakeMasks(coords, terrainAt);

  it("computes the same mask and tile key for every lake hex", () => {
    expect([...masks.keys()].sort()).toEqual(map.lakes.map((l) => hexKey(l.q, l.r)).sort());
    for (const lake of map.lakes) {
      const mask = masks.get(hexKey(lake.q, lake.r))!;
      expect(mask).toBe(lake.mask);
      expect(lakeTileKey(mask)).toBe(lake.key);
    }
  });

  it("draws the same tile file for every lake hex", () => {
    for (const lake of map.lakes) {
      const hex = { coord: { q: lake.q, r: lake.r }, terrain: "lake" as const, road: false };
      const ref = hexTileRef(hex, SHIPPED_TILES, new Map(), new Map(), masks);
      expect(ref).not.toBeNull();
      expect({ name: (ref!.tex as unknown as { name: string }).name, mirror: ref!.mirror }).toEqual({
        name: lake.file,
        mirror: false,
      });
    }
  });

  it("draws the same mouth sprites, in the same order", () => {
    const placements = joinPlacements(coords, terrainAt, JOIN_TERRAINS, HEX_SIZE, riverMouths(coords, terrainAt));
    const mouths = placements.filter(isMouthJoin).map((p) => ({
      q: p.owner.q,
      r: p.owner.r,
      direction: p.code - 5,
      code: p.code,
      file: joinVariant(SHIPPED_JOINS, p),
    }));
    expect(mouths).toEqual(map.mouths);
  });
});

describe("hexTileRef for lakes", () => {
  const lakeHex = { coord: { q: 2, r: 3 }, terrain: "lake" as const, road: false };

  it("falls back to the flat fill (null) for a mask with no tile", () => {
    const tiles: LoadedTiles = { byTerrain: {}, byOriented: {} };
    expect(hexTileRef(lakeHex, tiles, new Map(), new Map(), new Map([["2,3", 9]]))).toBeNull();
  });

  it("throws for a lake hex the caller computed no mask for", () => {
    expect(() => hexTileRef(lakeHex, SHIPPED_TILES, new Map(), new Map(), new Map())).toThrow(/no lake mask/);
  });
});
