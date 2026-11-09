import { describe, expect, it } from "vitest";

import { axialToOffset, decodeWorldMap, offsetToAxial, visibleCells } from "./worldMap";

describe("world map", () => {
  it("round-trips offset and axial coordinates like the server", () => {
    for (const [col, row] of [[0, 0], [1, 0], [2, 3], [7, 5], [348, 348]]) {
      expect(axialToOffset(offsetToAxial(col, row))).toEqual({ col, row });
    }
    expect(offsetToAxial(3, 2)).toEqual({ q: 3, r: 1 });
  });

  it("decodes grids and finds hexes and sites in O(1)", () => {
    const map = decodeWorldMap({
      width: 3,
      height: 2,
      terrain: ".wht~c",
      roads: "#....#",
      sites: [{ coord: offsetToAxial(2, 1), kind: "city", garrison: 3 }],
    });
    expect(map.hexAt(0, 0)).toEqual({ coord: { q: 0, r: 0 }, terrain: "plains", road: true });
    const c = offsetToAxial(2, 1);
    expect(map.hexAt(c.q, c.r)?.terrain).toBe("city");
    expect(map.siteAt(c.q, c.r)?.kind).toBe("city");
    expect(map.hexAt(5, 5)).toBeUndefined();
    expect(map.siteAt(0, 0)).toBeUndefined();
  });

  it("rejects malformed grids", () => {
    expect(() => decodeWorldMap({ width: 2, height: 2, terrain: "...", roads: "...." })).toThrow();
    expect(() => decodeWorldMap({ width: 1, height: 1, terrain: "?", roads: "." })).toThrow();
  });

  it("clamps the visible cell range to the map", () => {
    const r = visibleCells({ width: 100, height: 100 }, -500, -500, 960, 540, 32);
    expect(r.col0).toBe(0);
    expect(r.row0).toBe(0);
    expect(r.col1).toBe(Math.ceil(960 / 48) + 1);
    expect(r.row1).toBe(Math.ceil(540 / (Math.sqrt(3) * 32)) + 1);
  });
});
