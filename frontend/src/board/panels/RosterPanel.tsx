// The roster panel renderer (UI-SPEC §5): docked Pixi chrome on the left edge,
// dense rows of type · (q,r) · order · HP, with unordered units sorted to the
// top so "who still needs orders?" is impossible to miss. Drawn in the ratified
// Field-HQ register (docs/UI-STYLE.md): a parchment data face under a dark-wood
// tab strip carrying the UNITS folder tab, ink Special Elite columns aligned by
// layout (each column its own Text at a fixed x — the data font is not
// fixed-advance), and a brass seam along the map edge. Until the chrome sprites
// arrive it falls back to flat fills at the same geometry, mirroring the dock.
// Row derivation and hit-testing live in ./roster; this file is just the
// scene-graph drawing.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";
import { FONT_DATA, FONT_LABEL, THEME } from "@/theme/theme";

import type { LoadedChrome } from "./chrome";
import { ROW_HEIGHT, TAB_HEIGHT, type RosterRow } from "./roster";

// --- column geometry ---
const COL_MARKER = 8;
const COL_TYPE = 18;
const COL_COORD = 56;
const COL_LABEL = 118;
const PAD_RIGHT = 12;

// --- tab-strip geometry (folder tab inset in the wood band) ---
const TAB_X = 8;
const TAB_W = 84;
const TAB_PAD_TOP = 5;
/** NineSlice inset covering the tab art's brass rim + rounded shoulders. */
const TAB_SLICE = 26;
/** Texture fills emit as 1x mirrored quads (seamless) — tile them unscaled. */
const FACE_TILE_SCALE = 1.0;
const WOOD_TILE_SCALE = 1.0;

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
  // Flat fills under the ink text until the sprites arrive (and the seams /
  // washes that ride on top of them once they have).
  const drawChrome = (g: Graphics) => {
    g.clear();
    if (chrome === null) {
      g.rect(0, 0, rect.width, rect.height).fill({ color: THEME.parchment200 });
      g.rect(0, 0, rect.width, TAB_HEIGHT).fill({ color: THEME.wood900 });
    }
    // Ink shadow where the face meets the tab strip, then the brass seam
    // along the map edge (same weight as the dock's).
    g.rect(0, TAB_HEIGHT, rect.width, 3).fill({ color: THEME.ink900, alpha: 0.18 });
    g.rect(rect.width - 2, 0, 2, rect.height).fill({ color: THEME.brass600, alpha: 0.7 });
  };

  const drawRowDecor = (g: Graphics) => {
    g.clear();
    rows.forEach((row, i) => {
      const y = TAB_HEIGHT + i * ROW_HEIGHT;
      if (row.selected) {
        g.rect(1, y, rect.width - 3, ROW_HEIGHT).fill({ color: THEME.brass400, alpha: 0.35 });
        g.rect(1, y, rect.width - 3, ROW_HEIGHT).stroke({ width: 1, color: THEME.brass600 });
      } else if (i === hoverIndex) {
        g.rect(1, y, rect.width - 3, ROW_HEIGHT).fill({ color: THEME.ink900, alpha: 0.07 });
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
            texture={chrome.parchment}
            width={rect.width}
            height={rect.height}
            tileScale={{ x: FACE_TILE_SCALE, y: FACE_TILE_SCALE }}
          />
          <pixiTilingSprite
            texture={chrome.wood}
            width={rect.width}
            height={TAB_HEIGHT}
            tileScale={{ x: WOOD_TILE_SCALE, y: WOOD_TILE_SCALE }}
          />
          <pixiNineSliceSprite
            texture={chrome.tabActive}
            x={TAB_X}
            y={TAB_PAD_TOP}
            width={TAB_W}
            height={TAB_HEIGHT - TAB_PAD_TOP}
            leftWidth={TAB_SLICE}
            rightWidth={TAB_SLICE}
            topHeight={TAB_SLICE}
            bottomHeight={TAB_SLICE}
          />
        </>
      )}
      <pixiGraphics draw={drawChrome} />
      <pixiText
        text="UNITS"
        style={STYLE_TAB}
        anchor={{ x: 0.5, y: 0.5 }}
        x={TAB_X + TAB_W / 2}
        y={TAB_PAD_TOP + (TAB_HEIGHT - TAB_PAD_TOP) / 2}
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
