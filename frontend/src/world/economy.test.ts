import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import { forecast } from "./economy";
import { decodeWorldMap, offsetToAxial } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];

const S = (manpower = 0, fuel = 0, steel = 0) => ({ manpower, fuel, steel });

// A 4×1 map: base at col 0, farm col 1, oil col 2, plain col 3.
const map = decodeWorldMap({
  width: 4,
  height: 1,
  terrain: "ctt.",
  roads: "....",
  sites: [
    { coord: offsetToAxial(0, 0), kind: "base", garrison: 0 },
    { coord: offsetToAxial(1, 0), kind: "farm", garrison: 1 },
    { coord: offsetToAxial(2, 0), kind: "oil_field", garrison: 2 },
  ],
});

function state(held: number[], units: ("infantry" | "armor")[], stocks = S()): WorldState {
  return {
    turn: 3,
    settlements: [0, 1, 2].map((col) => ({
      coord: offsetToAxial(col, 0),
      controlled_by: held.includes(col) ? "p" : null,
    })),
    units: units.map((type, k) => ({
      id: k + 1,
      type,
      owner: "p",
      coord: offsetToAxial(3, 0),
      hp: 10,
      facing: "right" as const,
      posture: "deployed" as const,
      disordered: false,
    })),
    players: [{ id: "p", bank: 0, stocks, buildings: { barracks: 1 } }],
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
        recruit_cost: { infantry: S(3), artillery: S(2, 0, 3), armor: S(2, 0, 6) },
        upkeep: { infantry: S(1), artillery: S(1, 1), armor: S(1, 2) },
        buildings: { barracks: [], depot: [], workshop: [] },
        storage: [30, 50, 80],
        army: { base: 4, per_barracks: 2, sites_per_point: 4, ceiling: 15 },
      },
    },
  } as WorldState;
}

describe("forecast", () => {
  it("adds site income, subtracts upkeep and counts army size", () => {
    const f = forecast(state([0, 1], ["infantry", "armor"], S(10, 10, 10)), map, "p");
    expect(f.income).toEqual(S(7, 3, 1));
    expect(f.upkeep).toEqual(S(2, 2, 0));
    expect(f.next).toEqual(S(15, 11, 11));
    expect(f.armySize).toBe(6); // 4 + 2×1 barracks + 1 site ÷ 4
    expect(f.short).toEqual([]);
  });

  it("flags a resource that will run out", () => {
    const f = forecast(state([1], ["armor"], S(0, 1)), map, "p");
    expect(f.short).toEqual(["fuel"]);
    expect(f.next.fuel).toBe(0);
  });

  it("limits next turn's stocks to storage", () => {
    const f = forecast(state([0, 1, 2], [], S(29, 29, 29)), map, "p");
    expect(f.next).toEqual(S(30, 30, 30));
  });
});
