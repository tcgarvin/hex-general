// Replay control strip (UI-SPEC §13.2): full-width bottom dock shown during
// replay, drawn in the ratified Field-HQ register (docs/UI-STYLE.md §4). The
// strip is the dock's dark wood slab (brass seam on the map edge); the beat
// nav + speed toggle are square brass icon keycaps with engraved transport
// glyphs (play latches pressed while playing, 2× latches pressed while fast),
// the beat readout is typewriter ink on the wood beneath them, and the
// skip / start-planning action is a wide commit plate like LOCK ORDERS.
// Geometry + hit-testing live in ./replayControls; this file is just the
// drawing. Until the chrome sprites arrive it falls back to a flat placeholder
// look at the same geometry, mirroring the other panels.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import { beatDisplayLabel, type Beat } from "@/replay/beats";
import { FONT_DATA, FONT_DISPLAY, FONT_LABEL, THEME } from "@/theme/theme";

import type { Rect } from "../layout";
import type { IconBtnState, LoadedChrome } from "./chrome";
import {
  ACTION_FACE_CY,
  ACTION_H,
  ACTION_W,
  ACTION_Y,
  BEAT_LABEL_CX,
  BEAT_LABEL_Y,
  BTN_BACK_X,
  BTN_FACE_CX,
  BTN_FACE_CY,
  BTN_FWD_X,
  BTN_PLATE_DY,
  BTN_PLATE_H,
  BTN_PLAY_X,
  BTN_REWIND_X,
  BTN_SPRITE_H,
  BTN_W,
  BTN_Y,
  CTRL_PAD,
  SPEED_X,
  type ReplayAction,
} from "./replayControls";

// --- fallback palette (pre-chrome placeholder, UI-SPEC §14) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
const BTN_FILL = 0x2a3550;
const BTN_FILL_ACTIVE = 0x1e4080;
const BTN_FILL_DISABLED = 0x1a1d24;
const BTN_BORDER = 0x8fb7ff;

/** A dimmed keycap stands in for missing disabled art (no kit sprite). */
const DISABLED_ALPHA = 0.45;
/** Engraved glyph ink on the brass faces. */
const GLYPH_ALPHA = 0.8;

const STYLE_BEAT = new TextStyle({ fontFamily: FONT_DATA, fontSize: 12, fill: THEME.ivory100 });
const STYLE_SPEED = new TextStyle({
  fontFamily: FONT_LABEL,
  fontSize: 14,
  fill: THEME.ink900,
  fontWeight: "bold",
  letterSpacing: 1,
});
const STYLE_SPEED_FALLBACK = new TextStyle({
  fontFamily: FONT_LABEL,
  fontSize: 14,
  fill: THEME.ivory100,
  fontWeight: "bold",
  letterSpacing: 1,
});
// Sized like the LOCK ORDERS engraving: "START PLANNING" (~100px at 11px)
// stays inside the commit face's flat area at the drawn scale.
const STYLE_ACTION = new TextStyle({
  fontFamily: FONT_DISPLAY,
  fontSize: 11,
  fill: THEME.ink900,
  letterSpacing: 0.5,
});
const STYLE_ACTION_FALLBACK = new TextStyle({
  fontFamily: FONT_DISPLAY,
  fontSize: 11,
  fill: THEME.ivory100,
  letterSpacing: 0.5,
});

export interface ReplayControlsProps {
  beats: Beat[];
  /** -1 = pre-turn, 0..N-1 = resolved beat. */
  currentBeat: number;
  playing: boolean;
  speed: 1 | 2;
  onRewind: () => void;
  onBack: () => void;
  onPlayPause: () => void;
  onForward: () => void;
  onSkip: () => void;
  onToggleSpeed: () => void;
  onStartPlanning: () => void;
  /** Whether the game has ended (changes the action button label). */
  gameOver: boolean;
}

type TransportButton = "rewind" | "back" | "play_pause" | "forward" | "speed";

/** Which keycap art a transport button shows: a held press or a latched state
 *  (playing, 2×) sinks the face; disabled dims the up art (no kit sprite). */
function keycapState(
  action: TransportButton,
  latched: boolean,
  pressedAction: ReplayAction | null,
): IconBtnState {
  if (pressedAction === action || latched) return "pressed";
  return "up";
}

/** Engraved transport glyph on the keycap face: solid ink, stencil shapes. */
function drawTransportGlyph(
  g: Graphics,
  action: TransportButton,
  playing: boolean,
  cx: number,
  cy: number,
  color: number,
  alpha: number,
): void {
  switch (action) {
    case "rewind":
      g.rect(cx - 10, cy - 8, 3, 16).fill({ color, alpha });
      g.poly([cx + 9, cy - 8, cx + 9, cy + 8, cx - 4, cy]).fill({ color, alpha });
      break;
    case "back":
      g.poly([cx + 7, cy - 8, cx + 7, cy + 8, cx - 8, cy]).fill({ color, alpha });
      break;
    case "play_pause":
      if (playing) {
        g.rect(cx - 7, cy - 8, 5, 16).fill({ color, alpha });
        g.rect(cx + 2, cy - 8, 5, 16).fill({ color, alpha });
      } else {
        g.poly([cx - 6, cy - 9, cx + 9, cy, cx - 6, cy + 9]).fill({ color, alpha });
      }
      break;
    case "forward":
      g.poly([cx - 7, cy - 8, cx - 7, cy + 8, cx + 8, cy]).fill({ color, alpha });
      break;
    case "speed":
      // Text glyph — drawn as pixiText, not here.
      break;
  }
}

/** Docked full-width bottom strip for replay playback (UI-SPEC §13.2). */
export function ReplayControlPanel({
  rect,
  chrome,
  pressedAction,
  beats,
  currentBeat,
  playing,
  speed,
  gameOver,
}: ReplayControlsProps & {
  rect: Rect;
  /** Field-HQ chrome sprites (null until loaded → flat placeholder look). */
  chrome: LoadedChrome | null;
  /** Control currently held down (visual pressed state only). */
  pressedAction: ReplayAction | null;
}) {
  const atStart = currentBeat < 0;
  const atEnd = currentBeat >= beats.length - 1;
  const label = beatDisplayLabel(beats, currentBeat);
  const actionX = rect.width - CTRL_PAD - ACTION_W;

  const transport: {
    action: TransportButton;
    x: number;
    disabled: boolean;
    latched: boolean;
  }[] = [
    { action: "rewind", x: BTN_REWIND_X, disabled: atStart, latched: false },
    { action: "back", x: BTN_BACK_X, disabled: atStart, latched: false },
    { action: "play_pause", x: BTN_PLAY_X, disabled: atEnd && !playing, latched: playing },
    { action: "forward", x: BTN_FWD_X, disabled: atEnd, latched: false },
    { action: "speed", x: SPEED_X, disabled: false, latched: speed === 2 },
  ];

  // Ground layer: dark wood + a light wash + the brass map-edge seam, matching
  // the planning dock so the bottom strip reads as the same wood slab. The
  // fallback keeps the flat placeholder look (including plain button plates).
  const drawGround = (g: Graphics) => {
    g.clear();
    if (chrome === null) {
      g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
      g.moveTo(0, 0).lineTo(rect.width, 0).stroke({ width: 1, color: PANEL_BORDER });
      for (const b of transport) {
        const active = b.latched && !b.disabled;
        g.rect(b.x, BTN_Y + BTN_PLATE_DY, BTN_W, BTN_PLATE_H).fill({
          color: active ? BTN_FILL_ACTIVE : b.disabled ? BTN_FILL_DISABLED : BTN_FILL,
        });
        g.rect(b.x, BTN_Y + BTN_PLATE_DY, BTN_W, BTN_PLATE_H).stroke({
          width: 1,
          color: BTN_BORDER,
          alpha: b.disabled ? 0.4 : 1,
        });
      }
      g.rect(actionX, ACTION_Y, ACTION_W, ACTION_H).fill({ color: BTN_FILL });
      g.rect(actionX, ACTION_Y, ACTION_W, ACTION_H).stroke({ width: 1, color: BTN_BORDER });
      return;
    }
    g.rect(0, 0, rect.width, rect.height).fill({ color: THEME.ink900, alpha: 0.18 });
    g.rect(0, 0, rect.width, 2).fill({ color: THEME.brass600, alpha: 0.7 });
  };

  // Engraved glyphs over the keycap faces (both render paths); the face sinks
  // with the pressed art and the glyph follows it.
  const drawGlyphs = (g: Graphics) => {
    g.clear();
    const color = chrome === null ? THEME.ivory100 : THEME.ink900;
    for (const b of transport) {
      if (b.action === "speed") continue;
      const state = keycapState(b.action, b.latched, b.disabled ? null : pressedAction);
      const sunk = chrome !== null && state === "pressed" ? 2 : 0;
      drawTransportGlyph(
        g,
        b.action,
        playing,
        b.x + BTN_W * BTN_FACE_CX,
        BTN_Y + BTN_SPRITE_H * BTN_FACE_CY + sunk,
        color,
        b.disabled ? GLYPH_ALPHA * 0.4 : GLYPH_ALPHA,
      );
    }
  };

  const speedState = keycapState("speed", speed === 2, pressedAction);
  const actionPressed =
    pressedAction === "skip" || pressedAction === "start_planning" ? "pressed" : "up";

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      {chrome !== null && (
        <pixiTilingSprite
          texture={chrome.wood}
          width={rect.width}
          height={rect.height}
          tileScale={{ x: 1.0, y: 1.0 }}
          // Continue the dock's wood grain across the strip's panel seams.
          tilePosition={{ x: -rect.x, y: 0 }}
        />
      )}
      <pixiGraphics draw={drawGround} />

      {/* Transport + speed keycaps. */}
      {chrome !== null &&
        transport.map((b) => (
          <pixiSprite
            key={b.action}
            texture={
              chrome.iconbtn[keycapState(b.action, b.latched, b.disabled ? null : pressedAction)]
            }
            x={b.x}
            y={BTN_Y}
            width={BTN_W}
            height={BTN_SPRITE_H}
            alpha={b.disabled ? DISABLED_ALPHA : 1}
          />
        ))}
      <pixiGraphics draw={drawGlyphs} />

      {/* Speed toggle: engraved 1× / 2× on the keycap face. */}
      <pixiText
        text={`${speed}×`}
        style={chrome === null ? STYLE_SPEED_FALLBACK : STYLE_SPEED}
        alpha={chrome === null ? 1 : GLYPH_ALPHA}
        anchor={{ x: 0.5, y: 0.5 }}
        x={SPEED_X + BTN_W * BTN_FACE_CX}
        y={BTN_Y + BTN_SPRITE_H * BTN_FACE_CY + (chrome !== null && speedState === "pressed" ? 2 : 0)}
      />

      {/* Beat readout: typewriter ink on the wood, under the transport row. */}
      <pixiText
        text={label}
        style={STYLE_BEAT}
        anchor={{ x: 0.5, y: 0.5 }}
        x={BEAT_LABEL_CX}
        y={BEAT_LABEL_Y}
      />

      {/* Skip / start-planning action: a wide commit plate like LOCK ORDERS. */}
      {chrome !== null && (
        <pixiSprite
          texture={chrome.commit[actionPressed]}
          x={actionX}
          y={ACTION_Y}
          width={ACTION_W}
          height={ACTION_H}
        />
      )}
      <pixiText
        text={atEnd ? (gameOver ? "SEE RESULTS" : "START PLANNING") : "SKIP"}
        style={chrome === null ? STYLE_ACTION_FALLBACK : STYLE_ACTION}
        alpha={chrome === null ? 1 : 0.85}
        anchor={{ x: 0.5, y: 0.5 }}
        x={actionX + ACTION_W / 2}
        y={ACTION_Y + ACTION_H * ACTION_FACE_CY + (chrome !== null && actionPressed === "pressed" ? 2 : 0)}
      />
    </pixiContainer>
  );
}
