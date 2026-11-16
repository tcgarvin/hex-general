import { describe, expect, it } from "vitest";

import { hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { NO_ROUTE } from "./autoOrder";
import { desktopClick, type ClickInput } from "./clickOrder";

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

const reachTo = (...hexes: Axial[]) => new Map(hexes.map((h) => [hexKey(h.q, h.r), [h]]));

const click = (over: Partial<ClickInput> = {}): ClickInput => ({
  unit: unit(),
  hex: { q: 5, r: 0 },
  shift: false,
  mode: "select",
  ownHere: 0,
  hostile: new Set(),
  hasTravel: false,
  reach: new Map(),
  freshReach: () => new Map(),
  inRange: false,
  passable: true,
  route: (to) => [to],
  previous: undefined,
  notReachable: "not reachable",
  outOfRange: "out of range",
  ...over,
});

describe("desktopClick", () => {
  it("in select mode, a click on empty ground lets the unit go and selects the hex", () => {
    expect(desktopClick(click())).toEqual({ kind: "inspect" });
  });

  it("in select mode, a click on your own unit selects it", () => {
    expect(desktopClick(click({ ownHere: 1 }))).toEqual({ kind: "select" });
  });

  it("with no unit selected, selects or inspects", () => {
    expect(desktopClick(click({ unit: null }))).toEqual({ kind: "inspect" });
    expect(desktopClick(click({ unit: null, ownHere: 2 }))).toEqual({ kind: "select" });
  });

  it("orders in each ordering mode (the hook then returns to select)", () => {
    const hex = { q: 2, r: 0 };
    expect(desktopClick(click({ mode: "travel", hex }))).toEqual({
      kind: "order",
      order: { kind: "travel", destination: hex, via: [], onArrival: "deployed" },
    });
    expect(desktopClick(click({ mode: "move", hex, reach: reachTo(hex) }))).toEqual({
      kind: "order",
      order: { kind: "move", path: [hex], attack: "assault" },
    });
    expect(desktopClick(click({ mode: "barrage", hex, inRange: true }))).toEqual({
      kind: "order",
      order: { kind: "barrage", target: hex },
    });
  });

  it("orders a Move onto a hostile hex as an engagement, any other Move as an assault", () => {
    const hex = { q: 2, r: 0 };
    const hostile = new Set([hexKey(hex.q, hex.r)]);
    expect(desktopClick(click({ mode: "move", hex, reach: reachTo(hex), hostile }))).toEqual({
      kind: "order",
      order: { kind: "move", path: [hex], attack: "engage" },
    });
    const past = { q: 3, r: 0 };
    expect(desktopClick(click({ mode: "move", hex: past, reach: reachTo(past), hostile }))).toMatchObject({
      order: { attack: "assault" },
    });
  });

  it("keeps a previous travel's arrival posture", () => {
    const previous = { kind: "travel" as const, destination: { q: 9, r: 0 }, via: [], onArrival: "column" as const };
    const result = desktopClick(click({ mode: "travel", previous }));
    expect(result).toMatchObject({ kind: "order", order: { kind: "travel", onArrival: "column" } });
  });

  it("gives a notice (keeping the mode) when nothing can be staged", () => {
    expect(desktopClick(click({ mode: "move" }))).toEqual({ kind: "notice", text: "not reachable" });
    expect(desktopClick(click({ mode: "barrage" }))).toEqual({ kind: "notice", text: "out of range" });
    expect(desktopClick(click({ mode: "travel", route: () => null }))).toEqual({ kind: "notice", text: NO_ROUTE });
    expect(desktopClick(click({ mode: "travel", passable: false }))).toEqual({ kind: "notice", text: NO_ROUTE });
  });

  it("adds a waypoint on Shift+click in select mode only when the unit has a travel", () => {
    expect(desktopClick(click({ shift: true, hasTravel: true }))).toEqual({ kind: "waypoint" });
    expect(desktopClick(click({ shift: true, hasTravel: false }))).toEqual({ kind: "inspect" });
  });

  it("adds a waypoint on Shift+click in Travel mode, travel or not", () => {
    expect(desktopClick(click({ mode: "travel", shift: true }))).toEqual({ kind: "waypoint" });
  });

  it("Shift+click onto another hex of your units moves there when the fresh reach covers it, in any mode", () => {
    const hex = { q: 1, r: 0 };
    for (const mode of ["select", "travel", "move", "barrage"] as const) {
      expect(desktopClick(click({ mode, hex, shift: true, ownHere: 1, freshReach: () => reachTo(hex) }))).toEqual({
        kind: "order",
        order: { kind: "move", path: [hex], attack: "assault" },
      });
    }
  });

  it("Shift+click onto your units out of reach: select in select mode, else the mode's order", () => {
    const hex = { q: 4, r: 0 };
    expect(desktopClick(click({ hex, shift: true, ownHere: 1, hasTravel: true }))).toEqual({ kind: "select" });
    expect(desktopClick(click({ mode: "travel", hex, shift: true, ownHere: 1 }))).toMatchObject({
      kind: "order",
      order: { kind: "travel", destination: hex },
    });
    expect(desktopClick(click({ mode: "move", hex, shift: true, ownHere: 1 }))).toEqual({
      kind: "notice",
      text: "not reachable",
    });
  });

  it("a column's Shift+click onto your units never moves", () => {
    const hex = { q: 1, r: 0 };
    const input = click({ unit: unit({ posture: "column" }), hex, shift: true, ownHere: 1, freshReach: () => reachTo(hex) });
    expect(desktopClick(input)).toEqual({ kind: "select" });
  });
});
