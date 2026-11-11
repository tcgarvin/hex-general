// Pure camera maths for the world board: clamping, zoom about a point, eased
// jumps and keyboard pan velocity. `WorldRenderer` owns the live camera; these
// stay free of Pixi so they can be tested directly.

/** The world container's transform: screen = world * scale + (x, y). */
export interface Camera {
  x: number;
  y: number;
  scale: number;
}

export interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export interface Viewport {
  width: number;
  height: number;
}

/** Share of the viewport the map may leave empty on a side when it is larger than the screen. */
export const EDGE_MARGIN = 0.3;

/** Keyboard pan speed in screen pixels per second (the same feel at every zoom). */
export const PAN_SPEED = 900;

/** Duration of an animated camera jump. */
export const JUMP_MS = 200;

/**
 * One axis of the clamp. When the map is narrower than the viewport it is
 * centred; otherwise its edges may come in from the screen edges by at most
 * `margin` of the viewport, so the map always fills most of the screen.
 */
function clampAxis(offset: number, min: number, max: number, scale: number, view: number, margin: number): number {
  const size = (max - min) * scale;
  if (size <= view) return (view - size) / 2 - min * scale;
  const lo = view * (1 - margin) - max * scale;
  const hi = view * margin - min * scale;
  return Math.min(hi, Math.max(lo, offset));
}

/** `camera` with its position pulled back so the map overlaps the viewport (scale unchanged). */
export function clampCamera(camera: Camera, bounds: Bounds, view: Viewport, margin = EDGE_MARGIN): Camera {
  return {
    scale: camera.scale,
    x: clampAxis(camera.x, bounds.minX, bounds.maxX, camera.scale, view.width, margin),
    y: clampAxis(camera.y, bounds.minY, bounds.maxY, camera.scale, view.height, margin),
  };
}

/** The zoom at which the whole map just fits the viewport. */
export function fitScale(bounds: Bounds, view: Viewport): number {
  return Math.min(view.width / (bounds.maxX - bounds.minX), view.height / (bounds.maxY - bounds.minY));
}

/** The camera that shows the whole map, centred. */
export function fitCamera(bounds: Bounds, view: Viewport, scale = fitScale(bounds, view)): Camera {
  return clampCamera({ x: 0, y: 0, scale }, bounds, view);
}

/** Zoom to `scale`, keeping the world point under screen point `(px, py)` fixed. */
export function zoomAbout(camera: Camera, scale: number, px: number, py: number): Camera {
  const wx = (px - camera.x) / camera.scale;
  const wy = (py - camera.y) / camera.scale;
  return { scale, x: px - wx * scale, y: py - wy * scale };
}

/** The camera that puts world point `(wx, wy)` at the viewport centre at `scale`. */
export function centredOn(wx: number, wy: number, scale: number, view: Viewport): Camera {
  return { scale, x: view.width / 2 - wx * scale, y: view.height / 2 - wy * scale };
}

function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
}

/**
 * The camera a fraction `t` (0–1) of the way through a jump. It glides the
 * world point at the screen centre and zooms geometrically, so a jump that also
 * zooms doesn't swing sideways the way interpolating the raw transform would.
 */
export function cameraBetween(from: Camera, to: Camera, t: number, view: Viewport): Camera {
  if (t >= 1) return to;
  const e = easeInOut(Math.max(0, t));
  const cx0 = (view.width / 2 - from.x) / from.scale;
  const cy0 = (view.height / 2 - from.y) / from.scale;
  const cx1 = (view.width / 2 - to.x) / to.scale;
  const cy1 = (view.height / 2 - to.y) / to.scale;
  const scale = from.scale * (to.scale / from.scale) ** e;
  return centredOn(cx0 + (cx1 - cx0) * e, cy0 + (cy1 - cy0) * e, scale, view);
}

/** Which pan directions are held: each axis -1, 0 or 1. */
export interface PanInput {
  x: number;
  y: number;
  fast: boolean;
}

/**
 * The screen-pixel displacement of the map for `dt` seconds of held pan keys.
 * Diagonals are normalised so they are no faster than a straight pan; Shift
 * doubles the speed. The map moves opposite to the view (pan right ⇒ map left).
 */
export function panStep(input: PanInput, dt: number, speed = PAN_SPEED): { dx: number; dy: number } {
  const len = Math.hypot(input.x, input.y);
  if (len === 0) return { dx: 0, dy: 0 };
  const v = (speed * (input.fast ? 2 : 1) * dt) / len;
  return { dx: -input.x * v, dy: -input.y * v };
}

/** Screen-space margins where panels cover the board. */
export interface Insets {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/**
 * Whether screen point `(sx, sy)` sits comfortably inside the viewport, clear of
 * the `insets`. Insets are shrunk on a small screen so the clear area never
 * drops below the middle third of each axis.
 */
export function comfortablyVisible(sx: number, sy: number, view: Viewport, insets: Insets): boolean {
  const shrink = (a: number, b: number, size: number): [number, number] => {
    const room = size / 3;
    const spare = size - a - b;
    if (spare >= room) return [a, b];
    const k = Math.max(0, size - room) / Math.max(1, a + b);
    return [a * k, b * k];
  };
  const [left, right] = shrink(insets.left, insets.right, view.width);
  const [top, bottom] = shrink(insets.top, insets.bottom, view.height);
  return sx >= left && sx <= view.width - right && sy >= top && sy <= view.height - bottom;
}
