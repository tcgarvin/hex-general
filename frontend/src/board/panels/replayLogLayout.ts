// Geometry + hit-testing for the replay event-log panel (UI-SPEC §13.3). Kept
// separate from ReplayLogPanel.tsx so fast-refresh works and the input layer
// (Board) can measure content / hit-test rows without importing the renderer.

import type { LogBeat } from "@/replay/replayLog";

// --- row geometry (must match ReplayLogPanel.tsx) ---
export const LOG_PAD_X = 12;
export const LOG_PAD_TOP = 8;
export const HEADER_H = 22;
export const LINE_H = 16;
export const INDENT_X = 14;
/** Vertical gap before each beat header (except the first). */
export const BEAT_GAP = 6;

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
