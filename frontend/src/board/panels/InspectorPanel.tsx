// The terrain / hex inspector (UI-SPEC §7), docked at the top-right of the map
// per the §2 layout diagram. Describes the hovered hex — falling back to the
// selected unit's / recruit city's hex, then to a hover hint — so the panel is
// persistent through planning (stable layout, no popping). Content text comes
// from ./inspector; this file is just the drawing.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";
import type { components } from "@/lib/api/schema";

import { inspectorContent, type InspectedSettlement } from "./inspector";

type Hex = components["schemas"]["Hex"];
type Unit = components["schemas"]["Unit"];

// --- palette (UI-SPEC §14, matching the other docked panels) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
const TEXT_PRIMARY = 0xe8e4dc;
const TEXT_SECONDARY = 0x9aa0a6;

// --- geometry ---
const PAD_X = 12;
const TITLE_Y = 8;
const LINES_Y = 30;
const LINE_H = 16;

const style = (size: number, fill: number, weight: "normal" | "bold" = "normal") =>
  new TextStyle({ fontFamily: "monospace", fontSize: size, fill, fontWeight: weight });

const STYLE_TITLE = style(14, TEXT_PRIMARY, "bold");
const STYLE_COORD = style(12, TEXT_SECONDARY);
const STYLE_LINE = style(11, TEXT_SECONDARY);
const STYLE_PLACEHOLDER = style(12, TEXT_SECONDARY);

export interface InspectorPanelProps {
  rect: Rect;
  /** The inspected hex, or null when nothing is hovered or selected. */
  hex: Hex | null;
  /** The hex's settlement control state; null when it isn't a settlement. */
  settlement: InspectedSettlement | null;
  /** The unit occupying the hex, if one is visible to the viewer. */
  unit: Unit | null;
}

/** Docked top-right terrain / hex inspector. */
export function InspectorPanel({ rect, hex, settlement, unit }: InspectorPanelProps) {
  const drawChrome = (g: Graphics) => {
    g.clear();
    g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
    g.rect(0.5, 0.5, rect.width - 1, rect.height - 1).stroke({
      width: 1,
      color: PANEL_BORDER,
    });
  };

  if (hex === null) {
    return (
      <pixiContainer x={rect.x} y={rect.y}>
        <pixiGraphics draw={drawChrome} />
        <pixiText
          text="Hover a hex"
          style={STYLE_PLACEHOLDER}
          anchor={{ x: 0.5, y: 0.5 }}
          x={rect.width / 2}
          y={rect.height / 2}
        />
      </pixiContainer>
    );
  }

  const content = inspectorContent(hex, settlement, unit);
  return (
    <pixiContainer x={rect.x} y={rect.y}>
      <pixiGraphics draw={drawChrome} />
      <pixiText text={content.title} style={STYLE_TITLE} x={PAD_X} y={TITLE_Y} />
      <pixiText
        text={content.coord}
        style={STYLE_COORD}
        anchor={{ x: 1, y: 0 }}
        x={rect.width - PAD_X}
        y={TITLE_Y + 2}
      />
      {content.lines.map((line, i) => (
        <pixiText
          key={line}
          text={line}
          style={STYLE_LINE}
          x={PAD_X}
          y={LINES_Y + i * LINE_H}
        />
      ))}
    </pixiContainer>
  );
}
