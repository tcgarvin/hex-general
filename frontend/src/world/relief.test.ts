import { describe, expect, it } from "vitest";

import { coord, unit } from "@/replay/testFixtures";

import { hostileZoc } from "./moveRules";
import type { WorldOrder } from "./plan";
import { swapPartner, swapRole } from "./relief";

const ME = "me";
const front = unit(1, ME, coord(1, 0));
const fresh = unit(2, ME, coord(0, 0));
const enemy = unit(9, "foe", coord(2, 0));

/** Two plain Move orders, each unit into the other's hex: what a swap is. */
const swap: Record<number, WorldOrder> = {
  2: { kind: "move", path: [coord(1, 0)], attack: "assault" },
  1: { kind: "move", path: [coord(0, 0)], attack: "assault" },
};

describe("relief swaps", () => {
  const zoc = hostileZoc([front, fresh, enemy], ME);

  it("reads a relief from behind the line", () => {
    expect(swapRole(fresh, front, zoc)).toBe("relieves");
    expect(swapRole(front, fresh, zoc)).toBe("relieved");
  });

  it("finds the partner only when both moves step into each other's hex first", () => {
    const units = [front, fresh];
    expect(swapPartner(fresh, swap, units)?.id).toBe(1);
    expect(swapPartner(fresh, { 2: { kind: "move", path: [coord(1, 0)], attack: "assault" } }, units)).toBeNull();
  });

  it("finds the partner of a pass-through swap with longer paths", () => {
    const orders: Record<number, WorldOrder> = {
      2: { kind: "move", path: [coord(1, 0), coord(2, -1)], attack: "assault" },
      1: { kind: "move", path: [coord(0, 0), coord(-1, 0)], attack: "assault" },
    };
    expect(swapPartner(front, orders, [front, fresh])?.id).toBe(2);
  });
});
