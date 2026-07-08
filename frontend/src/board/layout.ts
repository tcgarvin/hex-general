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

/** Width of the left-docked roster panel (UI-SPEC §5). Sized for the Field-HQ
 *  construction: wood frame rails + stitched-leather gutter around the
 *  parchment page still need to fit the five row columns (UI-STYLE §4). */
export const ROSTER_WIDTH = 300;

/** Height of the bottom-docked strip holding the selected-unit + lock panels. */
export const BOTTOM_HEIGHT = 140;

/** Width of the lock control at the right of the bottom strip (UI-SPEC §11). */
export const LOCK_WIDTH = 176;

/** Width of the replay control strip at the left of the bottom dock (UI-SPEC §13.2);
 *  the event log fills the rest of the strip to its right (UI-SPEC §13.3). */
export const REPLAY_CONTROLS_WIDTH = 560;

/** Size of the top-right terrain / hex inspector (UI-SPEC §7). Sized for its
 *  tallest content: a city's economy block plus road, control, and unit lines. */
export const INSPECTOR_WIDTH = 208;
export const INSPECTOR_HEIGHT = 148;

export interface SurfaceLayout {
  /** Rectangle the map pans/zooms within (everything not covered by a panel). */
  map: Rect;
  /** Left-docked roster panel (UI-SPEC §5). */
  roster: Rect;
  /** Bottom-left: selected-unit detail + order palette (UI-SPEC §6, §8). In
   *  replay this holds the replay control strip instead (UI-SPEC §13.2). */
  selectedUnit: Rect;
  /** Bottom-right: lock control (UI-SPEC §11). Empty outside planning. */
  lock: Rect;
  /** Bottom-right event-log panel during replay (UI-SPEC §13.3). Empty otherwise. */
  replayLog: Rect;
  /** Top-right terrain / hex inspector (UI-SPEC §7), planning only. Unlike the
   *  docked panels it floats over the map's corner (the map rect is unchanged),
   *  so pointer input over it must be treated as panel, not map. */
  inspector: Rect;
}

/** An empty rectangle, used when a panel is hidden (e.g. outside planning). */
const EMPTY_RECT: Rect = { x: 0, y: 0, width: 0, height: 0 };

/**
 * Split the canvas into the map rectangle and the docked panel rectangles for
 * the planning surface: the bottom strip runs edge-to-edge across the full
 * width (selected-unit panel + lock control), and the roster docks on the left
 * *above* it, ending where the strip begins. The map fills the rectangle left
 * between them.
 */
export function surfaceLayout(width: number, height: number): SurfaceLayout {
  const rosterWidth = Math.min(ROSTER_WIDTH, Math.max(0, width));
  const bottomHeight = Math.min(BOTTOM_HEIGHT, Math.max(0, height));
  const bottomY = height - bottomHeight;
  const rosterHeight = bottomY;
  const lockWidth = Math.min(LOCK_WIDTH, Math.max(0, width));
  return {
    roster: { x: 0, y: 0, width: rosterWidth, height: rosterHeight },
    selectedUnit: {
      x: 0,
      y: bottomY,
      width: width - lockWidth,
      height: bottomHeight,
    },
    lock: {
      x: width - lockWidth,
      y: bottomY,
      width: lockWidth,
      height: bottomHeight,
    },
    replayLog: EMPTY_RECT,
    inspector: {
      x: Math.max(rosterWidth, width - INSPECTOR_WIDTH),
      y: 0,
      width: Math.min(INSPECTOR_WIDTH, Math.max(0, width - rosterWidth)),
      height: Math.min(INSPECTOR_HEIGHT, Math.max(0, bottomY)),
    },
    map: {
      x: rosterWidth,
      y: 0,
      width: Math.max(0, width - rosterWidth),
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
    replayLog: EMPTY_RECT,
    inspector: EMPTY_RECT,
  };
}

/**
 * Replay layout: roster docked left (read-only unit list); the bottom dock splits
 * into the replay control strip on the left (`selectedUnit`) and the scrollable
 * event log on the right (`replayLog`). `lock` is unused (empty). The control
 * strip is clamped so the log always gets a positive width on narrow canvases.
 */
export function replayLayout(width: number, height: number): SurfaceLayout {
  const rosterWidth = Math.min(ROSTER_WIDTH, Math.max(0, width));
  const bottomHeight = Math.min(BOTTOM_HEIGHT, Math.max(0, height));
  const bottomY = height - bottomHeight;
  const controlsWidth = Math.min(REPLAY_CONTROLS_WIDTH, Math.max(0, width));
  return {
    roster: { x: 0, y: 0, width: rosterWidth, height: bottomY },
    selectedUnit: { x: 0, y: bottomY, width: controlsWidth, height: bottomHeight },
    lock: EMPTY_RECT,
    inspector: EMPTY_RECT,
    replayLog: {
      x: controlsWidth,
      y: bottomY,
      width: Math.max(0, width - controlsWidth),
      height: bottomHeight,
    },
    map: {
      x: rosterWidth,
      y: 0,
      width: Math.max(0, width - rosterWidth),
      height: bottomY,
    },
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
