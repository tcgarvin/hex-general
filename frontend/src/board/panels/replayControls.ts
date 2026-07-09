// Geometry + hit-testing helpers for the replay control strip (UI-SPEC §13.2),
// in the ratified Field-HQ register (docs/UI-STYLE.md §4). Kept in a separate
// file from ReplayControlPanel so fast-refresh works correctly and the input
// layer (Board) can hit-test without importing the renderer.

import { BOTTOM_HEIGHT } from "../layout";

// --- button geometry (must match ReplayControlPanel.tsx) ---
// Transport buttons are the kit's square brass icon keycaps (iconbtn emit,
// 127×178 with transparent top/bottom padding): at a 56px sprite width the
// visible screwed-wood plate is ~56px square, starting ~10px below the
// sprite's top edge.
export const BTN_W = 56;
export const BTN_SPRITE_H = 78;
/** The visible plate's offset and height within the sprite footprint. */
export const BTN_PLATE_DY = 10;
export const BTN_PLATE_H = 56;
export const BTN_GAP = 6;
export const CTRL_PAD = 14;
/** Sprite top y — puts the plate in the strip's upper zone, caption below. */
export const BTN_Y = 16;
/** Engraved glyph center within the sprite (fraction of the sprite box). */
export const BTN_FACE_CX = 0.5;
export const BTN_FACE_CY = 0.48;

export const BTN_REWIND_X = CTRL_PAD;
export const BTN_BACK_X = BTN_REWIND_X + BTN_W + BTN_GAP;
export const BTN_PLAY_X = BTN_BACK_X + BTN_W + BTN_GAP;
export const BTN_FWD_X = BTN_PLAY_X + BTN_W + BTN_GAP;
export const SPEED_X = BTN_FWD_X + BTN_W + BTN_GAP * 3;

/** Beat caption (typewriter ink on the wood), centered under the transport row. */
export const BEAT_LABEL_CX = (BTN_REWIND_X + SPEED_X + BTN_W) / 2;
export const BEAT_LABEL_Y = BTN_Y + BTN_SPRITE_H + 14;

/** Wide action plate (SKIP / START PLANNING / SEE RESULTS): the kit's
 *  commit-plate art at the LockPanel's footprint, right-aligned in the strip. */
export const ACTION_W = 138;
export const ACTION_H = 90;
export const ACTION_Y = Math.round((BOTTOM_HEIGHT - ACTION_H) / 2);
/** The brass face's center within the commit sprite (fraction of its height). */
export const ACTION_FACE_CY = 0.5;

export type ReplayAction =
  | "rewind"
  | "back"
  | "play_pause"
  | "forward"
  | "skip"
  | "speed"
  | "start_planning";

/** Hit-test a panel-local point against replay control buttons. */
export function replayButtonAt(
  localX: number,
  localY: number,
  panelWidth: number,
  atFinalBeat: boolean,
): ReplayAction | null {
  // Transport + speed keycaps: the visible plate is the hit target.
  const plate = (bx: number) =>
    localX >= bx &&
    localX < bx + BTN_W &&
    localY >= BTN_Y + BTN_PLATE_DY &&
    localY < BTN_Y + BTN_PLATE_DY + BTN_PLATE_H;

  if (plate(BTN_REWIND_X)) return "rewind";
  if (plate(BTN_BACK_X)) return "back";
  if (plate(BTN_PLAY_X)) return "play_pause";
  if (plate(BTN_FWD_X)) return "forward";
  if (plate(SPEED_X)) return "speed";

  const actionX = panelWidth - CTRL_PAD - ACTION_W;
  if (
    localX >= actionX &&
    localX < actionX + ACTION_W &&
    localY >= ACTION_Y &&
    localY < ACTION_Y + ACTION_H
  ) {
    return atFinalBeat ? "start_planning" : "skip";
  }

  return null;
}
