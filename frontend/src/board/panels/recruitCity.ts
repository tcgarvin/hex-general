// Pure geometry + derivation for the bottom-left recruit panel (UI-SPEC §6/§8,
// SPEC §6.5). Mirrors the selected-unit palette split: the Pixi `RecruitPanel`
// renderer and the board's input layer share one definition of the recruit
// buttons, their hit-boxes, and the affordability/cap derivation.

import { hexKey, type Axial } from "@/board/hex";
import type { Rect } from "@/board/layout";
import type { components } from "@/lib/api/schema";
import { RECRUIT_COST, UNIT_TYPES } from "@/planning/rules";
import type { StagedRecruit } from "@/planning/usePlanning";

import { BTN_GAP, BTN_H, BTN_W, BTN_X0, BTN_Y } from "./selectedUnit";

type UnitType = components["schemas"]["UnitType"];

// --- panel geometry (local to the recruit rect) ---
// Field-HQ (docs/UI-STYLE.md §4): the recruit type buttons reuse the order
// palette's sprite footprint and row position (./selectedUnit), so the dock
// keeps one rhythm whichever palette is showing. Labels + costs render on the
// wood beneath the brass faces.
export const RBTN_W = BTN_W;
export const RBTN_H = BTN_H;
export const RBTN_GAP = BTN_GAP;
export const RBTN_X0 = BTN_X0;
export const RBTN_Y = BTN_Y;

export interface RecruitButton {
  type: UnitType;
  label: string;
  cost: number;
}

/** The recruit buttons, one per unit type, in display order (SPEC §5.1). */
export function recruitButtons(): RecruitButton[] {
  return UNIT_TYPES.map((t) => ({ type: t, label: t.toUpperCase(), cost: RECRUIT_COST[t] }));
}

/** The panel-local rectangle of the i-th recruit button. */
export function recruitButtonRect(index: number): Rect {
  return {
    x: RBTN_X0 + index * (RBTN_W + RBTN_GAP),
    y: RBTN_Y,
    width: RBTN_W,
    height: RBTN_H,
  };
}

/** Hit-test a panel-local point against the recruit buttons (null if none). */
export function recruitButtonAt(localX: number, localY: number): UnitType | null {
  if (localY < RBTN_Y || localY >= RBTN_Y + RBTN_H) return null;
  const buttons = recruitButtons();
  const offset = localX - RBTN_X0;
  if (offset < 0) return null;
  const stride = RBTN_W + RBTN_GAP;
  const index = Math.floor(offset / stride);
  if (index < 0 || index >= buttons.length) return null;
  // Reject the gap between buttons.
  if (offset - index * stride >= RBTN_W) return null;
  return buttons[index].type;
}

export interface RecruitAvailability {
  /** Muster left to spend at THIS city (bank minus recruits staged elsewhere). */
  budget: number;
  /** Unit-cap slots left for THIS city (cap minus own units minus recruits elsewhere). */
  slotsLeft: number;
  /** The type currently staged at this city, if any. */
  currentType: UnitType | null;
}

/**
 * What can be recruited at `city`, given the running plan. Recruits staged at
 * *other* cities have already committed their cost and a cap slot, so they shrink
 * this city's budget and slots; the recruit already staged at this city does not
 * (selecting a type there replaces it). This is the same accounting the API's
 * `_validate_recruitments` does — advisory only; the server is authoritative.
 */
export function recruitAvailability(
  city: Axial,
  recruitBySite: Readonly<Record<string, StagedRecruit>>,
  bank: number,
  ownUnitCount: number,
  unitCap: number,
): RecruitAvailability {
  const cityKey = hexKey(city.q, city.r);
  let spentElsewhere = 0;
  let countElsewhere = 0;
  let currentType: UnitType | null = null;
  for (const [key, recruit] of Object.entries(recruitBySite)) {
    if (key === cityKey) {
      currentType = recruit.type;
      continue;
    }
    spentElsewhere += RECRUIT_COST[recruit.type];
    countElsewhere += 1;
  }
  return {
    budget: bank - spentElsewhere,
    slotsLeft: unitCap - ownUnitCount - countElsewhere,
    currentType,
  };
}

/** Whether a recruit button is affordable and within the unit cap at this city. */
export function canRecruit(button: RecruitButton, avail: RecruitAvailability): boolean {
  return button.cost <= avail.budget && avail.slotsLeft >= 1;
}
