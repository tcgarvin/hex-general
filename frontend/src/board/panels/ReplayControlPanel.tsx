// Replay control strip (UI-SPEC §13.2): full-width bottom dock shown during
// replay. Draws the beat nav buttons, speed toggle, beat label, and the
// skip-to-end / start-planning button into the Pixi scene graph.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import { beatDisplayLabel, type Beat } from "@/replay/beats";

import type { Rect } from "../layout";
import {
  BTN_BACK_X,
  BTN_FWD_X,
  BTN_H,
  BTN_PLAY_X,
  BTN_REWIND_X,
  BTN_W,
  BTN_Y,
  CTRL_PAD,
  SKIP_W,
  SPEED_W,
  SPEED_X,
  START_W,
} from "./replayControls";

// --- palette (mirrors the other panels: UI-SPEC §14) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
const BTN_FILL = 0x2a3550;
const BTN_FILL_ACTIVE = 0x1e4080;
const BTN_FILL_DISABLED = 0x1a1d24;
const BTN_BORDER = 0x8fb7ff;
const BTN_BORDER_DISABLED = 0x3a3f4b;
const TEXT_PRIMARY = 0xe8e4dc;
const TEXT_SECONDARY = 0x9aa0a6;
const TEXT_ACCENT = 0x8fb7ff;
const TEXT_DISABLED = 0x4a5060;
const TEXT_BEAT = 0xffe066;

const style = (size: number, fill: number, weight: "normal" | "bold" = "normal") =>
  new TextStyle({ fontFamily: "monospace", fontSize: size, fill, fontWeight: weight });

const S_BTN = style(14, TEXT_PRIMARY, "bold");
const S_BTN_ACTIVE = style(14, TEXT_ACCENT, "bold");
const S_BTN_DISABLED = style(14, TEXT_DISABLED, "bold");
const S_SPEED = style(12, TEXT_SECONDARY, "bold");
const S_SPEED_ACTIVE = style(12, TEXT_ACCENT, "bold");
const S_BEAT = style(13, TEXT_BEAT, "bold");
const S_ACTION = style(13, TEXT_PRIMARY, "bold");

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

/** Docked full-width bottom strip for replay playback (UI-SPEC §13.2). */
export function ReplayControlPanel({
  rect,
  beats,
  currentBeat,
  playing,
  speed,
  gameOver,
}: ReplayControlsProps & { rect: Rect }) {
  const atStart = currentBeat < 0;
  const atEnd = currentBeat >= beats.length - 1;
  const label = beatDisplayLabel(beats, currentBeat);

  const drawChrome = (g: Graphics) => {
    g.clear();
    g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
    g.moveTo(0, 0).lineTo(rect.width, 0).stroke({ width: 1, color: PANEL_BORDER });

    const drawBtn = (bx: number, bw: number, active: boolean, disabled: boolean) => {
      g.rect(bx, BTN_Y, bw, BTN_H).fill({
        color: active ? BTN_FILL_ACTIVE : disabled ? BTN_FILL_DISABLED : BTN_FILL,
      });
      g.rect(bx, BTN_Y, bw, BTN_H).stroke({
        width: 1,
        color: disabled ? BTN_BORDER_DISABLED : BTN_BORDER,
        alpha: disabled ? 0.4 : 1,
      });
    };

    drawBtn(BTN_REWIND_X, BTN_W, false, atStart);
    drawBtn(BTN_BACK_X, BTN_W, false, atStart);
    drawBtn(BTN_PLAY_X, BTN_W, playing, atEnd && !playing);
    drawBtn(BTN_FWD_X, BTN_W, false, atEnd);
    drawBtn(SPEED_X, SPEED_W, false, false);

    if (atEnd) {
      drawBtn(rect.width - CTRL_PAD - START_W, START_W, false, false);
    } else {
      drawBtn(rect.width - CTRL_PAD - SKIP_W, SKIP_W, false, false);
    }
  };

  const btnCx = (bx: number, bw: number) => bx + bw / 2;
  const btnCy = BTN_Y + BTN_H / 2;

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      <pixiGraphics draw={drawChrome} />

      {/* |◄ rewind to start */}
      <pixiText
        text="|◄"
        style={atStart ? S_BTN_DISABLED : S_BTN}
        anchor={{ x: 0.5, y: 0.5 }}
        x={btnCx(BTN_REWIND_X, BTN_W)}
        y={btnCy}
      />

      {/* ◄ step back one beat */}
      <pixiText
        text="◄"
        style={atStart ? S_BTN_DISABLED : S_BTN}
        anchor={{ x: 0.5, y: 0.5 }}
        x={btnCx(BTN_BACK_X, BTN_W)}
        y={btnCy}
      />

      {/* ▶ / ⏸ play or pause */}
      <pixiText
        text={playing ? "⏸" : "▶"}
        style={playing ? S_BTN_ACTIVE : atEnd ? S_BTN_DISABLED : S_BTN}
        anchor={{ x: 0.5, y: 0.5 }}
        x={btnCx(BTN_PLAY_X, BTN_W)}
        y={btnCy}
      />

      {/* ►| step forward one beat */}
      <pixiText
        text="►|"
        style={atEnd ? S_BTN_DISABLED : S_BTN}
        anchor={{ x: 0.5, y: 0.5 }}
        x={btnCx(BTN_FWD_X, BTN_W)}
        y={btnCy}
      />

      {/* speed toggle */}
      <pixiText
        text={`${speed}×`}
        style={speed === 2 ? S_SPEED_ACTIVE : S_SPEED}
        anchor={{ x: 0.5, y: 0.5 }}
        x={btnCx(SPEED_X, SPEED_W)}
        y={btnCy}
      />

      {/* beat label — centered in the available space */}
      <pixiText
        text={label}
        style={S_BEAT}
        anchor={{ x: 0.5, y: 0.5 }}
        x={rect.width / 2}
        y={btnCy}
      />

      {/* skip to end / start planning / see results */}
      {atEnd ? (
        <pixiText
          text={gameOver ? "See Results" : "Start Planning"}
          style={S_ACTION}
          anchor={{ x: 0.5, y: 0.5 }}
          x={rect.width - CTRL_PAD - START_W / 2}
          y={btnCy}
        />
      ) : (
        <pixiText
          text="Skip ►|"
          style={S_BTN}
          anchor={{ x: 0.5, y: 0.5 }}
          x={rect.width - CTRL_PAD - SKIP_W / 2}
          y={btnCy}
        />
      )}
    </pixiContainer>
  );
}
