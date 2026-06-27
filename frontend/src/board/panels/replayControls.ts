// Hit-testing helpers for the replay control strip (UI-SPEC §13.2). Kept in a
// separate file from ReplayControlPanel so fast-refresh works correctly.

import { BOTTOM_HEIGHT } from "../layout";

// --- button geometry (must match ReplayControlPanel.tsx) ---
export const BTN_W = 44;
export const BTN_H = 40;
export const BTN_Y = Math.round((BOTTOM_HEIGHT - BTN_H) / 2);
export const BTN_GAP = 6;
export const CTRL_PAD = 12;
export const SPEED_W = 44;
export const SKIP_W = 90;
export const START_W = 138;

export const BTN_REWIND_X = CTRL_PAD;
export const BTN_BACK_X = BTN_REWIND_X + BTN_W + BTN_GAP;
export const BTN_PLAY_X = BTN_BACK_X + BTN_W + BTN_GAP;
export const BTN_FWD_X = BTN_PLAY_X + BTN_W + BTN_GAP;
export const SPEED_X = BTN_FWD_X + BTN_W + BTN_GAP * 3;

/** Left edge of the (left-aligned) beat label, just right of the speed toggle. */
export const BEAT_LABEL_X = SPEED_X + SPEED_W + 20;

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
  const hit = (bx: number, bw: number) =>
    localX >= bx &&
    localX < bx + bw &&
    localY >= BTN_Y &&
    localY < BTN_Y + BTN_H;

  if (hit(BTN_REWIND_X, BTN_W)) return "rewind";
  if (hit(BTN_BACK_X, BTN_W)) return "back";
  if (hit(BTN_PLAY_X, BTN_W)) return "play_pause";
  if (hit(BTN_FWD_X, BTN_W)) return "forward";
  if (hit(SPEED_X, SPEED_W)) return "speed";

  if (atFinalBeat) {
    if (hit(panelWidth - CTRL_PAD - START_W, START_W)) return "start_planning";
  } else {
    if (hit(panelWidth - CTRL_PAD - SKIP_W, SKIP_W)) return "skip";
  }

  return null;
}
