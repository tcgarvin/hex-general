import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import { contactZone, inView } from "../contact";
import type { PlannedRoute } from "../useWorldPlan";
import { arrivalText, edgeOfContact, unitReadout, type ReadoutInput } from "./readout";

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

const route = (over: Partial<PlannedRoute> = {}): PlannedRoute => ({
  unitId: 1,
  from: { q: 0, r: 0 },
  path: [{ q: 1, r: 0 }],
  kind: "travel",
  eta: 1,
  waypoints: [],
  attack: null,
  halt: null,
  fog: inView([{ q: 1, r: 0 }]),
  ...over,
});

const input = (over: Partial<ReadoutInput> = {}): ReadoutInput => ({
  unit: unit(),
  order: undefined,
  route: undefined,
  swap: null,
  partnerName: null,
  expected: null,
  blocked: null,
  warning: null,
  zone: new Set(),
  armed: null,
  ...over,
});

describe("unitReadout", () => {
  it("prompts a unit with no orders by posture", () => {
    expect(unitReadout(input()).lines[0].text).toMatch(/lit hex to move/);
    expect(unitReadout(input({ unit: unit({ posture: "column" }) })).lines[0].text).toMatch(/Deploy to unpack/);
    expect(unitReadout(input({ unit: unit({ type: "artillery" }) })).lines[0].text).toMatch(/shell it/);
  });

  it("names a travel's destination and arrival, and the stop short of contact", () => {
    const zone = contactZone([{ q: 9, r: 0 }]);
    const order = { kind: "travel" as const, destination: { q: 5, r: 0 }, via: [], onArrival: "deployed" as const };
    const read = unitReadout(input({ order, route: route({ eta: 3 }), zone }));
    expect(read.title).toBe("Travel to (5,0) · arrives in 3 turns");
    expect(read.lines[0].text).toBe("Stops at (5,0), one hex short of contact, and deploys in order.");
  });

  it("says where a travel halts, disordered", () => {
    const order = { kind: "travel" as const, destination: { q: 5, r: 0 }, via: [], onArrival: "deployed" as const };
    const read = unitReadout(input({ order, route: route({ halt: { q: 2, r: 0 } }) }));
    expect(read.lines).toContainEqual({ text: "Halts on contact at (2,0), disordered.", tone: "bad" });
  });

  it("gives an attack its expected result", () => {
    const order = { kind: "move" as const, path: [{ q: 1, r: 0 }] };
    const expected = { title: "Attack", lines: ["You deal 4, take 2."], note: "If it stays put." };
    const read = unitReadout(input({ order, route: route({ kind: "move", attack: { q: 1, r: 0 } }), expected }));
    expect(read.kind).toBe("attack");
    expect(read.title).toBe("Attack (1,0)");
    expect(read.lines.map((l) => l.text)).toEqual(["You deal 4, take 2.", "If it stays put."]);
  });

  it("leads a Move your own units will block with the warning", () => {
    const order = { kind: "move" as const, path: [{ q: 1, r: 0 }] };
    const read = unitReadout(input({ order, route: route({ kind: "move" }), blocked: "Blocked unless 2nd Infantry moves out of the way first" }));
    expect(read.title).toBe("Move to (1,0)");
    expect(read.lines[0]).toEqual({ text: "Blocked unless 2nd Infantry moves out of the way first.", tone: "bad" });
  });

  it("asks for a tap when armed", () => {
    expect(unitReadout(input({ armed: "barrage" })).title).toBe("Barrage");
    expect(unitReadout(input({ armed: "waypoint" })).lines[0].text).toMatch(/0 of 4 used/);
  });
});

describe("helpers", () => {
  it("words the arrival", () => {
    expect(arrivalText(1)).toBe("arrives this turn");
    expect(arrivalText(4)).toBe("arrives in 4 turns");
  });

  it("finds the edge of the contact zone", () => {
    const zone = contactZone([{ q: 9, r: 0 }]);
    expect(edgeOfContact({ q: 5, r: 0 }, zone)).toBe(true);
    expect(edgeOfContact({ q: 2, r: 0 }, zone)).toBe(false);
    expect(edgeOfContact({ q: 7, r: 0 }, zone)).toBe(false);
  });
});
