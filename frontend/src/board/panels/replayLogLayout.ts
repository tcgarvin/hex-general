// Geometry + hit-testing for the replay event-log panel (UI-SPEC §13.3). Kept
// separate from ReplayLogPanel.tsx so fast-refresh works and the input layer
// (Board) can measure content / hit-test rows without importing the renderer.

import type { LogBeat } from "@/replay/replayLog";

// --- well + row geometry (must match ReplayLogPanel.tsx) ---
// The log sits in a recessed brass-lipped inset well on the dock wood
// (docs/UI-STYLE.md §4 "Wells"), so the text area is inset from the panel
// edge by the well rim on every side.
/** The well's inset from the panel edges (sides / top / bottom). */
export const WELL_INSET_X = 8;
export const WELL_INSET_TOP = 10;
export const WELL_INSET_BOTTOM = 10;
/** Rows render (and are culled) inside this inset from the panel edge —
 *  the well's rim plus a small margin of its recessed face. */
export const LOG_CLIP_Y = 26;
/** Left margin of the current-beat marker column inside the well. */
export const LOG_PAD_X = 26;
/** Text starts right of the marker column. */
export const LOG_TEXT_X = LOG_PAD_X + 14;
/** Content y=0 renders at this panel-local y when scroll is 0. */
export const LOG_PAD_TOP = LOG_CLIP_Y;
export const HEADER_H = 22;
export const LINE_H = 16;
export const INDENT_X = 14;
/** Vertical gap before each beat header (except the first). */
export const BEAT_GAP = 6;

/** The scrollable viewport height inside the well for a panel of `panelHeight`. */
export function logViewportHeight(panelHeight: number): number {
  return Math.max(0, panelHeight - 2 * LOG_CLIP_Y);
}

/** A measured beat block: its vertical extent within the scrollable content. */
export interface MeasuredBeat {
  beatIndex: number;
  /** Top y of the beat header, in content space (0 = top of content). */
  top: number;
  /** Full height of the header + all its entry lines. */
  height: number;
}

export interface LogMeasure {
  beats: MeasuredBeat[];
  /** Total scrollable content height. */
  contentHeight: number;
}

/** Measure each beat's vertical extent so the panel can scroll and hit-test. */
export function measureReplayLog(logBeats: LogBeat[]): LogMeasure {
  const beats: MeasuredBeat[] = [];
  let y = 0;
  logBeats.forEach((beat, i) => {
    if (i > 0) y += BEAT_GAP;
    const top = y;
    let height = HEADER_H;
    for (const entry of beat.entries) height += entry.lines.length * LINE_H;
    beats.push({ beatIndex: beat.beatIndex, top, height });
    y = top + height;
  });
  return { beats, contentHeight: y };
}

/** The maximum scroll offset for content of `contentHeight` in a `viewportH` panel. */
export function maxScroll(contentHeight: number, viewportH: number): number {
  return Math.max(0, contentHeight - viewportH);
}

/**
 * The beat index at a panel-local y (already net of the panel's own origin),
 * given the current scroll offset, or null when the click misses every beat.
 */
export function beatIndexAtY(
  measure: LogMeasure,
  localY: number,
  scroll: number,
): number | null {
  const contentY = localY - LOG_PAD_TOP + scroll;
  for (const beat of measure.beats) {
    if (contentY >= beat.top && contentY < beat.top + beat.height) return beat.beatIndex;
  }
  return null;
}
