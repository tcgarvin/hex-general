// Render-side hex geometry (Phase 8). The engine owns gameplay hex math
// (`api/app/engine/hex_coords.py`); this module is purely about turning axial
// `q,r` into screen pixels and back for the Pixi board.
//
// **Flat-top** orientation (Panzer General / Civ style): flat edges on top and
// bottom, points east/west. The scenario loader authors a rectangular grid and
// converts it to a rectangular *region* of axial space (flat-top odd-q offset),
// so rendering those axial coords with the flat-top layout below draws a
// rectangle — and visual adjacency equals the engine's axial adjacency.

export interface Axial {
  q: number;
  r: number;
}

// --- Axial topology (orientation-independent; mirrors the engine's adjacency in
// `api/app/engine/hex_coords.py`). These are about which `q,r` are neighbours,
// not where they land on screen, so they hold for any render orientation. ---

/** The six axial neighbour offsets, in a fixed order. */
export const AXIAL_DIRECTIONS: readonly Axial[] = [
  { q: 1, r: 0 },
  { q: 1, r: -1 },
  { q: 0, r: -1 },
  { q: -1, r: 0 },
  { q: -1, r: 1 },
  { q: 0, r: 1 },
];

/** Stable map key for an axial coord. */
export function hexKey(q: number, r: number): string {
  return `${q},${r}`;
}

/** Whether two axial coords are the same hex. */
export function axialEqual(a: Axial, b: Axial): boolean {
  return a.q === b.q && a.r === b.r;
}

/** The six hexes adjacent to `a`. */
export function axialNeighbors(a: Axial): Axial[] {
  return AXIAL_DIRECTIONS.map((d) => ({ q: a.q + d.q, r: a.r + d.r }));
}

/** Whether `a` and `b` are adjacent (distance 1). */
export function isAdjacent(a: Axial, b: Axial): boolean {
  if (axialEqual(a, b)) return false;
  return AXIAL_DIRECTIONS.some((d) => a.q + d.q === b.q && a.r + d.r === b.r);
}

/** Hex distance between two axial coords (mirrors the engine's `distance`). */
export function axialDistance(a: Axial, b: Axial): number {
  const dq = a.q - b.q;
  const dr = a.r - b.r;
  return (Math.abs(dq) + Math.abs(dq + dr) + Math.abs(dr)) / 2;
}

/** Hex size (centre-to-corner radius, pixels) — shared across board and replay. */
export const HEX_SIZE = 32;

const SQRT3 = Math.sqrt(3);

/**
 * Centre pixel of hex `(q, r)` for a given hex `size` (centre-to-corner radius),
 * before any pan/zoom transform. Flat-top axial layout.
 */
export function axialToPixel(q: number, r: number, size: number): { x: number; y: number } {
  return {
    x: size * (1.5 * q),
    y: size * ((SQRT3 / 2) * q + SQRT3 * r),
  };
}

/** Inverse of {@link axialToPixel}: a world pixel to fractional axial coords. */
export function pixelToAxialFractional(x: number, y: number, size: number): { q: number; r: number } {
  return {
    q: ((2 / 3) * x) / size,
    r: ((-1 / 3) * x + (SQRT3 / 3) * y) / size,
  };
}

/** Round fractional axial coords to the nearest hex (via cube rounding). */
export function axialRound(q: number, r: number): Axial {
  // axial -> cube (x = q, z = r, y = -x - z)
  const cx = q;
  const cz = r;
  const cy = -cx - cz;

  let rx = Math.round(cx);
  let ry = Math.round(cy);
  let rz = Math.round(cz);

  const dx = Math.abs(rx - cx);
  const dy = Math.abs(ry - cy);
  const dz = Math.abs(rz - cz);

  if (dx > dy && dx > dz) {
    rx = -ry - rz;
  } else if (dy > dz) {
    ry = -rx - rz;
  } else {
    rz = -rx - ry;
  }

  return { q: rx, r: rz };
}

/** A world pixel to the hex that contains it. */
export function pixelToAxial(x: number, y: number, size: number): Axial {
  const frac = pixelToAxialFractional(x, y, size);
  return axialRound(frac.q, frac.r);
}

/**
 * Corner points of a hex, as a flat `[x0, y0, x1, y1, …]` array suitable for
 * `Graphics.poly`. Centred on `(cx, cy)`; flat-top (first corner due east).
 */
export function hexCornerPoints(cx: number, cy: number, size: number): number[] {
  const points: number[] = [];
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI / 180) * (60 * i);
    points.push(cx + size * Math.cos(angle), cy + size * Math.sin(angle));
  }
  return points;
}

export interface MapBounds {
  minX: number;
  minY: number;
  width: number;
  height: number;
}

/**
 * Pixel bounding box covering every hex in `coords` at the given size, padded by
 * a hex radius so outer corners aren't clipped. Computed from the actual stored
 * coordinates (which may be negative after the offset→axial conversion), so it
 * works for any map shape. Used to fit the board into the viewport.
 */
export function mapPixelBounds(coords: readonly Axial[], size: number): MapBounds {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  for (const { q, r } of coords) {
    const { x, y } = axialToPixel(q, r, size);
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  }

  return {
    minX: minX - size,
    minY: minY - size,
    width: maxX - minX + size * 2,
    height: maxY - minY + size * 2,
  };
}
