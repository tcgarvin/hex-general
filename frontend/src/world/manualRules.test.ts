// The field manual's hard-coded rules against the engine's Python source: each number
// in `MANUAL_RULES` must still be what the engine uses.

import { describe, expect, it } from "vitest";

import { UNIT_STATS } from "@/planning/rules";

import clockPy from "../../../api/app/world/clock.py?raw";
import combatPy from "../../../api/app/engine/combat.py?raw";
import economyRulesPy from "../../../api/app/engine/economy_rules.py?raw";
import movementPy from "../../../api/app/engine/movement.py?raw";
import repairPy from "../../../api/app/engine/repair.py?raw";
import statsPy from "../../../api/app/engine/stats.py?raw";
import { DEPLOY_DISTANCE } from "./contact";
import { GARRISON_FORCES, MANUAL_RULES } from "./manualRules";
import { COLUMN_RULES } from "./routing";

/** The single capture group of `pattern` in `source`, as a number. */
function num(source: string, pattern: RegExp): number {
  const m = source.match(pattern);
  if (m === null) throw new Error(`pattern ${pattern} not found`);
  return Number(m[1]);
}

const columnField = (name: string) => num(statsPy, new RegExp(`${name}: int = Field\\(default=(\\d+)`));

describe("field manual rules match the engine", () => {
  it("column rules", () => {
    expect(MANUAL_RULES.stackCap).toBe(columnField("stack_cap"));
    expect(MANUAL_RULES.columnDefensePenalty).toBe(columnField("column_defense_penalty"));
    expect(MANUAL_RULES.disorderDefensePenalty).toBe(columnField("disorder_defense_penalty"));
    expect(MANUAL_RULES.spillRadius).toBe(columnField("spill_radius"));
    expect(DEPLOY_DISTANCE).toBe(columnField("deploy_distance"));
    expect(COLUMN_RULES.pulsesPerTurn).toBe(columnField("pulses_per_turn"));
    expect(COLUMN_RULES.packPulses).toBe(columnField("pack_pulses"));
    expect(COLUMN_RULES.roadStepPulses).toBe(columnField("road_step_pulses"));
    expect(COLUMN_RULES.offroadStepPulses).toBe(columnField("offroad_step_pulses"));
  });

  it("combat, repair and road rules", () => {
    expect(MANUAL_RULES.holdBonus).toBe(num(combatPy, /if has_hold_order:\s*bonus \+= (\d+)/));
    expect(MANUAL_RULES.damageMin).toBe(num(combatPy, /_MIN_DAMAGE = (\d+)/));
    expect(MANUAL_RULES.damageMax).toBe(num(combatPy, /_MAX_DAMAGE = (\d+)/));
    expect(MANUAL_RULES.restAnywhere.heal).toBe(num(repairPy, /_REST_ANYWHERE = \((\d+), \d+\)/));
    expect(MANUAL_RULES.restAnywhere.ceiling).toBe(num(repairPy, /_REST_ANYWHERE = \(\d+, (\d+)\)/));
    expect(MANUAL_RULES.roadBonus).toBe(num(movementPy, /_path_is_all_road\(unit\.coord, path, hexes\):\s*return base \+ (\d+)/));
  });

  it("terrain defence", () => {
    const settlement = num(statsPy, /SETTLEMENT_DEFENSE_BONUS = (\d+)/);
    expect(MANUAL_RULES.settlementDefense).toBe(settlement);
    for (const [terrain, bonus] of Object.entries(MANUAL_RULES.terrainDefense)) {
      const block = statsPy.match(new RegExp(`Terrain\\.${terrain.toUpperCase()}: TerrainStats\\(([^)]*)\\)`));
      if (block === null) throw new Error(`no TERRAIN_STATS entry for ${terrain}`);
      const value = block[1].includes("SETTLEMENT_DEFENSE_BONUS") ? settlement : num(block[1], /defense_bonus=(\d+)/);
      expect(bonus, terrain).toBe(value);
    }
  });

  it("unit stats the manual quotes", () => {
    for (const [type, stats] of Object.entries(UNIT_STATS)) {
      const block = statsPy.match(new RegExp(`UnitType\\.${type.toUpperCase()}: UnitStats\\(([^)]*)\\)`));
      if (block === null) throw new Error(`no UNIT_STATS entry for ${type}`);
      expect(stats.maxHp, type).toBe(num(block[1], /max_hp=(\d+)/));
      expect(stats.move, type).toBe(num(block[1], /move=(\d+)/));
      expect(stats.attack, type).toBe(num(block[1], /attack=(\d+)/));
      expect(stats.defense, type).toBe(num(block[1], /defense=(\d+)/));
      expect(stats.range, type).toBe(num(block[1], /attack_range=(\d+)/));
      expect(stats.vision, type).toBe(num(block[1], /vision=(\d+)/));
    }
  });

  it("garrisons and the clock", () => {
    const block = economyRulesPy.match(/GARRISON_FORCES[^=]*= \{([\s\S]*?)\n\}/);
    if (block === null) throw new Error("no GARRISON_FORCES");
    const tiers: Record<number, [string, number][]> = {};
    for (const line of block[1].matchAll(/(\d+): \((.*)\),?$/gm)) {
      tiers[Number(line[1])] = [...line[2].matchAll(/UnitType\.(\w+), (\d+)/g)].map((m) => [m[1].toLowerCase(), Number(m[2])]);
    }
    expect(tiers).toEqual(Object.fromEntries(Object.entries(GARRISON_FORCES).map(([k, v]) => [k, v.map(([t, hp]) => [t, hp])])));
    expect(clockPy).toContain('TICK_ZONE = ZoneInfo("America/New_York")');
    expect(clockPy).toContain("TICK_HOURS: tuple[int, ...] = (1, 13)");
    expect(MANUAL_RULES.tickTimes).toBe("01:00 and 13:00 New York time");
  });
});
