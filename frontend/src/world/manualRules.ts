// Rules the field manual quotes that the client has no other copy of. Everything else
// it shows comes from the world's own config (`economyOf`, `state.config`) or from the
// planner's existing mirrors (`UNIT_STATS`, `COLUMN_RULES`, `DEPLOY_DISTANCE`).
// `manualRules.test.ts` reads each number back out of the engine's Python source, so
// a rule change there fails the test until this file follows.

import type { components } from "@/lib/api/schema";

type UnitType = components["schemas"]["UnitType"];

export const MANUAL_RULES = {
  /** `ColumnRules.stack_cap` (api/app/engine/stats.py): friendly columns per hex. */
  stackCap: 6,
  /** `ColumnRules.column_defense_penalty`: a packed column's defence loss. */
  columnDefensePenalty: 3,
  /** `ColumnRules.disorder_defense_penalty`: a disordered unit's defence loss. */
  disorderDefensePenalty: 2,
  /** `ColumnRules.spill_radius`: how far a deploying column looks for a free hex. */
  spillRadius: 2,
  /** `defender_modifiers` in api/app/engine/combat.py: Hold's defence bonus. */
  holdBonus: 1,
  /** `_MIN_DAMAGE` / `_MAX_DAMAGE` in api/app/engine/combat.py. */
  damageMin: 1,
  damageMax: 5,
  /** `_REST_ANYWHERE` in api/app/engine/repair.py: (HP a turn, ceiling) off settlement ground. */
  restAnywhere: { heal: 1, ceiling: 7 },
  /** `_move_budget` in api/app/engine/movement.py: extra steps for an all-road path. */
  roadBonus: 1,
  /** `TERRAIN_STATS[...].defense_bonus` in api/app/engine/stats.py (town and city share
   * `SETTLEMENT_DEFENSE_BONUS`, which a founded site's hex also gives). */
  terrainDefense: { plains: 0, woods: 2, hills: 1, town: 2, city: 2, bridge: 0 },
  settlementDefense: 2,
  /** `TICK_HOURS` / `TICK_ZONE` in api/app/world/clock.py: a "twice_daily" world's turns. */
  tickTimes: "01:00 and 13:00 New York time",
} as const;

/**
 * `GARRISON_FORCES` in api/app/engine/economy_rules.py: what defends a neutral site at
 * each tier, the first unit on the site and the rest beside it, as (type, HP).
 */
export const GARRISON_FORCES: Readonly<Record<number, readonly (readonly [UnitType, number])[]>> = {
  1: [["infantry", 5]],
  2: [["infantry", 10]],
  3: [
    ["infantry", 10],
    ["artillery", 10],
  ],
  4: [
    ["infantry", 10],
    ["infantry", 10],
    ["artillery", 10],
  ],
};
