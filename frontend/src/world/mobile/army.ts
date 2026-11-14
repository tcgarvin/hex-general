// Who in your army has nothing to do this turn, and how the phone's bars say it.

import type { components } from "@/lib/api/schema";

import type { WorldView } from "../useWorld";
import type { WorldPlanning } from "../useWorldPlan";

type Unit = components["schemas"]["Unit"];

/** Your units, those with nothing to do this turn first, each group in id order. */
export function armyOf(view: WorldView, planning: WorldPlanning): { idle: Unit[]; ordered: Unit[] } {
  const own = view.state.units.filter((u) => u.owner === view.your_slot).sort((a, b) => a.id - b.id);
  return { idle: own.filter((u) => !planning.busy(u)), ordered: own.filter((u) => planning.busy(u)) };
}

/** "Hold the 3 without orders" (or "the 2 deployed" when idle columns are left out). */
export function holdAllLabel(holdable: number, idle: number): string {
  return holdable === idle ? `Hold the ${holdable} without orders` : `Hold the ${holdable} deployed without orders`;
}

/** The plate's small line: "no orders yet · 5 idle", "3 orders · 2 idle", "unsent changes". */
export function planStatus(view: WorldView, planning: WorldPlanning): string {
  const { idle } = armyOf(view, planning);
  const orders = Object.keys(planning.plan.orders).length;
  if (planning.submitting) return "sending…";
  if (!planning.dirty && planning.submitted) return "submitted · editable";
  const head = planning.dirty && planning.submitted ? "unsent changes" : orders === 0 ? "no orders yet" : `${orders} order${orders === 1 ? "" : "s"}`;
  return idle.length > 0 ? `${head} · ${idle.length} idle` : head;
}
