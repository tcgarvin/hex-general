// The roster panel renderer (UI-SPEC §5): docked Pixi chrome on the left edge,
// dense rows of type · (q,r) · order · HP, with unordered units sorted to the
// top so "who still needs orders?" is impossible to miss. Drawn in the ratified
// Field-HQ register (docs/UI-STYLE.md §4), built like the concept's roster: a
// wood frame with brass corner brackets around a dark saddle-leather ground
// with a stitched seam running inside the rail, and on the leather a raw-edged
// parchment page with the UNITS folder tab rising from its top edge — a
// parchment patch bridges the tab's bottom rim over the page's rough top edge
// so tab and page read as one piece of paper. Rows are ink Special Elite
// columns aligned by fixed-x layout (the data font is not fixed-advance).
// Until the chrome sprites arrive it falls back to flat fills at the same
// geometry, mirroring the dock. Row derivation and hit-testing live in
// ./roster; this file is just the scene-graph drawing.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";
import { FONT_DATA, FONT_LABEL, THEME } from "@/theme/theme";

import type { LoadedChrome } from "./chrome";
import { ROW_HEIGHT, TAB_HEIGHT, type RosterRow } from "./roster";

// --- construction geometry (frame → leather → page → tab → columns) ---
/** NineSlice inset covering the outer frame's rails + bracket elbows. */
const FRAME_SLICE = 34;
/** Stitched leather ground: inset past the frame rail so the seam runs
 *  mid-gutter between rail and page (the seam sits ~10px inside the stitch
 *  panel's own edge). */
const STITCH_INSET = 22;
/** NineSlice inset covering the stitch panel's edge-through-seam band. */
const STITCH_SLICE = 24;
/** The parchment page (rough sheet edge included), inset far enough from the
 *  frame rail that the leather gutter and its stitch seam stay visible. */
const PAGE_X = 42;
const PAGE_Y = 56;
const PAGE_BOTTOM_INSET = 42;
/** Rendered width of the page sheet's rough edge band (sheet-tall emit). */
const PAGE_EDGE = 12;
/** NineSlice inset covering the sheet's rough edge band. */
const SHEET_SLICE = 16;
/** Folder tab: rises from the page's top edge onto the leather; its bottom
 *  rim and the page's rough top edge are bridged by a parchment patch. */
const TAB_X = 54;
const TAB_W = 92;
const TAB_Y = 30;
const TAB_H = 40;
/** NineSlice inset covering the tab art's brass rim + rounded shoulders. */
const TAB_SLICE = 26;
/** How far inside the tab's side rims the bridge patch stays. */
const PATCH_INSET = 8;
/** Texture fills emit as 1x mirrored quads (seamless) — tile them unscaled. */
const FACE_TILE_SCALE = 1.0;

// --- column geometry (inside the page face) ---
const COL_MARKER = 66;
const COL_TYPE = 78;
const COL_COORD = 116;
const COL_LABEL = 172;
const PAD_RIGHT = 58;

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
  const pageW = rect.width - 2 * PAGE_X;
  const pageH = rect.height - PAGE_Y - PAGE_BOTTOM_INSET;

  // Flat fills under the ink text until the sprites arrive.
  const drawGroundFallback = (g: Graphics) => {
    g.clear();
    if (chrome !== null) return;
    g.rect(0, 0, rect.width, rect.height).fill({ color: THEME.canvas800 });
    g.rect(0, 0, rect.width, rect.height).stroke({ width: 8, color: THEME.wood900 });
    g.rect(PAGE_X, PAGE_Y, pageW, pageH).fill({ color: THEME.parchment200 });
    g.rect(TAB_X, TAB_Y, TAB_W, PAGE_Y - TAB_Y + PAGE_EDGE).fill({
      color: THEME.parchment200,
    });
  };

  // Paper shadow under the page (and the tab's shoulders), so the sheet reads
  // as lying on the leather rather than painted onto it.
  const drawPageShadow = (g: Graphics) => {
    g.clear();
    if (chrome === null) return;
    for (const { dx, dy, alpha } of [
      { dx: 3, dy: 5, alpha: 0.18 },
      { dx: 1, dy: 2, alpha: 0.22 },
    ]) {
      g.roundRect(PAGE_X + dx, PAGE_Y + dy, pageW, pageH, 8).fill({
        color: THEME.ink900,
        alpha,
      });
      g.roundRect(TAB_X + dx, TAB_Y + dy, TAB_W, TAB_H, 8).fill({
        color: THEME.ink900,
        alpha,
      });
    }
  };

  const drawRowDecor = (g: Graphics) => {
    g.clear();
    const rowX = PAGE_X + PAGE_EDGE - 4;
    const rowW = pageW - 2 * PAGE_EDGE + 8;
    rows.forEach((row, i) => {
      const y = TAB_HEIGHT + i * ROW_HEIGHT;
      if (row.selected) {
        g.rect(rowX, y, rowW, ROW_HEIGHT).fill({
          color: THEME.brass400,
          alpha: 0.35,
        });
        g.rect(rowX, y, rowW, ROW_HEIGHT).stroke({
          width: 1,
          color: THEME.brass600,
        });
      } else if (i === hoverIndex) {
        g.rect(rowX, y, rowW, ROW_HEIGHT).fill({
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
        <>
          <pixiTilingSprite
            texture={chrome.leather}
            width={rect.width}
            height={rect.height}
            tileScale={{ x: FACE_TILE_SCALE, y: FACE_TILE_SCALE }}
          />
          <pixiNineSliceSprite
            texture={chrome.stitchLeatherTall}
            x={STITCH_INSET}
            y={STITCH_INSET}
            width={rect.width - 2 * STITCH_INSET}
            height={rect.height - 2 * STITCH_INSET}
            leftWidth={STITCH_SLICE}
            rightWidth={STITCH_SLICE}
            topHeight={STITCH_SLICE}
            bottomHeight={STITCH_SLICE}
          />
        </>
      )}
      <pixiGraphics draw={drawGroundFallback} />
      <pixiGraphics draw={drawPageShadow} />
      {chrome !== null && (
        <>
          <pixiTilingSprite
            texture={chrome.parchment}
            x={PAGE_X + PAGE_EDGE - 4}
            y={PAGE_Y + PAGE_EDGE - 4}
            width={pageW - 2 * PAGE_EDGE + 8}
            height={pageH - 2 * PAGE_EDGE + 8}
            tileScale={{ x: FACE_TILE_SCALE, y: FACE_TILE_SCALE }}
          />
          <pixiNineSliceSprite
            texture={chrome.sheetTall}
            x={PAGE_X}
            y={PAGE_Y}
            width={pageW}
            height={pageH}
            leftWidth={SHEET_SLICE}
            rightWidth={SHEET_SLICE}
            topHeight={SHEET_SLICE}
            bottomHeight={SHEET_SLICE}
          />
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
            x={TAB_X + PATCH_INSET}
            y={TAB_Y + TAB_H - 8}
            width={TAB_W - 2 * PATCH_INSET}
            height={PAGE_Y + PAGE_EDGE - (TAB_Y + TAB_H) + 12}
            tileScale={{ x: FACE_TILE_SCALE, y: FACE_TILE_SCALE }}
          />
        </>
      )}
      <pixiText
        text="UNITS"
        style={STYLE_TAB}
        anchor={{ x: 0.5, y: 0.5 }}
        x={TAB_X + TAB_W / 2}
        y={TAB_Y + TAB_H / 2 - 2}
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
          texture={chrome.frameLeather}
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
