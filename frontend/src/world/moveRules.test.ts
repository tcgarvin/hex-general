import { describe, expect, it } from "vitest";

import { hexKey, type Axial, type Hex } from "@/board/hex";
import { reachableContinuations } from "@/planning/reach";
import type { components } from "@/lib/api/schema";

import {
  CANNOT_PACK_WARNING,
  hostileHexes,
  moveStepRules,
  PINNED_WARNING,
  withdrawalWarning,
  type MoveContext,
} from "./moveRules";
import type { WorldOrder } from "./plan";
import { decodeWorldMap } from "./worldMap";

type Unit = components["schemas"]["Unit"];

// A 9×9 open plain, no roads: infantry reach 2, armor 4.
const map = decodeWorldMap({ width: 9, height: 9, terrain: ".".repeat(81), roads: ".".repeat(81), sites: [] });
const index = new Map<string, Hex>(map.hexes.map((h) => [hexKey(h.coord.q, h.coord.r), h]));

let nextId = 1;
function unit(owner: string, type: Unit["type"], q: number, r: number, extra: Partial<Unit> = {}): Unit {
  return { id: nextId++, owner, type, coord: { q, r }, hp: 10, posture: "deployed", facing: "right", disordered: false, ...extra };
}

function reach(mover: Unit, others: Unit[], orders: Record<number, WorldOrder> = {}, standing: MoveContext["standing"] = []) {
  const ctx: MoveContext = { units: [mover, ...others], viewer: "me", orders, standing };
  return reachableContinuations(mover, [], index, moveStepRules(mover, ctx));
}

const k = (a: Axial) => hexKey(a.q, a.r);

describe("move reach with own units", () => {
  it("never ends on or passes through a stationary own unit", () => {
    const me = unit("me", "infantry", 4, 2);
    const friend = unit("me", "infantry", 4, 3); // directly south
    const r = reach(me, [friend]);
    expect(r.has(k(friend.coord))).toBe(false);
    // (4,4) is two steps south, through the friend: infantry can't get round in 2.
    expect(r.has("4,4")).toBe(false);
    // Armor can, and goes round.
    const around = reach(unit("me", "armor", 4, 2), [friend]).get("4,4");
    expect(around).toHaveLength(3);
    expect(around!.some((s) => s.q === 4 && s.r === 3)).toBe(false);
  });

  it("lets a step land where a moving own unit has left", () => {
    const me = unit("me", "infantry", 4, 2);
    const friend = unit("me", "infantry", 4, 3);
    const r = reach(me, [friend], { [friend.id]: { kind: "move", path: [{ q: 4, r: 4 }] } });
    expect(r.get(k(friend.coord))).toEqual([{ q: 4, r: 3 }]);
    // But not the hex that friend's move ends on.
    expect(r.has("4,4")).toBe(false);
  });

  it("never steps into a hex on the pulse another own unit steps into it (both would jam)", () => {
    const me = unit("me", "infantry", 4, 4);
    const friend = unit("me", "armor", 6, 3);
    // The friend enters (5,3) on pulse 1 and (4,3) on pulse 2.
    const orders: Record<number, WorldOrder> = {
      [friend.id]: { kind: "move", path: [{ q: 5, r: 3 }, { q: 4, r: 3 }, { q: 3, r: 3 }] },
    };
    const r = reach(me, [friend], orders);
    // (4,3) on pulse 1 is fine: the friend gets there on pulse 2 and passes on.
    expect(r.has("4,3")).toBe(true);
    // Pulse 1 into (5,3) would jam: no path starts there.
    for (const path of r.values()) expect(path[0].q === 5 && path[0].r === 3).toBe(false);
  });

  it("treats a friend packing for travel as in place until pulse 5", () => {
    const armor = unit("me", "armor", 2, 3);
    const friend = unit("me", "infantry", 3, 3); // adjacent: step 1
    const travel: WorldOrder = { kind: "travel", destination: { q: 8, r: 0 }, via: [], onArrival: "deployed" };
    const r = reach(armor, [friend], { [friend.id]: travel });
    expect(r.has(k(friend.coord))).toBe(false);
    // A standing travel order packs the same way.
    expect(reach(armor, [friend], {}, [{ unit_id: friend.id, route: [{ q: 4, r: 3 }], on_arrival: "deployed" } as never]).has(k(friend.coord))).toBe(false);
  });

  it("ignores own columns: deployed units and columns stack apart", () => {
    const me = unit("me", "infantry", 4, 2);
    const column = unit("me", "infantry", 4, 3, { posture: "column" });
    expect(reach(me, [column]).get("4,3")).toEqual([{ q: 4, r: 3 }]);
  });
});

describe("move reach with hostile units", () => {
  it("offers a hostile hex as the end of a move (an attack) but never a hex past it", () => {
    const me = unit("me", "armor", 1, 4);
    const enemy = unit("rival", "infantry", 3, 3);
    const r = reach(me, [enemy]);
    const attack = r.get(k(enemy.coord));
    expect(attack).toBeDefined();
    for (const path of r.values()) {
      expect(path.slice(0, -1).some((s) => s.q === 3 && s.r === 3)).toBe(false);
    }
    expect(hostileHexes([me, enemy], "me")).toEqual(new Set([k(enemy.coord)]));
  });

  it("lets several units attack the same hostile hex", () => {
    const enemy = unit("neutral", "infantry", 4, 4);
    const first = unit("me", "infantry", 4, 2);
    const second = unit("me", "armor", 6, 2);
    const orders: Record<number, WorldOrder> = {
      [first.id]: { kind: "move", path: [{ q: 4, r: 3 }, { q: 4, r: 4 }] },
    };
    expect(reach(second, [first, enemy], orders).has(k(enemy.coord))).toBe(true);
  });

  it("keeps others out of the hex an attacker strikes from, since it stays there unless it wins", () => {
    const enemy = unit("neutral", "infantry", 4, 5);
    const attacker = unit("me", "infantry", 4, 3);
    const orders: Record<number, WorldOrder> = {
      [attacker.id]: { kind: "move", path: [{ q: 4, r: 4 }, { q: 4, r: 5 }] },
    };
    // Armor going by (4,4) would get there on pulse 3, after the attacker took it on pulse 1.
    const armor = unit("me", "armor", 5, 1);
    const r = reach(armor, [attacker, enemy], orders);
    expect(r.has("4,4")).toBe(false);
    for (const path of r.values()) expect(path.some((s) => s.q === 4 && s.r === 4)).toBe(false);
    // It still gets at the enemy another way.
    expect(r.has(k(enemy.coord))).toBe(true);
  });

  it("treats a unit attacking from where it stands as staying put", () => {
    const enemy = unit("neutral", "infantry", 4, 4);
    const attacker = unit("me", "infantry", 4, 3);
    const orders: Record<number, WorldOrder> = { [attacker.id]: { kind: "move", path: [{ q: 4, r: 4 }] } };
    expect(reach(unit("me", "infantry", 4, 2), [attacker, enemy], orders).has(k(attacker.coord))).toBe(false);
  });

  it("does not let artillery or a disordered unit attack", () => {
    const enemy = unit("neutral", "infantry", 4, 3);
    expect(reach(unit("me", "artillery", 4, 2), [enemy]).has("4,3")).toBe(false);
    expect(reach(unit("me", "infantry", 4, 2, { disordered: true }), [enemy]).has("4,3")).toBe(false);
    expect(reach(unit("me", "infantry", 4, 2), [enemy]).has("4,3")).toBe(true);
  });

  it("stops an engaged unit stepping from one enemy-adjacent hex to another", () => {
    // Enemy at (4,4); we start at (4,3), adjacent, so engaged.
    const me = unit("me", "infantry", 4, 3);
    const enemy = unit("rival", "infantry", 4, 4);
    const r = reach(me, [enemy]);
    // (5,3) is adjacent to both: the direct ZOC→ZOC step is refused, so it is
    // only reached by stepping out of the ZOC first.
    expect(r.get("5,3")).toEqual([{ q: 5, r: 2 }, { q: 5, r: 3 }]);
    // Withdrawing to a free hex is fine, and so is attacking.
    expect(r.has("4,2")).toBe(true);
    expect(r.has("4,4")).toBe(true);
  });

  it("lets a unit enter enemy ZOC but not slide along it", () => {
    const me = unit("me", "armor", 4, 0);
    const enemy = unit("rival", "infantry", 4, 4);
    const r = reach(me, [enemy]);
    // (4,3) touches the enemy: reachable straight down (4,1)→(4,2)→(4,3).
    expect(r.get("4,3")).toEqual([{ q: 4, r: 1 }, { q: 4, r: 2 }, { q: 4, r: 3 }]);
    // From (4,3) the sideways step to (5,3), also enemy-adjacent, is refused, so
    // (5,3) must be reached from outside the ZOC, never via (4,3).
    const side = r.get("5,3");
    expect(side?.some((s) => s.q === 4 && s.r === 3)).toBe(false);
  });

  it("enemy columns exert no ZOC", () => {
    const me = unit("me", "infantry", 4, 3);
    const column = unit("rival", "infantry", 4, 4, { posture: "column" });
    expect(reach(me, [column]).has("5,3")).toBe(true);
  });
});

describe("withdrawal warning", () => {
  const me = unit("me", "armor", 4, 4);
  const enemy = unit("s2", "infantry", 5, 4); // adjacent (east)
  const garrison = unit("neutral", "infantry", 5, 4);
  const away: WorldOrder = { kind: "move", path: [{ q: 3, r: 4 }] };
  const warn = (order: WorldOrder | undefined, others: Unit[]) =>
    withdrawalWarning(me, order, [me, ...others], "me", hostileHexes([me, ...others], "me"));

  it("warns a unit stepping away from an adjacent enemy that it may be caught", () => {
    expect(warn(away, [enemy])).toBe(PINNED_WARNING);
  });

  it("does not warn an attack, a unit with no enemy beside it, or one beside a garrison", () => {
    expect(warn({ kind: "move", path: [enemy.coord] }, [enemy])).toBeNull();
    expect(warn(away, [unit("s2", "infantry", 7, 4)])).toBeNull();
    expect(warn(away, [garrison])).toBeNull(); // garrisons always hold
    expect(warn(undefined, [enemy])).toBeNull();
    expect(warn({ kind: "hold" }, [enemy])).toBeNull();
  });

  it("tells a Travel from next to any hostile that it can't pack", () => {
    const travel: WorldOrder = { kind: "travel", destination: { q: 0, r: 0 }, via: [], onArrival: "deployed" };
    expect(warn(travel, [garrison])).toBe(CANNOT_PACK_WARNING);
    expect(warn(travel, [unit("s2", "infantry", 5, 4, { posture: "column" })])).toBeNull();
  });
});
