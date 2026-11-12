// The planning marks the board draws (`WorldOverlay`), projected from the plan.

import { hexKey } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import type { TurnTrails } from "./turnReport";
import type { WorldPlanning } from "./useWorldPlan";
import type { WorldOverlay } from "./WorldRenderer";

type Unit = components["schemas"]["Unit"];

/**
 * The overlay for the current plan. `highlightUnit` is the unit whose
 * Army-panel row is hovered: its route is drawn as strongly as a selected one.
 */
export function overlayOf(
  planning: WorldPlanning,
  units: readonly Unit[],
  trails: TurnTrails | null,
  highlightUnit: number | null = null,
): WorldOverlay {
  const selectedId = planning.selectedUnit?.id ?? null;
  const barrages = units.flatMap((u) => {
    const order = planning.plan.orders[u.id];
    return order?.kind === "barrage" ? [{ from: u.coord, target: order.target }] : [];
  });
  return {
    selectedUnit: planning.selectedUnit?.coord ?? null,
    selectedHex: planning.selectedUnit === null ? planning.selectedHex : null,
    routes: planning.routes.map((r) => ({
      from: r.from,
      path: r.path,
      kind: r.kind,
      selected: r.unitId === selectedId,
      highlighted: r.unitId === highlightUnit,
      attack: r.attack,
      waypoints: r.waypoints,
      halt: r.halt,
      unscouted: r.fog.unscouted,
    })),
    barrages,
    reach: [...planning.moveReach.values()].map((path) => {
      const hex = path[path.length - 1];
      return { hex, attack: planning.hostile.has(hexKey(hex.q, hex.r)) };
    }),
    barrageRange: planning.barrageRange,
    recruits: Object.values(planning.plan.recruits).map((r) => r.coord),
    lastTurn: trails,
  };
}
