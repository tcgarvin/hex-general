import { describe, expect, it } from "vitest";

import { coord, unit } from "@/replay/testFixtures";

import { hostileZoc, swapBlocked, swapNeighbours, swapOrders, swapPartner, swapRole } from "./relief";

const ME = "me";
const front = unit(1, ME, coord(1, 0));
const fresh = unit(2, ME, coord(0, 0));
const enemy = unit(9, "foe", coord(2, 0));

describe("relief swaps", () => {
  const zoc = hostileZoc([front, fresh, enemy], ME);

  it("orders two one-hex moves into each other's hex", () => {
    expect(swapOrders(fresh, front)).toEqual({
      2: { kind: "move", path: [coord(1, 0)] },
      1: { kind: "move", path: [coord(0, 0)] },
    });
  });

  it("allows a relief from behind the line", () => {
    expect(swapBlocked(fresh, front, zoc)).toBeNull();
    expect(swapRole(fresh, front, zoc)).toBe("relieves");
    expect(swapRole(front, fresh, zoc)).toBe("relieved");
  });

  it("refuses when both hexes touch the enemy (ZOC to ZOC), or a unit is packed or not next door", () => {
    const beside = unit(3, ME, coord(2, -1)); // also next to the enemy
    expect(swapBlocked(front, beside, zoc)).toMatch(/both hexes touch the enemy/);
    expect(swapBlocked(fresh, { ...front, posture: "column" }, zoc)).toMatch(/only deployed units/);
    expect(swapBlocked(fresh, unit(4, ME, coord(5, 5)), zoc)).toMatch(/neighbouring/);
  });

  it("finds the partner only when both moves step into each other's hex first", () => {
    const units = [front, fresh];
    expect(swapPartner(fresh, swapOrders(fresh, front), units)?.id).toBe(1);
    expect(swapPartner(fresh, { 2: { kind: "move", path: [coord(1, 0)] } }, units)).toBeNull();
  });

  it("lists deployed own neighbours", () => {
    expect(swapNeighbours(fresh, [front, fresh, enemy, { ...unit(5, ME, coord(0, 1)), posture: "column" }]).map((u) => u.id)).toEqual([
      1,
    ]);
  });
});
