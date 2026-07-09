// Replay event-log panel (UI-SPEC §13.3): the scrollable, beat-grouped log that
// occupies the right portion of the bottom dock during replay, drawn in the
// ratified Field-HQ register (docs/UI-STYLE.md §4): the log sits in a recessed
// brass-lipped inset well on the dock's dark wood slab, its entries in
// typewriter ivory on the well's dark face. Past beats are dimmed, the current
// beat is brass-lit with an ink marker in the margin, future beats are faded.
// Rows that would fall outside the well are culled so nothing overflows onto
// the rim. Until the chrome sprites arrive it falls back to a flat placeholder
// look at the same geometry, mirroring the other panels.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { LogBeat } from "@/replay/replayLog";
import { FONT_DATA, THEME } from "@/theme/theme";

import type { Rect } from "../layout";
import type { LoadedChrome } from "./chrome";
import {
  HEADER_H,
  INDENT_X,
  LINE_H,
  LOG_CLIP_Y,
  LOG_PAD_TOP,
  LOG_PAD_X,
  LOG_TEXT_X,
  WELL_INSET_BOTTOM,
  WELL_INSET_TOP,
  WELL_INSET_X,
  type LogMeasure,
} from "./replayLogLayout";

// --- fallback palette (pre-chrome placeholder, UI-SPEC §14) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;

// NineSlice insets: the well's leather+lip border, in emitted-texture pixels
// (scripts/ui-kit-prep.py).
const WELL_SLICE = 22;

const style = (size: number, fill: number, weight: "normal" | "bold" = "normal") =>
  new TextStyle({ fontFamily: FONT_DATA, fontSize: size, fill, fontWeight: weight });

type BeatState = "past" | "current" | "future";

// Header + body styles and row alpha per beat state: the current beat reads
// brass-lit, past beats settle into the secondary ivory, future beats fade.
const HEADER_STYLE: Record<BeatState, TextStyle> = {
  past: style(12, THEME.ivory400, "bold"),
  current: style(12, THEME.brass400, "bold"),
  future: style(12, THEME.ivory400, "bold"),
};
const BODY_STYLE: Record<BeatState, TextStyle> = {
  past: style(12, THEME.ivory400),
  current: style(12, THEME.ivory100),
  future: style(12, THEME.ivory400),
};
const ROW_ALPHA: Record<BeatState, number> = { past: 0.85, current: 1, future: 0.4 };

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
  alpha: number;
  x: number;
  y: number;
}

/** The scrollable, beat-grouped event log docked at the bottom-right in replay. */
export function ReplayLogPanel({
  rect,
  chrome,
  logBeats,
  measure,
  currentBeat,
  scroll,
}: ReplayLogProps & {
  rect: Rect;
  /** Field-HQ chrome sprites (null until loaded → flat placeholder look). */
  chrome: LoadedChrome | null;
}) {
  // Ground layer: dark wood + a light wash + the brass map-edge seam,
  // continuing the control strip's slab; the fallback keeps the flat look.
  const drawGround = (g: Graphics) => {
    g.clear();
    if (chrome === null) {
      g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
      // Top border, plus a left divider against the control strip.
      g.moveTo(0, 0).lineTo(rect.width, 0).stroke({ width: 1, color: PANEL_BORDER });
      g.moveTo(0, 0).lineTo(0, rect.height).stroke({ width: 1, color: PANEL_BORDER });
      return;
    }
    g.rect(0, 0, rect.width, rect.height).fill({ color: THEME.ink900, alpha: 0.18 });
    g.rect(0, 0, rect.width, 2).fill({ color: THEME.brass600, alpha: 0.7 });
  };

  // Build the visible rows: walk each beat's header + entry lines, convert
  // content y to panel-local y, and keep only rows fully inside the well.
  const rows: RenderRow[] = [];
  let currentHeaderY: number | null = null;
  const toScreenY = (contentY: number) => LOG_PAD_TOP + contentY - scroll;
  const fits = (y: number, h: number) => y >= LOG_CLIP_Y && y + h <= rect.height - LOG_CLIP_Y;

  logBeats.forEach((beat, i) => {
    const measured = measure.beats[i];
    const state = beatStateFor(beat.beatIndex, currentBeat);

    const headerY = toScreenY(measured.top);
    if (fits(headerY, HEADER_H)) {
      if (state === "current") currentHeaderY = headerY;
      rows.push({
        key: `h${beat.beatIndex}`,
        text: beat.label,
        style: HEADER_STYLE[state],
        alpha: ROW_ALPHA[state],
        x: LOG_TEXT_X,
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
            alpha: ROW_ALPHA[state],
            x: LOG_TEXT_X + (line.indent === 1 ? INDENT_X : 0),
            y,
          });
        }
        lineContentY += LINE_H;
      }
    }
  });

  // Brass marker in the margin beside the current beat's header.
  const drawMarker = (g: Graphics) => {
    g.clear();
    if (currentHeaderY === null) return;
    const cy = currentHeaderY + HEADER_H / 2 - 1;
    g.poly([LOG_PAD_X, cy - 5, LOG_PAD_X, cy + 5, LOG_PAD_X + 8, cy]).fill({
      color: chrome === null ? 0xffe066 : THEME.brass400,
    });
  };

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      {chrome !== null && (
        <pixiTilingSprite
          texture={chrome.wood}
          width={rect.width}
          height={rect.height}
          tileScale={{ x: 1.0, y: 1.0 }}
          // Continue the dock's wood grain across the strip's panel seams.
          tilePosition={{ x: -rect.x, y: 0 }}
        />
      )}
      <pixiGraphics draw={drawGround} />
      {chrome !== null && (
        <pixiNineSliceSprite
          texture={chrome.well}
          x={WELL_INSET_X}
          y={WELL_INSET_TOP}
          width={rect.width - 2 * WELL_INSET_X}
          height={rect.height - WELL_INSET_TOP - WELL_INSET_BOTTOM}
          leftWidth={WELL_SLICE}
          rightWidth={WELL_SLICE}
          topHeight={WELL_SLICE}
          bottomHeight={WELL_SLICE}
        />
      )}
      {rows.map((row) => (
        <pixiText
          key={row.key}
          text={row.text}
          style={row.style}
          alpha={row.alpha}
          x={row.x}
          y={row.y}
        />
      ))}
      <pixiGraphics draw={drawMarker} />
    </pixiContainer>
  );
}
