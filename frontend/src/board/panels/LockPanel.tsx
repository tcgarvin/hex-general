// The bottom-right lock control (UI-SPEC §11), drawn into the Pixi scene graph.
// This is a readiness indicator, not an "End Turn" button: in this async game it
// *locks* your orders, and the server resolves once both players lock. Idle units
// are allowed (they hold position), so idle is a non-blocking warning, not a gate.
// The whole button rectangle is the hit target; the board routes clicks to onLock.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";

// --- palette (UI-SPEC §14) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
const BTN_FILL = 0x2f3d63;
const BTN_FILL_BUSY = 0x21283a;
const BTN_BORDER = 0x8fb7ff;
const TEXT_PRIMARY = 0xe8e4dc;
const TEXT_SECONDARY = 0x9aa0a6;
const TEXT_WARN = 0xf5b342;
const TEXT_ERROR = 0xff6b6b;

const PAD = 12;
const BTN_H = 56;

const style = (size: number, fill: number, weight: "normal" | "bold" = "normal") =>
  new TextStyle({ fontFamily: "monospace", fontSize: size, fill, fontWeight: weight });

const STYLE_BTN = style(14, TEXT_PRIMARY, "bold");
const STYLE_TALLY = style(12, TEXT_SECONDARY);
const STYLE_WARN = style(12, TEXT_WARN, "bold");
// A long API error must wrap rather than overflow the narrow panel.
const STYLE_ERROR = new TextStyle({
  fontFamily: "monospace",
  fontSize: 10,
  fill: TEXT_ERROR,
  align: "center",
  wordWrap: true,
  wordWrapWidth: 150,
});

export interface LockPanelProps {
  rect: Rect;
  /** Units with a staged order. */
  ordered: number;
  /** Total of the viewer's units. */
  total: number;
  /** Idle (unordered) units — a non-blocking warning. */
  idle: number;
  /** A submission is in flight (button reads "Locking…"). */
  submitting: boolean;
  /** Submission error, if the last lock attempt failed. */
  error: string | null;
}

/** Docked bottom-right lock readiness block. */
export function LockPanel({ rect, ordered, total, idle, submitting, error }: LockPanelProps) {
  const drawChrome = (g: Graphics) => {
    g.clear();
    g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
    g.moveTo(0, 0).lineTo(rect.width, 0);
    g.stroke({ width: 1, color: PANEL_BORDER });

    // the lock button itself
    g.rect(PAD, PAD, rect.width - PAD * 2, BTN_H).fill({
      color: submitting ? BTN_FILL_BUSY : BTN_FILL,
    });
    g.rect(PAD, PAD, rect.width - PAD * 2, BTN_H).stroke({
      width: 1.5,
      color: BTN_BORDER,
      alpha: submitting ? 0.5 : 1,
    });
  };

  const cx = rect.width / 2;

  return (
    <pixiContainer x={rect.x} y={rect.y}>
      <pixiGraphics draw={drawChrome} />
      <pixiText
        text={submitting ? "Locking…" : "LOCK ORDERS"}
        style={STYLE_BTN}
        anchor={{ x: 0.5, y: 0.5 }}
        x={cx}
        y={PAD + BTN_H / 2}
      />
      <pixiText
        text={`${ordered} / ${total} set`}
        style={STYLE_TALLY}
        anchor={{ x: 0.5, y: 0 }}
        x={cx}
        y={PAD + BTN_H + 12}
      />
      {idle > 0 && (
        <pixiText
          text={`⚠ ${idle} idle`}
          style={STYLE_WARN}
          anchor={{ x: 0.5, y: 0 }}
          x={cx}
          y={PAD + BTN_H + 32}
        />
      )}
      {error !== null && (
        <pixiText
          text={error}
          style={STYLE_ERROR}
          anchor={{ x: 0.5, y: 0 }}
          x={cx}
          y={rect.height - 30}
        />
      )}
    </pixiContainer>
  );
}
