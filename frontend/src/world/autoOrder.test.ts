import { describe, expect, it } from "vitest";

import { hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import {
  autoTap,
  closerMove,
  COLUMN_IN_CONTACT,
  followingNotice,
  followTap,
  holdAllUnits,
  NO_ROUTE,
  NOTHING_CLOSER,
  ownOnHex,
  TOWARD_IT,
  type AutoTapInput,
} from "./autoOrder";
import { contactZone } from "./contact";
import type { MoveContext } from "./moveRules";
import type { WorldOrder } from "./plan";

type Unit = components["schemas"]["Unit"];

const unit = (over: Partial<Unit> = {}): Unit => ({
  id: 1,
  owner: "p1",
  type: "infantry",
  hp: 10,
  coord: { q: 0, r: 0 },
  posture: "deployed",
  disordered: false,
  facing: "right",
  ...over,
});

/** A straight route along +q from the origin to `to` (same r), for tests. */
const lineRoute = (from: Axial) => (to: Axial) => {
  if (to.r !== from.r || to.q <= from.q) return null;
  const path: Axial[] = [];
  for (let q = from.q + 1; q <= to.q; q++) path.push({ q, r: from.r });
  return path;
};

const reachOf = (...paths: Axial[][]) => new Map(paths.map((p) => [hexKey(p[p.length - 1].q, p[p.length - 1].r), p]));

const input = (over: Partial<AutoTapInput> = {}): AutoTapInput => {
  const u = over.unit ?? unit();
  return {
    unit: u,
    hex: { q: 5, r: 0 },
    own: { count: 0, leaving: null },
    ignoreOwn: false,
    name: (x) => `Unit ${x.id}`,
    armed: null,
    reach: new Map(),
    hostile: new Set(),
    zone: new Set(),
    passable: true,
    route: lineRoute(u.coord),
    previous: undefined,
    outOfRange: "out of range",
    ...over,
  };
};

describe("autoTap", () => {
  it("selects your own unit before anything else, even when armed", () => {
    expect(autoTap(input({ own: { count: 1, leaving: null }, armed: "barrage" }))).toEqual({ kind: "select" });
  });

  it("adds a waypoint when 'add a stop' is armed", () => {
    expect(autoTap(input({ armed: "waypoint" }))).toEqual({ kind: "waypoint" });
  });

  it("shells a hex in range when barrage is armed, and keeps it armed when out of range", () => {
    const guns = unit({ type: "artillery" });
    expect(autoTap(input({ unit: guns, armed: "barrage", hex: { q: 2, r: 0 } }))).toEqual({
      kind: "order",
      order: { kind: "barrage", target: { q: 2, r: 0 } },
      notice: null,
      armed: null,
    });
    expect(autoTap(input({ unit: guns, armed: "barrage", hex: { q: 9, r: 0 } }))).toEqual({
      kind: "notice",
      text: "out of range",
      armed: "barrage",
    });
  });

  it("barrages a visible hostile in range with deployed artillery, rather than attacking it", () => {
    const guns = unit({ type: "artillery" });
    const enemy = { q: 1, r: 0 };
    const result = autoTap(input({ unit: guns, hex: enemy, hostile: new Set([hexKey(1, 0)]), reach: reachOf([enemy]) }));
    expect(result).toMatchObject({ kind: "order", order: { kind: "barrage", target: enemy } });
  });

  it("moves (or attacks) a deployed unit to a hex in reach", () => {
    const path = [{ q: 1, r: 0 }, { q: 2, r: 0 }];
    expect(autoTap(input({ hex: { q: 2, r: 0 }, reach: reachOf(path) }))).toMatchObject({
      kind: "order",
      order: { kind: "move", path },
      notice: null,
    });
    const attack = autoTap(input({ hex: { q: 2, r: 0 }, reach: reachOf(path), hostile: new Set([hexKey(2, 0)]) }));
    expect(attack).toMatchObject({ kind: "order", order: { kind: "move", path } });
  });

  it("moves a deployed unit inside the contact zone toward a far hex instead of travelling", () => {
    const zone = contactZone([{ q: 2, r: 2 }]);
    const near = [{ q: 1, r: 0 }];
    const nearer = [{ q: 1, r: 0 }, { q: 2, r: 0 }];
    const result = autoTap(input({ hex: { q: 8, r: 0 }, zone, reach: reachOf(near, nearer) }));
    expect(result).toEqual({ kind: "order", order: { kind: "move", path: nearer }, notice: TOWARD_IT, armed: null });
  });

  it("does not move toward a hostile hex, and says so when nothing gets closer", () => {
    const zone = contactZone([{ q: 0, r: 2 }]);
    const ahead = [{ q: 1, r: 0 }];
    const back = [{ q: -1, r: 0 }];
    const result = autoTap(input({ hex: { q: 8, r: 0 }, zone, reach: reachOf(ahead, back), hostile: new Set([hexKey(1, 0)]) }));
    expect(result).toEqual({ kind: "notice", text: NOTHING_CLOSER, armed: null });
  });

  it("treats a deployed unit whose first travel step enters the zone like one inside it", () => {
    // Contact source 4 hexes ahead: the origin is outside the zone, (1,0) is inside.
    const zone = contactZone([{ q: 4, r: 0 }]);
    const step = [{ q: -1, r: 1 }];
    const result = autoTap(input({ hex: { q: 8, r: 0 }, zone, reach: reachOf(step) }));
    expect(result).toEqual({ kind: "notice", text: NOTHING_CLOSER, armed: null });
  });

  it("travels to a clear hex, deploying on arrival", () => {
    expect(autoTap(input())).toEqual({
      kind: "order",
      order: { kind: "travel", destination: { q: 5, r: 0 }, via: [], onArrival: "deployed" },
      notice: null,
      armed: null,
    });
  });

  it("keeps a previous travel's Stay packed choice", () => {
    const column = unit({ posture: "column" });
    const previous = { kind: "travel" as const, destination: { q: 3, r: 0 }, via: [], onArrival: "column" as const };
    expect(autoTap(input({ unit: column, previous }))).toMatchObject({ order: { kind: "travel", onArrival: "column" } });
  });

  it("stops a travel one hex short of known contact, deployed", () => {
    // Contact at (9,0): the zone starts at (6,0), so the column stops at (5,0).
    const zone = contactZone([{ q: 9, r: 0 }]);
    const result = autoTap(input({ unit: unit({ posture: "column" }), hex: { q: 8, r: 0 }, zone }));
    expect(result).toEqual({
      kind: "order",
      order: { kind: "travel", destination: { q: 5, r: 0 }, via: [], onArrival: "deployed" },
      notice: null,
      armed: null,
    });
  });

  it("gives a column that would halt where it stands no order", () => {
    const zone = contactZone([{ q: 2, r: 0 }]);
    expect(autoTap(input({ unit: unit({ posture: "column" }), zone }))).toEqual({
      kind: "notice",
      text: COLUMN_IN_CONTACT,
      armed: null,
    });
  });

  it("says there is no route for an impassable hex or one with no route", () => {
    expect(autoTap(input({ passable: false }))).toEqual({ kind: "notice", text: NO_ROUTE, armed: null });
    expect(autoTap(input({ hex: { q: -4, r: 0 } }))).toEqual({ kind: "notice", text: NO_ROUTE, armed: null });
  });
});

describe("closerMove", () => {
  it("prefers the shorter path among equally close ends", () => {
    const long = [{ q: 0, r: 1 }, { q: 1, r: 1 }];
    const short = [{ q: 1, r: 0 }];
    // Both end 4 hexes from (5,0).
    expect(closerMove({ q: 0, r: 0 }, { q: 5, r: 0 }, reachOf(long, short), new Set())).toEqual(short);
  });
});

describe("holdAllUnits", () => {
  it("holds deployed units with nothing to do, not columns, ordered units or standing travel", () => {
    const idle = unit({ id: 1 });
    const ordered = unit({ id: 2 });
    const travelling = unit({ id: 3 });
    const column = unit({ id: 4, posture: "column" });
    const picked = holdAllUnits(
      [idle, ordered, travelling, column],
      { 2: { kind: "rest" } },
      [{ unit_id: 3, destination: { q: 9, r: 9 }, route: [], on_arrival: "deployed" }],
    );
    expect(picked.map((u) => u.id)).toEqual([1]);
  });
});

describe("tapping a hex one of your units stands on", () => {
  const friend = unit({ id: 2, coord: { q: 1, r: 0 } });
  const there = { q: 1, r: 0 };
  const reach = reachOf([there]);
  const leaving = { count: 1, leaving: friend };

  it("follows a unit that is leaving its hex, with a notice", () => {
    expect(autoTap(input({ hex: there, own: leaving, reach }))).toEqual({
      kind: "order",
      order: { kind: "move", path: [there] },
      notice: followingNotice("Unit 2", "Tap"),
      armed: null,
    });
    expect(followingNotice("2nd Infantry", "Tap")).toBe("Following 2nd Infantry into its hex. Tap again to select 2nd Infantry.");
  });

  it("selects the unit there on the second tap, once the Move ends on its hex", () => {
    expect(autoTap(input({ hex: there, own: leaving, reach, previous: { kind: "move", path: [there] } }))).toEqual({ kind: "select" });
  });

  it("selects a unit that is not leaving, or one out of reach, or when a tap is armed", () => {
    expect(autoTap(input({ hex: there, own: { count: 1, leaving: null }, reach }))).toEqual({ kind: "select" });
    expect(autoTap(input({ hex: there, own: leaving, reach: new Map() }))).toEqual({ kind: "select" });
    expect(autoTap(input({ hex: there, own: leaving, reach, armed: "waypoint" }))).toEqual({ kind: "select" });
  });

  it("selects when the selected unit is a column", () => {
    expect(followTap({ ...input({ hex: there, own: leaving, reach }), unit: unit({ posture: "column" }) }, "Click")).toEqual({
      kind: "select",
    });
  });

  it("orders the unit there on a long-press, as if none of your units stood on it", () => {
    const stays = { count: 1, leaving: null };
    expect(autoTap(input({ hex: there, own: stays, reach, ignoreOwn: true }))).toEqual({
      kind: "order",
      order: { kind: "move", path: [there] },
      notice: null,
      armed: null,
    });
    // Beyond reach: the usual travel rule.
    const far = { q: 5, r: 0 };
    expect(autoTap(input({ hex: far, own: stays, ignoreOwn: true }))).toMatchObject({
      kind: "order",
      order: { kind: "travel", destination: far },
    });
    // On the selected unit's own hex it still selects.
    expect(autoTap(input({ hex: { q: 0, r: 0 }, own: { count: 1, leaving: null }, ignoreOwn: true }))).toEqual({ kind: "select" });
  });
});

describe("ownOnHex", () => {
  const me = unit({ id: 1 });
  const friend = unit({ id: 2, coord: { q: 1, r: 0 } });
  const ctx = (orders: MoveContext["orders"], units: Unit[] = [me, friend]): MoveContext => ({
    units,
    viewer: "p1",
    orders,
    standing: [],
  });

  it("names the lone unit there when it is ordered out of its hex", () => {
    expect(ownOnHex([friend], me.id, ctx({ 2: { kind: "move", path: [{ q: 2, r: 0 }] } }))).toEqual({ count: 1, leaving: friend });
    const travel: WorldOrder = { kind: "travel", destination: { q: 8, r: 0 }, via: [], onArrival: "deployed" };
    expect(ownOnHex([friend], me.id, ctx({ 2: travel })).leaving).toBe(friend);
  });

  it("names nobody for a unit staying put, a column, a stack, or the selected unit itself", () => {
    expect(ownOnHex([friend], me.id, ctx({})).leaving).toBeNull();
    expect(ownOnHex([friend], me.id, ctx({ 2: { kind: "hold" } })).leaving).toBeNull();
    const column = { ...friend, posture: "column" as const };
    expect(ownOnHex([column], me.id, ctx({}, [me, column])).leaving).toBeNull();
    const other = unit({ id: 3, coord: { q: 1, r: 0 }, posture: "column" });
    expect(ownOnHex([friend, other], me.id, ctx({ 2: { kind: "move", path: [{ q: 2, r: 0 }] } }))).toEqual({ count: 2, leaving: null });
    expect(ownOnHex([me], me.id, ctx({ 1: { kind: "move", path: [{ q: 1, r: 1 }] } })).leaving).toBeNull();
  });

  it("names nobody for a unit stepping into the selected unit's own hex: that is half a swap, not a follow", () => {
    // `me` stands at the default coord; the friend's Move steps onto it.
    expect(ownOnHex([friend], me.id, ctx({ 2: { kind: "move", path: [me.coord] } })).leaving).toBeNull();
    // Passing through my hex on the way elsewhere counts the same.
    expect(ownOnHex([friend], me.id, ctx({ 2: { kind: "move", path: [me.coord, { q: -1, r: 0 }] } })).leaving).toBeNull();
  });

  it("names nobody for a unit attacking from where it stands", () => {
    const enemy = unit({ id: 9, owner: "p2", coord: { q: 2, r: 0 } });
    expect(ownOnHex([friend], me.id, ctx({ 2: { kind: "move", path: [{ q: 2, r: 0 }] } }, [me, friend, enemy])).leaving).toBeNull();
  });
});
