// The static world map (WORLD_SPEC §10.1), decoded from the API's CompactMap.
//
// A world is ~350×350 hexes, so this keeps the map as one row-major array over the
// odd-q offset rectangle (the same layout the server encodes) and looks hexes up
// by index instead of by string key. `hexAt(q, r)` is O(1). The `Hex` objects are
// built once at decode time, so code shared with the 2-player board (road and river
// tile selection) can take them as-is.

import type { components } from "@/lib/api/schema";

import { axialToPixel, type Axial, type Hex } from "@/board/hex";

type Terrain = components["schemas"]["Terrain"];
export type MapSite = components["schemas"]["MapSite"];
export type CompactMap = components["schemas"]["CompactMap"];
export type SiteKind = MapSite["kind"];

/** Terrain grid characters (api/app/engine/scenario.py `TERRAIN_CHARS`). */
export const TERRAIN_BY_CHAR: Readonly<Record<string, Terrain>> = {
  ".": "plains",
  w: "woods",
  h: "hills",
  "~": "river",
  "=": "bridge",
  o: "lake",
  t: "town",
  c: "city",
};
const ROAD_CHAR = "#";

/** Odd-q offset `(col, row)` → axial (matches the server's `offset_to_axial`). */
export function offsetToAxial(col: number, row: number): Axial {
  return { q: col, r: row - (col - (col & 1)) / 2 };
}

/** Axial → odd-q offset `(col, row)` (the server's `axial_to_offset`). */
export function axialToOffset(a: Axial): { col: number; row: number } {
  return { col: a.q, row: a.r + (a.q - (a.q & 1)) / 2 };
}

export interface WorldMap {
  readonly width: number;
  readonly height: number;
  /** Row-major: `hexes[row * width + col]`. */
  readonly hexes: readonly Hex[];
  readonly sites: readonly MapSite[];
  /** The hex at axial `(q, r)`, or `undefined` off the map (a real answer). */
  hexAt(q: number, r: number): Hex | undefined;
  /** The site at axial `(q, r)`, or `undefined` if the hex has none. */
  siteAt(q: number, r: number): MapSite | undefined;
}

/**
 * Decode the server's compact map. Throws on a malformed payload (wrong grid
 * length, unknown terrain char): the API validates its own output, so a bad map
 * here is a contract break worth failing loudly on.
 */
export function decodeWorldMap(compact: CompactMap): WorldMap {
  const { width, height, terrain, roads } = compact;
  const size = width * height;
  if (terrain.length !== size || roads.length !== size) {
    throw new Error(`world map grids are ${terrain.length}/${roads.length}, expected ${size}`);
  }
  const hexes: Hex[] = new Array(size);
  for (let i = 0; i < size; i++) {
    const t = TERRAIN_BY_CHAR[terrain[i]];
    if (t === undefined) throw new Error(`unknown terrain char ${terrain[i]} at ${i}`);
    hexes[i] = { coord: offsetToAxial(i % width, Math.floor(i / width)), terrain: t, road: roads[i] === ROAD_CHAR };
  }
  // Maps the browser cached before 2026-11-10 (served `immutable`, kept for a year)
  // predate site levels, so their sites have no `level`. The generated type says it
  // is always present; the server fills the default 1, and so do we here, at the
  // boundary, so internal code can keep treating `level` as a real number.
  const sites = (compact.sites ?? []).map((s) => ((s as { level?: number }).level === undefined ? { ...s, level: 1 } : s));
  const siteIndex = new Map<number, MapSite>();
  const indexOf = (q: number, r: number): number => {
    const { col, row } = axialToOffset({ q, r });
    if (col < 0 || col >= width || row < 0 || row >= height) return -1;
    return row * width + col;
  };
  for (const s of sites) siteIndex.set(indexOf(s.coord.q, s.coord.r), s);
  return {
    width,
    height,
    hexes,
    sites,
    hexAt(q, r) {
      const i = indexOf(q, r);
      return i < 0 ? undefined : hexes[i];
    },
    siteAt(q, r) {
      return siteIndex.get(indexOf(q, r));
    },
  };
}

/** An inclusive rectangle of offset cells. */
export interface CellRange {
  col0: number;
  col1: number;
  row0: number;
  row1: number;
}

const SQRT3 = Math.sqrt(3);

/**
 * The offset cells whose hexes may overlap the world-pixel rectangle
 * `[x0, x1] × [y0, y1]` (hex centre-to-corner radius `size`), padded by one cell
 * and clamped to the map. Flat-top odd-q: column `c` is centred at `1.5·size·c`,
 * row `r` at `√3·size·(r + ½·(c & 1))`.
 */
export function visibleCells(
  map: { width: number; height: number },
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  size: number,
): CellRange {
  const colW = 1.5 * size;
  const rowH = SQRT3 * size;
  return {
    col0: Math.max(0, Math.floor(x0 / colW) - 1),
    col1: Math.min(map.width - 1, Math.ceil(x1 / colW) + 1),
    row0: Math.max(0, Math.floor(y0 / rowH) - 1),
    row1: Math.min(map.height - 1, Math.ceil(y1 / rowH) + 1),
  };
}

/** World-pixel bounds of the whole map (hex centres ± one radius). */
export function worldPixelBounds(
  map: { width: number; height: number },
  size: number,
): { minX: number; minY: number; maxX: number; maxY: number } {
  const last = axialToPixel(map.width - 1, 0, size);
  return {
    minX: -size,
    minY: -size,
    maxX: last.x + size,
    maxY: SQRT3 * size * (map.height + 0.5) + size,
  };
}
