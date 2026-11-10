import { describe, expect, it } from "vitest";

import {
  allSiteTileKeys,
  BASE_FOOTPRINT,
  BASE_STAGES,
  baseFootprint,
  baseTileKeys,
  SITE_TILE_KINDS,
  SITE_TILE_LEVELS,
  siteTileKey,
} from "./siteTiles";

const keys = allSiteTileKeys();
const grid: [number, number][] = [];
for (let q = -6; q <= 6; q++) for (let r = -6; r <= 6; r++) grid.push([q, r]);

// Produced by a one-off Python snippet that loads scripts/board-mock.py and calls
// SiteSet.discover(art/codex/terrain/candidates/sites-ratified) then .choose /
// .footprint_tiles for each coordinate. Row i uses kind kinds[i % 4], level i % 3 + 1,
// stage i % 4 + 1 (same coordinate list for both tables).
const SINGLE_FIXTURE: [string, number, number, number, string][] = [
  ["farm", 1, 0, 0, "l1_00.png"],
  ["oil_field", 2, 1, 0, "l2_00.png"],
  ["steel_mill", 3, 0, 1, "l3_00.png"],
  ["factory", 1, -1, 2, "l1_00.png"],
  ["farm", 2, 3, -2, "l2_00.png"],
  ["oil_field", 3, 7, 5, "l3_01.png"],
  ["steel_mill", 1, -4, -4, "l1_01.png"],
  ["factory", 2, 12, -7, "l2_03.png"],
  ["farm", 3, 5, 5, "l3_02.png"],
  ["oil_field", 1, 2, 9, "l1_01.png"],
  ["steel_mill", 2, -8, 3, "l2_02.png"],
  ["factory", 3, 15, 15, "l3_02.png"],
  ["farm", 1, 9, -3, "l1_00.png"],
  ["oil_field", 2, -1, -1, "l2_03.png"],
  ["steel_mill", 3, 20, 4, "l3_00.png"],
  ["factory", 1, 6, -11, "l1_02.png"],
  ["farm", 2, -13, 8, "l2_00.png"],
  ["oil_field", 3, 4, 1, "l3_02.png"],
  ["steel_mill", 1, 11, 11, "l1_02.png"],
  ["factory", 2, 30, -20, "l2_02.png"],
];
const BASE_FIXTURE: [number, number, number, string][] = [
  [1, 0, 0, "stage1_a"],
  [2, 1, 0, "stage2_a"],
  [3, 0, 1, "stage3_a"],
  [4, -1, 2, "stage4_a"],
  [1, 3, -2, "stage1_b"],
  [2, 7, 5, "stage2_b"],
  [3, -4, -4, "stage3_a"],
  [4, 12, -7, "stage4_b"],
  [1, 5, 5, "stage1_a"],
  [2, 2, 9, "stage2_b"],
  [3, -8, 3, "stage3_a"],
  [4, 15, 15, "stage4_a"],
  [1, 9, -3, "stage1_a"],
  [2, -1, -1, "stage2_b"],
  [3, 20, 4, "stage3_b"],
  [4, 6, -11, "stage4_a"],
  [1, -13, 8, "stage1_b"],
  [2, 4, 1, "stage2_a"],
  [3, 11, 11, "stage3_a"],
  [4, 30, -20, "stage4_a"],
];

describe("site tile art set", () => {
  it("has at least two variants for every kind and level", () => {
    for (const kind of SITE_TILE_KINDS) {
      for (const level of SITE_TILE_LEVELS) {
        const n = keys.filter((k) => k.startsWith(`./siteTiles/${kind}/l${level}_`)).length;
        expect(n, `${kind} l${level}`).toBeGreaterThanOrEqual(2);
      }
    }
  });

  it("has variants a and b for every stage with exactly N hex tiles", () => {
    for (const stage of BASE_STAGES) {
      for (const v of ["a", "b"]) {
        const dir = `./siteTiles/base/stage${stage}_${v}/`;
        const files = keys.filter((k) => k.startsWith(dir));
        expect(files.map((k) => k.slice(dir.length)).sort()).toEqual(
          Array.from({ length: stage }, (_, i) => `h${i}.png`),
        );
      }
    }
  });
});

describe("baseFootprint", () => {
  it("is the four-hex offset list in order", () => {
    expect(BASE_FOOTPRINT).toEqual([
      { q: 0, r: 0 },
      { q: 1, r: 0 },
      { q: 0, r: 1 },
      { q: 1, r: 1 },
    ]);
  });

  it("offsets the first N hexes by the anchor", () => {
    expect(baseFootprint({ q: 3, r: -2 }, 1)).toEqual([{ q: 3, r: -2 }]);
    expect(baseFootprint({ q: 3, r: -2 }, 3)).toEqual([
      { q: 3, r: -2 },
      { q: 4, r: -2 },
      { q: 3, r: -1 },
    ]);
    expect(baseFootprint({ q: 3, r: -2 }, 4)[3]).toEqual({ q: 4, r: -1 });
  });
});

describe("variant choice", () => {
  it("is deterministic and reaches every variant", () => {
    for (const [q, r] of grid) {
      expect(siteTileKey("oil_field", 2, q, r)).toBe(siteTileKey("oil_field", 2, q, r));
    }
    const picked = new Set(grid.map(([q, r]) => siteTileKey("oil_field", 2, q, r)));
    expect(picked.size).toBe(4);
    const bases = new Set(grid.map(([q, r]) => baseTileKeys({ q, r }, 2)[0].key.split("/")[3]));
    expect(bases).toEqual(new Set(["stage2_a", "stage2_b"]));
  });

  it("draws all hexes of one base from the same variant dir", () => {
    for (const stage of BASE_STAGES) {
      for (const [q, r] of grid) {
        const tiles = baseTileKeys({ q, r }, stage);
        expect(tiles).toHaveLength(stage);
        expect(new Set(tiles.map((t) => t.key.split("/")[3])).size).toBe(1);
        expect(tiles.map((t) => t.coord)).toEqual(baseFootprint({ q, r }, stage));
        expect(tiles.map((t) => t.key.split("/")[4])).toEqual(
          Array.from({ length: stage }, (_, i) => `h${i}.png`),
        );
      }
    }
  });

  it("throws loudly on bad kind, level or stage", () => {
    expect(() => siteTileKey("castle", 1, 0, 0)).toThrow(/castle/);
    expect(() => siteTileKey("farm", 0, 0, 0)).toThrow(/0/);
    expect(() => siteTileKey("farm", 4, 0, 0)).toThrow(/4/);
    expect(() => siteTileKey("farm", 1.5, 0, 0)).toThrow(/1\.5/);
    expect(() => baseFootprint({ q: 0, r: 0 }, 0)).toThrow(/0/);
    expect(() => baseTileKeys({ q: 0, r: 0 }, 5)).toThrow(/5/);
  });
});

describe("parity with scripts/board-mock.py", () => {
  it("picks the same single-hex site files", () => {
    for (const [kind, level, q, r, file] of SINGLE_FIXTURE) {
      expect(siteTileKey(kind, level, q, r), `${kind} l${level} ${q},${r}`).toBe(
        `./siteTiles/${kind}/${file}`,
      );
    }
  });

  it("picks the same base variant dir", () => {
    for (const [stage, q, r, dir] of BASE_FIXTURE) {
      const tiles = baseTileKeys({ q, r }, stage);
      expect(tiles.map((t) => t.key)).toEqual(
        tiles.map((_, i) => `./siteTiles/base/${dir}/h${i}.png`),
      );
    }
  });
});
