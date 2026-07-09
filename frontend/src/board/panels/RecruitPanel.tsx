// The bottom-left recruit panel (UI-SPEC §6/§8, SPEC §6.5), drawn into the Pixi
// scene graph in place of the selected-unit panel when a friendly city is
// selected, in the ratified Field-HQ register (docs/UI-STYLE.md): the same
// frameless raw-edged parchment sheet lying on the dock's dark wood carries the
// city's identity — the city hex (plus the staged recruit, once one is chosen)
// in the brass-lipped portrait well, RECRUIT on the engraved nameplate, and
// ruled BUDGET/SLOTS columns in ink — while the type buttons render as the
// palette's brass faces on the wood to the sheet's right, type glyph engraved
// on the face, label + cost beneath. Clicking the staged type again clears it.
// Geometry + the availability math live in ./recruitCity; this file is just the
// drawing. Until the chrome sprites arrive it falls back to a flat placeholder
// look at the same geometry, mirroring the other panels.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Axial } from "@/board/hex";
import type { Rect } from "@/board/layout";
import { playerColor } from "@/board/palette";
import type { LoadedUnits } from "@/board/units";
import type { components } from "@/lib/api/schema";
import type { StagedRecruit } from "@/planning/usePlanning";
import { FONT_DATA, FONT_DISPLAY, FONT_LABEL, THEME } from "@/theme/theme";

import type { ButtonState, LoadedChrome } from "./chrome";
import {
  canRecruit,
  recruitAvailability,
  recruitButtonRect,
  recruitButtons,
  RBTN_GAP,
  RBTN_W,
  RBTN_X0,
} from "./recruitCity";
import {
  CARD_H,
  CARD_W,
  CARD_X,
  CARD_Y,
  CONTENT_X,
  FACE_H,
  FACE_W,
  FACE_X,
  FACE_Y,
  BTN_LABEL_DY,
  PORTRAIT_SIZE,
  PORTRAIT_X,
  PORTRAIT_Y,
  WELL_SIZE,
  WELL_X,
  WELL_Y,
} from "./selectedUnit";
import type { PortraitTile } from "./SelectedUnitPanel";

type Facing = components["schemas"]["Facing"];
type UnitType = components["schemas"]["UnitType"];

// --- fallback palette (pre-chrome placeholder, UI-SPEC §14) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;

// --- detail-block geometry (mirrors the selected-unit card) ---
const TEXT_X = CONTENT_X;
const PLATE_Y = 18;
const PLATE_W = 140;
const PLATE_H = 24;
/** BUDGET/SLOTS columns (label over value, a thin ink rule between), on the
 *  same rows as the unit card's MOVE/ATK/DEF so the dock keeps one rhythm. */
const COL_W = 70;
const COL_LABEL_Y = 71;
const COL_VALUE_Y = 84;
const COL_RULE_Y0 = 73;
const COL_RULE_Y1 = 100;
/** The staged-type line sits on the wood, under the type buttons. */
const BUILDING_Y = 118;
/** Cost line under each button's label, in the engraved-label stack. */
const BTN_COST_DY = BTN_LABEL_DY + 15;
/** Right padding for the coord line. */
const PANEL_PAD_RIGHT = 12;
/** Advisory column right of the type buttons (hidden on narrow panels). */
const HINT_X = RBTN_X0 + 3 * (RBTN_W + RBTN_GAP) + 12;
const HINT_Y = 30;

const style = (family: string, size: number, fill: number, weight: "normal" | "bold" = "normal") =>
  new TextStyle({ fontFamily: family, fontSize: size, fill, fontWeight: weight });

const STYLE_TITLE = style(FONT_DISPLAY, 14, THEME.ink900);
const STYLE_COORD = style(FONT_DATA, 11, THEME.ivory400);
const STYLE_COL_LABEL = new TextStyle({
  fontFamily: FONT_LABEL,
  fontSize: 10,
  fill: THEME.ink900,
  fontWeight: "bold",
  letterSpacing: 1,
});
const STYLE_COL_VALUE = style(FONT_DATA, 13, THEME.ink900);
const STYLE_BUILDING = style(FONT_DATA, 11, THEME.ivory100);
const STYLE_HINT = new TextStyle({
  fontFamily: FONT_LABEL,
  fontSize: 11,
  fill: THEME.warning,
  fontWeight: "bold",
  letterSpacing: 1,
});
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
const STYLE_COST = style(FONT_DATA, 10, THEME.ivory400);

// NineSlice insets, in emitted-texture pixels (scripts/ui-kit-prep.py) — same
// values as the selected-unit card, which shares this construction.
const WELL_SLICE = 22;
const PLATE_SLICE_X = 24;
const PLATE_SLICE_Y = 12;
const SHEET_SLICE = 16;
/** Paper shadow under the sheet (two stacked offset rects — no blur in Graphics). */
const SHADOW_OFFSETS: readonly { dx: number; dy: number; alpha: number }[] = [
  { dx: 3, dy: 5, alpha: 0.18 },
  { dx: 1, dy: 2, alpha: 0.22 },
];
/** Texture fills emit as 1x mirrored quads (seamless) — tile them unscaled. */
const PARCHMENT_TILE_SCALE = 1.0;
/** The brass face's center within the button sprite (fraction of its size). */
const BTN_FACE_CX = 0.5;
const BTN_FACE_CY = 0.44;

export interface RecruitPanelProps {
  rect: Rect;
  /** The selected friendly city. */
  city: Axial;
  /** The viewer's slot, used to tint the recruit preview sprite. */
  owner: string;
  /** Two-layer unit sprites for the staged-type portrait (null → vector fallback). */
  tex: LoadedUnits | null;
  /** The city's hex tile for the portrait backdrop (null until tiles load). */
  tile: PortraitTile | null;
  /** Field-HQ chrome sprites (null until loaded → flat placeholder look). */
  chrome: LoadedChrome | null;
  /** Which way the recruited unit would face (matches the map's recruit ghost). */
  facing: Facing;
  /** Staged recruits across all cities (for the running budget/cap math). */
  recruitBySite: Readonly<Record<string, StagedRecruit>>;
  /** The viewer's current muster bank. */
  bank: number;
  /** The viewer's current unit count (against the cap). */
  ownUnitCount: number;
  /** The unit cap (GameConfig.unit_cap). */
  unitCap: number;
  /** Orders are locked: the type palette is shown greyed-out and inert. */
  locked: boolean;
  /** Type button under the pointer (visual hover state only). */
  hoverType: UnitType | null;
  /** Type button currently held down (visual pressed state only). */
  pressedType: UnitType | null;
}

/** Resolve which art state a type button shows (UI-STYLE §4). */
function buttonState(
  type: UnitType,
  disabled: boolean,
  active: boolean,
  hoverType: UnitType | null,
  pressedType: UnitType | null,
): ButtonState {
  if (disabled) return "disabled";
  if (pressedType === type) return "pressed";
  if (active) return "active";
  if (hoverType === type) return "hover";
  return "up";
}

/** Engraved unit-type glyph on the button face: the board marker shapes in ink. */
function drawTypeGlyph(g: Graphics, type: UnitType, cx: number, cy: number): void {
  const r = 9;
  if (type === "infantry") {
    g.circle(cx, cy, r);
  } else if (type === "armor") {
    g.roundRect(cx - r, cy - r, r * 2, r * 2, 3);
  } else {
    g.poly([cx, cy - r, cx + r, cy + r * 0.8, cx - r, cy + r * 0.8]);
  }
  g.fill({ color: THEME.ink900, alpha: 0.8 });
}

/** Pre-sprite vector fallback: the staged type's marker, centered in the
 *  portrait box (matches the board's UnitMarker fallback shapes). */
function drawFallbackMarker(g: Graphics, type: UnitType, owner: string): void {
  const cx = PORTRAIT_X + PORTRAIT_SIZE / 2;
  const cy = PORTRAIT_Y + PORTRAIT_SIZE / 2;
  const r = 26;
  if (type === "infantry") {
    g.circle(cx, cy, r);
  } else if (type === "armor") {
    g.roundRect(cx - r, cy - r, r * 2, r * 2, 6);
  } else {
    g.poly([cx, cy - r, cx + r, cy + r * 0.8, cx - r, cy + r * 0.8]);
  }
  g.fill({ color: playerColor(owner) });
  g.stroke({ width: 2, color: 0x000000 });
}

/** An empty-city glyph (a hollow keep) when the tiles haven't loaded yet. */
function drawKeepGlyph(g: Graphics): void {
  const cx = PORTRAIT_X + PORTRAIT_SIZE / 2;
  const cy = PORTRAIT_Y + PORTRAIT_SIZE / 2;
  const r = 22;
  g.roundRect(cx - r, cy - r * 0.8, r * 2, r * 1.6, 3);
  g.stroke({ width: 2, color: THEME.ivory400 });
}

/** Docked bottom-left recruit detail + type palette. */
export function RecruitPanel({
  rect,
  city,
  owner,
  tex,
  tile,
  chrome,
  facing,
  recruitBySite,
  bank,
  ownUnitCount,
  unitCap,
  locked,
  hoverType,
  pressedType,
}: RecruitPanelProps) {
  const buttons = recruitButtons();
  const avail = recruitAvailability(city, recruitBySite, bank, ownUnitCount, unitCap);
  const pair = avail.currentType === null ? null : (tex?.[avail.currentType] ?? null);

  // Ground layer: dark wood + a light wash + the brass map-edge seam (chrome),
  // or the flat placeholder fill until the sprites arrive.
  const drawGround = (g: Graphics) => {
    g.clear();
    if (chrome === null) {
      g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
      g.moveTo(0, 0).lineTo(rect.width, 0);
      g.moveTo(rect.width, 0).lineTo(rect.width, rect.height);
      g.stroke({ width: 1, color: PANEL_BORDER });
      return;
    }
    g.rect(0, 0, rect.width, rect.height).fill({ color: THEME.ink900, alpha: 0.18 });
    g.rect(0, 0, rect.width, 2).fill({ color: THEME.brass600, alpha: 0.7 });
  };

  // Cast shadow under the parchment sheet, matching the selected-unit card.
  const drawSheetShadow = (g: Graphics) => {
    g.clear();
    if (chrome === null) return;
    for (const { dx, dy, alpha } of SHADOW_OFFSETS) {
      g.roundRect(CARD_X + dx, CARD_Y + dy, CARD_W, CARD_H, 8).fill({
        color: THEME.ink900,
        alpha,
      });
    }
  };

  const drawDetail = (g: Graphics) => {
    g.clear();

    if (chrome === null) {
      // Placeholder card: flat parchment fill + wood keyline at the sheet's
      // footprint, so the ink text reads before the sprites arrive.
      g.roundRect(CARD_X, CARD_Y, CARD_W, CARD_H, 6).fill({ color: THEME.parchment200 });
      g.roundRect(CARD_X, CARD_Y, CARD_W, CARD_H, 6).stroke({ width: 3, color: THEME.wood700 });
      g.rect(TEXT_X, PLATE_Y, PLATE_W, PLATE_H).stroke({ width: 1, color: THEME.brass600 });
    }

    // Dark backdrop inside the well's lip (behind the portrait art).
    g.rect(PORTRAIT_X, PORTRAIT_Y, PORTRAIT_SIZE, PORTRAIT_SIZE).fill({
      color: THEME.ink900,
    });
    if (tile === null) drawKeepGlyph(g);
    if (avail.currentType !== null && pair === null) {
      drawFallbackMarker(g, avail.currentType, owner);
    }

    // Thin ink rule between the BUDGET/SLOTS columns.
    g.moveTo(TEXT_X + COL_W, COL_RULE_Y0).lineTo(TEXT_X + COL_W, COL_RULE_Y1);
    g.stroke({ width: 1, color: THEME.ink900, alpha: 0.3 });

    // Fallback-only: plain button plates.
    if (chrome === null) {
      buttons.forEach((b, i) => {
        const r = recruitButtonRect(i);
        const active = !locked && avail.currentType === b.type;
        g.rect(r.x, r.y, r.width, r.height).fill({ color: active ? 0x2f3d63 : 0x1e222c });
        g.rect(r.x, r.y, r.width, r.height).stroke({ width: 1, color: PANEL_BORDER });
      });
    }
  };

  // Type glyphs over the button faces (both render paths).
  const drawGlyphs = (g: Graphics) => {
    g.clear();
    buttons.forEach((b, i) => {
      const r = recruitButtonRect(i);
      const disabled = locked || (avail.currentType !== b.type && !canRecruit(b, avail));
      const active = !locked && avail.currentType === b.type;
      const state = buttonState(b.type, disabled, active, hoverType, pressedType);
      // The face sinks with the pressed/active art; the glyph follows it.
      const sunk = state === "pressed" || state === "active" ? 2 : 0;
      drawTypeGlyph(g, b.type, r.x + r.width * BTN_FACE_CX, r.y + r.height * BTN_FACE_CY + sunk);
    });
  };

  // Owner-coloured keyline around the portrait, inside the well's brass lip
  // (the selected city is always the viewer's own).
  const drawFrame = (g: Graphics) => {
    g.clear();
    g.rect(PORTRAIT_X, PORTRAIT_Y, PORTRAIT_SIZE, PORTRAIT_SIZE).stroke({
      width: 2,
      color: playerColor(owner),
    });
  };

  const nothingAffordable =
    avail.currentType === null && !buttons.some((b) => canRecruit(b, avail));
  const hint = locked
    ? "ORDERS LOCKED"
    : avail.slotsLeft < 1
      ? "UNIT CAP REACHED"
      : nothingAffordable
        ? "NOT ENOUGH MUSTER"
        : null;
  // The advisory column sits right of the type buttons; hide it on a panel too
  // narrow to fit it without overlapping (mirrors the selected-unit warnings).
  const showHint = hint !== null && rect.width >= HINT_X + 190;

  const infoColumns = [
    { label: "BUDGET", value: avail.budget },
    { label: "SLOTS", value: avail.slotsLeft },
  ];

  // Portrait sprites: the city's hex tile fills the box (a max-zoom view of the
  // city), with the staged recruit standing on it once a type is chosen. Same
  // west-native mirror rule as the board's markers.
  const portraitCx = PORTRAIT_X + PORTRAIT_SIZE / 2;
  const portraitCy = PORTRAIT_Y + PORTRAIT_SIZE / 2;
  const tileScale = tile === null ? 1 : PORTRAIT_SIZE / tile.tex.width;
  const unitScale = pair === null ? 1 : PORTRAIT_SIZE / pair.base.width;
  const unitSx = facing === "right" ? -unitScale : unitScale;

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      {chrome !== null && (
        <pixiTilingSprite
          texture={chrome.wood}
          width={rect.width}
          height={rect.height}
          tileScale={{ x: 1.0, y: 1.0 }}
        />
      )}
      <pixiGraphics draw={drawGround} />
      <pixiGraphics draw={drawSheetShadow} />
      {chrome !== null && (
        <>
          <pixiTilingSprite
            texture={chrome.parchment}
            x={FACE_X}
            y={FACE_Y}
            width={FACE_W}
            height={FACE_H}
            tileScale={{ x: PARCHMENT_TILE_SCALE, y: PARCHMENT_TILE_SCALE }}
          />
          <pixiNineSliceSprite
            texture={chrome.sheet}
            x={CARD_X}
            y={CARD_Y}
            width={CARD_W}
            height={CARD_H}
            leftWidth={SHEET_SLICE}
            rightWidth={SHEET_SLICE}
            topHeight={SHEET_SLICE}
            bottomHeight={SHEET_SLICE}
          />
        </>
      )}
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
            tint={playerColor(owner)}
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
        text="RECRUIT"
        style={STYLE_TITLE}
        anchor={{ x: 0.5, y: 0.5 }}
        x={TEXT_X + PLATE_W / 2}
        y={PLATE_Y + PLATE_H / 2}
      />
      <pixiText
        text={`(${city.q},${city.r})`}
        style={STYLE_COORD}
        anchor={{ x: 1, y: 0 }}
        x={rect.width - PANEL_PAD_RIGHT}
        y={PLATE_Y + 6}
      />
      {infoColumns.map((c, i) => (
        <pixiText
          key={c.label}
          text={c.label}
          style={STYLE_COL_LABEL}
          anchor={{ x: 0.5, y: 0 }}
          x={TEXT_X + COL_W / 2 + i * COL_W}
          y={COL_LABEL_Y}
        />
      ))}
      {infoColumns.map((c, i) => (
        <pixiText
          key={c.label}
          text={String(c.value)}
          style={STYLE_COL_VALUE}
          anchor={{ x: 0.5, y: 0 }}
          x={TEXT_X + COL_W / 2 + i * COL_W}
          y={COL_VALUE_Y}
        />
      ))}
      {chrome !== null &&
        buttons.map((b, i) => {
          const r = recruitButtonRect(i);
          const disabled = locked || (avail.currentType !== b.type && !canRecruit(b, avail));
          const active = !locked && avail.currentType === b.type;
          const state = buttonState(b.type, disabled, active, hoverType, pressedType);
          return (
            <pixiSprite
              key={b.type}
              texture={chrome.btn[state]}
              x={r.x}
              y={r.y}
              width={r.width}
              height={r.height}
            />
          );
        })}
      <pixiGraphics draw={drawGlyphs} />
      {buttons.map((b, i) => {
        const r = recruitButtonRect(i);
        const disabled = locked || (avail.currentType !== b.type && !canRecruit(b, avail));
        return (
          <pixiContainer key={b.type}>
            <pixiText
              text={b.label}
              style={disabled ? STYLE_BTN_DISABLED : STYLE_BTN}
              anchor={{ x: 0.5, y: 0.5 }}
              x={r.x + r.width / 2}
              y={r.y + BTN_LABEL_DY}
            />
            <pixiText
              text={`COST ${b.cost}`}
              style={STYLE_COST}
              alpha={disabled ? 0.6 : 1}
              anchor={{ x: 0.5, y: 0.5 }}
              x={r.x + r.width / 2}
              y={r.y + BTN_COST_DY}
            />
          </pixiContainer>
        );
      })}
      <pixiText
        text={`Building: ${avail.currentType ?? "—"}`}
        style={STYLE_BUILDING}
        x={RBTN_X0}
        y={BUILDING_Y}
      />
      {showHint && <pixiText text={`⚠ ${hint}`} style={STYLE_HINT} x={HINT_X} y={HINT_Y} />}
    </pixiContainer>
  );
}
