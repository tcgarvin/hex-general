// The bottom-left selected-unit panel (UI-SPEC §6 detail + §8 order palette),
// drawn into the Pixi scene graph. The most old-school panel: a square portrait
// on the left (the unit's painterly sprite standing on its own hex's terrain
// tile, framed in the owner's colour), stats on the right (5 HP pips + the
// exact value, the §5.3 stat table, and the current-order line), with the
// type-gated order palette beneath. Button geometry + hit-testing live in
// ./selectedUnit; this file is just the drawing.

import type { Graphics, Texture } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";
import { playerColor } from "@/board/palette";
import type { LoadedUnits } from "@/board/units";
import type { components } from "@/lib/api/schema";
import { UNIT_STATS } from "@/planning/rules";
import type { OrderMode, StagedOrder } from "@/planning/usePlanning";
import { sortWarnings, type UnitWarning } from "@/planning/warnings";

import {
  BTN_H,
  CONTENT_X,
  orderLineText,
  paletteButtonRect,
  paletteButtons,
  PORTRAIT_SIZE,
  PORTRAIT_X,
  PORTRAIT_Y,
  type PaletteAction,
} from "./selectedUnit";

type Unit = components["schemas"]["Unit"];

/** The terrain tile the portrait stands the unit on (the board's pick for the
 *  unit's hex — texture + the oriented-art mirror flag). */
export interface PortraitTile {
  readonly tex: Texture;
  readonly mirror: boolean;
}

// --- palette (UI-SPEC §14: charcoal panel, steel borders, warm off-white text) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
const PORTRAIT_FILL = 0x0e1015;
const TEXT_PRIMARY = 0xe8e4dc;
const TEXT_SECONDARY = 0x9aa0a6;
const PIP_FILLED = 0x8fb7ff;
const PIP_EMPTY = 0x2b2f38;
const BTN_FILL = 0x1e222c;
const BTN_FILL_ACTIVE = 0x2f3d63;
const BTN_BORDER_ACTIVE = 0x8fb7ff;
const BTN_TEXT = 0xe8e4dc;
const BTN_TEXT_DISABLED = 0x565b65;
const WARN_ERROR = 0xff6b6b;
const WARN_INFO = 0xf5b342;

// --- detail-block geometry ---
const TEXT_X = CONTENT_X;
const PIP_W = 16;
const PIP_H = 12;
const PIP_GAP = 4;
/** Right padding for the coord + order line. */
const PANEL_PAD_RIGHT = 12;

const style = (size: number, fill: number, weight: "normal" | "bold" = "normal") =>
  new TextStyle({ fontFamily: "monospace", fontSize: size, fill, fontWeight: weight });

const STYLE_TYPE = style(14, TEXT_PRIMARY, "bold");
const STYLE_COORD = style(12, TEXT_SECONDARY);
const STYLE_HP = style(11, TEXT_PRIMARY);
const STYLE_STAT = style(11, TEXT_SECONDARY);
const STYLE_ORDER = style(11, TEXT_PRIMARY);
const STYLE_PLACEHOLDER = style(13, TEXT_SECONDARY);
const STYLE_BTN = style(11, BTN_TEXT, "bold");
const STYLE_BTN_DISABLED = style(11, BTN_TEXT_DISABLED, "bold");
const STYLE_WARN_ERROR = style(11, WARN_ERROR);
const STYLE_WARN_INFO = style(11, WARN_INFO);

/** Left edge of the right-hand advisory column, kept clear of the stat columns. */
const WARN_X = 380;
const WARN_Y = 30;
const WARN_LINE = 18;

export interface SelectedUnitPanelProps {
  rect: Rect;
  /** The selected unit, or null when nothing is selected. */
  unit: Unit | null;
  /** Two-layer unit sprites (null until loaded → vector-marker fallback). */
  tex: LoadedUnits | null;
  /** The unit's hex tile for the portrait backdrop (null until tiles load). */
  tile: PortraitTile | null;
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

/** Pre-sprite vector fallback: the unit's type marker, centered in the portrait
 *  box (matches the board's UnitMarker fallback shapes). */
function drawFallbackIcon(g: Graphics, unit: Unit): void {
  const color = playerColor(unit.owner);
  const cx = PORTRAIT_X + PORTRAIT_SIZE / 2;
  const cy = PORTRAIT_Y + PORTRAIT_SIZE / 2;
  const r = 34;
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
  staged,
  mode,
  orderLabel,
  warnings,
  locked,
}: SelectedUnitPanelProps) {
  const pair = unit === null ? null : (tex?.[unit.type] ?? null);

  const drawChrome = (g: Graphics) => {
    g.clear();
    g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
    // top + right borders (the strip's seams against the map and the lock panel)
    g.moveTo(0, 0).lineTo(rect.width, 0);
    g.moveTo(rect.width, 0).lineTo(rect.width, rect.height);
    g.stroke({ width: 1, color: PANEL_BORDER });

    if (unit === null) return;

    // portrait backdrop (the sprites + frame render on top of this graphics)
    g.rect(PORTRAIT_X, PORTRAIT_Y, PORTRAIT_SIZE, PORTRAIT_SIZE).fill({
      color: PORTRAIT_FILL,
    });
    if (pair === null) drawFallbackIcon(g, unit);

    // HP pips: each pip = 2 HP, filled by floor(hp / 2) (UI-SPEC §6).
    const filled = Math.floor(unit.hp / 2);
    for (let i = 0; i < 5; i++) {
      const x = TEXT_X + i * (PIP_W + PIP_GAP);
      g.rect(x, 30, PIP_W, PIP_H).fill({ color: i < filled ? PIP_FILLED : PIP_EMPTY });
    }

    // palette buttons — all inert (greyed, no active highlight) once locked
    const buttons = paletteButtons(unit);
    buttons.forEach((b, i) => {
      const r = paletteButtonRect(i);
      const active = !locked && isActive(b.action, staged, mode);
      const disabled = locked || (b.action === "clear" && staged === undefined);
      g.rect(r.x, r.y, r.width, r.height).fill({
        color: active ? BTN_FILL_ACTIVE : BTN_FILL,
      });
      g.rect(r.x, r.y, r.width, r.height).stroke({
        width: active ? 1.5 : 1,
        color: active ? BTN_BORDER_ACTIVE : PANEL_BORDER,
        alpha: disabled ? 0.5 : 1,
      });
    });
  };

  // Owner-coloured frame, drawn over the portrait art so its edge stays crisp.
  const drawFrame = (g: Graphics) => {
    g.clear();
    if (unit === null) return;
    g.rect(PORTRAIT_X, PORTRAIT_Y, PORTRAIT_SIZE, PORTRAIT_SIZE).stroke({
      width: 2,
      color: playerColor(unit.owner),
    });
  };

  if (unit === null) {
    return (
      <pixiContainer x={rect.x} y={rect.y}>
        <pixiGraphics draw={drawChrome} />
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
  // The advisory column sits to the right of the stats; hide it on a panel too
  // narrow to fit it without overlapping the palette text.
  const showWarnings = rect.width >= WARN_X + 200;
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
      <pixiGraphics draw={drawChrome} />
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
      <pixiText text={unit.type.toUpperCase()} style={STYLE_TYPE} x={TEXT_X} y={8} />
      <pixiText
        text={`(${unit.coord.q},${unit.coord.r})`}
        style={STYLE_COORD}
        anchor={{ x: 1, y: 0 }}
        x={rect.width - PANEL_PAD_RIGHT}
        y={11}
      />
      <pixiText
        text={`${unit.hp}/10`}
        style={STYLE_HP}
        anchor={{ x: 0, y: 0.5 }}
        x={TEXT_X + 5 * (PIP_W + PIP_GAP) + 6}
        y={30 + PIP_H / 2}
      />
      <pixiText
        text={`Move ${stats.move}   Atk ${stats.attack}   Def ${stats.defense}`}
        style={STYLE_STAT}
        x={TEXT_X}
        y={50}
      />
      <pixiText
        text={`Range ${stats.range}  Vision ${stats.vision}  Upkeep ${stats.upkeep}`}
        style={STYLE_STAT}
        x={TEXT_X}
        y={66}
      />
      <pixiText
        text={`Order: ${orderLineText(staged, orderLabel)}`}
        style={STYLE_ORDER}
        x={TEXT_X}
        y={84}
      />
      {buttons.map((b, i) => {
        const r = paletteButtonRect(i);
        const disabled = locked || (b.action === "clear" && staged === undefined);
        return (
          <pixiText
            key={b.action}
            text={!locked && mode === "barrage" && b.action === "barrage" ? "Aiming…" : b.label}
            style={disabled ? STYLE_BTN_DISABLED : STYLE_BTN}
            anchor={{ x: 0.5, y: 0.5 }}
            x={r.x + r.width / 2}
            y={r.y + BTN_H / 2}
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
