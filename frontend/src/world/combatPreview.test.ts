import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import fixture from "./__fixtures__/combat.json";
import {
  attackPhrase,
  barrageStrike,
  forecastAttack,
  forecastBarrage,
  groundDefense,
  melee,
} from "./combatPreview";

type UnitType = components["schemas"]["UnitType"];
type Terrain = components["schemas"]["Terrain"];

// `combat.json` is the real engine's damage over a table of matchups
// (`__fixtures__/combat_fixture.py`); the preview must reproduce every row.
describe("damage parity with the engine", () => {
  const settlementGround = (row: { terrain: string; ground: string }) =>
    row.ground === "site" || row.terrain === "town" || row.terrain === "city";

  it("finds settlement ground and its defence as the engine does", () => {
    for (const row of [...fixture.melee, ...fixture.barrage]) {
      expect(settlementGround(row)).toBe(row.settlement_ground);
    }
  });

  it.each(fixture.melee.map((row, i) => [i, row] as const))("melee row %i", (_, row) => {
    const ground = groundDefense(row.terrain as Terrain, row.settlement_ground);
    const { dealt, taken } = melee(
      row.attackers.map((a) => a.type as UnitType),
      { type: row.defender.type as UnitType, ground, hold: row.defender.hold, disordered: row.defender.disordered },
    );
    expect(dealt.map((s) => s.damage)).toEqual(row.out.dealt);
    expect(taken.map((s) => s.damage)).toEqual(row.out.taken);
    const total = dealt.reduce((sum, s) => sum + s.damage, 0);
    expect(Math.max(0, row.defender.hp - total)).toBe(row.out.defender_hp_after);
    expect(total >= row.defender.hp).toBe(row.out.defender_destroyed);
    expect(row.attackers.map((a, k) => taken[k].damage >= a.hp)).toEqual(row.out.attackers_destroyed);
  });

  it.each(fixture.barrage.map((row, i) => [i, row] as const))("barrage row %i", (_, row) => {
    const ground = groundDefense(row.terrain as Terrain, row.settlement_ground);
    const strikes = row.occupants.map((o) =>
      barrageStrike({ type: o.type as UnitType, ground, hold: o.hold, disordered: o.disordered, column: o.column }),
    );
    expect(strikes.map((s) => s.damage)).toEqual(row.out.map((h) => h.damage));
    expect(row.occupants.map((o, k) => Math.max(0, o.hp - strikes[k].damage))).toEqual(row.out.map((h) => h.hp_after));
  });
});

describe("attack forecast", () => {
  const garrison = {
    type: "infantry" as const,
    hp: 10,
    terrain: "plains" as const,
    settlementGround: true,
    disordered: false,
    column: false,
    hold: "yes" as const,
  };

  it("reads a garrison assault at the 1-damage floor with its modifiers", () => {
    const f = forecastAttack({ type: "infantry", hp: 10 }, garrison);
    expect(f.modifiers).toEqual(["+2 site", "+1 hold"]);
    expect(attackPhrase(f)).toBe("You deal 1 (floor 1), take 1 (floor 1)");
    expect(f.kills).toBe(false);
  });

  it("gives both numbers when another player's unit may or may not hold", () => {
    const f = forecastAttack({ type: "armor", hp: 3 }, { ...garrison, hold: "maybe", settlementGround: false });
    expect(f.modifiers).toEqual(["+1 if it holds"]);
    expect(attackPhrase(f)).toBe("You deal 2 (1 if it holds), take 1 (floor 1)");
  });

  it("treats a column as deploying disordered before the blow", () => {
    const f = forecastAttack({ type: "armor", hp: 10 }, { ...garrison, column: true, settlementGround: false, hp: 2 });
    expect(f.modifiers).toEqual(["+1 hold", "−2 disordered"]);
    expect(f.dealt.damage).toBe(3);
    expect(f.kills).toBe(true);
  });

  it("flags an attacker the return blow would destroy", () => {
    expect(forecastAttack({ type: "artillery", hp: 2 }, garrison).dies).toBe(true);
  });
});

describe("barrage forecast", () => {
  it("strikes the deployed occupant first, then columns at −3 with no ground", () => {
    const base = { terrain: "woods" as const, settlementGround: false, disordered: false, owner: "s2" };
    const [deployed, column] = forecastBarrage([
      { ...base, type: "infantry", hp: 4, column: true, hold: "no" },
      { ...base, type: "infantry", hp: 10, column: false, hold: "maybe" },
    ]);
    expect(deployed.modifiers).toEqual(["+2 woods", "+1 if it holds"]);
    expect(deployed.damage.damage).toBe(1);
    expect(deployed.ifHolds).toBeNull(); // already at the floor
    expect(column.modifiers).toEqual(["−3 packed column"]);
    expect(column.damage.damage).toBe(3);
    expect(column.kills).toBe(false);
  });
});
