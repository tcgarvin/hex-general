import { describe, expect, it } from "vitest";

import { orientedKey, variantIndex } from "./tiles";

describe("orientedKey", () => {
  it("groups numbered variants of an oriented shape under one key", () => {
    expect(orientedKey("straight_v_00")).toBe("straight_v");
    expect(orientedKey("straight_v_01")).toBe("straight_v");
    expect(orientedKey("straight_d_12")).toBe("straight_d");
  });

  it("leaves suffix-less basenames as their own group", () => {
    expect(orientedKey("straight_v")).toBe("straight_v");
    expect(orientedKey("straight_d")).toBe("straight_d");
    // the generic fallback tile and plain cosmetic variants keep their names
    expect(orientedKey("00")).toBe("00");
    expect(orientedKey("03")).toBe("03");
  });
});

describe("variantIndex", () => {
  it("is deterministic and in range", () => {
    for (const [q, r] of [
      [0, 0],
      [3, -2],
      [-7, 11],
      [128, 512],
    ]) {
      const i = variantIndex(q, r, 3);
      expect(i).toBe(variantIndex(q, r, 3));
      expect(i).toBeGreaterThanOrEqual(0);
      expect(i).toBeLessThan(3);
    }
  });

  it("returns 0 for single-variant groups", () => {
    expect(variantIndex(5, 9, 1)).toBe(0);
  });
});
