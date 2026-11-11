// Pure movement prediction for the planning UI (roadmap Phase 9, SPEC §7.1, §3.3).
// Mirrors the engine's per-turn step budget (`api/app/engine/movement.py`):
//
//   * a unit follows an *exact* path of adjacent, passable hexes;
//   * its step budget is its type's move points, +1 if the origin and **every**
//     path hex carry a road (`Hex.road`, strictly — a bridge is not a road);
//   * the path may not revisit a hex or its own origin.
//
// The engine is still the authority (combat and other units' moves are dynamic
// and resolve at execution); this only constrains what a player can draw so the
// plan reflects where a unit can actually reach this turn. Callers that know
// the board pass `StepRules` for occupancy and ZOC (the world planner builds
// them in `world/moveRules.ts`).

import {
  axialEqual,
  axialNeighbors,
  hexKey,
  isAdjacent,
  type Axial,
  type Hex,
} from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { isPassable, UNIT_MOVE } from "./rules";

type Unit = components["schemas"]["Unit"];

export type HexIndex = ReadonlyMap<string, Hex>;

/** Occupancy and ZOC limits on a path, on top of terrain, adjacency and budget. */
export interface StepRules {
  /** Whether the unit may step from `head` into `next` as step number `step` (1-based). */
  canEnter(head: Axial, next: Axial, step: number): boolean;
  /** Whether a path may carry on past `hex` (an attack target ends the path). */
  canPass(hex: Axial): boolean;
  /** Whether a path may end on `hex`. */
  canEnd(hex: Axial): boolean;
}

const OPEN_BOARD: StepRules = { canEnter: () => true, canPass: () => true, canEnd: () => true };

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
 * Every hex this unit can still reach by *continuing* `basePath`, mapped to the
 * full exact path (origin → hex) that gets there. The shortest continuation to
 * each hex wins (BFS), so hovering a hex previews a sensible route and clicking
 * it commits that route; clicking intermediate hexes shapes a multi-waypoint
 * exact path. Hexes already in `basePath` (and the origin) are never revisited.
 *
 * With an empty `basePath` this is the unit's full movement range, each hex
 * carrying the route from the unit to it. `rules` add occupancy: a hex that may
 * be passed but not ended on is explored but not offered, and one that may be
 * ended on but not passed (an attack) is offered but not explored past.
 */
export function reachableContinuations(
  unit: Unit,
  basePath: readonly Axial[],
  hexes: HexIndex,
  rules: StepRules = OPEN_BOARD,
): Map<string, Axial[]> {
  const routes = new Map<string, Axial[]>();
  if (basePath.length > 0 && !rules.canPass(basePath[basePath.length - 1])) return routes;

  // Seed the visited set with everything already committed so BFS only explores
  // genuinely new hexes (canExtendPath also forbids revisits — this just prunes).
  const visited = new Set<string>([hexKey(unit.coord.q, unit.coord.r)]);
  for (const step of basePath) visited.add(hexKey(step.q, step.r));

  const queue: Axial[][] = [[...basePath]];
  while (queue.length > 0) {
    const path = queue.shift()!;
    const head = path.length > 0 ? path[path.length - 1] : unit.coord;
    for (const next of axialNeighbors(head)) {
      const key = hexKey(next.q, next.r);
      if (visited.has(key)) continue;
      if (!canExtendPath(unit, path, next, hexes)) continue;
      // Not marked visited when refused: a hex blocked early (a friend still
      // packing) may open to a longer path that steps into it later.
      if (!rules.canEnter(head, next, path.length + 1)) continue;
      visited.add(key);
      const route = [...path, next];
      if (rules.canEnd(next)) routes.set(key, route);
      if (rules.canPass(next)) queue.push(route);
    }
  }

  return routes;
}
