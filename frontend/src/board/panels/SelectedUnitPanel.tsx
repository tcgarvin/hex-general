// The bottom-left selected-unit panel (UI-SPEC §6 detail + §8 order palette),
// drawn into the Pixi scene graph. The most old-school panel: a unit icon on the
// left, stats on the right (5 HP pips + the exact value, the §5.3 stat table, and
// the current-order line), with the type-gated order palette beneath. Button
// geometry + hit-testing live in ./selectedUnit; this file is just the drawing.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";
import { playerColor } from "@/board/palette";
import type { components } from "@/lib/api/schema";
import { UNIT_STATS } from "@/planning/rules";
import type { OrderMode, StagedOrder } from "@/planning/usePlanning";
import { sortWarnings, type UnitWarning } from "@/planning/warnings";

import {
  BTN_H,
  orderLineText,
  paletteButtonRect,
  paletteButtons,
  type PaletteAction,
} from "./selectedUnit";

type Unit = components["schemas"]["Unit"];

// --- palette (UI-SPEC §14: charcoal panel, steel borders, warm off-white text) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
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
const ICON_CX = 30;
const ICON_CY = 44;
const ICON_R = 20;
const TEXT_X = 64;
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
const WARN_X = 300;
const WARN_Y = 30;
const WARN_LINE = 18;

export interface SelectedUnitPanelProps {
  rect: Rect;
  /** The selected unit, or null when nothing is selected. */
  unit: Unit | null;
  /** The selected unit's staged order, if any. */
  staged: StagedOrder | undefined;
  /** Interaction mode (drives the Move/Barrage active state). */
  mode: OrderMode;
  /** The §5.2 order label (MOVE/ATTACK/BARRAGE/REST/HOLD/—), computed by Board. */
  orderLabel: string;
  /** Advisory ZOC/traffic warnings for this unit (SPEC §16.1). */
  warnings: readonly UnitWarning[];
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

/** Draw the unit's type marker (matches the board UnitMarker shapes). */
function drawIcon(g: Graphics, unit: Unit): void {
  const color = playerColor(unit.owner);
  const cx = ICON_CX;
  const cy = ICON_CY;
  const r = ICON_R;
  if (unit.type === "infantry") {
    g.circle(cx, cy, r);
  } else if (unit.type === "armor") {
    g.roundRect(cx - r, cy - r, r * 2, r * 2, 4);
  } else {
    g.poly([cx, cy - r, cx + r, cy + r * 0.8, cx - r, cy + r * 0.8]);
  }
  g.fill({ color });
  g.stroke({ width: 1.5, color: 0x000000 });
}

/** Docked bottom-left selected-unit detail + order palette. */
export function SelectedUnitPanel({
  rect,
  unit,
  staged,
  mode,
  orderLabel,
  warnings,
}: SelectedUnitPanelProps) {
  const drawChrome = (g: Graphics) => {
    g.clear();
    g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
    // top + right borders (the strip's seams against the map and the lock panel)
    g.moveTo(0, 0).lineTo(rect.width, 0);
    g.moveTo(rect.width, 0).lineTo(rect.width, rect.height);
    g.stroke({ width: 1, color: PANEL_BORDER });

    if (unit === null) return;

    drawIcon(g, unit);

    // HP pips: each pip = 2 HP, filled by floor(hp / 2) (UI-SPEC §6).
    const filled = Math.floor(unit.hp / 2);
    for (let i = 0; i < 5; i++) {
      const x = TEXT_X + i * (PIP_W + PIP_GAP);
      g.rect(x, 30, PIP_W, PIP_H).fill({ color: i < filled ? PIP_FILLED : PIP_EMPTY });
    }

    // palette buttons
    const buttons = paletteButtons(unit);
    buttons.forEach((b, i) => {
      const r = paletteButtonRect(i);
      const active = isActive(b.action, staged, mode);
      const disabled = b.action === "clear" && staged === undefined;
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

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      <pixiGraphics draw={drawChrome} />
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
        x={PANEL_PAD_RIGHT}
        y={84}
      />
      {buttons.map((b, i) => {
        const r = paletteButtonRect(i);
        const disabled = b.action === "clear" && staged === undefined;
        return (
          <pixiText
            key={b.action}
            text={mode === "barrage" && b.action === "barrage" ? "Aiming…" : b.label}
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
