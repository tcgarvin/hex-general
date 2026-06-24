// Client-side mirror of the gameplay constants the planning UI needs to *predict*
// movement (SPEC §3.4 passability, §3.3 road bonus, §5.3 move points). The server
// is the authority — these only drive advisory range/path affordances so a player
// can see where a unit can go before locking. Kept in one place so a stat change
// in `api/app/engine/stats.py` has a single frontend touch-point.

import type { components } from "@/lib/api/schema";

type Terrain = components["schemas"]["Terrain"];
type UnitType = components["schemas"]["UnitType"];

/** Static per-type combat/economy stats (SPEC §5.3; `UNIT_STATS` in stats.py). */
export interface UnitStats {
  /** Base movement points (before the §3.3 road bonus). */
  move: number;
  attack: number;
  defense: number;
  /** Weapon/attack range in hexes — inf/armor 1, artillery 3 (distinct from vision). */
  range: number;
  /** Sight radius, 2 for every type in v0.1 (SPEC §9.3). */
  vision: number;
  /** Muster drained per turn while alive (SPEC §6.2). */
  upkeep: number;
}

/**
 * The v0.1 unit stat table — the single frontend mirror of `api/app/engine/stats.py`.
 * The server is authoritative; these only drive advisory planning affordances
 * (range/path prediction, the selected-unit panel, the muster forecast).
 */
export const UNIT_STATS: Record<UnitType, UnitStats> = {
  infantry: { move: 2, attack: 4, defense: 5, range: 1, vision: 2, upkeep: 1 },
  armor: { move: 4, attack: 7, defense: 4, range: 1, vision: 2, upkeep: 2 },
  artillery: { move: 1, attack: 5, defense: 2, range: 3, vision: 2, upkeep: 2 },
};

/** Base movement points per unit type (convenience view over {@link UNIT_STATS}). */
export const UNIT_MOVE: Record<UnitType, number> = {
  infantry: UNIT_STATS.infantry.move,
  armor: UNIT_STATS.armor.move,
  artillery: UNIT_STATS.artillery.move,
};

/**
 * Artillery barrage range in hexes (`UNIT_STATS.artillery.range`). Only artillery
 * may barrage (SPEC §7.2); a target hex must be within this distance.
 */
export const ARTILLERY_RANGE = UNIT_STATS.artillery.range;

/** Muster income a controlled settlement yields per turn (SPEC §6.1): town +2, city +3. */
export const SETTLEMENT_INCOME: Partial<Record<Terrain, number>> = {
  town: 2,
  city: 3,
};

/** Stationary-defender terrain defense bonus (SPEC §4.2). */
export const TERRAIN_DEFENSE: Record<Terrain, number> = {
  plains: 0,
  woods: 2,
  hills: 1,
  river: 0,
  bridge: 0,
  town: 2,
  city: 2,
};

/** Terrain that blocks line of sight in v0.1 — only woods (SPEC §9.2). */
export function blocksLineOfSight(terrain: Terrain): boolean {
  return terrain === "woods";
}

/** Terrain a unit can never enter (`TERRAIN_STATS[...].passable is False`). */
const IMPASSABLE_TERRAIN: ReadonlySet<Terrain> = new Set<Terrain>(["river"]);

/** Whether a unit may enter a hex of this terrain. */
export function isPassable(terrain: Terrain): boolean {
  return !IMPASSABLE_TERRAIN.has(terrain);
}
