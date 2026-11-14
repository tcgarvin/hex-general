import { describe, expect, it } from "vitest";

import { hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import {
  autoTap,
  closerMove,
  COLUMN_IN_CONTACT,
  holdAllUnits,
  NO_ROUTE,
  NOTHING_CLOSER,
  TOWARD_IT,
  type AutoTapInput,
} from "./autoOrder";
import { contactZone } from "./contact";

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
    ownHere: false,
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
    expect(autoTap(input({ ownHere: true, armed: "barrage" }))).toEqual({ kind: "select" });
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
