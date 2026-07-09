// The terrain / hex inspector (UI-SPEC §7), docked at the top-right of the map
// per the §2 layout diagram. Describes the hovered hex — falling back to the
// selected unit's / recruit city's hex, then to a hover hint — so the panel is
// persistent through planning (stable layout, no popping). Drawn in the
// ratified Field-HQ register (docs/UI-STYLE.md §4): the panel floats over the
// map corner, so it reads as a raw-edged parchment field note lying on the map
// under the header bar — cast shadow, terrain name on an engraved brass
// nameplate, coord + property lines in typewriter ink. Until the chrome
// sprites arrive it falls back to a flat parchment card at the same geometry,
// mirroring the dock. Content text comes from ./inspector; this file is just
// the drawing.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";
import type { components } from "@/lib/api/schema";
import { FONT_DATA, FONT_DISPLAY, FONT_LABEL, THEME } from "@/theme/theme";

import type { LoadedChrome } from "./chrome";
import { inspectorContent, type InspectedSettlement } from "./inspector";

type Hex = components["schemas"]["Hex"];
type Unit = components["schemas"]["Unit"];

// --- geometry (inside the sheet's rough edge band) ---
const PAD_X = 20;
const PLATE_Y = 14;
const PLATE_W = 112;
const PLATE_H = 22;
const LINES_Y = 48;
const LINE_H = 15;
/** Rendered width of the sheet's rough edge band (sheet emit). */
const EDGE = 12;
// NineSlice insets: the sheet's rough edge band and the nameplate's
// screw-capped ends, in emitted-texture pixels (scripts/ui-kit-prep.py).
const SHEET_SLICE = 16;
const PLATE_SLICE_X = 24;
const PLATE_SLICE_Y = 12;
/** Paper shadow under the sheet: two stacked offset rects fake a soft cast
 *  shadow (Pixi Graphics has no blur). */
const SHADOW_OFFSETS: readonly { dx: number; dy: number; alpha: number }[] = [
  { dx: 3, dy: 5, alpha: 0.18 },
  { dx: 1, dy: 2, alpha: 0.22 },
];
/** Texture fills emit as 1x mirrored quads (seamless) — tile them unscaled. */
const PARCHMENT_TILE_SCALE = 1.0;

const style = (family: string, size: number, fill: number) =>
  new TextStyle({ fontFamily: family, fontSize: size, fill });

const STYLE_TITLE = style(FONT_DISPLAY, 13, THEME.ink900);
const STYLE_COORD = style(FONT_DATA, 11, THEME.canvas800);
const STYLE_LINE = style(FONT_DATA, 11, THEME.ink900);
const STYLE_PLACEHOLDER = style(FONT_LABEL, 12, THEME.canvas800);

export interface InspectorPanelProps {
  rect: Rect;
  /** The inspected hex, or null when nothing is hovered or selected. */
  hex: Hex | null;
  /** The hex's settlement control state; null when it isn't a settlement. */
  settlement: InspectedSettlement | null;
  /** The unit occupying the hex, if one is visible to the viewer. */
  unit: Unit | null;
  /** Field-HQ chrome sprites (null until loaded → flat placeholder look). */
  chrome: LoadedChrome | null;
}

/** Docked top-right terrain / hex inspector. */
export function InspectorPanel({ rect, hex, settlement, unit, chrome }: InspectorPanelProps) {
  // Cast shadow under the sheet, so it reads as paper lying on the map.
  const drawShadow = (g: Graphics) => {
    g.clear();
    if (chrome === null) return;
    for (const { dx, dy, alpha } of SHADOW_OFFSETS) {
      g.roundRect(dx, dy, rect.width, rect.height, 8).fill({
        color: THEME.ink900,
        alpha,
      });
    }
  };

  // Placeholder card: flat parchment fill + wood keyline at the sheet's
  // footprint, so the ink text reads before the sprites arrive.
  const drawFallback = (g: Graphics) => {
    g.clear();
    if (chrome !== null) return;
    g.roundRect(0, 0, rect.width, rect.height, 6).fill({ color: THEME.parchment200 });
    g.roundRect(0, 0, rect.width, rect.height, 6).stroke({ width: 3, color: THEME.wood700 });
  };

  const sheet = (
    <>
      <pixiGraphics draw={drawShadow} />
      <pixiGraphics draw={drawFallback} />
      {chrome !== null && (
        <>
          <pixiTilingSprite
            texture={chrome.parchment}
            x={EDGE - 4}
            y={EDGE - 4}
            width={rect.width - 2 * EDGE + 8}
            height={rect.height - 2 * EDGE + 8}
            tileScale={{ x: PARCHMENT_TILE_SCALE, y: PARCHMENT_TILE_SCALE }}
          />
          <pixiNineSliceSprite
            texture={chrome.sheet}
            width={rect.width}
            height={rect.height}
            leftWidth={SHEET_SLICE}
            rightWidth={SHEET_SLICE}
            topHeight={SHEET_SLICE}
            bottomHeight={SHEET_SLICE}
          />
        </>
      )}
    </>
  );

  if (hex === null) {
    return (
      <pixiContainer x={rect.x} y={rect.y}>
        {sheet}
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
      {sheet}
      {chrome !== null && (
        <pixiNineSliceSprite
          texture={chrome.plate}
          x={PAD_X}
          y={PLATE_Y}
          width={PLATE_W}
          height={PLATE_H}
          leftWidth={PLATE_SLICE_X}
          rightWidth={PLATE_SLICE_X}
          topHeight={PLATE_SLICE_Y}
          bottomHeight={PLATE_SLICE_Y}
        />
      )}
      <pixiText
        text={content.title}
        style={STYLE_TITLE}
        anchor={{ x: 0.5, y: 0.5 }}
        x={PAD_X + PLATE_W / 2}
        y={PLATE_Y + PLATE_H / 2}
      />
      <pixiText
        text={content.coord}
        style={STYLE_COORD}
        anchor={{ x: 1, y: 0.5 }}
        x={rect.width - PAD_X}
        y={PLATE_Y + PLATE_H / 2}
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
