// Relief in place and friendly swaps (WORLD_SPEC §4.7), as the planner describes
// them. Read-only: there is no swap order.
//
// The engine (`api/app/engine/movement.py`, `_resolve_follow_chains` then
// `_friendly_cycles`) lets two friendly deployed units that step into each
// other's hex in the same pulse swap instead of jamming. So a swap is simply two
// ordinary Move orders whose first steps go into each other's hex: both step on
// pulse 1. The player gives them like any Move (a long-press, or Shift+click on
// desktop, orders a unit onto a hex one of their units stands on, and
// `planConflicts` in `moveRules.ts` warns while only one half is ordered). This
// module only names what two such orders amount to, for the order text.

import { axialEqual, hexKey } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import type { WorldOrder } from "./plan";

type Unit = components["schemas"]["Unit"];

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
 * when it steps back out of one, and otherwise simply `swaps`. `zoc` is
 * `hostileZoc` (`moveRules.ts`).
 */
export function swapRole(unit: Unit, partner: Unit, zoc: ReadonlySet<string>): SwapRole {
  const here = zoc.has(hexKey(unit.coord.q, unit.coord.r));
  const there = zoc.has(hexKey(partner.coord.q, partner.coord.r));
  if (there && !here) return "relieves";
  if (here && !there) return "relieved";
  return "swaps";
}
