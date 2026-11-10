import { describe, expect, it } from "vitest";

import { hexKey } from "@/board/hex";
import { TERRAIN_FILL } from "@/board/palette";

import { baseTileKeys, siteTileKey } from "./siteTiles";
import { decodeWorldMap, offsetToAxial, type MapSite } from "./worldMap";
import { overviewHexFill, SITE_OVERVIEW_FILL, baseSiteTiles, baseStageOf, changedBaseTiles, hexTileChoice, staticSiteTiles } from "./worldTiles";

// A 4x3 world: plains everywhere except a town at (1,0) offset and a city at (3,2).
const FARM = offsetToAxial(0, 0);
const OLD_FARM = offsetToAxial(1, 0); // an old stored map: the farm stands on its town hex
const FACTORY = offsetToAxial(2, 0);
const BASE = offsetToAxial(0, 1);
const VACANT = offsetToAxial(2, 1);
const CITY = offsetToAxial(3, 2);
const PLAIN = offsetToAxial(1, 2);

const SITES: MapSite[] = [
  { coord: FARM, kind: "farm", level: 2, garrison: 1 },
  { coord: OLD_FARM, kind: "farm", level: 1, garrison: 1 },
  { coord: FACTORY, kind: "factory", level: 3, garrison: 2 },
  { coord: BASE, kind: "base", level: 1, garrison: 0 },
  { coord: VACANT, kind: "base", level: 1, garrison: 0 },
  { coord: CITY, kind: "city", level: 1, garrison: 3 },
];

const map = decodeWorldMap({ width: 4, height: 3, terrain: ".t.........c", roads: "............", sites: SITES });

// The founded base and every garrisoned site have settlements; the vacant slot has none.
const settlements = [
  { coord: FARM, controlled_by: null },
  { coord: OLD_FARM, controlled_by: null },
  { coord: FACTORY, controlled_by: "p1" },
  { coord: BASE, controlled_by: "p1" },
  { coord: CITY, controlled_by: "neutral" },
];

const staticTiles = staticSiteTiles(map.sites);
const baseTiles = baseSiteTiles(map, settlements);
const choice = (a: { q: number; r: number }) => hexTileChoice(a.q, a.r, staticTiles, baseTiles);

describe("world hex tile choice", () => {
  it("draws a leveled site's painted tile for its kind and level", () => {
    expect(choice(FARM)).toEqual({ kind: "site", key: siteTileKey("farm", 2, FARM.q, FARM.r) });
    expect(choice(FACTORY)).toEqual({ kind: "site", key: siteTileKey("factory", 3, FACTORY.q, FACTORY.r) });
  });

  it("draws the stage-1 base tile on a founded base", () => {
    expect(baseStageOf({ coord: BASE, controlled_by: "p1" })).toBe(1);
    expect(choice(BASE)).toEqual({ kind: "site", key: baseTileKeys(BASE, 1)[0].key });
    expect(baseTiles.size).toBe(1);
  });

  it("keeps the terrain tile on a vacant base slot, a city site and a plain hex", () => {
    expect(choice(VACANT)).toEqual({ kind: "terrain" });
    expect(choice(CITY)).toEqual({ kind: "terrain" });
    expect(choice(PLAIN)).toEqual({ kind: "terrain" });
  });

  it("paints an old stored map's farm over its town hex", () => {
    expect(map.hexAt(OLD_FARM.q, OLD_FARM.r)?.terrain).toBe("town");
    expect(choice(OLD_FARM)).toEqual({ kind: "site", key: siteTileKey("farm", 1, OLD_FARM.q, OLD_FARM.r) });
  });

  it("throws on a site level outside 1-3", () => {
    expect(() => staticSiteTiles([{ coord: FARM, kind: "farm", level: 4, garrison: 1 }])).toThrow(/level/);
    expect(() => staticSiteTiles([{ coord: FARM, kind: "oil_field", level: 0, garrison: 1 }])).toThrow(/level/);
  });

  it("throws on a settlement that stands on no map site", () => {
    expect(() => baseSiteTiles(map, [{ coord: PLAIN, controlled_by: null }])).toThrow(/no map site/);
  });

  it("reports the hexes whose base tile appears or goes when settlements change", () => {
    const joined = baseSiteTiles(map, [...settlements, { coord: VACANT, controlled_by: "p2" }]);
    expect(changedBaseTiles(baseTiles, joined)).toEqual([VACANT]);
    const fallen = baseSiteTiles(map, settlements.filter((s) => hexKey(s.coord.q, s.coord.r) !== hexKey(BASE.q, BASE.r)));
    expect(changedBaseTiles(baseTiles, fallen)).toEqual([BASE]);
    expect(changedBaseTiles(baseTiles, baseSiteTiles(map, settlements))).toEqual([]);
  });
});

describe("overview fill", () => {
  it("colours each capturable kind with its site fill, even on an old town hex", () => {
    expect(overviewHexFill(map, FARM.q, FARM.r)).toBe(SITE_OVERVIEW_FILL.farm);
    expect(overviewHexFill(map, OLD_FARM.q, OLD_FARM.r)).toBe(SITE_OVERVIEW_FILL.farm);
    expect(overviewHexFill(map, FACTORY.q, FACTORY.r)).toBe(SITE_OVERVIEW_FILL.factory);
    expect(new Set(Object.values(SITE_OVERVIEW_FILL)).size).toBe(4);
  });

  it("keeps terrain fills for cities, bases and plain hexes", () => {
    expect(overviewHexFill(map, CITY.q, CITY.r)).toBe(TERRAIN_FILL.city);
    expect(overviewHexFill(map, BASE.q, BASE.r)).toBe(TERRAIN_FILL.plains);
    expect(overviewHexFill(map, VACANT.q, VACANT.r)).toBe(TERRAIN_FILL.plains);
    expect(overviewHexFill(map, PLAIN.q, PLAIN.r)).toBe(TERRAIN_FILL.plains);
  });

  it("gives a lake hex the water fill", () => {
    const lakeMap = decodeWorldMap({ width: 2, height: 1, terrain: "o~", roads: "..", sites: [] });
    expect(overviewHexFill(lakeMap, 0, 0)).toBe(TERRAIN_FILL.lake);
    expect(TERRAIN_FILL.lake).toBe(TERRAIN_FILL.river);
  });
});
