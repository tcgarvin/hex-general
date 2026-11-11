// Keyboard control of the world board: camera keys (held pan, zoom, centre, fit)
// and order keys for the selected unit. One window listener, reading the latest
// planning state through a ref so it isn't re-bound every render.
//
// Pan keys go by physical position (`code`), so WASD sits under the left hand on
// any layout; the rest go by the character typed. Keys are ignored while typing
// in a form field and when Ctrl/Meta/Alt is held (browser shortcuts), apart from
// Ctrl+Enter. Tab and Space only act when focus isn't on a control, so keyboard
// users can still tab through the panels and press buttons.

import { useEffect, useRef } from "react";

import type { Axial } from "@/board/hex";

import { orderKindsFor, type OrderKind } from "./plan";
import type { WorldPlanning } from "./useWorldPlan";
import { TACTICAL_SCALE, type WorldRenderer } from "./WorldRenderer";

/** Zoom step for one key press or button click. */
export const ZOOM_STEP = 1.25;

/** The shortcut list the `?` help shows. */
export const SHORTCUTS: readonly { keys: string; does: string }[] = [
  { keys: "W A S D / arrows", does: "Pan (hold Shift: faster)" },
  { keys: "+ / − or E / Q", does: "Zoom in / out" },
  { keys: "Home or Space", does: "Centre on your base (Home only in playback)" },
  { keys: "F or 0", does: "Show the whole world" },
  { keys: "Tab / Shift+Tab or . / ,", does: "Next / previous unit (idle first)" },
  { keys: "T", does: "Travel mode" },
  { keys: "M", does: "Move mode" },
  { keys: "B", does: "Barrage mode (artillery)" },
  { keys: "R / H", does: "Rest / Hold" },
  { keys: "X or Delete", does: "Clear the unit's order" },
  { keys: "Shift+click", does: "Swap places with a neighbouring unit of yours" },
  { keys: "Esc", does: "Leave the mode, then deselect" },
  { keys: "Ctrl+Enter", does: "Submit the plan" },
  { keys: "?", does: "This list" },
  { keys: "Space", does: "Playback: play / pause" },
  { keys: "← / →", does: "Playback: step back / forward a beat" },
  { keys: "Esc", does: "Playback: back to planning" },
];

const PAN_CODES: Readonly<Record<string, readonly [number, number]>> = {
  KeyW: [0, -1],
  ArrowUp: [0, -1],
  KeyS: [0, 1],
  ArrowDown: [0, 1],
  KeyA: [-1, 0],
  ArrowLeft: [-1, 0],
  KeyD: [1, 0],
  ArrowRight: [1, 0],
};

const ORDER_KEYS: Readonly<Record<string, OrderKind>> = { t: "travel", m: "move", b: "barrage", r: "rest", h: "hold" };

export interface HotkeyOptions {
  renderer: WorldRenderer | null;
  planning: WorldPlanning;
  /** Where Home/Space centre the view (your base). */
  home: Axial | null;
  /** Whether the plan's submit button is enabled. */
  canSubmit: boolean;
  helpOpen: boolean;
  setHelpOpen: (open: boolean) => void;
  /**
   * Turn playback has the board: only the camera keys act here (the transport
   * bar takes Space, Left/Right and Esc), and no orders are given.
   */
  playback?: boolean;
}

/** Camera keys that stay live during playback (Left/Right step the playback instead). */
const PLAYBACK_PAN_CODES = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "ArrowUp", "ArrowDown"]);
const PLAYBACK_KEYS = new Set(["+", "=", "e", "-", "_", "q", "f", "0", "Home", "?"]);

function typingIn(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/** Focus is on nothing in particular (the page or the board), not on a control. */
function focusIsFree(): boolean {
  const el = document.activeElement;
  return el === null || el === document.body || el.tagName === "CANVAS";
}

export function useWorldHotkeys(options: HotkeyOptions): void {
  const latest = useRef(options);
  useEffect(() => {
    latest.current = options;
  });
  const { renderer } = options;

  useEffect(() => {
    if (renderer === null) return;
    const held = new Set<string>();
    let fast = false;
    const syncPan = () => {
      let x = 0;
      let y = 0;
      for (const code of held) {
        x += PAN_CODES[code][0];
        y += PAN_CODES[code][1];
      }
      renderer.setPanInput({ x: Math.sign(x), y: Math.sign(y), fast });
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (typingIn(e.target)) return;
      const o = latest.current;
      if (o.playback === true) {
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
        if (!PLAYBACK_PAN_CODES.has(e.code) && !PLAYBACK_KEYS.has(key)) return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        if (o.canSubmit) void o.planning.submit();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "Shift") {
        fast = true;
        syncPan();
        return;
      }
      if (e.code in PAN_CODES) {
        e.preventDefault(); // arrows would scroll a panel
        fast = e.shiftKey;
        held.add(e.code);
        syncPan();
        return;
      }
      if (handleKey(e, o, renderer)) e.preventDefault();
    };

    const onKeyUp = (e: KeyboardEvent) => {
      // Always let go, even with a modifier now held, so a pan never sticks.
      if (e.key === "Shift") fast = false;
      held.delete(e.code);
      syncPan();
    };

    const onBlur = () => {
      held.clear();
      fast = false;
      syncPan();
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
      renderer.setPanInput({ x: 0, y: 0, fast: false });
    };
  }, [renderer]);
}

/** Act on one non-pan key; whether it was ours (and the browser default should be stopped). */
function handleKey(e: KeyboardEvent, o: HotkeyOptions, renderer: WorldRenderer): boolean {
  const { planning } = o;
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  switch (key) {
    case "Escape":
      if (o.helpOpen) o.setHelpOpen(false);
      else planning.cancel();
      return true;
    case "?":
      o.setHelpOpen(!o.helpOpen);
      return true;
    case "+":
    case "=":
    case "e":
      renderer.zoomBy(ZOOM_STEP);
      return true;
    case "-":
    case "_":
    case "q":
      renderer.zoomBy(1 / ZOOM_STEP);
      return true;
    case "f":
    case "0":
      renderer.fitAll();
      return true;
    case "Home":
    case " ":
      if (key === " " && !focusIsFree()) return false;
      if (o.home !== null) renderer.centerOn(o.home, TACTICAL_SCALE);
      return true;
    case "Tab":
    case ".":
    case ",": {
      if (key === "Tab" && !focusIsFree()) return false;
      const back = key === "," || (key === "Tab" && e.shiftKey);
      const unit = planning.cycleUnit(back ? -1 : 1);
      if (unit !== null) renderer.focusUnit(unit.coord);
      return true;
    }
  }
  const unit = planning.selectedUnit;
  if (unit === null || e.repeat) return false;
  if (key === "x" || key === "Delete") {
    planning.setOrder(unit.id, null);
    return true;
  }
  const kind = ORDER_KEYS[key];
  if (kind === undefined || !orderKindsFor(unit).includes(kind)) return false;
  if (kind === "travel" || kind === "move" || kind === "barrage") {
    planning.setMode(kind);
  } else {
    planning.setMode("travel");
    planning.setOrder(unit.id, { kind } as { kind: "rest" | "hold" });
  }
  return true;
}
