// The roster panel renderer (UI-SPEC §5): docked Pixi chrome on the left edge,
// dense monospaced rows of type · (q,r) · order · HP, with unordered units sorted
// to the top so "who still needs orders?" is impossible to miss. Row derivation
// and hit-testing live in ./roster; this file is just the scene-graph drawing.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";

import { ROW_HEIGHT, TAB_HEIGHT, type RosterRow } from "./roster";

// --- column geometry ---
const COL_MARKER = 7;
const COL_TYPE = 17;
const COL_COORD = 54;
const COL_LABEL = 116;
const PAD_RIGHT = 9;

// --- palette (UI-SPEC §14: charcoal panel, steel borders, warm off-white text) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
const ROW_SELECTED = 0x2a3550;
const ROW_HOVER = 0x1e222c;
const SELECT_OUTLINE = 0x8fb7ff;
const TEXT_PRIMARY = 0xe8e4dc;
const TEXT_SECONDARY = 0x9aa0a6;
const MARK_IDLE = 0xf5b342;
const MARK_ORDERED = 0x8fb7ff;

const style = (size: number, fill: number, weight: "normal" | "bold" = "normal") =>
  new TextStyle({ fontFamily: "monospace", fontSize: size, fill, fontWeight: weight });

const STYLE_TAB = style(12, TEXT_PRIMARY, "bold");
const STYLE_TYPE = style(11, TEXT_PRIMARY, "bold");
const STYLE_COORD = style(11, TEXT_SECONDARY);
const STYLE_LABEL = style(11, TEXT_PRIMARY);
const STYLE_IDLE_LABEL = style(11, MARK_IDLE);
const STYLE_HP = style(11, TEXT_SECONDARY);
const STYLE_MARK_IDLE = style(12, MARK_IDLE, "bold");
const STYLE_MARK_ORDERED = style(11, MARK_ORDERED, "bold");

export interface RosterPanelProps {
  rect: Rect;
  rows: RosterRow[];
  hoverIndex: number | null;
}

/** Docked left-edge roster, drawn into the Pixi scene graph. */
export function RosterPanel({ rect, rows, hoverIndex }: RosterPanelProps) {
  const drawChrome = (g: Graphics) => {
    g.clear();
    g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
    g.moveTo(rect.width, 0).lineTo(rect.width, rect.height);
    g.moveTo(0, TAB_HEIGHT).lineTo(rect.width, TAB_HEIGHT);
    g.stroke({ width: 1, color: PANEL_BORDER });
  };

  const drawRowBackgrounds = (g: Graphics) => {
    g.clear();
    rows.forEach((row, i) => {
      const y = TAB_HEIGHT + i * ROW_HEIGHT;
      if (row.selected) {
        g.rect(1, y, rect.width - 2, ROW_HEIGHT).fill({ color: ROW_SELECTED });
        g.rect(1, y, rect.width - 2, ROW_HEIGHT).stroke({ width: 1, color: SELECT_OUTLINE });
      } else if (i === hoverIndex) {
        g.rect(1, y, rect.width - 2, ROW_HEIGHT).fill({ color: ROW_HOVER });
      }
    });
  };

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      <pixiGraphics draw={drawChrome} />
      <pixiText text="UNITS" style={STYLE_TAB} x={9} y={7} />
      <pixiGraphics draw={drawRowBackgrounds} />
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
  return (
    <pixiContainer>
      <pixiText
        text={row.idle ? "!" : "✓"}
        style={row.idle ? STYLE_MARK_IDLE : STYLE_MARK_ORDERED}
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
        style={row.idle ? STYLE_IDLE_LABEL : STYLE_LABEL}
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
