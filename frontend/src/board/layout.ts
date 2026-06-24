// Surface layout for the all-Pixi game board (UI-SPEC §2): a single Pixi canvas
// hosts the map plus docked chrome panels that reserve space at the edges. This
// module turns the canvas size into the rectangles each piece occupies, and gives
// the input layer a cheap hit-test for "which region is this pointer over?". The
// panels render *over* the map's rectangle (opaque), and the map is fit/centered
// into whatever rectangle is left so nothing important hides behind a panel.

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Width of the left-docked roster panel (UI-SPEC §5). */
export const ROSTER_WIDTH = 224;

/** Height of the bottom-docked strip holding the selected-unit + lock panels. */
export const BOTTOM_HEIGHT = 140;

/** Width of the lock control at the right of the bottom strip (UI-SPEC §11). */
export const LOCK_WIDTH = 176;

export interface SurfaceLayout {
  /** Rectangle the map pans/zooms within (everything not covered by a panel). */
  map: Rect;
  /** Left-docked roster panel (UI-SPEC §5). */
  roster: Rect;
  /** Bottom-left: selected-unit detail + order palette (UI-SPEC §6, §8). */
  selectedUnit: Rect;
  /** Bottom-right: lock control (UI-SPEC §11). */
  lock: Rect;
}

/** An empty rectangle, used when a panel is hidden (e.g. outside planning). */
const EMPTY_RECT: Rect = { x: 0, y: 0, width: 0, height: 0 };

/**
 * Split the canvas into the map rectangle and the docked panel rectangles for
 * the planning surface: the roster runs full-height on the left, and a bottom
 * strip (to the right of the roster) carries the selected-unit panel and the
 * lock control. The map fills whatever rectangle is left.
 */
export function surfaceLayout(width: number, height: number): SurfaceLayout {
  const rosterWidth = Math.min(ROSTER_WIDTH, Math.max(0, width));
  const bottomWidth = Math.max(0, width - rosterWidth);
  const bottomHeight = Math.min(BOTTOM_HEIGHT, Math.max(0, height));
  const bottomY = height - bottomHeight;
  const lockWidth = Math.min(LOCK_WIDTH, bottomWidth);
  return {
    roster: { x: 0, y: 0, width: rosterWidth, height },
    selectedUnit: {
      x: rosterWidth,
      y: bottomY,
      width: bottomWidth - lockWidth,
      height: bottomHeight,
    },
    lock: {
      x: width - lockWidth,
      y: bottomY,
      width: lockWidth,
      height: bottomHeight,
    },
    map: {
      x: rosterWidth,
      y: 0,
      width: bottomWidth,
      height: bottomY,
    },
  };
}

/** The full-map layout used outside planning (no docked panels). */
export function fullMapLayout(width: number, height: number): SurfaceLayout {
  return {
    map: { x: 0, y: 0, width, height },
    roster: EMPTY_RECT,
    selectedUnit: EMPTY_RECT,
    lock: EMPTY_RECT,
  };
}

/** Whether a canvas-space point falls inside `rect`. */
export function pointInRect(x: number, y: number, rect: Rect): boolean {
  return (
    x >= rect.x &&
    x < rect.x + rect.width &&
    y >= rect.y &&
    y < rect.y + rect.height
  );
}
