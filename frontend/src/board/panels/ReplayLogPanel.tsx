// Replay event-log panel (UI-SPEC §13.3): the scrollable, beat-grouped log that
// occupies the right portion of the bottom dock during replay. Past beats are
// dimmed, the current beat is highlighted, future beats are greyed. Rows that
// would fall outside the panel are culled so nothing overflows onto the map.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { LogBeat } from "@/replay/replayLog";

import type { Rect } from "../layout";
import {
  HEADER_H,
  INDENT_X,
  LINE_H,
  LOG_PAD_TOP,
  LOG_PAD_X,
  type LogMeasure,
} from "./replayLogLayout";

// --- palette (mirrors ReplayControlPanel: UI-SPEC §14) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
const TEXT_PRIMARY = 0xe8e4dc;
const TEXT_SECONDARY = 0x9aa0a6;
const TEXT_DISABLED = 0x4a5060;
const TEXT_BEAT = 0xffe066;

const style = (size: number, fill: number, weight: "normal" | "bold" = "normal") =>
  new TextStyle({ fontFamily: "monospace", fontSize: size, fill, fontWeight: weight });

type BeatState = "past" | "current" | "future";

// Header + body styles per beat state.
const HEADER_STYLE: Record<BeatState, TextStyle> = {
  past: style(12, TEXT_SECONDARY, "bold"),
  current: style(12, TEXT_BEAT, "bold"),
  future: style(12, TEXT_DISABLED, "bold"),
};
const BODY_STYLE: Record<BeatState, TextStyle> = {
  past: style(12, TEXT_SECONDARY),
  current: style(12, TEXT_PRIMARY),
  future: style(12, TEXT_DISABLED),
};
const HEADER_GLYPH: Record<BeatState, string> = { past: "▸", current: "▶", future: "░" };

export interface ReplayLogProps {
  logBeats: LogBeat[];
  measure: LogMeasure;
  /** -1 = pre-turn, 0..N-1 = resolved beat. */
  currentBeat: number;
  /** Scroll offset in content pixels (0 = top). */
  scroll: number;
}

function beatStateFor(beatIndex: number, currentBeat: number): BeatState {
  if (beatIndex < currentBeat) return "past";
  if (beatIndex === currentBeat) return "current";
  return "future";
}

/** One text row positioned in panel-local space, after scroll + culling. */
interface RenderRow {
  key: string;
  text: string;
  style: TextStyle;
  x: number;
  y: number;
}

/** The scrollable, beat-grouped event log docked at the bottom-right in replay. */
export function ReplayLogPanel({
  rect,
  logBeats,
  measure,
  currentBeat,
  scroll,
}: ReplayLogProps & { rect: Rect }) {
  const drawChrome = (g: Graphics) => {
    g.clear();
    g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
    // Top border, plus a left divider against the control strip.
    g.moveTo(0, 0).lineTo(rect.width, 0).stroke({ width: 1, color: PANEL_BORDER });
    g.moveTo(0, 0).lineTo(0, rect.height).stroke({ width: 1, color: PANEL_BORDER });
  };

  // Build the visible rows: walk each beat's header + entry lines, convert
  // content y to panel-local y, and keep only rows fully inside the panel.
  const rows: RenderRow[] = [];
  const toScreenY = (contentY: number) => LOG_PAD_TOP + contentY - scroll;
  const fits = (y: number, h: number) => y >= 0 && y + h <= rect.height;

  logBeats.forEach((beat, i) => {
    const measured = measure.beats[i];
    const state = beatStateFor(beat.beatIndex, currentBeat);

    const headerY = toScreenY(measured.top);
    if (fits(headerY, HEADER_H)) {
      rows.push({
        key: `h${beat.beatIndex}`,
        text: `${HEADER_GLYPH[state]} ${beat.label}`,
        style: HEADER_STYLE[state],
        x: LOG_PAD_X,
        y: headerY,
      });
    }

    let lineContentY = measured.top + HEADER_H;
    for (let e = 0; e < beat.entries.length; e++) {
      const entry = beat.entries[e];
      for (let l = 0; l < entry.lines.length; l++) {
        const line = entry.lines[l];
        const y = toScreenY(lineContentY);
        if (fits(y, LINE_H)) {
          rows.push({
            key: `b${beat.beatIndex}-${e}-${l}`,
            text: line.text,
            style: BODY_STYLE[state],
            x: LOG_PAD_X + (line.indent === 1 ? INDENT_X : 0),
            y,
          });
        }
        lineContentY += LINE_H;
      }
    }
  });

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      <pixiGraphics draw={drawChrome} />
      {rows.map((row) => (
        <pixiText key={row.key} text={row.text} style={row.style} x={row.x} y={row.y} />
      ))}
    </pixiContainer>
  );
}
