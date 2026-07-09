// The bottom-right game-over indicator (UI-SPEC §11), drawn into the Pixi scene
// graph in place of the lock control once the match is decided — there are no
// more turns to lock, so the button's spot becomes a static win/lose/draw
// readout. In the ratified Field-HQ register (docs/UI-STYLE.md §4) that readout
// is an engraved brass nameplate mounted on the dock's dark wood, ringed by an
// outcome-coloured keyline (win green / loss red / draw amber), with the victory
// reason in typewriter ink beneath. The full outcome also lives in the header's
// "Game" menu; this is the at-a-glance marker where the player's eyes already
// are for the lock button. Until the chrome sprites arrive it falls back to a
// flat placeholder look at the same geometry, mirroring the other panels.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";
import { FONT_DATA, FONT_DISPLAY, THEME } from "@/theme/theme";

import type { LoadedChrome } from "./chrome";

// --- fallback palette (pre-chrome placeholder, UI-SPEC §14) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
const FALLBACK_TEXT = 0xe8e4dc;

// Win reads green, loss red, draw neutral amber — the theme's status colours,
// distinct from the commit plate this readout replaces.
const ACCENT_WIN = THEME.valid;
const ACCENT_LOSS = THEME.invalid;
const ACCENT_DRAW = THEME.warning;

// --- geometry (local to the 176×140 lock rect) ---
const PAD = 13;
const PLATE_Y = 28;
const PLATE_H = 32;
/** Reason line under the plate (word-wrapped for long reasons). */
const REASON_Y = PLATE_Y + PLATE_H + 14;

// NineSlice insets for the nameplate's screw-capped ends (scripts/ui-kit-prep.py).
const PLATE_SLICE_X = 24;
const PLATE_SLICE_Y = 12;

const STYLE_RESULT = new TextStyle({
  fontFamily: FONT_DISPLAY,
  fontSize: 15,
  fill: THEME.ink900,
  letterSpacing: 1,
});
// Fallback plate is dark, so the pre-chrome result is light instead of engraved.
const STYLE_RESULT_FALLBACK = new TextStyle({
  fontFamily: FONT_DISPLAY,
  fontSize: 15,
  fill: FALLBACK_TEXT,
  letterSpacing: 1,
});
const STYLE_REASON = new TextStyle({
  fontFamily: FONT_DATA,
  fontSize: 11,
  fill: THEME.ivory400,
  align: "center",
  wordWrap: true,
  wordWrapWidth: 150,
});

export type GameOutcome = "win" | "lose" | "draw";

export interface GameOverPanelProps {
  rect: Rect;
  /** Field-HQ chrome sprites (null until loaded → flat placeholder look). */
  chrome: LoadedChrome | null;
  outcome: GameOutcome;
  /** The victory reason, e.g. "elimination" / "turn limit" (already humanised). */
  reason: string;
}

const RESULT_TEXT: Record<GameOutcome, string> = {
  win: "YOU WIN",
  lose: "YOU LOSE",
  draw: "DRAW",
};

const ACCENT: Record<GameOutcome, number> = {
  win: ACCENT_WIN,
  lose: ACCENT_LOSS,
  draw: ACCENT_DRAW,
};

/** Docked bottom-right outcome block, sized like the lock control it replaces. */
export function GameOverPanel({ rect, chrome, outcome, reason }: GameOverPanelProps) {
  const accent = ACCENT[outcome];
  const plateW = rect.width - PAD * 2;

  // Ground layer: dark wood + a light wash + the brass map-edge seam, matching
  // the lock panel this readout replaces so the bottom strip reads as one wood
  // slab. The fallback keeps the flat placeholder look (plain accent plate).
  const drawGround = (g: Graphics) => {
    g.clear();
    if (chrome === null) {
      g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
      g.moveTo(0, 0).lineTo(rect.width, 0);
      g.stroke({ width: 1, color: PANEL_BORDER });
      g.rect(PAD, PLATE_Y, plateW, PLATE_H).fill({ color: PANEL_FILL });
      g.rect(PAD, PLATE_Y, plateW, PLATE_H).stroke({ width: 1.5, color: accent });
      return;
    }
    g.rect(0, 0, rect.width, rect.height).fill({ color: THEME.ink900, alpha: 0.18 });
    g.rect(0, 0, rect.width, 2).fill({ color: THEME.brass600, alpha: 0.7 });
  };

  // Outcome keyline ringing the mounted nameplate (chrome path only).
  const drawAccent = (g: Graphics) => {
    g.clear();
    if (chrome === null) return;
    g.roundRect(PAD - 3, PLATE_Y - 3, plateW + 6, PLATE_H + 6, 3).stroke({
      width: 2,
      color: accent,
      alpha: 0.9,
    });
  };

  const cx = rect.width / 2;

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      {chrome !== null && (
        <pixiTilingSprite
          texture={chrome.wood}
          width={rect.width}
          height={rect.height}
          tileScale={{ x: 1.0, y: 1.0 }}
          // Continue the dock's wood grain across the panel seam: both panels
          // sit on one strip, so offset the pattern by this panel's origin.
          tilePosition={{ x: -rect.x, y: 0 }}
        />
      )}
      <pixiGraphics draw={drawGround} />
      <pixiGraphics draw={drawAccent} />
      {chrome !== null && (
        <pixiNineSliceSprite
          texture={chrome.plate}
          x={PAD}
          y={PLATE_Y}
          width={plateW}
          height={PLATE_H}
          leftWidth={PLATE_SLICE_X}
          rightWidth={PLATE_SLICE_X}
          topHeight={PLATE_SLICE_Y}
          bottomHeight={PLATE_SLICE_Y}
        />
      )}
      <pixiText
        text={RESULT_TEXT[outcome]}
        style={chrome === null ? STYLE_RESULT_FALLBACK : STYLE_RESULT}
        anchor={{ x: 0.5, y: 0.5 }}
        x={cx}
        y={PLATE_Y + PLATE_H / 2}
      />
      <pixiText
        text={reason}
        style={STYLE_REASON}
        anchor={{ x: 0.5, y: 0 }}
        x={cx}
        y={REASON_Y}
      />
    </pixiContainer>
  );
}
