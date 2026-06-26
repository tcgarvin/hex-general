// The bottom-left recruit panel (UI-SPEC §6/§8, SPEC §6.5), drawn into the Pixi
// scene graph in place of the selected-unit panel when a friendly city is
// selected. A city glyph on the left, the city's recruit budget + cap slots on
// the right, and a row of type buttons (cost shown, affordability-gated) beneath.
// Clicking the staged type again clears it. Geometry + the availability math live
// in ./recruitCity; this file is just the drawing.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Axial } from "@/board/hex";
import type { Rect } from "@/board/layout";
import { playerColor } from "@/board/palette";
import type { components } from "@/lib/api/schema";
import type { StagedRecruit } from "@/planning/usePlanning";

import {
  canRecruit,
  recruitAvailability,
  recruitButtonRect,
  recruitButtons,
  RBTN_H,
} from "./recruitCity";

type UnitType = components["schemas"]["UnitType"];

// --- palette (UI-SPEC §14) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
const TEXT_PRIMARY = 0xe8e4dc;
const TEXT_SECONDARY = 0x9aa0a6;
const TEXT_WARN = 0xf5b342;
const BTN_FILL = 0x1e222c;
const BTN_FILL_ACTIVE = 0x2f3d63;
const BTN_BORDER_ACTIVE = 0x8fb7ff;
const BTN_TEXT = 0xe8e4dc;
const BTN_TEXT_DISABLED = 0x565b65;

// --- detail-block geometry (mirrors the selected-unit panel) ---
const ICON_CX = 30;
const ICON_CY = 44;
const ICON_R = 20;
const TEXT_X = 64;
const PANEL_PAD_RIGHT = 12;
const PANEL_PAD_LEFT = 12;

const style = (size: number, fill: number, weight: "normal" | "bold" = "normal") =>
  new TextStyle({ fontFamily: "monospace", fontSize: size, fill, fontWeight: weight });

const STYLE_TITLE = style(14, TEXT_PRIMARY, "bold");
const STYLE_COORD = style(12, TEXT_SECONDARY);
const STYLE_INFO = style(11, TEXT_SECONDARY);
const STYLE_STAGED = style(11, TEXT_PRIMARY);
const STYLE_HINT = style(11, TEXT_WARN);
const STYLE_BTN = style(11, BTN_TEXT, "bold");
const STYLE_BTN_DISABLED = style(11, BTN_TEXT_DISABLED, "bold");

export interface RecruitPanelProps {
  rect: Rect;
  /** The selected friendly city. */
  city: Axial;
  /** The viewer's slot, used to tint the recruit preview icon. */
  owner: string;
  /** Staged recruits across all cities (for the running budget/cap math). */
  recruitBySite: Readonly<Record<string, StagedRecruit>>;
  /** The viewer's current muster bank. */
  bank: number;
  /** The viewer's current unit count (against the cap). */
  ownUnitCount: number;
  /** The unit cap (GameConfig.unit_cap). */
  unitCap: number;
}

/** Draw a unit type marker (matches the board UnitMarker / selected-unit shapes). */
function drawTypeIcon(g: Graphics, type: UnitType, owner: string): void {
  const cx = ICON_CX;
  const cy = ICON_CY;
  const r = ICON_R;
  if (type === "infantry") {
    g.circle(cx, cy, r);
  } else if (type === "armor") {
    g.roundRect(cx - r, cy - r, r * 2, r * 2, 4);
  } else {
    g.poly([cx, cy - r, cx + r, cy + r * 0.8, cx - r, cy + r * 0.8]);
  }
  g.fill({ color: playerColor(owner) });
  g.stroke({ width: 1.5, color: 0x000000 });
}

/** Draw an empty-city glyph (a hollow keep) when nothing is staged yet. */
function drawCityGlyph(g: Graphics): void {
  const cx = ICON_CX;
  const cy = ICON_CY;
  const r = ICON_R;
  g.roundRect(cx - r, cy - r * 0.8, r * 2, r * 1.6, 3);
  g.stroke({ width: 1.5, color: TEXT_SECONDARY });
}

/** Docked bottom-left recruit detail + type palette. */
export function RecruitPanel({
  rect,
  city,
  owner,
  recruitBySite,
  bank,
  ownUnitCount,
  unitCap,
}: RecruitPanelProps) {
  const buttons = recruitButtons();
  const avail = recruitAvailability(city, recruitBySite, bank, ownUnitCount, unitCap);

  const drawChrome = (g: Graphics) => {
    g.clear();
    g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
    // top + right borders (the strip's seams against the map and the lock panel)
    g.moveTo(0, 0).lineTo(rect.width, 0);
    g.moveTo(rect.width, 0).lineTo(rect.width, rect.height);
    g.stroke({ width: 1, color: PANEL_BORDER });

    if (avail.currentType === null) drawCityGlyph(g);
    else drawTypeIcon(g, avail.currentType, owner);

    buttons.forEach((b, i) => {
      const r = recruitButtonRect(i);
      const active = avail.currentType === b.type;
      const enabled = active || canRecruit(b, avail);
      g.rect(r.x, r.y, r.width, r.height).fill({
        color: active ? BTN_FILL_ACTIVE : BTN_FILL,
      });
      g.rect(r.x, r.y, r.width, r.height).stroke({
        width: active ? 1.5 : 1,
        color: active ? BTN_BORDER_ACTIVE : PANEL_BORDER,
        alpha: enabled ? 1 : 0.5,
      });
    });
  };

  const nothingAffordable =
    avail.currentType === null && !buttons.some((b) => canRecruit(b, avail));
  const hint =
    avail.slotsLeft < 1
      ? "Unit cap reached"
      : nothingAffordable
        ? "Not enough muster"
        : null;

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      <pixiGraphics draw={drawChrome} />
      <pixiText text="RECRUIT" style={STYLE_TITLE} x={TEXT_X} y={8} />
      <pixiText
        text={`(${city.q},${city.r})`}
        style={STYLE_COORD}
        anchor={{ x: 1, y: 0 }}
        x={rect.width - PANEL_PAD_RIGHT}
        y={11}
      />
      <pixiText
        text={`Budget ${avail.budget}   Slots ${avail.slotsLeft}`}
        style={STYLE_INFO}
        x={TEXT_X}
        y={50}
      />
      <pixiText
        text={`Building: ${avail.currentType ?? "—"}`}
        style={STYLE_STAGED}
        x={PANEL_PAD_LEFT}
        y={84}
      />
      {hint !== null && (
        <pixiText
          text={`⚠ ${hint}`}
          style={STYLE_HINT}
          anchor={{ x: 1, y: 0 }}
          x={rect.width - PANEL_PAD_RIGHT}
          y={84}
        />
      )}
      {buttons.map((b, i) => {
        const r = recruitButtonRect(i);
        const active = avail.currentType === b.type;
        const enabled = active || canRecruit(b, avail);
        return (
          <pixiText
            key={b.type}
            text={`${b.label} ${b.cost}`}
            style={enabled ? STYLE_BTN : STYLE_BTN_DISABLED}
            anchor={{ x: 0.5, y: 0.5 }}
            x={r.x + r.width / 2}
            y={r.y + RBTN_H / 2}
          />
        );
      })}
    </pixiContainer>
  );
}
