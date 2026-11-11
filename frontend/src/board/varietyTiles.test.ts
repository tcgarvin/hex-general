import type { Texture } from "pixi.js";
import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import fixture from "./__fixtures__/variety.json";
import { AXIAL_DIRECTIONS, hexKey, type Axial, type Hex } from "./hex";
import { hexTileRef } from "./hexTile";
import { roadCourseEdges, roadTileRefs } from "./roadTiles";
import { groupTileFiles, type LoadedTiles } from "./tiles";
import {
  saltedIndex,
  SHIPPED_VARIETY,
  VARIETY_DENSITIES,
  VARIETY_MIXES,
  varietyCatalog,
  varietyExclusions,
  varietyPicks,
} from "./varietyTiles";
import settings from "./varietySettings.json";

type Terrain = components["schemas"]["Terrain"];

/** `__fixtures__/variety.json`, written by `scripts/variety-fixture.py`. */
interface FixtureMap {
  name: string;
  terrain: string[];
  roads: string[];
  sites: { col: number; row: number; kind: string; level: number }[];
  picks: { q: number; r: number; key: string; kind: string; size: string; mirror: boolean }[];
}

const TERRAIN: Record<string, Terrain> = {
  ".": "plains",
  w: "woods",
  h: "hills",
  "~": "river",
  "=": "bridge",
  t: "town",
  c: "city",
  o: "lake",
};
// Mirrors BASE_FOOTPRINT in src/world/siteTiles.ts (and board-mock's stage 4).
const BASE_FOOTPRINT: Axial[] = [
  { q: 0, r: 0 },
  { q: 1, r: 0 },
  { q: 0, r: 1 },
  { q: 1, r: 1 },
];
const TILE_KEYS = Object.keys(import.meta.glob("./tiles/**/*.png"));
const CATALOG = varietyCatalog(TILE_KEYS);

const axial = (col: number, row: number): Axial => ({ q: col, r: row - (col - (col & 1)) / 2 });

function decode(m: FixtureMap) {
  const hexes: Hex[] = [];
  m.terrain.forEach((line, row) => {
    [...line].forEach((ch, col) => {
      hexes.push({ coord: axial(col, row), terrain: TERRAIN[ch], road: m.roads[row]?.[col] === "#" });
    });
  });
  const byKey = new Map(hexes.map((h) => [hexKey(h.coord.q, h.coord.r), h]));
  const hexAt = (q: number, r: number) => byKey.get(hexKey(q, r));
  const roadTiles = roadTileRefs(hexes, roadCourseEdges(hexes, hexAt));
  const sites = m.sites.map((s) => ({ kind: s.kind, coord: axial(s.col, s.row) }));
  return { hexes, hexAt, roadTiles, exclusions: varietyExclusions(sites, BASE_FOOTPRINT) };
}

const MAPS = fixture.maps as FixtureMap[];

describe("varietyPicks parity with scripts/board-mock.py (variety.json)", () => {
  it("uses the same settings and catalog as the fixture", () => {
    expect(fixture.settings).toEqual({ density: settings.density, mix: settings.mix, repeatRadius: settings.repeatRadius });
    expect(CATALOG).toEqual(fixture.catalog);
    expect(SHIPPED_VARIETY.density).toEqual(VARIETY_DENSITIES[settings.density]);
    expect(SHIPPED_VARIETY.mediumShare).toBe(VARIETY_MIXES[settings.mix]);
  });

  for (const m of MAPS) {
    it(`reproduces every pick on ${m.name}`, () => {
      const { hexes, hexAt, roadTiles, exclusions } = decode(m);
      const picks = varietyPicks(hexes, hexAt, roadTiles, exclusions, CATALOG);
      const got = [...picks].map(([key, p]) => {
        const [q, r] = key.split(",").map(Number);
        return { q, r, key: p.key, kind: p.kind, size: p.size, mirror: p.kind === "road" && roadTiles.get(key)!.mirror };
      });
      const order = (a: { q: number; r: number }, b: { q: number; r: number }) => a.q - b.q || a.r - b.r;
      expect(got.sort(order)).toEqual([...m.picks].sort(order));
    });
  }

  it("draws each pick through hexTileRef with the road's mirror flag", () => {
    const tiles: LoadedTiles = groupTileFiles(
      TILE_KEYS.map((key) => [key, { name: key.replace("./tiles/", "").replace(/\.png$/, "") } as unknown as Texture] as const),
    );
    const m = MAPS.find((x) => x.name === "world-s3")!;
    const { hexes, hexAt, roadTiles, exclusions } = decode(m);
    const picks = varietyPicks(hexes, hexAt, roadTiles, exclusions, CATALOG);
    for (const fx of m.picks) {
      const h = hexAt(fx.q, fx.r)!;
      const ref = hexTileRef(h, tiles, new Map(), roadTiles, new Map(), picks)!;
      expect((ref.tex as unknown as { name: string }).name).toBe(fx.key);
      expect(ref.mirror).toBe(fx.mirror);
    }
    expect(m.picks.some((p) => p.mirror)).toBe(true);
  });
});

describe("varietyPicks rules", () => {
  const world = decode(MAPS.find((x) => x.name === "world-s3")!);
  const picks = varietyPicks(world.hexes, world.hexAt, world.roadTiles, world.exclusions, CATALOG);

  it("never decorates a site or base-footprint hex, and no two decorated hexes touch", () => {
    for (const key of picks.keys()) {
      expect(world.exclusions.covered.has(key)).toBe(false);
      const [q, r] = key.split(",").map(Number);
      for (const d of AXIAL_DIRECTIONS) expect(picks.has(hexKey(q + d.q, r + d.r))).toBe(false);
    }
  });

  it("decorates nothing without variety tiles", () => {
    const none = varietyPicks(world.hexes, world.hexAt, world.roadTiles, world.exclusions, { small: null, medium: null });
    expect(none.size).toBe(0);
  });

  it("matches the salted hash bit for bit on large and negative coordinates", () => {
    // values from scripts/board-mock.py salted_index
    expect(saltedIndex(0, 0, 11, 1000)).toBe(fixtureHash.zero);
    expect(saltedIndex(348, -173, 16, 1000)).toBe(fixtureHash.far);
    expect(saltedIndex(-5, 7, 14, 1000)).toBe(fixtureHash.negative);
  });
});

// board-mock.py: salted_index(0, 0, 11, 1000), (348, -173, 16, 1000), (-5, 7, 14, 1000)
const fixtureHash = fixture.hashes as { zero: number; far: number; negative: number };
