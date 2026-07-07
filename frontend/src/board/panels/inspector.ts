// Content for the terrain / hex inspector panel (UI-SPEC §7): the docked
// top-right panel describing the hovered (or selected) hex. This module is the
// pure text builder — values come from the SPEC §4.2 / §3 mirrors in
// `planning/rules.ts` — so it unit-tests without Pixi; `InspectorPanel.tsx`
// does the drawing.

import type { components } from "@/lib/api/schema";
import {
  blocksLineOfSight,
  isPassable,
  SETTLEMENT_INCOME,
  TERRAIN_DEFENSE,
  UNIT_STATS,
} from "@/planning/rules";

type Hex = components["schemas"]["Hex"];
type Unit = components["schemas"]["Unit"];

/** A settlement's control state on the inspected hex (null owner ⇒ neutral). */
export interface InspectedSettlement {
  owner: string | null;
}

export interface InspectorContent {
  /** Terrain name, upper-cased ("WOODS", "CITY", …). */
  title: string;
  /** The hex's axial coordinate, "(q,r)". */
  coord: string;
  /** Property lines, top to bottom (UI-SPEC §7). */
  lines: string[];
}

const cap = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

/**
 * Build the §7 inspector text for a hex: terrain defense, then either the
 * settlement economy lines (muster / repair / recruit) or the movement line,
 * the special-terrain notes (LOS, river crossing), the road layer, control,
 * and the occupying unit when one is visible (fog keeps hidden enemies out of
 * `GameState.units`, so any occupant passed here is known to the viewer).
 */
export function inspectorContent(
  hex: Hex,
  settlement: InspectedSettlement | null,
  unit: Unit | null,
): InspectorContent {
  const lines: string[] = [];
  lines.push(`Defense +${TERRAIN_DEFENSE[hex.terrain]}`);

  const income = SETTLEMENT_INCOME[hex.terrain];
  if (income !== undefined) {
    // Settlement hex: economy in place of the (always-1) move cost, per the
    // §7 samples. Rest repairs +2 to cap 10 here (SPEC §7.3); only cities
    // recruit (SPEC §6.5).
    lines.push(`Muster +${income}`);
    lines.push("Repair +2 (resting)");
    if (hex.terrain === "city") lines.push("Recruit: yes");
  } else {
    lines.push(isPassable(hex.terrain) ? "Move cost 1" : "Impassable");
  }

  if (hex.terrain === "bridge") lines.push("Passable river crossing");
  if (blocksLineOfSight(hex.terrain)) lines.push("Blocks LOS: yes");

  // Road layer (SPEC §3.3). Settlements only note it when present, keeping
  // their taller economy block within the panel.
  if (income === undefined || hex.road) {
    lines.push(`Road: ${hex.road ? "yes" : "no"}`);
  }

  if (settlement !== null) {
    lines.push(
      `Control: ${settlement.owner === null ? "Neutral" : cap(settlement.owner)}`,
    );
  }

  if (unit !== null) {
    lines.push(
      `Unit: ${cap(unit.owner)} ${cap(unit.type)} ${unit.hp}/${UNIT_STATS[unit.type].maxHp}`,
    );
  }

  return {
    title: hex.terrain.toUpperCase(),
    coord: `(${hex.coord.q},${hex.coord.r})`,
    lines,
  };
}
