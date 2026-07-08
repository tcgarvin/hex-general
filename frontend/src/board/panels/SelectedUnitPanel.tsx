// The bottom-left selected-unit panel (UI-SPEC §6 detail + §8 order palette),
// drawn into the Pixi scene graph in the ratified Field-HQ register
// (docs/UI-STYLE.md): worn-canvas dock strip, the unit portrait in a
// brass-lipped inset well, the type on an engraved nameplate, typewriter data
// text, and the sprite-built order palette (blank brass faces + composited
// icon/label, one art state per interaction state). Button geometry +
// hit-testing live in ./selectedUnit; this file is just the drawing. Until
// the chrome sprites arrive it falls back to the flat placeholder look at the
// same geometry, mirroring the terrain/unit texture fallbacks.

import type { Graphics, Texture } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";
import { playerColor } from "@/board/palette";
import type { LoadedUnits } from "@/board/units";
import type { components } from "@/lib/api/schema";
import { UNIT_STATS } from "@/planning/rules";
import type { OrderMode, StagedOrder } from "@/planning/usePlanning";
import { sortWarnings, type UnitWarning } from "@/planning/warnings";
import { FONT_DATA, FONT_DISPLAY, FONT_LABEL, THEME } from "@/theme/theme";

import type { ButtonState, LoadedChrome } from "./chrome";
import {
  BTN_GAP,
  BTN_LABEL_DY,
  BTN_W,
  BTN_X0,
  CONTENT_X,
  orderLineText,
  paletteButtonRect,
  paletteButtons,
  PORTRAIT_SIZE,
  PORTRAIT_X,
  PORTRAIT_Y,
  WELL_SIZE,
  WELL_X,
  WELL_Y,
  type PaletteAction,
} from "./selectedUnit";

type Unit = components["schemas"]["Unit"];

/** The terrain tile the portrait stands the unit on (the board's pick for the
 *  unit's hex — texture + the oriented-art mirror flag). */
export interface PortraitTile {
  readonly tex: Texture;
  readonly mirror: boolean;
}

// --- fallback palette (pre-chrome placeholder, UI-SPEC §14) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
const PORTRAIT_FILL = 0x0e1015;

// --- detail-block geometry ---
const TEXT_X = CONTENT_X;
const PLATE_Y = 6;
const PLATE_W = 180;
const PLATE_H = 26;
const PIP_W = 16;
const PIP_H = 12;
const PIP_GAP = 4;
const PIP_Y = 44;
const STAT_Y1 = 62;
const STAT_Y2 = 78;
const ORDER_Y = 118;
/** Right padding for the coord line. */
const PANEL_PAD_RIGHT = 12;

const style = (family: string, size: number, fill: number, weight: "normal" | "bold" = "normal") =>
  new TextStyle({ fontFamily: family, fontSize: size, fill, fontWeight: weight });

const STYLE_TYPE = style(FONT_DISPLAY, 14, THEME.ink900);
const STYLE_TYPE_FALLBACK = style(FONT_DISPLAY, 14, THEME.ivory100);
const STYLE_COORD = style(FONT_DATA, 11, THEME.ivory400);
const STYLE_HP = style(FONT_DATA, 11, THEME.ivory100);
const STYLE_STAT = style(FONT_DATA, 11, THEME.ivory400);
const STYLE_ORDER = style(FONT_DATA, 11, THEME.ivory100);
const STYLE_PLACEHOLDER = style(FONT_LABEL, 13, THEME.ivory400);
const STYLE_BTN = new TextStyle({
  fontFamily: FONT_LABEL,
  fontSize: 11,
  fill: THEME.ivory100,
  fontWeight: "bold",
  letterSpacing: 1,
});
const STYLE_BTN_DISABLED = new TextStyle({
  fontFamily: FONT_LABEL,
  fontSize: 11,
  fill: THEME.ivory400,
  fontWeight: "bold",
  letterSpacing: 1,
});
const STYLE_WARN_ERROR = style(FONT_LABEL, 11, THEME.invalid);
const STYLE_WARN_INFO = style(FONT_LABEL, 11, THEME.warning);

/** Left edge of the right-hand advisory column, clear of the palette row. */
const WARN_X = BTN_X0 + 5 * (BTN_W + BTN_GAP) + 12;
const WARN_Y = 30;
const WARN_LINE = 18;

// NineSlice insets: the well's leather+lip border and the nameplate's
// screw-capped ends, in emitted-texture pixels (scripts/ui-kit-prep.py).
const WELL_SLICE = 22;
const PLATE_SLICE_X = 24;
const PLATE_SLICE_Y = 12;
/** The brass face's center within the button sprite (fraction of its size). */
const BTN_FACE_CX = 0.5;
const BTN_FACE_CY = 0.44;

export interface SelectedUnitPanelProps {
  rect: Rect;
  /** The selected unit, or null when nothing is selected. */
  unit: Unit | null;
  /** Two-layer unit sprites (null until loaded → vector-marker fallback). */
  tex: LoadedUnits | null;
  /** The unit's hex tile for the portrait backdrop (null until tiles load). */
  tile: PortraitTile | null;
  /** Field-HQ chrome sprites (null until loaded → flat placeholder look). */
  chrome: LoadedChrome | null;
  /** The selected unit's staged order, if any. */
  staged: StagedOrder | undefined;
  /** Interaction mode (drives the Move/Barrage active state). */
  mode: OrderMode;
  /** The §5.2 order label (MOVE/ATTACK/BARRAGE/REST/HOLD/—), computed by Board. */
  orderLabel: string;
  /** Advisory ZOC/traffic warnings for this unit (SPEC §16.1). */
  warnings: readonly UnitWarning[];
  /** Orders are locked: the palette is shown greyed-out and inert (review only). */
  locked: boolean;
  /** Palette button under the pointer (visual hover state only). */
  hoverAction: PaletteAction | null;
  /** Palette button currently held down (visual pressed state only). */
  pressedAction: PaletteAction | null;
}

/** Whether a palette button reads as the active mode/order (UI-SPEC §8). */
function isActive(action: PaletteAction, staged: StagedOrder | undefined, mode: OrderMode): boolean {
  switch (action) {
    case "move":
      return mode === "move" && staged?.kind === "move";
    case "barrage":
      return mode === "barrage" || staged?.kind === "barrage";
    case "rest":
      return staged?.kind === "rest";
    case "hold":
      return staged?.kind === "hold";
    case "clear":
      return false;
  }
}

/** Resolve which art state a palette button shows (UI-STYLE §4). */
function buttonState(
  action: PaletteAction,
  disabled: boolean,
  active: boolean,
  hoverAction: PaletteAction | null,
  pressedAction: PaletteAction | null,
): ButtonState {
  if (disabled) return "disabled";
  if (pressedAction === action) return "pressed";
  if (active) return "active";
  if (hoverAction === action) return "hover";
  return "up";
}

/** Engraved order glyph on the button face: dark ink, simple stencil shapes. */
function drawOrderIcon(g: Graphics, action: PaletteAction, cx: number, cy: number): void {
  switch (action) {
    case "move":
      g.poly([cx - 8, cy - 8, cx + 1, cy - 8, cx + 9, cy, cx + 1, cy + 8, cx - 8, cy + 8, cx, cy]);
      g.fill({ color: THEME.ink900, alpha: 0.8 });
      break;
    case "barrage":
      g.circle(cx, cy, 7).stroke({ width: 3, color: THEME.ink900, alpha: 0.8 });
      for (const [dx, dy] of [
        [0, -1],
        [1, 0],
        [0, 1],
        [-1, 0],
      ]) {
        g.moveTo(cx + dx * 7, cy + dy * 7).lineTo(cx + dx * 11, cy + dy * 11);
      }
      g.stroke({ width: 3, color: THEME.ink900, alpha: 0.8 });
      g.circle(cx, cy, 1.5).fill({ color: THEME.ink900, alpha: 0.8 });
      break;
    case "rest":
      g.poly([cx - 10, cy + 8, cx, cy - 8, cx + 10, cy + 8, cx + 4, cy + 8, cx, cy + 1, cx - 4, cy + 8]);
      g.fill({ color: THEME.ink900, alpha: 0.8 });
      break;
    case "hold":
      g.poly([cx - 8, cy - 8, cx + 8, cy - 8, cx + 8, cy, cx, cy + 9, cx - 8, cy]);
      g.fill({ color: THEME.ink900, alpha: 0.8 });
      break;
    case "clear":
      g.moveTo(cx - 7, cy - 7).lineTo(cx + 7, cy + 7);
      g.moveTo(cx - 7, cy + 7).lineTo(cx + 7, cy - 7);
      g.stroke({ width: 4, color: THEME.ink900, alpha: 0.8 });
      break;
  }
}

/** Pre-sprite vector fallback: the unit's type marker, centered in the portrait
 *  box (matches the board's UnitMarker fallback shapes). */
function drawFallbackIcon(g: Graphics, unit: Unit): void {
  const color = playerColor(unit.owner);
  const cx = PORTRAIT_X + PORTRAIT_SIZE / 2;
  const cy = PORTRAIT_Y + PORTRAIT_SIZE / 2;
  const r = 26;
  if (unit.type === "infantry") {
    g.circle(cx, cy, r);
  } else if (unit.type === "armor") {
    g.roundRect(cx - r, cy - r, r * 2, r * 2, 6);
  } else {
    g.poly([cx, cy - r, cx + r, cy + r * 0.8, cx - r, cy + r * 0.8]);
  }
  g.fill({ color });
  g.stroke({ width: 2, color: 0x000000 });
}

/** Docked bottom-left selected-unit detail + order palette. */
export function SelectedUnitPanel({
  rect,
  unit,
  tex,
  tile,
  chrome,
  staged,
  mode,
  orderLabel,
  warnings,
  locked,
  hoverAction,
  pressedAction,
}: SelectedUnitPanelProps) {
  const pair = unit === null ? null : (tex?.[unit.type] ?? null);

  // Ground layer: worn canvas + a darkening wash (chrome), or the flat
  // placeholder fill until the sprites arrive.
  const drawGround = (g: Graphics) => {
    g.clear();
    if (chrome === null) {
      g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
      g.moveTo(0, 0).lineTo(rect.width, 0);
      g.moveTo(rect.width, 0).lineTo(rect.width, rect.height);
      g.stroke({ width: 1, color: PANEL_BORDER });
      return;
    }
    // Wash over the canvas tiling: deepens the field so text and brass read.
    g.rect(0, 0, rect.width, rect.height).fill({ color: THEME.ink900, alpha: 0.3 });
    // Brass seam along the map edge.
    g.rect(0, 0, rect.width, 2).fill({ color: THEME.brass600, alpha: 0.7 });
  };

  const drawDetail = (g: Graphics) => {
    g.clear();
    if (unit === null) return;

    if (chrome === null) {
      // Placeholder portrait backdrop + well outline.
      g.rect(PORTRAIT_X, PORTRAIT_Y, PORTRAIT_SIZE, PORTRAIT_SIZE).fill({
        color: PORTRAIT_FILL,
      });
    } else {
      // Dark backdrop inside the well's lip (behind the portrait art).
      g.rect(PORTRAIT_X, PORTRAIT_Y, PORTRAIT_SIZE, PORTRAIT_SIZE).fill({
        color: THEME.ink900,
      });
    }
    if (pair === null) drawFallbackIcon(g, unit);

    // HP pips: each pip = 2 HP, filled by floor(hp / 2) (UI-SPEC §6).
    const filled = Math.floor(unit.hp / 2);
    for (let i = 0; i < 5; i++) {
      const x = TEXT_X + i * (PIP_W + PIP_GAP);
      if (i < filled) {
        g.rect(x, PIP_Y, PIP_W, PIP_H).fill({ color: THEME.brass400 });
      } else {
        g.rect(x, PIP_Y, PIP_W, PIP_H).fill({ color: THEME.ink900, alpha: 0.5 });
        g.rect(x, PIP_Y, PIP_W, PIP_H).stroke({ width: 1, color: THEME.wood500 });
      }
    }

    // Fallback-only: plain button plates + nameplate strip.
    if (chrome === null) {
      g.rect(TEXT_X, PLATE_Y, PLATE_W, PLATE_H).stroke({ width: 1, color: PANEL_BORDER });
      const buttons = paletteButtons(unit);
      buttons.forEach((b, i) => {
        const r = paletteButtonRect(i);
        const active = !locked && isActive(b.action, staged, mode);
        g.rect(r.x, r.y, r.width, r.height).fill({ color: active ? 0x2f3d63 : 0x1e222c });
        g.rect(r.x, r.y, r.width, r.height).stroke({ width: 1, color: PANEL_BORDER });
      });
    }
  };

  // Order glyphs over the button faces (both render paths).
  const drawIcons = (g: Graphics) => {
    g.clear();
    if (unit === null) return;
    paletteButtons(unit).forEach((b, i) => {
      const r = paletteButtonRect(i);
      const disabled = locked || (b.action === "clear" && staged === undefined);
      const active = !locked && isActive(b.action, staged, mode);
      const state = buttonState(b.action, disabled, active, hoverAction, pressedAction);
      // The face sinks with the pressed/active art; the glyph follows it.
      const sunk = state === "pressed" || state === "active" ? 2 : 0;
      drawOrderIcon(g, b.action, r.x + r.width * BTN_FACE_CX, r.y + r.height * BTN_FACE_CY + sunk);
    });
  };

  // Owner-coloured keyline around the portrait, inside the well's brass lip.
  const drawFrame = (g: Graphics) => {
    g.clear();
    if (unit === null) return;
    g.rect(PORTRAIT_X, PORTRAIT_Y, PORTRAIT_SIZE, PORTRAIT_SIZE).stroke({
      width: 2,
      color: playerColor(unit.owner),
    });
  };

  const ground = (
    <>
      {chrome !== null && (
        <pixiTilingSprite
          texture={chrome.canvas}
          width={rect.width}
          height={rect.height}
          tileScale={{ x: 0.55, y: 0.55 }}
        />
      )}
      <pixiGraphics draw={drawGround} />
    </>
  );

  if (unit === null) {
    return (
      <pixiContainer x={rect.x} y={rect.y}>
        {ground}
        <pixiText
          text="Select a unit"
          style={STYLE_PLACEHOLDER}
          anchor={{ x: 0.5, y: 0.5 }}
          x={rect.width / 2}
          y={rect.height / 2}
        />
      </pixiContainer>
    );
  }

  const stats = UNIT_STATS[unit.type];
  const buttons = paletteButtons(unit);
  // The advisory column sits right of the palette; hide it on a panel too
  // narrow to fit it without overlapping.
  const showWarnings = rect.width >= WARN_X + 190;
  const shownWarnings = showWarnings ? sortWarnings(warnings).slice(0, 3) : [];

  // Portrait sprites: the hex tile and the unit both fill the box, so the
  // portrait reads as a max-zoom view of that hex. Same west-native mirror
  // rule as the board's UnitMarker; oriented tiles keep their board mirror.
  const portraitCx = PORTRAIT_X + PORTRAIT_SIZE / 2;
  const portraitCy = PORTRAIT_Y + PORTRAIT_SIZE / 2;
  const tileScale = tile === null ? 1 : PORTRAIT_SIZE / tile.tex.width;
  const unitScale = pair === null ? 1 : PORTRAIT_SIZE / pair.base.width;
  const unitSx = unit.facing === "right" ? -unitScale : unitScale;

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      {ground}
      <pixiGraphics draw={drawDetail} />
      {chrome !== null && (
        <pixiNineSliceSprite
          texture={chrome.well}
          x={WELL_X}
          y={WELL_Y}
          width={WELL_SIZE}
          height={WELL_SIZE}
          leftWidth={WELL_SLICE}
          rightWidth={WELL_SLICE}
          topHeight={WELL_SLICE}
          bottomHeight={WELL_SLICE}
        />
      )}
      {tile !== null && (
        <pixiSprite
          texture={tile.tex}
          anchor={0.5}
          x={portraitCx}
          y={portraitCy}
          scale={{ x: tile.mirror ? -tileScale : tileScale, y: tileScale }}
        />
      )}
      {pair !== null && (
        <>
          <pixiSprite
            texture={pair.base}
            anchor={0.5}
            x={portraitCx}
            y={portraitCy}
            scale={{ x: unitSx, y: unitScale }}
          />
          <pixiSprite
            texture={pair.mask}
            anchor={0.5}
            x={portraitCx}
            y={portraitCy}
            scale={{ x: unitSx, y: unitScale }}
            tint={playerColor(unit.owner)}
          />
        </>
      )}
      <pixiGraphics draw={drawFrame} />
      {chrome !== null && (
        <pixiNineSliceSprite
          texture={chrome.plate}
          x={TEXT_X}
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
        text={unit.type.toUpperCase()}
        style={chrome === null ? STYLE_TYPE_FALLBACK : STYLE_TYPE}
        anchor={{ x: 0.5, y: 0.5 }}
        x={TEXT_X + PLATE_W / 2}
        y={PLATE_Y + PLATE_H / 2}
      />
      <pixiText
        text={`(${unit.coord.q},${unit.coord.r})`}
        style={STYLE_COORD}
        anchor={{ x: 1, y: 0 }}
        x={rect.width - PANEL_PAD_RIGHT}
        y={PLATE_Y + 6}
      />
      <pixiText
        text={`${unit.hp}/10`}
        style={STYLE_HP}
        anchor={{ x: 0, y: 0.5 }}
        x={TEXT_X + 5 * (PIP_W + PIP_GAP) + 6}
        y={PIP_Y + PIP_H / 2}
      />
      <pixiText
        text={`Move ${stats.move}  Atk ${stats.attack}  Def ${stats.defense}`}
        style={STYLE_STAT}
        x={TEXT_X}
        y={STAT_Y1}
      />
      <pixiText
        text={`Rng ${stats.range}  Vis ${stats.vision}  Upk ${stats.upkeep}`}
        style={STYLE_STAT}
        x={TEXT_X}
        y={STAT_Y2}
      />
      <pixiText
        text={`Order: ${orderLineText(staged, orderLabel)}`}
        style={STYLE_ORDER}
        x={TEXT_X}
        y={ORDER_Y}
      />
      {chrome !== null &&
        buttons.map((b, i) => {
          const r = paletteButtonRect(i);
          const disabled = locked || (b.action === "clear" && staged === undefined);
          const active = !locked && isActive(b.action, staged, mode);
          const state = buttonState(b.action, disabled, active, hoverAction, pressedAction);
          return (
            <pixiSprite
              key={b.action}
              texture={chrome.btn[state]}
              x={r.x}
              y={r.y}
              width={r.width}
              height={r.height}
            />
          );
        })}
      <pixiGraphics draw={drawIcons} />
      {buttons.map((b, i) => {
        const r = paletteButtonRect(i);
        const disabled = locked || (b.action === "clear" && staged === undefined);
        return (
          <pixiText
            key={b.action}
            text={
              !locked && mode === "barrage" && b.action === "barrage"
                ? "AIMING…"
                : b.label.toUpperCase()
            }
            style={disabled ? STYLE_BTN_DISABLED : STYLE_BTN}
            anchor={{ x: 0.5, y: 0.5 }}
            x={r.x + r.width / 2}
            y={r.y + BTN_LABEL_DY}
          />
        );
      })}
      {shownWarnings.map((w, i) => (
        <pixiText
          key={i}
          text={`⚠ ${w.message}`}
          style={w.severity === "error" ? STYLE_WARN_ERROR : STYLE_WARN_INFO}
          x={WARN_X}
          y={WARN_Y + i * WARN_LINE}
        />
      ))}
    </pixiContainer>
  );
}
