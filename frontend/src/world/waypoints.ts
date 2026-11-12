// Waypoints on a Travel order: hexes the route must pass through, in order, so a
// column can be steered around a garrison or along a chosen road (WORLD_SPEC §5).
// Shift+click adds one to the selected unit's travel; the engine plans each leg
// between stops separately (`findRoute`). These checks mirror the server's
// (`_validate_travel` and `TravelOrder.via` in the API) so a bad waypoint is
// refused at the click with a reason rather than by the submit.

import { axialEqual, type Axial } from "@/board/hex";
import { isPassable } from "@/planning/rules";

import type { WorldMap } from "./worldMap";

/** A column router: `findRoute` over the map's costs (or a cached one). */
export type RouteFn = (from: Axial, to: Axial, via: readonly Axial[]) => Axial[] | null;

/** `TravelOrder.via`'s `max_length` in api/app/engine/models.py. */
export const MAX_WAYPOINTS = 4;

/** A hex as the planner writes it in prose: "(q,r)". */
export const at = (a: Axial) => `(${a.q},${a.r})`;

/**
 * The waypoints after adding `hex` as the last one, with the route through them,
 * or why it can't be added.
 */
export function addWaypoint(
  route: RouteFn,
  map: WorldMap,
  from: Axial,
  travel: { destination: Axial; via: readonly Axial[] },
  hex: Axial,
): { via: Axial[]; route: Axial[] } | { error: string } {
  if (travel.via.length >= MAX_WAYPOINTS) return { error: `At most ${MAX_WAYPOINTS} waypoints on one Travel order` };
  const cell = map.hexAt(hex.q, hex.r);
  if (cell === undefined) return { error: `${at(hex)} is off the map` };
  if (!isPassable(cell.terrain)) return { error: `A waypoint must be on land: ${at(hex)} is ${cell.terrain}` };
  if (axialEqual(hex, from)) return { error: "The unit already stands there" };
  if (axialEqual(hex, travel.destination)) return { error: "That is the destination" };
  if (travel.via.some((w) => axialEqual(w, hex))) return { error: `${at(hex)} is already a waypoint` };
  const via = [...travel.via, hex];
  const path = route(from, travel.destination, via);
  if (path === null) return { error: `No route through ${at(hex)}: it can't be reached by land` };
  return { via, route: path };
}

/** The waypoints without the one at `index`. */
export function removeWaypoint(via: readonly Axial[], index: number): Axial[] {
  return via.filter((_, k) => k !== index);
}
