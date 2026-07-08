// The bottom-right lock control (UI-SPEC §11), drawn into the Pixi scene graph
// in the ratified Field-HQ register (docs/UI-STYLE.md §4): the commit plate — a
// wide blank brass face on a screwed wood backplate — sits directly on the
// bottom dock's dark wood strip, with the stencil LOCK ORDERS label engraved on
// the brass and the readiness tally in typewriter ink below. This is a
// readiness indicator, not an "End Turn" button: in this async game it *locks*
// your orders, and the server resolves once both players lock. Idle units are
// allowed (they hold position), so idle is a non-blocking warning, not a gate.
// The whole panel rectangle is the hit target; the board routes clicks to
// onLock. Until the chrome sprites arrive it falls back to a flat placeholder
// look at the same geometry, mirroring the other panels.

import type { Graphics } from "pixi.js";
import { TextStyle } from "pixi.js";

import type { Rect } from "@/board/layout";
import { FONT_DATA, FONT_DISPLAY, FONT_LABEL, THEME } from "@/theme/theme";

import type { CommitState, LoadedChrome } from "./chrome";

// --- fallback palette (pre-chrome placeholder, UI-SPEC §14) ---
const PANEL_FILL = 0x14161c;
const PANEL_BORDER = 0x3a3f4b;
const BTN_FILL = 0x2f3d63;
const BTN_FILL_BUSY = 0x21283a;
const BTN_BORDER = 0x8fb7ff;

// --- geometry (local to the 176×140 lock rect) ---
/** Commit-plate footprint: the 237×155 sprite at ~0.61, aspect preserved. */
const BTN_X = 16;
const BTN_Y = 6;
const BTN_W = 144;
const BTN_H = 94;
/** The brass face's center within the sprite (fraction of its height). */
const BTN_FACE_CY = 0.5;
/** Readiness tally under the plate, then one advisory line under that. */
const TALLY_Y = BTN_Y + BTN_H + 6;
const ADVISORY_Y = TALLY_Y + 18;

// Sized so "LOCK ORDERS" (~94px at 13px + 0.5 tracking) stays inside the brass
// face's flat area (~104px within the sprite's chamfers at the drawn scale).
const STYLE_COMMIT = new TextStyle({
  fontFamily: FONT_DISPLAY,
  fontSize: 13,
  fill: THEME.ink900,
  letterSpacing: 0.5,
});
// Fallback plate is dark, so the pre-chrome label is light instead of engraved.
const STYLE_COMMIT_FALLBACK = new TextStyle({
  fontFamily: FONT_DISPLAY,
  fontSize: 13,
  fill: THEME.ivory100,
  letterSpacing: 0.5,
});
const STYLE_TALLY = new TextStyle({ fontFamily: FONT_DATA, fontSize: 12, fill: THEME.ivory400 });
const STYLE_WARN = new TextStyle({
  fontFamily: FONT_LABEL,
  fontSize: 11,
  fill: THEME.warning,
  fontWeight: "bold",
  letterSpacing: 1,
});
// A long API error must wrap rather than overflow the narrow panel.
const STYLE_ERROR = new TextStyle({
  fontFamily: FONT_LABEL,
  fontSize: 10,
  fill: THEME.invalid,
  align: "center",
  wordWrap: true,
  wordWrapWidth: 150,
});

export interface LockPanelProps {
  rect: Rect;
  /** Field-HQ chrome sprites (null until loaded → flat placeholder look). */
  chrome: LoadedChrome | null;
  /** Units with a staged order. */
  ordered: number;
  /** Total of the viewer's units. */
  total: number;
  /** Idle (unordered) units — a non-blocking warning. */
  idle: number;
  /** A submission is in flight (button reads "LOCKING…"). */
  submitting: boolean;
  /** Submission error, if the last lock attempt failed. */
  error: string | null;
  /** Orders are already locked: the plate stays pressed in and is inert. */
  locked: boolean;
}

/** Which commit-plate art a lock state shows: locked keeps the plate sunk in,
 *  an in-flight submission greys it out, otherwise it sits proud. */
function commitState(submitting: boolean, locked: boolean): CommitState {
  if (locked) return "pressed";
  if (submitting) return "disabled";
  return "up";
}

/** Docked bottom-right lock readiness block. */
export function LockPanel({ rect, chrome, ordered, total, idle, submitting, error, locked }: LockPanelProps) {
  const state = commitState(submitting, locked);
  const label = locked ? "LOCKED" : submitting ? "LOCKING…" : "LOCK ORDERS";
  // The brass face sinks with the pressed art; the engraved label follows it.
  const sunk = state === "pressed" ? 2 : 0;

  // Ground layer: dark wood + a light wash + the brass map-edge seam, matching
  // the selected-unit dock so the bottom strip reads as one wood slab. The
  // fallback keeps the flat placeholder look (including a plain button plate).
  const drawGround = (g: Graphics) => {
    g.clear();
    if (chrome === null) {
      g.rect(0, 0, rect.width, rect.height).fill({ color: PANEL_FILL });
      g.moveTo(0, 0).lineTo(rect.width, 0);
      g.stroke({ width: 1, color: PANEL_BORDER });
      const dimmed = submitting || locked;
      g.rect(BTN_X, BTN_Y, BTN_W, BTN_H).fill({ color: dimmed ? BTN_FILL_BUSY : BTN_FILL });
      g.rect(BTN_X, BTN_Y, BTN_W, BTN_H).stroke({
        width: 1.5,
        color: BTN_BORDER,
        alpha: dimmed ? 0.5 : 1,
      });
      return;
    }
    g.rect(0, 0, rect.width, rect.height).fill({ color: THEME.ink900, alpha: 0.18 });
    g.rect(0, 0, rect.width, 2).fill({ color: THEME.brass600, alpha: 0.7 });
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
      {chrome !== null && (
        <pixiSprite texture={chrome.commit[state]} x={BTN_X} y={BTN_Y} width={BTN_W} height={BTN_H} />
      )}
      <pixiText
        text={label}
        style={chrome === null ? STYLE_COMMIT_FALLBACK : STYLE_COMMIT}
        alpha={chrome === null ? 1 : state === "up" ? 0.85 : 0.65}
        anchor={{ x: 0.5, y: 0.5 }}
        x={cx}
        y={BTN_Y + BTN_H * BTN_FACE_CY + sunk}
      />
      {locked ? (
        <pixiText
          text="waiting on opponent…"
          style={STYLE_TALLY}
          anchor={{ x: 0.5, y: 0 }}
          x={cx}
          y={TALLY_Y}
        />
      ) : (
        <>
          <pixiText
            text={`${ordered} / ${total} set`}
            style={STYLE_TALLY}
            anchor={{ x: 0.5, y: 0 }}
            x={cx}
            y={TALLY_Y}
          />
          {/* One advisory line: a failed submission outranks the idle nudge. */}
          {error !== null ? (
            <pixiText
              text={error}
              style={STYLE_ERROR}
              anchor={{ x: 0.5, y: 0 }}
              x={cx}
              y={ADVISORY_Y}
            />
          ) : (
            idle > 0 && (
              <pixiText
                text={`⚠ ${idle} IDLE`}
                style={STYLE_WARN}
                anchor={{ x: 0.5, y: 0 }}
                x={cx}
                y={ADVISORY_Y}
              />
            )
          )}
        </>
      )}
    </pixiContainer>
  );
}
