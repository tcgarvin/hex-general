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

export interface SurfaceLayout {
  /** Rectangle the map pans/zooms within (everything not covered by a panel). */
  map: Rect;
  /** Left-docked roster panel. */
  roster: Rect;
}

/** Split the canvas into the map rectangle and the docked panel rectangles. */
export function surfaceLayout(width: number, height: number): SurfaceLayout {
  const rosterWidth = Math.min(ROSTER_WIDTH, Math.max(0, width));
  return {
    roster: { x: 0, y: 0, width: rosterWidth, height },
    map: { x: rosterWidth, y: 0, width: width - rosterWidth, height },
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
