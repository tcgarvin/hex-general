import { describe, expect, it } from "vitest";

import { coord, unit } from "@/replay/testFixtures";

import { DEFAULT_FACING, defaultFacing, facingAfterStep } from "./facing";

describe("facingAfterStep", () => {
  it("faces right on a rightward step and left on a leftward step", () => {
    expect(facingAfterStep("left", coord(0, 0), coord(1, 0))).toBe("right");
    expect(facingAfterStep("right", coord(1, 0), coord(0, 1))).toBe("left");
  });

  it("keeps the prior facing on a purely vertical step", () => {
    expect(facingAfterStep("left", coord(2, 2), coord(2, 3))).toBe("left");
    expect(facingAfterStep("right", coord(2, 3), coord(2, 2))).toBe("right");
  });
});

describe("defaultFacing (engine default_facing mirror)", () => {
  it("faces the nearest enemy", () => {
    const units = [unit(1, "p2", coord(0, 0)), unit(2, "p2", coord(9, 0))];
    expect(defaultFacing(coord(3, 0), "p1", units)).toBe("left");
    expect(defaultFacing(coord(7, 0), "p1", units)).toBe("right");
  });

  it("ignores own units when picking the nearest enemy", () => {
    const units = [unit(1, "p1", coord(2, 0)), unit(2, "p2", coord(9, 0))];
    expect(defaultFacing(coord(3, 0), "p1", units)).toBe("right");
  });

  it("breaks distance ties by lowest unit id", () => {
    // Two enemies equidistant, one left (id 5) and one right (id 3): id 3 wins.
    const units = [unit(5, "p2", coord(0, 0)), unit(3, "p2", coord(6, 0))];
    expect(defaultFacing(coord(3, 0), "p1", units)).toBe("right");
  });

  it("falls back to the default with no enemies or a same-column enemy", () => {
    expect(defaultFacing(coord(3, 0), "p1", [unit(1, "p1", coord(0, 0))])).toBe(DEFAULT_FACING);
    expect(defaultFacing(coord(3, 0), "p1", [unit(1, "p2", coord(3, 4))])).toBe(DEFAULT_FACING);
  });
});
