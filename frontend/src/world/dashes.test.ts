import { describe, expect, it } from "vitest";

import { dashSegments } from "./dashes";

describe("dashSegments", () => {
  it("cuts a straight line into dashes and gaps", () => {
    const segs = dashSegments([{ x: 0, y: 0 }, { x: 25, y: 0 }], 5, 5);
    expect(segs).toEqual([
      [0, 0, 5, 0],
      [10, 0, 15, 0],
      [20, 0, 25, 0],
    ]);
  });

  it("carries the pattern across a corner", () => {
    const segs = dashSegments([{ x: 0, y: 0 }, { x: 3, y: 0 }, { x: 3, y: 10 }], 5, 5);
    // The first dash bends round the corner: 3 along x, then 2 down y.
    expect(segs[0]).toEqual([0, 0, 3, 0]);
    expect(segs[1]).toEqual([3, 0, 3, 2]);
    expect(segs[2]).toEqual([3, 7, 3, 10]);
  });

  it("returns nothing for a single point", () => {
    expect(dashSegments([{ x: 1, y: 1 }], 4, 2)).toEqual([]);
  });

  it("rejects a pattern that would never advance", () => {
    expect(() => dashSegments([{ x: 0, y: 0 }, { x: 1, y: 0 }], 0, 2)).toThrow();
  });
});
