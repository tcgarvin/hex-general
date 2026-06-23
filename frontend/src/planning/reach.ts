// Pure movement prediction for the planning UI (roadmap Phase 9, SPEC §7.1, §3.3).
// Mirrors the engine's per-turn step budget (`api/app/engine/movement.py`):
//
//   * a unit follows an *exact* path of adjacent, passable hexes;
//   * its step budget is its type's move points, +1 if the origin and **every**
//     path hex carry a road (`Hex.road`, strictly — a bridge is not a road);
//   * the path may not revisit a hex or its own origin.
//
// The engine is still the authority (ZOC stops, stacking, and combat are dynamic
// and resolve at execution — out of scope here); this only constrains what a
// player can draw so the plan reflects where a unit can actually reach this turn.

import { axialEqual, axialNeighbors, hexKey, isAdjacent, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { isPassable, UNIT_MOVE } from "./rules";

type Hex = components["schemas"]["Hex"];
type Unit = components["schemas"]["Unit"];

export type HexIndex = ReadonlyMap<string, Hex>;

/** Whether the origin and every hex in `path` carries a road (off-map ⇒ false). */
function pathIsAllRoad(origin: Axial, path: readonly Axial[], hexes: HexIndex): boolean {
  for (const coord of [origin, ...path]) {
    const hex = hexes.get(hexKey(coord.q, coord.r));
    if (hex === undefined || !hex.road) return false;
  }
  return true;
}

/** This unit's step budget for `path` — move points plus the §3.3 road bonus. */
export function moveBudget(unit: Unit, path: readonly Axial[], hexes: HexIndex): number {
  return UNIT_MOVE[unit.type] + (pathIsAllRoad(unit.coord, path, hexes) ? 1 : 0);
}

/**
 * Whether `next` is a legal extension of `unit`'s current `path` — adjacent to
 * the path's end (the unit's hex when empty), on-map, passable, not revisiting,
 * and within the step budget once added.
 */
export function canExtendPath(
  unit: Unit,
  path: readonly Axial[],
  next: Axial,
  hexes: HexIndex,
): boolean {
  const end = path.length > 0 ? path[path.length - 1] : unit.coord;
  if (!isAdjacent(end, next)) return false;

  const hex = hexes.get(hexKey(next.q, next.r));
  if (hex === undefined || !isPassable(hex.terrain)) return false;

  if (axialEqual(next, unit.coord)) return false;
  if (path.some((step) => axialEqual(step, next))) return false;

  const extended = [...path, next];
  return extended.length <= moveBudget(unit, extended, hexes);
}

/**
 * Every hex this unit can finish a turn on, as `"q,r"` keys. A hex is reachable
 * iff some exact path under {@link canExtendPath} ends there; enumerated by DFS
 * (step budgets are tiny, ≤5, and paths never revisit, so this stays cheap).
 */
export function reachableHexes(unit: Unit, hexes: HexIndex): Set<string> {
  const reached = new Set<string>();

  const explore = (path: Axial[]): void => {
    const from = path.length > 0 ? path[path.length - 1] : unit.coord;
    for (const next of axialNeighbors(from)) {
      if (!canExtendPath(unit, path, next, hexes)) continue;
      reached.add(hexKey(next.q, next.r));
      explore([...path, next]);
    }
  };

  explore([]);
  return reached;
}
