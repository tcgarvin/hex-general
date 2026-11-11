import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import { economyOf, forecast } from "./economy";
import {
  buildOption,
  EMPTY_PLAN,
  orderKindsFor,
  planCost,
  planFromSubmission,
  planProblems,
  recruitOptions,
  samePlan,
  toSubmission,
  type Submission,
  type WorldPlan,
} from "./plan";
import { parsePlan } from "./planStore";
import { decodeWorldMap, offsetToAxial } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];
type Unit = WorldState["units"][number];

const S = (manpower = 0, fuel = 0, steel = 0) => ({ manpower, fuel, steel });

// base col 0, city col 1, factory col 2, farm col 3, plain col 4.
const map = decodeWorldMap({
  width: 5,
  height: 1,
  terrain: "cctt.",
  roads: ".....",
  sites: [
    { coord: offsetToAxial(0, 0), kind: "base", garrison: 0, level: 1 },
    { coord: offsetToAxial(1, 0), kind: "city", garrison: 0, level: 1 },
    { coord: offsetToAxial(2, 0), kind: "factory", garrison: 0, level: 1 },
    { coord: offsetToAxial(3, 0), kind: "farm", garrison: 0, level: 1 },
  ],
});

const at = (col: number) => offsetToAxial(col, 0);

function unit(id: number, col: number, extra: Partial<Unit> = {}): Unit {
  return {
    id,
    type: "infantry",
    owner: "p",
    coord: at(col),
    hp: 10,
    facing: "right",
    posture: "deployed",
    disordered: false,
    ...extra,
  };
}

function state({
  stocks = S(10, 10, 10),
  buildings = {},
  construction = null,
  units = [],
}: {
  stocks?: ReturnType<typeof S>;
  buildings?: Record<string, number>;
  construction?: { building: "barracks" | "depot" | "workshop"; level: number; completes_on: number } | null;
  units?: Unit[];
} = {}): WorldState {
  return {
    turn: 5,
    settlements: [0, 1, 2, 3].map((col) => ({ coord: at(col), controlled_by: "p" })),
    units,
    players: [{ id: "p", bank: 0, stocks, buildings, construction }],
    resigned: [],
    standing_orders: [],
    config: {
      unit_cap: 15,
      muster_bank_cap: 20,
      settlement_repair: 3,
      victory: { turn_limit: 100, city_elimination: false, town_points: 1, city_points: 3 },
      economy: {
        site_income: {
          base: S(5, 3, 1),
          city: S(3, 0, 1),
          farm: S(2),
          oil_field: S(0, 3),
          steel_mill: S(0, 0, 2),
          factory: S(0, 0, 1),
        },
        level_income: {
          base: S(),
          city: S(),
          farm: S(1),
          oil_field: S(0, 1),
          steel_mill: S(0, 0, 1),
          factory: S(0, 0, 1),
        },
        recruit_cost: { infantry: S(3), artillery: S(2, 0, 3), armor: S(2, 0, 6) },
        upkeep: { infantry: S(1), artillery: S(1, 1), armor: S(1, 2) },
        buildings: {
          barracks: [
            { cost: S(5, 0, 8), turns: 2 },
            { cost: S(5, 0, 13), turns: 3 },
          ],
          depot: [{ cost: S(0, 0, 12), turns: 2 }],
          workshop: [{ cost: S(4, 0, 10), turns: 3 }],
        },
        storage: [30, 50, 80],
        army: { base: 4, per_barracks: 2, sites_per_point: 4, ceiling: 15 },
      },
    },
  } as WorldState;
}

describe("submission round trip", () => {
  it("projects a staged plan to the wire and back", () => {
    const plan: WorldPlan = {
      orders: {
        3: { kind: "travel", destination: at(4), via: [], onArrival: "column" },
        1: { kind: "hold" },
        2: { kind: "move", path: [at(2)] },
      },
      recruits: { a: { coord: at(1), type: "infantry" } },
      build: "barracks",
      disband: [2, 1],
    };
    const wire = toSubmission(plan, "p");
    expect(wire.orders?.map((o) => o.unit_id)).toEqual([1, 2, 3]);
    expect(wire.orders?.[2]).toEqual({ kind: "travel", unit_id: 3, destination: at(4), via: [], on_arrival: "column" });
    expect(wire.recruitments).toEqual([{ owner: "p", type: "infantry", coord: at(1) }]);
    expect(samePlan(planFromSubmission(wire), plan, "p")).toBe(true);
  });

  it("keeps a travel order's waypoints in order through the wire and storage", () => {
    const plan: WorldPlan = {
      ...EMPTY_PLAN,
      orders: { 4: { kind: "travel", destination: at(4), via: [at(2), at(1)], onArrival: "deployed" } },
    };
    const wire = toSubmission(plan, "p");
    expect(wire.orders?.[0]).toMatchObject({ via: [at(2), at(1)] });
    expect(planFromSubmission(wire).orders[4]).toEqual(plan.orders[4]);
    expect(parsePlan(JSON.stringify(plan))).toEqual(plan);
  });

  it("treats no submission as the empty plan", () => {
    expect(planFromSubmission(null)).toBe(EMPTY_PLAN);
    const empty: Submission = { orders: [], recruitments: [], build: null, disband: [] };
    expect(samePlan(planFromSubmission(empty), EMPTY_PLAN, "p")).toBe(true);
  });
});

describe("orderKindsFor", () => {
  it("offers columns travel, deploy, rest and hold but not move or barrage", () => {
    expect(orderKindsFor(unit(1, 4, { posture: "column", type: "artillery" }))).toEqual([
      "travel",
      "march",
      "deploy",
      "rest",
      "hold",
    ]);
  });

  it("offers barrage only to deployed artillery", () => {
    expect(orderKindsFor(unit(1, 4, { type: "artillery" }))).toContain("barrage");
    expect(orderKindsFor(unit(1, 4))).not.toContain("barrage");
  });
});

describe("base orders", () => {
  it("prices the next level and blocks it while building", () => {
    const economy = economyOf(state());
    expect(buildOption(state(), economy, "p", "barracks")).toMatchObject({ level: 1, cost: S(5, 0, 8), blocked: [] });
    const busy = state({ stocks: S(10, 0, 3), construction: { building: "depot", level: 1, completes_on: 7 } });
    expect(buildOption(busy, economy, "p", "barracks")?.blocked).toEqual(["one building at a time", "needs 5 more steel"]);
    expect(buildOption(state({ buildings: { depot: 1 } }), economy, "p", "depot")).toBeNull();
  });

  it("counts the plan's recruits but not a build it would replace", () => {
    const s = state({ stocks: S(6, 0, 10) });
    const economy = economyOf(s);
    const plan: WorldPlan = { ...EMPTY_PLAN, build: "depot", recruits: { a: { coord: at(0), type: "infantry" } } };
    expect(buildOption(s, economy, "p", "barracks", plan)?.blocked).toEqual(["needs 2 more manpower"]);
  });

  it("lets a build finishing this turn order the level after it", () => {
    const done = state({ construction: { building: "barracks", level: 1, completes_on: 5 } });
    expect(buildOption(done, economyOf(done), "p", "barracks")).toMatchObject({ level: 2, cost: S(5, 0, 13) });
  });

  it("recruits by site kind, needs a workshop for artillery and an empty hex", () => {
    const s = state();
    const economy = economyOf(s);
    const types = (col: number, st = s) => recruitOptions(st, map, economy, "p", at(col));
    expect(types(0).map((o) => [o.type, o.blocked])).toEqual([
      ["infantry", []],
      ["artillery", ["needs a workshop"]],
    ]);
    expect(types(2).map((o) => o.type)).toEqual(["armor"]);
    expect(types(3)).toEqual([]); // farms don't recruit
    expect(types(4)).toEqual([]); // no site
    const occupied = state({ units: [unit(1, 1)] });
    expect(types(1, occupied)[0].blocked).toEqual(["a unit stands on the hex"]);
  });

  it("lists every reason a recruit is blocked", () => {
    const s = state({ stocks: S(1, 0, 0), units: [unit(1, 0), unit(2, 4), unit(3, 4), unit(4, 4)] });
    const options = recruitOptions(s, map, economyOf(s), "p", at(0), EMPTY_PLAN, 4);
    expect(options.map((o) => o.blocked)).toEqual([
      ["a unit stands on the hex", "army is full (4)", "needs 2 more manpower"],
      ["needs a workshop", "a unit stands on the hex", "army is full (4)", "needs 1 more manpower and 3 more steel"],
    ]);
  });

  it("counts a barracks or workshop finishing this turn, as the engine does", () => {
    // Four units fill an army of 4. The barracks finishes with this turn (5):
    // the economy phase completes it before recruits appear, so the army is 6.
    const full = [unit(1, 4), unit(2, 4), unit(3, 4), unit(4, 4)];
    const finishing = state({ units: full, construction: { building: "barracks", level: 1, completes_on: 5 } });
    const size = forecast(finishing, map, "p").armySize;
    expect(size).toBe(6);
    expect(recruitOptions(finishing, map, economyOf(finishing), "p", at(0), EMPTY_PLAN, size)[0].blocked).toEqual([]);
    const plan: WorldPlan = { ...EMPTY_PLAN, recruits: { a: { coord: at(0), type: "infantry" } } };
    expect(planProblems(plan, finishing, economyOf(finishing), "p", size)).toEqual([]);

    // One that finishes next turn does not count yet, and the reason says when it will.
    const later = state({ units: full, construction: { building: "barracks", level: 1, completes_on: 6 } });
    const laterSize = forecast(later, map, "p").armySize;
    expect(laterSize).toBe(4);
    expect(recruitOptions(later, map, economyOf(later), "p", at(0), EMPTY_PLAN, laterSize)[0].blocked).toEqual([
      "army is full (4); barracks 1 raises it to 6 from turn 6",
    ]);

    const workshop = state({ construction: { building: "workshop", level: 1, completes_on: 5 } });
    const guns = recruitOptions(workshop, map, economyOf(workshop), "p", at(0));
    expect(guns.find((o) => o.type === "artillery")?.blocked).toEqual([]);
    const slowWorkshop = state({ construction: { building: "workshop", level: 1, completes_on: 7 } });
    expect(
      recruitOptions(slowWorkshop, map, economyOf(slowWorkshop), "p", at(0)).find((o) => o.type === "artillery")?.blocked,
    ).toEqual(["needs a workshop (ready from turn 7)"]);
  });

  it("lets a recruit staged here be swapped without counting itself", () => {
    const s = state({ stocks: S(3, 0, 0), units: [unit(1, 4), unit(2, 4), unit(3, 4)] });
    const plan: WorldPlan = { ...EMPTY_PLAN, recruits: { [`${at(0).q},${at(0).r}`]: { coord: at(0), type: "infantry" } } };
    expect(recruitOptions(s, map, economyOf(s), "p", at(0), plan, 4)[0].blocked).toEqual([]);
  });

  it("totals the plan's spending and flags overspending and army size", () => {
    const s = state({ stocks: S(10, 0, 10), units: [unit(1, 4), unit(2, 4), unit(3, 4)] });
    const economy = economyOf(s);
    const plan: WorldPlan = {
      ...EMPTY_PLAN,
      build: "barracks",
      recruits: { a: { coord: at(0), type: "infantry" }, b: { coord: at(2), type: "armor" } },
    };
    expect(planCost(plan, s, economy, "p")).toEqual(S(10, 0, 14));
    expect(planProblems(plan, s, economy, "p", 4)).toEqual([
      "The build and recruits cost more than your stocks.",
      "Recruiting takes the army past its size of 4.",
    ]);
  });
});
