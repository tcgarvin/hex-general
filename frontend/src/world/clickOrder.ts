// Board clicks on the desktop layout: what one click (or Shift+click) does, given
// the selected unit and the plan mode. Pure, like `autoOrder.ts` for phone taps,
// so the planning hook gathers the inputs and carries out the result.
//
// A selected unit opens in "select" mode: a plain click there never orders. It
// selects your unit on the hex (a stack cycles), or else selects the hex and lets
// the unit go. Travel, Move and Barrage are chosen on the unit panel or with
// T / M / B, and are one shot: a click that stages an order goes back to "select"
// (the unit stays selected), a click that only gives a notice keeps the mode so
// the player can pick again.
//
// The rule, first match wins:
//  1. no unit selected: your unit there selects, any other hex is inspected;
//  2. Shift+click not on another hex of your units, in Travel mode or in "select"
//     mode with a travel to extend: the travel's next waypoint (the mode stays);
//  3. Shift+click on another hex holding your units, the selected unit deployed
//     and this turn's reach covering it: Move there, in any mode (two units
//     ordered into each other's hex swap);
//  4. that Shift+click in "select" mode otherwise: a plain click (select there);
//  5. a hex with your units (not a Shift+click order): select there;
//  6. Barrage: shell the hex if in range, else say it is out of range;
//  7. Move: move there if in reach, else say it is not reachable;
//  8. Travel: travel there (keeping a previous travel's arrival posture) if it
//     has a route, else say there is none;
//  9. "select": inspect the hex.

import { axialEqual, hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { NO_ROUTE } from "./autoOrder";
import type { WorldOrder } from "./plan";

type Unit = components["schemas"]["Unit"];

/** What a board click means while a unit is selected ("select": no order). */
export type PlanMode = "select" | "travel" | "move" | "barrage";

export interface ClickInput {
  /** The selected unit (yours), or `null`. */
  unit: Unit | null;
  hex: Axial;
  shift: boolean;
  mode: PlanMode;
  /** How many of your units stand on the clicked hex. */
  ownHere: number;
  /** The unit has a staged or standing travel a waypoint would join (`travelBase`). */
  hasTravel: boolean;
  /** The move-mode reach (`moveReach`; empty outside Move mode). */
  reach: ReadonlyMap<string, readonly Axial[]>;
  /** A fresh move reach for the unit, ignoring your own units' hexes (`planMoveReach`), for rule 3. */
  freshReach: () => ReadonlyMap<string, readonly Axial[]>;
  /** The hex is within the unit's barrage range. */
  inRange: boolean;
  /** The hex is on the map and passable. */
  passable: boolean;
  /** The column route from the unit to `to` (no waypoints), or `null`: none. */
  route: (to: Axial) => readonly Axial[] | null;
  /** The unit's staged order, if any. */
  previous: WorldOrder | undefined;
  notReachable: string;
  outOfRange: string;
}

export type ClickResult =
  /** Select your unit there (stepping through a stack). */
  | { kind: "select" }
  /** Select the hex and let the unit go. */
  | { kind: "inspect" }
  /** Add the hex as the travel's next waypoint; the mode stays. */
  | { kind: "waypoint" }
  /** Stage the order, then go back to "select" with the unit still selected. */
  | { kind: "order"; order: WorldOrder }
  /** Say why nothing was staged; the mode stays. */
  | { kind: "notice"; text: string };

/** What a desktop board click does (see the header). */
export function desktopClick(input: ClickInput): ClickResult {
  const { unit, hex, shift, mode } = input;
  if (unit === null) return input.ownHere > 0 ? { kind: "select" } : { kind: "inspect" };
  const ordering = shift && input.ownHere > 0 && !axialEqual(hex, unit.coord);
  if (shift && !ordering && (mode === "travel" || (mode === "select" && input.hasTravel))) {
    return { kind: "waypoint" };
  }
  const key = hexKey(hex.q, hex.r);
  if (ordering) {
    // Packing to travel one hex onto a friend is never meant, so this is a Move or nothing.
    const path = unit.posture === "deployed" ? input.freshReach().get(key) : undefined;
    if (path !== undefined) return { kind: "order", order: { kind: "move", path: [...path] } };
    if (mode === "select") return { kind: "select" };
  } else if (input.ownHere > 0) {
    return { kind: "select" };
  }
  switch (mode) {
    case "barrage":
      return input.inRange ? { kind: "order", order: { kind: "barrage", target: hex } } : { kind: "notice", text: input.outOfRange };
    case "move": {
      const path = input.reach.get(key);
      return path !== undefined ? { kind: "order", order: { kind: "move", path: [...path] } } : { kind: "notice", text: input.notReachable };
    }
    case "travel": {
      if (!input.passable || input.route(hex) === null) return { kind: "notice", text: NO_ROUTE };
      const onArrival = input.previous?.kind === "travel" ? input.previous.onArrival : "deployed";
      return { kind: "order", order: { kind: "travel", destination: hex, via: [], onArrival } };
    }
    case "select":
      return { kind: "inspect" };
  }
}
