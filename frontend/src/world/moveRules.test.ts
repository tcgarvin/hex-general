import { describe, expect, it } from "vitest";

import { hexKey, type Axial, type Hex } from "@/board/hex";
import { reachableContinuations } from "@/planning/reach";
import type { components } from "@/lib/api/schema";

import {
  CANNOT_PACK_WARNING,
  conflictText,
  hostileHexes,
  moveStepRules,
  PINNED_WARNING,
  planConflicts,
  planMoveReach,
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
    const r = reach(me, [friend], { [friend.id]: { kind: "move", path: [{ q: 4, r: 4 }], attack: "assault" } });
    expect(r.get(k(friend.coord))).toEqual([{ q: 4, r: 3 }]);
    // But not the hex that friend's move ends on.
    expect(r.has("4,4")).toBe(false);
  });

  it("never steps into a hex on the pulse another own unit steps into it (both would jam)", () => {
    const me = unit("me", "infantry", 4, 4);
    const friend = unit("me", "armor", 6, 3);
    // The friend enters (5,3) on pulse 1 and (4,3) on pulse 2.
    const orders: Record<number, WorldOrder> = {
      [friend.id]: { kind: "move", path: [{ q: 5, r: 3 }, { q: 4, r: 3 }, { q: 3, r: 3 }], attack: "assault" },
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
      [first.id]: { kind: "move", path: [{ q: 4, r: 3 }, { q: 4, r: 4 }], attack: "assault" },
    };
    expect(reach(second, [first, enemy], orders).has(k(enemy.coord))).toBe(true);
  });

  it("keeps others out of the hex an attacker strikes from, since it stays there unless it wins", () => {
    const enemy = unit("neutral", "infantry", 4, 5);
    const attacker = unit("me", "infantry", 4, 3);
    const orders: Record<number, WorldOrder> = {
      [attacker.id]: { kind: "move", path: [{ q: 4, r: 4 }, { q: 4, r: 5 }], attack: "assault" },
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
    const orders: Record<number, WorldOrder> = { [attacker.id]: { kind: "move", path: [{ q: 4, r: 4 }], attack: "assault" } };
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
  // A militia picket beside me whose site is two hexes east: I stand outside its zone.
  const militiaBeside = unit("neutral", "infantry", 5, 4, { post: { q: 6, r: 4 } });
  const away: WorldOrder = { kind: "move", path: [{ q: 3, r: 4 }], attack: "assault" };
  const warn = (order: WorldOrder | undefined, others: Unit[]) =>
    withdrawalWarning(me, order, [me, ...others], "me", hostileHexes([me, ...others], "me"));

  it("warns a unit stepping away from an adjacent enemy that it may be caught", () => {
    expect(warn(away, [enemy])).toBe(PINNED_WARNING);
  });

  it("does not warn an attack, a unit with no enemy beside it, or one beside militia whose zone it is outside", () => {
    expect(warn({ kind: "move", path: [enemy.coord], attack: "assault" }, [enemy])).toBeNull();
    expect(warn(away, [unit("s2", "infantry", 7, 4)])).toBeNull();
    expect(warn(away, [militiaBeside])).toBeNull(); // militia strike only inside their zone
    expect(warn(undefined, [enemy])).toBeNull();
    expect(warn({ kind: "hold" }, [enemy])).toBeNull();
  });

  it("warns a unit standing in a militia zone that the militia beside it may strike", () => {
    // Its site is the hex south of me, so I stand on the ring of its zone.
    const picket = unit("neutral", "infantry", 5, 4, { post: { q: 4, r: 5 } });
    expect(warn(away, [picket])).toBe(PINNED_WARNING);
    // On the site hex itself too.
    expect(warn(away, [unit("neutral", "infantry", 5, 4, { post: { q: 4, r: 4 } })])).toBe(PINNED_WARNING);
    // The keeper on its own site hex always Holds, so it never pins.
    expect(warn(away, [unit("neutral", "infantry", 5, 4, { post: { q: 5, r: 4 } })])).toBeNull();
    // A militia gun never moves, so it never pins.
    expect(warn(away, [unit("neutral", "artillery", 5, 4, { post: { q: 4, r: 5 } })])).toBeNull();
  });

  it("tells a Travel from next to any hostile that it can't pack", () => {
    const travel: WorldOrder = { kind: "travel", destination: { q: 0, r: 0 }, via: [], onArrival: "deployed" };
    expect(warn(travel, [militiaBeside])).toBe(CANNOT_PACK_WARNING);
    expect(warn(travel, [unit("s2", "infantry", 5, 4, { posture: "column" })])).toBeNull();
  });
});

describe("move reach ignoring your own units where it must", () => {
  const full = (mover: Unit, others: Unit[], orders: Record<number, WorldOrder> = {}) =>
    planMoveReach(mover, [], index, { units: [mover, ...others], viewer: "me", orders, standing: [] });

  it("offers a stationary friend's hex, and the hexes past it, provisionally", () => {
    const me = unit("me", "infantry", 4, 2);
    const friend = unit("me", "infantry", 4, 3);
    const r = full(me, [friend]);
    expect(r.get("4,3")).toEqual([{ q: 4, r: 3 }]);
    expect(r.get("4,4")).toEqual([{ q: 4, r: 3 }, { q: 4, r: 4 }]);
  });

  it("keeps the strict path where there is one, so paths don't run through friends needlessly", () => {
    const armor = unit("me", "armor", 4, 2);
    const friend = unit("me", "infantry", 4, 3);
    const strict = reach(armor, [friend]).get("4,4");
    expect(full(armor, [friend]).get("4,4")).toEqual(strict);
    expect(strict!.some((s) => s.q === 4 && s.r === 3)).toBe(false);
  });

  it("still obeys hostile units and ZOC", () => {
    const me = unit("me", "armor", 1, 4);
    const enemy = unit("rival", "infantry", 3, 3);
    const friend = unit("me", "infantry", 2, 4);
    const r = full(me, [enemy, friend]);
    expect(r.has("3,3")).toBe(true); // an attack
    // Never a hex reached through the enemy's hex.
    for (const path of r.values()) expect(path.slice(0, -1).some((s) => s.q === 3 && s.r === 3)).toBe(false);
    // An engaged unit can't slide from one enemy-adjacent hex to another, friends or not.
    const engaged = unit("me", "infantry", 4, 3);
    const foe = unit("rival", "infantry", 4, 4);
    // A friend standing on the way out (5,2): the direct ZOC-to-ZOC step into (5,3)
    // is still refused, so (5,3) is reached only through the friend, provisionally.
    const r2 = full(engaged, [foe, unit("me", "infantry", 5, 2)]);
    expect(r2.get("5,3")).toEqual([{ q: 5, r: 2 }, { q: 5, r: 3 }]);
  });
});

describe("plan conflicts", () => {
  const conflicts = (units: Unit[], orders: Record<number, WorldOrder>, standing: MoveContext["standing"] = []) =>
    planConflicts({ units, viewer: "me", orders, standing });
  const move = (...path: [number, number][]): WorldOrder => ({ kind: "move", path: path.map(([q, r]) => ({ q, r })), attack: "assault" });
  const name = (u: Unit) => `Unit ${u.id}`;

  it("blocks a Move into a friend that stays put, and clears once the friend is ordered out", () => {
    const me = unit("me", "infantry", 4, 2);
    const friend = unit("me", "infantry", 4, 3);
    const blocked = conflicts([me, friend], { [me.id]: move([4, 3]) });
    expect(blocked.get(me.id)).toEqual({ kind: "blocked", by: friend });
    expect(conflictText(blocked.get(me.id)!, name)).toBe(`Blocked unless Unit ${friend.id} moves out of the way first`);
    expect(conflicts([me, friend], { [me.id]: move([4, 3]), [friend.id]: move([4, 4]) }).size).toBe(0);
  });

  it("does not depend on which order was given first, and catches an order a later one spoils", () => {
    const me = unit("me", "infantry", 4, 2);
    const friend = unit("me", "infantry", 4, 3);
    const a = conflicts([me, friend], { [me.id]: move([4, 3]), [friend.id]: move([4, 4]) });
    const b = conflicts([friend, me], { [friend.id]: move([4, 4]), [me.id]: move([4, 3]) });
    expect([...a]).toEqual([...b]);
    // The friend's Move replaced by Hold: the follower's Move is blocked again.
    expect(conflicts([me, friend], { [me.id]: move([4, 3]), [friend.id]: { kind: "hold" } }).get(me.id)?.kind).toBe("blocked");
  });

  it("is clean for a mutual swap, a pass-through swap with longer paths, and a rotation", () => {
    const me = unit("me", "infantry", 4, 2);
    const friend = unit("me", "infantry", 4, 3);
    expect(conflicts([me, friend], { [me.id]: move([4, 3]), [friend.id]: move([4, 2]) }).size).toBe(0);
    expect(conflicts([me, friend], { [me.id]: move([4, 3], [4, 4]), [friend.id]: move([4, 2], [4, 1]) }).size).toBe(0);
    const third = unit("me", "infantry", 5, 2); // next to both
    const rotation = { [me.id]: move([5, 2]), [third.id]: move([4, 3]), [friend.id]: move([4, 2]) };
    expect(conflicts([me, friend, third], rotation).size).toBe(0);
  });

  it("warns the half of a swap that is left when the other half is cleared", () => {
    const me = unit("me", "infantry", 4, 2);
    const friend = unit("me", "infantry", 4, 3);
    expect(conflicts([me, friend], { [friend.id]: move([4, 2]) }).get(friend.id)).toEqual({ kind: "blocked", by: me });
  });

  it("blocks the second of two Moves ending on one hex", () => {
    const me = unit("me", "infantry", 4, 2);
    const friend = unit("me", "infantry", 4, 5);
    const c = conflicts([me, friend], { [me.id]: move([4, 3]), [friend.id]: move([4, 4], [4, 3]) });
    expect(c.has(me.id)).toBe(false);
    expect(c.get(friend.id)).toEqual({ kind: "sameEnd", by: me });
    expect(conflictText(c.get(friend.id)!, name)).toBe(`Ends where Unit ${me.id} ends: only one can stop there`);
  });

  it("jams two Moves stepping into one hex on the same pulse", () => {
    const me = unit("me", "infantry", 4, 2);
    const friend = unit("me", "infantry", 4, 4);
    const c = conflicts([me, friend], { [me.id]: move([4, 3]), [friend.id]: move([4, 3]) });
    expect(c.get(me.id)).toEqual({ kind: "jam", by: friend });
    expect(c.get(friend.id)).toEqual({ kind: "jam", by: me });
    expect(conflictText(c.get(me.id)!, name)).toBe(`Jams with Unit ${friend.id}: both step into the same hex at once`);
  });

  it("keeps others out of the hex an attacker strikes from", () => {
    const enemy = unit("neutral", "infantry", 4, 5);
    const attacker = unit("me", "infantry", 4, 3);
    const me = unit("me", "armor", 4, 2);
    const c = conflicts([me, attacker, enemy], { [attacker.id]: move([4, 4], [4, 5]), [me.id]: move([4, 3], [4, 4]) });
    expect(c.get(me.id)).toEqual({ kind: "attacker", by: attacker });
    expect(c.has(attacker.id)).toBe(false);
  });

  it("blocks a step into a friend still packing for travel", () => {
    const me = unit("me", "infantry", 4, 2);
    const friend = unit("me", "infantry", 4, 3);
    const travel: WorldOrder = { kind: "travel", destination: { q: 8, r: 8 }, via: [], onArrival: "deployed" };
    expect(conflicts([me, friend], { [me.id]: move([4, 3]), [friend.id]: travel }).get(me.id)?.kind).toBe("packing");
  });

  it("blocks a unit following one that is itself blocked", () => {
    const rear = unit("me", "infantry", 4, 1);
    const front = unit("me", "infantry", 4, 2);
    const wall = unit("me", "infantry", 4, 3);
    const c = conflicts([rear, front, wall], { [rear.id]: move([4, 2]), [front.id]: move([4, 3]) });
    expect(c.get(front.id)).toEqual({ kind: "blocked", by: wall });
    expect(c.get(rear.id)).toEqual({ kind: "blocked", by: front });
    // Order the wall out and both clear.
    expect(conflicts([rear, front, wall], { [rear.id]: move([4, 2]), [front.id]: move([4, 3]), [wall.id]: move([4, 4]) }).size).toBe(0);
  });

  it("ignores columns and an attack on a hostile hex", () => {
    const me = unit("me", "infantry", 4, 2);
    const column = unit("me", "infantry", 4, 3, { posture: "column" });
    expect(conflicts([me, column], { [me.id]: move([4, 3]) }).size).toBe(0);
    const enemy = unit("rival", "infantry", 5, 2);
    const other = unit("me", "infantry", 5, 3);
    expect(conflicts([me, enemy, other], { [me.id]: move([5, 2]), [other.id]: move([5, 2]) }).size).toBe(0);
  });
});
