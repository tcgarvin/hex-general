// Relief in place and friendly swaps (WORLD_SPEC §4.7), as the planner orders them.
//
// The engine (`api/app/engine/movement.py`, `_resolve_follow_chains` then
// `_friendly_cycles`) lets two friendly deployed units that step into each
// other's hex in the same pulse swap instead of jamming. So a swap is simply two
// one-hex Move orders, each into the other's hex: both step on pulse 1. ZOC still
// applies to each step (`_classify`): a unit that starts the pulse next to a
// hostile deployed unit may not step into another hex next to one, so when both
// hexes touch the enemy neither step is legal and the swap can't be ordered.

import { axialEqual, axialNeighbors, hexKey, isAdjacent } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import type { WorldOrder } from "./plan";

type Unit = components["schemas"]["Unit"];

/** Hexes next to a visible hostile deployed unit (columns exert no ZOC). */
export function hostileZoc(units: readonly Unit[], viewer: string): Set<string> {
  const zoc = new Set<string>();
  for (const u of units) {
    if (u.owner === viewer || u.posture !== "deployed") continue;
    for (const n of axialNeighbors(u.coord)) zoc.add(hexKey(n.q, n.r));
  }
  return zoc;
}

/** Why `unit` and `friend` can't swap places this turn, or `null` if they can. */
export function swapBlocked(unit: Unit, friend: Unit, zoc: ReadonlySet<string>): string | null {
  if (unit.id === friend.id) return "pick another unit to swap with";
  if (unit.owner !== friend.owner) return "only your own units swap places";
  if (!isAdjacent(unit.coord, friend.coord)) return "units swap only with a neighbouring hex";
  if (unit.posture !== "deployed" || friend.posture !== "deployed") return "only deployed units swap (columns stack instead)";
  if (zoc.has(hexKey(unit.coord.q, unit.coord.r)) && zoc.has(hexKey(friend.coord.q, friend.coord.r))) {
    return "both hexes touch the enemy: an engaged unit can't step into another";
  }
  return null;
}

/** The two Move orders that swap `unit` and `friend` on the first pulse. */
export function swapOrders(unit: Unit, friend: Unit): Record<number, WorldOrder> {
  return {
    [unit.id]: { kind: "move", path: [friend.coord] },
    [friend.id]: { kind: "move", path: [unit.coord] },
  };
}

/**
 * The own unit `unit` swaps with under the plan: the one whose hex its Move
 * steps into first while that unit's Move steps first into `unit`'s hex. Both
 * steps fall on pulse 1, which is when the engine resolves the swap.
 */
export function swapPartner(
  unit: Unit,
  orders: Readonly<Record<number, WorldOrder>>,
  units: readonly Unit[],
): Unit | null {
  const order = orders[unit.id];
  if (order?.kind !== "move" || order.path.length === 0) return null;
  const first = order.path[0];
  const partner = units.find((u) => u.id !== unit.id && u.owner === unit.owner && u.posture === "deployed" && axialEqual(u.coord, first));
  if (partner === undefined) return null;
  const back = orders[partner.id];
  return back?.kind === "move" && back.path.length > 0 && axialEqual(back.path[0], unit.coord) ? partner : null;
}

export type SwapRole = "relieves" | "relieved" | "swaps";

/**
 * How `unit` takes part in a swap with `partner`: it `relieves` the partner when
 * it steps up into a hex touching the enemy from one that doesn't, is `relieved`
 * when it steps back out of one, and otherwise simply `swaps`.
 */
export function swapRole(unit: Unit, partner: Unit, zoc: ReadonlySet<string>): SwapRole {
  const here = zoc.has(hexKey(unit.coord.q, unit.coord.r));
  const there = zoc.has(hexKey(partner.coord.q, partner.coord.r));
  if (there && !here) return "relieves";
  if (here && !there) return "relieved";
  return "swaps";
}

/** Own deployed units next to `unit` that it could swap with (blocked or not). */
export function swapNeighbours(unit: Unit, units: readonly Unit[]): Unit[] {
  if (unit.posture !== "deployed") return [];
  return units.filter(
    (u) => u.id !== unit.id && u.owner === unit.owner && u.posture === "deployed" && isAdjacent(u.coord, unit.coord),
  );
}
