// The bottom-right game-over indicator (UI-SPEC §11), drawn into the Pixi scene
// graph in place of the lock control once the match is decided — there are no more
// turns to lock, so the button becomes a static win/lose/draw readout. The full
// outcome (with reason) also lives in the header's "Game" menu; this is the at-a-
// glance marker where the player's eyes already are for the lock button.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";

// --- palette (UI-SPEC §14), shared visual language with LockPanel ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
const TEXT_PRIMARY = 0xe8e4dc;
const TEXT_SECONDARY = 0x9aa0a6;
// Win reads green, loss red, draw neutral amber — distinct from the lock blue.
const ACCENT_WIN = 0x6cd07a;
const ACCENT_LOSS = 0xff6b6b;
const ACCENT_DRAW = 0xf5b342;

const PAD = 12;
const BTN_H = 56;

const style = (size: number, fill: number, weight: "normal" | "bold" = "normal") =>
  new TextStyle({ fontFamily: "monospace", fontSize: size, fill, fontWeight: weight });

const STYLE_RESULT = style(16, TEXT_PRIMARY, "bold");
const STYLE_REASON = style(11, TEXT_SECONDARY);

export type GameOutcome = "win" | "lose" | "draw";

export interface GameOverPanelProps {
  rect: Rect;
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
export function GameOverPanel({ rect, outcome, reason }: GameOverPanelProps) {
  const accent = ACCENT[outcome];

  const drawChrome = (g: Graphics) => {
    g.clear();
    g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
    g.moveTo(0, 0).lineTo(rect.width, 0);
    g.stroke({ width: 1, color: PANEL_BORDER });

    // outcome plate, accented by result
    g.rect(PAD, PAD, rect.width - PAD * 2, BTN_H).fill({ color: PANEL_FILL });
    g.rect(PAD, PAD, rect.width - PAD * 2, BTN_H).stroke({ width: 1.5, color: accent });
  };

  const cx = rect.width / 2;

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      <pixiGraphics draw={drawChrome} />
      <pixiText
        text={RESULT_TEXT[outcome]}
        style={STYLE_RESULT}
        anchor={{ x: 0.5, y: 0.5 }}
        x={cx}
        y={PAD + BTN_H / 2}
      />
      <pixiText
        text={reason}
        style={STYLE_REASON}
        anchor={{ x: 0.5, y: 0 }}
        x={cx}
        y={PAD + BTN_H + 12}
      />
    </pixiContainer>
  );
}
