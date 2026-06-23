// Client-side mirror of the gameplay constants the planning UI needs to *predict*
// movement (SPEC §3.4 passability, §3.3 road bonus, §5.3 move points). The server
// is the authority — these only drive advisory range/path affordances so a player
// can see where a unit can go before locking. Kept in one place so a stat change
// in `api/app/engine/stats.py` has a single frontend touch-point.

import type { components } from "@/lib/api/schema";

type Terrain = components["schemas"]["Terrain"];
type UnitType = components["schemas"]["UnitType"];

/** Base movement points per unit type (`UNIT_STATS[...].move` in stats.py). */
export const UNIT_MOVE: Record<UnitType, number> = {
  infantry: 2,
  armor: 4,
  artillery: 1,
};

/**
 * Artillery barrage range in hexes (`UNIT_STATS[artillery].attack_range`). Only
 * artillery may barrage (SPEC §7.2); a target hex must be within this distance.
 */
export const ARTILLERY_RANGE = 3;

/** Terrain a unit can never enter (`TERRAIN_STATS[...].passable is False`). */
const IMPASSABLE_TERRAIN: ReadonlySet<Terrain> = new Set<Terrain>(["river"]);

/** Whether a unit may enter a hex of this terrain. */
export function isPassable(terrain: Terrain): boolean {
  return !IMPASSABLE_TERRAIN.has(terrain);
}
