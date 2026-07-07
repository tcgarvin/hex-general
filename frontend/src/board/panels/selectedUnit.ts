// Pure geometry + derivation for the bottom-left selected-unit panel (UI-SPEC §6
// detail + §8 order palette). Kept framework-free so the Pixi `SelectedUnitPanel`
// renderer and the board's input layer share one definition of the palette
// buttons and their hit-boxes — the same split the roster panel uses.

import type { Rect } from "@/board/layout";
import type { components } from "@/lib/api/schema";
import type { StagedOrder } from "@/planning/usePlanning";

type Unit = components["schemas"]["Unit"];

/** A palette action — the order kinds the selected unit can be given (§8). */
export type PaletteAction = "move" | "barrage" | "rest" | "hold" | "clear";

export interface PaletteButton {
  action: PaletteAction;
  label: string;
}

// --- panel geometry (local to the selected-unit rect) ---
export const PANEL_PAD = 12;
/** Square portrait box on the panel's left (UI-SPEC §6 "portrait/icon left"),
 *  inset `PANEL_PAD` from the strip edges: 140px strip − 2×12 = 116px square. */
export const PORTRAIT_X = PANEL_PAD;
export const PORTRAIT_Y = PANEL_PAD;
export const PORTRAIT_SIZE = 116;
/** Left edge of the text + palette column, clear of the portrait. */
export const CONTENT_X = PORTRAIT_X + PORTRAIT_SIZE + 16;
export const BTN_W = 58;
export const BTN_H = 26;
export const BTN_GAP = 6;
/** Top of the palette button row within the panel. */
export const BTN_Y = 104;

/**
 * The palette buttons for the selected unit, in display order (UI-SPEC §8).
 * Infantry/armor: Move · Rest · Hold · Clear. Artillery adds Barrage (it moves
 * *or* barrages, never attacks-by-move). "Attack" is never a button — it's the
 * label a Move takes on when it enters enemy contact.
 */
export function paletteButtons(unit: Unit): PaletteButton[] {
  const buttons: PaletteButton[] = [{ action: "move", label: "Move" }];
  if (unit.type === "artillery") buttons.push({ action: "barrage", label: "Barrage" });
  buttons.push(
    { action: "rest", label: "Rest" },
    { action: "hold", label: "Hold" },
    { action: "clear", label: "Clear" },
  );
  return buttons;
}

/** The panel-local rectangle of the i-th palette button. */
export function paletteButtonRect(index: number): Rect {
  return {
    x: CONTENT_X + index * (BTN_W + BTN_GAP),
    y: BTN_Y,
    width: BTN_W,
    height: BTN_H,
  };
}

/** Hit-test a panel-local point against the palette buttons (null if none). */
export function paletteButtonAt(
  localX: number,
  localY: number,
  unit: Unit,
): PaletteAction | null {
  if (localY < BTN_Y || localY >= BTN_Y + BTN_H) return null;
  const buttons = paletteButtons(unit);
  const offset = localX - CONTENT_X;
  if (offset < 0) return null;
  const stride = BTN_W + BTN_GAP;
  const index = Math.floor(offset / stride);
  if (index < 0 || index >= buttons.length) return null;
  // Reject the gap between buttons.
  if (offset - index * stride >= BTN_W) return null;
  return buttons[index].action;
}

/**
 * The §6 current-order line: the §5.2 label (`label`) plus its target detail —
 * the full planned path for a move, the target hex for a barrage, nothing for
 * rest/hold. `label` is computed by the caller (it needs enemy positions to tell
 * MOVE from ATTACK), so this stays a pure formatter.
 */
export function orderLineText(order: StagedOrder | undefined, label: string): string {
  if (order === undefined) return "—";
  switch (order.kind) {
    case "move": {
      const path = order.path.map((p) => `(${p.q},${p.r})`).join(" → ");
      return path.length === 0 ? label : `${label} → ${path}`;
    }
    case "barrage":
      return `${label} (${order.target.q},${order.target.r})`;
    case "rest":
    case "hold":
      return label;
  }
}
