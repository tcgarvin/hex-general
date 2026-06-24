// "Which of my units still need orders?" — the core organizing question of the
// planning UI (UI-SPEC §1). This derives the headline order tally and the idle
// set from a player's units and their staged orders. Traffic-risk / ZOC-locked /
// invalid warning kinds (UI-SPEC §4) join here as the path overlays that detect
// them land; for now `idle` is the one warning we can compute exactly client-side.

import type { components } from "@/lib/api/schema";

import type { StagedOrder } from "./usePlanning";

type Unit = components["schemas"]["Unit"];

export interface OrderTally {
  /** The viewer's own units. */
  total: number;
  /** How many of them have a staged order. */
  ordered: number;
  /** Ids of own units with no staged order (idle ⇒ they hold position). */
  idleUnitIds: number[];
}

/** Tally staged orders across the viewer's units (`ownUnits` is already slot-filtered). */
export function orderTally(
  ownUnits: readonly Unit[],
  stagedByUnit: Readonly<Record<number, StagedOrder>>,
): OrderTally {
  const idleUnitIds: number[] = [];
  let ordered = 0;
  for (const u of ownUnits) {
    if (stagedByUnit[u.id] !== undefined) ordered += 1;
    else idleUnitIds.push(u.id);
  }
  return { total: ownUnits.length, ordered, idleUnitIds };
}
