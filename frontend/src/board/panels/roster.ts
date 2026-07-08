// Pure roster derivation (UI-SPEC §5): turn the viewer's own units + their staged
// orders into sorted, display-ready rows, and map a roster-local y back to a row.
// Kept framework-free so the input layer and the Pixi `RosterPanel` share it.

import { hexKey } from "@/board/hex";
import type { components } from "@/lib/api/schema";
import type { PlanningController, StagedOrder } from "@/planning/usePlanning";

type Unit = components["schemas"]["Unit"];

/** Row geometry shared by the renderer and hit-testing. TAB_HEIGHT is where
 *  the row list starts: below the frame's top rail, the folder tab, and the
 *  parchment page's rough top edge (UI-STYLE §4 construction). */
export const TAB_HEIGHT = 80;
export const ROW_HEIGHT = 20;

export const TYPE_LABEL: Record<Unit["type"], string> = {
  infantry: "INF",
  armor: "ARM",
  artillery: "ART",
};

/** A derived, display-ready roster row. */
export interface RosterRow {
  unit: Unit;
  typeLabel: string;
  /** UI-SPEC §5.2 label: — / MOVE / ATTACK / BARRAGE / REST / HOLD. */
  orderLabel: string;
  idle: boolean;
  selected: boolean;
  damaged: boolean;
  /** The unit has an `error`-severity advisory problem (ZOC stop / traffic jam). */
  warning: boolean;
}

/** Map a staged order to its display label (UI-SPEC §5.2). `ATTACK` is a derived
 *  label for a Move whose destination is enemy-held — still a Move on the wire. */
export function orderLabelOf(
  order: StagedOrder | undefined,
  enemyHexes: ReadonlySet<string>,
): string {
  if (order === undefined) return "—";
  switch (order.kind) {
    case "move": {
      const dest = order.path[order.path.length - 1];
      const intoEnemy = dest !== undefined && enemyHexes.has(hexKey(dest.q, dest.r));
      return intoEnemy ? "ATTACK" : "MOVE";
    }
    case "barrage":
      return "BARRAGE";
    case "rest":
      return "REST";
    case "hold":
      return "HOLD";
  }
}

/** Sort rank (UI-SPEC §5.1 default sort): unordered first, then warned (a plan
 *  that will misfire needs attention), then selected, then damaged, then the rest. */
function rankOf(row: { idle: boolean; warning: boolean; selected: boolean; damaged: boolean }): number {
  if (row.idle) return 0;
  if (row.warning) return 1;
  if (row.selected) return 2;
  if (row.damaged) return 3;
  return 4;
}

const NO_WARNINGS: ReadonlySet<number> = new Set();

/** Build the sorted roster rows for the viewer's own units. `warnedUnitIds`
 *  carries the §16.1 error-severity problems (ZOC stop / traffic jam). */
export function rosterRows(
  ownUnits: readonly Unit[],
  planning: PlanningController,
  enemyHexes: ReadonlySet<string>,
  warnedUnitIds: ReadonlySet<number> = NO_WARNINGS,
): RosterRow[] {
  const rows = ownUnits.map((unit): RosterRow => {
    const order = planning.stagedByUnit[unit.id];
    return {
      unit,
      typeLabel: TYPE_LABEL[unit.type],
      orderLabel: orderLabelOf(order, enemyHexes),
      idle: order === undefined,
      selected: unit.id === planning.selectedUnitId,
      damaged: unit.hp < 10,
      warning: warnedUnitIds.has(unit.id),
    };
  });
  // Stable sort: rank, with the input order as the implicit tiebreak (Array.sort
  // is stable, and `rows` preserves `ownUnits`' order).
  return rows.sort((a, b) => rankOf(a) - rankOf(b));
}

/** The roster row index at a roster-local y, or null if outside the row list. */
export function rosterRowAt(localY: number, rowCount: number): number | null {
  if (localY < TAB_HEIGHT) return null;
  const i = Math.floor((localY - TAB_HEIGHT) / ROW_HEIGHT);
  return i >= 0 && i < rowCount ? i : null;
}

/** Read-only roster rows for replay mode — own units with no order labels. */
export function replayRosterRows(ownUnits: readonly Unit[]): RosterRow[] {
  return ownUnits.map((unit): RosterRow => ({
    unit,
    typeLabel: TYPE_LABEL[unit.type],
    orderLabel: "—",
    idle: false,
    selected: false,
    damaged: unit.hp < 10,
    warning: false,
  }));
}
