// The roster panel renderer (UI-SPEC §5): docked Pixi chrome on the left edge,
// dense rows of type · (q,r) · order · HP, with unordered units sorted to the
// top so "who still needs orders?" is impossible to miss. Drawn in the ratified
// Field-HQ register (docs/UI-STYLE.md §4): a wood frame with brass corner
// brackets around a parchment page, with a recessed canvas slot along the top
// that the UNITS folder tab rises out of — the slot's lower edge breaks under
// the tab and a parchment patch hides the tab's bottom rim, so tab and page
// read as one continuous piece of paper. Rows are ink Special Elite columns
// aligned by fixed-x layout (the data font is not fixed-advance). Until the
// chrome sprites arrive it falls back to flat fills at the same geometry,
// mirroring the dock. Row derivation and hit-testing live in ./roster; this
// file is just the scene-graph drawing.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";
import { FONT_DATA, FONT_LABEL, THEME } from "@/theme/theme";

import type { LoadedChrome } from "./chrome";
import { ROW_HEIGHT, TAB_HEIGHT, type RosterRow } from "./roster";

// --- construction geometry (frame → page → tab slot → columns) ---
/** NineSlice inset covering the outer frame's rails + bracket elbows. */
const FRAME_SLICE = 34;
/** Recessed canvas slot the folder tab rises from (inside the frame rail). */
const WELL_X = 22;
const WELL_Y = 26;
const WELL_H = 30;
/** Folder tab: overlaps the slot's lower edge; its bottom rim is hidden by a
 *  parchment patch so the tab face flows into the page face. */
const TAB_X = 34;
const TAB_W = 92;
const TAB_Y = 28;
const TAB_H = 36;
/** How far past the tab's sides the slot-edge gap and rim patch extend. */
const TAB_MARGIN = 2;
/** NineSlice inset covering the tab art's brass rim + rounded shoulders. */
const TAB_SLICE = 26;
/** Texture fills emit as 1x mirrored quads (seamless) — tile them unscaled. */
const FACE_TILE_SCALE = 1.0;

// --- column geometry ---
const COL_MARKER = 36;
const COL_TYPE = 46;
const COL_COORD = 84;
const COL_LABEL = 140;
const PAD_RIGHT = 32;

const style = (family: string, size: number, fill: number) =>
  new TextStyle({ fontFamily: family, fontSize: size, fill });

const STYLE_TAB = new TextStyle({
  fontFamily: FONT_LABEL,
  fontSize: 12,
  fill: THEME.ink900,
  fontWeight: "bold",
  letterSpacing: 1,
});
const STYLE_TYPE = style(FONT_DATA, 11, THEME.ink900);
const STYLE_COORD = style(FONT_DATA, 11, THEME.canvas800);
const STYLE_LABEL = style(FONT_DATA, 11, THEME.ink900);
const STYLE_IDLE_LABEL = style(FONT_DATA, 11, THEME.canvas600);
const STYLE_HP = style(FONT_DATA, 11, THEME.canvas800);
const STYLE_WARN_LABEL = style(FONT_DATA, 11, THEME.invalid);
// Idle/warning marks are status lamps: a small amber/red disc (drawn in
// drawRowDecor) under a bold "!" — amber text alone sinks into parchment.
const MARK_LAMP_R = 6;
const STYLE_MARK_IDLE = new TextStyle({
  fontFamily: FONT_LABEL,
  fontSize: 11,
  fill: THEME.ink900,
  fontWeight: "bold",
});
const STYLE_MARK_ORDERED = style(FONT_DATA, 11, THEME.valid);
const STYLE_MARK_WARNING = new TextStyle({
  fontFamily: FONT_LABEL,
  fontSize: 11,
  fill: THEME.ivory100,
  fontWeight: "bold",
});

export interface RosterPanelProps {
  rect: Rect;
  rows: RosterRow[];
  hoverIndex: number | null;
  /** Field-HQ chrome sprites (null until loaded → flat placeholder look). */
  chrome: LoadedChrome | null;
}

/** Docked left-edge roster, drawn into the Pixi scene graph. */
export function RosterPanel({ rect, rows, hoverIndex, chrome }: RosterPanelProps) {
  const wellW = rect.width - 2 * WELL_X;
  const wellBottom = WELL_Y + WELL_H;

  // Flat fills under the ink text until the sprites arrive.
  const drawGroundFallback = (g: Graphics) => {
    g.clear();
    if (chrome !== null) return;
    g.rect(0, 0, rect.width, rect.height).fill({ color: THEME.parchment200 });
    g.rect(0, 0, rect.width, rect.height).stroke({ width: 8, color: THEME.wood900 });
    g.rect(WELL_X, WELL_Y, wellW, WELL_H).fill({ color: THEME.canvas800 });
    g.rect(TAB_X, TAB_Y, TAB_W, wellBottom - TAB_Y).fill({ color: THEME.parchment200 });
  };

  // The recessed slot: darkening wash over the canvas tiling, an inner-shadow
  // band along its top, and an ink edge that breaks under the tab so the tab
  // face flows out of the slot into the page.
  const drawWell = (g: Graphics) => {
    g.clear();
    if (chrome === null) return;
    g.rect(WELL_X, WELL_Y, wellW, WELL_H).fill({ color: THEME.ink900, alpha: 0.28 });
    g.rect(WELL_X, WELL_Y, wellW, 3).fill({ color: THEME.ink900, alpha: 0.3 });
    const edge = { width: 1, color: THEME.ink900, alpha: 0.5 } as const;
    g.moveTo(WELL_X, WELL_Y).lineTo(WELL_X + wellW, WELL_Y).stroke(edge);
    g.moveTo(WELL_X, WELL_Y).lineTo(WELL_X, wellBottom).stroke(edge);
    g.moveTo(WELL_X + wellW, WELL_Y).lineTo(WELL_X + wellW, wellBottom).stroke(edge);
    g.moveTo(WELL_X, wellBottom).lineTo(TAB_X - TAB_MARGIN, wellBottom).stroke(edge);
    g.moveTo(TAB_X + TAB_W + TAB_MARGIN, wellBottom)
      .lineTo(WELL_X + wellW, wellBottom)
      .stroke(edge);
  };

  const drawRowDecor = (g: Graphics) => {
    g.clear();
    rows.forEach((row, i) => {
      const y = TAB_HEIGHT + i * ROW_HEIGHT;
      if (row.selected) {
        g.rect(WELL_X + 2, y, wellW - 4, ROW_HEIGHT).fill({
          color: THEME.brass400,
          alpha: 0.35,
        });
        g.rect(WELL_X + 2, y, wellW - 4, ROW_HEIGHT).stroke({
          width: 1,
          color: THEME.brass600,
        });
      } else if (i === hoverIndex) {
        g.rect(WELL_X + 2, y, wellW - 4, ROW_HEIGHT).fill({
          color: THEME.ink900,
          alpha: 0.07,
        });
      }
      if (row.warning || row.idle) {
        const cy = y + ROW_HEIGHT / 2;
        g.circle(COL_MARKER, cy, MARK_LAMP_R).fill({
          color: row.warning ? THEME.invalid : THEME.warning,
        });
        g.circle(COL_MARKER, cy, MARK_LAMP_R).stroke({
          width: 1,
          color: THEME.ink900,
          alpha: 0.5,
        });
      }
    });
  };

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      {chrome !== null && (
        <pixiTilingSprite
          texture={chrome.parchment}
          width={rect.width}
          height={rect.height}
          tileScale={{ x: FACE_TILE_SCALE, y: FACE_TILE_SCALE }}
        />
      )}
      <pixiGraphics draw={drawGroundFallback} />
      {chrome !== null && (
        <pixiTilingSprite
          texture={chrome.canvas}
          x={WELL_X}
          y={WELL_Y}
          width={wellW}
          height={WELL_H}
          tileScale={{ x: 0.55, y: 0.55 }}
        />
      )}
      <pixiGraphics draw={drawWell} />
      {chrome !== null && (
        <>
          <pixiNineSliceSprite
            texture={chrome.tabActive}
            x={TAB_X}
            y={TAB_Y}
            width={TAB_W}
            height={TAB_H}
            leftWidth={TAB_SLICE}
            rightWidth={TAB_SLICE}
            topHeight={TAB_SLICE}
            bottomHeight={TAB_SLICE}
          />
          <pixiTilingSprite
            texture={chrome.parchment}
            x={TAB_X - TAB_MARGIN}
            y={wellBottom + 1}
            width={TAB_W + 2 * TAB_MARGIN}
            height={TAB_Y + TAB_H - wellBottom + 2}
            tileScale={{ x: FACE_TILE_SCALE, y: FACE_TILE_SCALE }}
          />
        </>
      )}
      <pixiText
        text="UNITS"
        style={STYLE_TAB}
        anchor={{ x: 0.5, y: 0.5 }}
        x={TAB_X + TAB_W / 2}
        y={TAB_Y + (wellBottom - TAB_Y) / 2}
      />
      <pixiGraphics draw={drawRowDecor} />
      {rows.map((row, i) => (
        <RosterRowView
          key={row.unit.id}
          row={row}
          y={TAB_HEIGHT + i * ROW_HEIGHT}
          rectWidth={rect.width}
        />
      ))}
      {chrome !== null && (
        <pixiNineSliceSprite
          texture={chrome.frameRoster}
          width={rect.width}
          height={rect.height}
          leftWidth={FRAME_SLICE}
          rightWidth={FRAME_SLICE}
          topHeight={FRAME_SLICE}
          bottomHeight={FRAME_SLICE}
        />
      )}
    </pixiContainer>
  );
}

/** One roster row's text columns. */
function RosterRowView({
  row,
  y,
  rectWidth,
}: {
  row: RosterRow;
  y: number;
  rectWidth: number;
}) {
  const cy = y + ROW_HEIGHT / 2;
  const markText = row.warning || row.idle ? "!" : "✓";
  const markStyle = row.warning
    ? STYLE_MARK_WARNING
    : row.idle
      ? STYLE_MARK_IDLE
      : STYLE_MARK_ORDERED;
  const labelStyle = row.warning ? STYLE_WARN_LABEL : row.idle ? STYLE_IDLE_LABEL : STYLE_LABEL;
  return (
    <pixiContainer>
      <pixiText
        text={markText}
        style={markStyle}
        anchor={{ x: 0.5, y: 0.5 }}
        x={COL_MARKER}
        y={cy}
      />
      <pixiText text={row.typeLabel} style={STYLE_TYPE} anchor={{ x: 0, y: 0.5 }} x={COL_TYPE} y={cy} />
      <pixiText
        text={`(${row.unit.coord.q},${row.unit.coord.r})`}
        style={STYLE_COORD}
        anchor={{ x: 0, y: 0.5 }}
        x={COL_COORD}
        y={cy}
      />
      <pixiText
        text={row.orderLabel}
        style={labelStyle}
        anchor={{ x: 0, y: 0.5 }}
        x={COL_LABEL}
        y={cy}
      />
      <pixiText
        text={`${row.unit.hp}/10`}
        style={STYLE_HP}
        anchor={{ x: 1, y: 0.5 }}
        x={rectWidth - PAD_RIGHT}
        y={cy}
      />
    </pixiContainer>
  );
}
