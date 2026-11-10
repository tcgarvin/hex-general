// Same-terrain join sprites (shared by the 2-player board and the world board).
//
// Every woods/city tile keeps a ring of plain ground inside its hexagon, so two
// adjacent hexes of the same terrain show a thin grass moat between them and a
// forest or city reads as a grid of islands. A join sprite is painted over each
// such seam: a soft lens across the shared edge of two same-terrain hexes, and
// a small disc on the corner shared by three, so the mass reads as one.
//
// The sprites come from `scripts/tile-foundry/joins.py` (contract in
// `joins/README.md` + `joins/joins.json`); `scripts/board-mock.py --joins` is the
// offline reference renderer, and this module selects and places sprites exactly
// as its `choose_joins` does (the variant hash is pinned against the Python in
// `joins.test.ts`). They live outside `tiles/` so the terrain glob never sees them.
//
// Draw them after the base terrain tiles and before the grid, roads, fog and
// units: every edge join first, then every vertex fill, each in `compareJoins`
// order. Never mirror or rotate one; the light is painted in.

import { Assets, type Texture } from "pixi.js";

import type { components } from "@/lib/api/schema";

import { AXIAL_DIRECTIONS, axialToPixel, type Axial } from "./hex";

type Terrain = components["schemas"]["Terrain"];

/** Hex width (px, point to point) the join canvases were painted at (`joins.json`). */
export const JOIN_HEX_WIDTH = 128;

/**
 * Join kind (the file-name stem before `_<nn>`) → code. Codes 0-2 are the edge
 * between hex A and A + `AXIAL_DIRECTIONS[code]`; codes 3 + i are the corner A
 * shares with A + d_i and A + d_(i+1). Each edge and corner of the map has
 * exactly one owner hex A under this naming.
 */
export const JOIN_KINDS = {
  edge_se: 0,
  edge_ne: 1,
  edge_n: 2,
  vert_e: 3,
  vert_ne: 4,
} as const;

export type JoinCode = (typeof JOIN_KINDS)[keyof typeof JOIN_KINDS];

const JOIN_CODES: readonly JoinCode[] = [0, 1, 2, 3, 4];
const EDGE_CODE_COUNT = 3;
/** Screen angle (degrees, y-down) of the corner a vertex code names, from A's centre. */
const VERTEX_ANGLES_DEG = [0, 300] as const;

// Eagerly resolve every join PNG's URL at build time ("./joins/woods/edge_n_00.png").
const JOIN_MODULES = import.meta.glob<string>("./joins/**/*.png", {
  eager: true,
  query: "?url",
  import: "default",
});

/** Per joinable terrain, the sprite variants of each code (index = code). */
export type JoinGroups<T> = ReadonlyMap<Terrain, readonly (readonly T[])[]>;

/** Join textures, loaded. */
export type LoadedJoins = JoinGroups<Texture>;

/**
 * Group join glob keys (`./joins/<terrain>/<kind>_<nn>.png`) by terrain and
 * code, variants in `localeCompare` order like tile variants. Throws on an
 * unknown kind or a terrain missing any kind: the art set is built as a whole by
 * `joins.py`, so a gap is a broken export, not a state to render around.
 */
export function groupJoinFiles<T>(entries: readonly (readonly [string, T])[]): JoinGroups<T> {
  const sorted = [...entries].sort(([a], [b]) => a.localeCompare(b));
  const groups = new Map<Terrain, T[][]>();
  for (const [key, value] of sorted) {
    const parts = key.split("/");
    if (parts.length !== 4 || parts[1] !== "joins") {
      throw new Error(`join sprite ${key}: expected ./joins/<terrain>/<kind>_<nn>.png`);
    }
    const terrain = parts[2] as Terrain;
    const kind = parts[3].replace(/\.png$/, "").replace(/_\d+$/, "");
    if (!(kind in JOIN_KINDS)) {
      throw new Error(`join sprite ${key}: kind must be one of ${Object.keys(JOIN_KINDS).join(", ")}`);
    }
    const code = JOIN_KINDS[kind as keyof typeof JOIN_KINDS];
    let byCode = groups.get(terrain);
    if (byCode === undefined) {
      byCode = JOIN_CODES.map(() => []);
      groups.set(terrain, byCode);
    }
    byCode[code].push(value);
  }
  for (const [terrain, byCode] of groups) {
    const missing = Object.keys(JOIN_KINDS).filter((_, code) => byCode[code].length === 0);
    if (missing.length > 0) {
      throw new Error(`join sprites for ${terrain} are missing kinds: ${missing.join(", ")}`);
    }
  }
  return groups;
}

const JOIN_URLS = groupJoinFiles(Object.entries(JOIN_MODULES));

/** Terrains that have join art; selection only joins hexes of these. */
export const JOIN_TERRAINS: ReadonlySet<Terrain> = new Set(JOIN_URLS.keys());

/**
 * Load every join sprite, linear-filtered like the 128 px painterly tiles (the
 * canvases are 64 px but painted at the tiles' native scale). A load failure
 * rejects; the caller keeps rendering without joins and logs it.
 */
export async function loadJoinTextures(): Promise<LoadedJoins> {
  const out = new Map<Terrain, Texture[][]>();
  await Promise.all(
    [...JOIN_URLS].map(async ([terrain, byCode]) => {
      const textures = await Promise.all(
        byCode.map((urls) =>
          Promise.all(
            urls.map(async (url) => {
              const texture: Texture = await Assets.load(url);
              texture.source.scaleMode = "linear";
              return texture;
            }),
          ),
        ),
      );
      out.set(terrain, textures);
    }),
  );
  return out;
}

/** World scale for a join sprite on a board of hex radius `hexSize`. */
export function joinSpriteScale(hexSize: number): number {
  return (2 * hexSize) / JOIN_HEX_WIDTH;
}

/**
 * Join-sprite variant in `[0, count)` for owner hex `(q, r)` and `code`:
 * `variantIndex` (tiles.ts) with a third hash term, so the five joins a hex owns
 * do not all pick the same variant. Must stay identical to
 * `join_variant_index` in `scripts/board-mock.py` (pinned in the tests).
 */
export function joinVariantIndex(q: number, r: number, code: JoinCode, count: number): number {
  if (count <= 1) return 0;
  let h = (q | 0) * 0x1f1f1f1f + (r | 0) * 0x85ebca6b + code * 0x27d4eb2f;
  h ^= h >>> 15;
  h = Math.imul(h, 0x2c1b3c6d);
  h ^= h >>> 12;
  return Math.abs(h) % count;
}

/** One join sprite to draw: which seam it covers and where its canvas centre goes. */
export interface JoinPlacement {
  readonly terrain: Terrain;
  /** The owner hex A (the seam is named by A and `code`). */
  readonly owner: Axial;
  readonly code: JoinCode;
  /** The hexes the join spans: A and its neighbour (edge), or A and two neighbours (vertex). */
  readonly members: readonly Axial[];
  /** Anchor in world px: the shared edge midpoint, or the shared corner. */
  readonly x: number;
  readonly y: number;
}

/** Whether a placement is an edge lens (else a vertex fill). */
export function isEdgeJoin(p: JoinPlacement): boolean {
  return p.code < EDGE_CODE_COUNT;
}

function neighbour(a: Axial, direction: number): Axial {
  const d = AXIAL_DIRECTIONS[direction];
  return { q: a.q + d.q, r: a.r + d.r };
}

/**
 * The joins hex `a` owns (its edges 0-2 and corners 0-1), in code order: each
 * whose member hexes all have `a`'s terrain, and only if that terrain has join
 * art. `terrainAt` returns `undefined` off the map (no join across the border).
 */
export function joinsOwnedBy(
  a: Axial,
  terrainAt: (q: number, r: number) => Terrain | undefined,
  joinable: ReadonlySet<Terrain>,
  hexSize: number,
): JoinPlacement[] {
  const terrain = terrainAt(a.q, a.r);
  if (terrain === undefined || !joinable.has(terrain)) return [];
  const centre = axialToPixel(a.q, a.r, hexSize);
  const out: JoinPlacement[] = [];
  for (const code of JOIN_CODES) {
    const directions = code < EDGE_CODE_COUNT ? [code] : [code - EDGE_CODE_COUNT, code - EDGE_CODE_COUNT + 1];
    const others = directions.map((d) => neighbour(a, d));
    if (!others.every((o) => terrainAt(o.q, o.r) === terrain)) continue;
    let x: number;
    let y: number;
    if (code < EDGE_CODE_COUNT) {
      const b = axialToPixel(others[0].q, others[0].r, hexSize);
      x = (centre.x + b.x) / 2;
      y = (centre.y + b.y) / 2;
    } else {
      const angle = (VERTEX_ANGLES_DEG[code - EDGE_CODE_COUNT] * Math.PI) / 180;
      x = centre.x + hexSize * Math.cos(angle);
      y = centre.y + hexSize * Math.sin(angle);
    }
    out.push({ terrain, owner: { q: a.q, r: a.r }, code, members: [{ q: a.q, r: a.r }, ...others], x, y });
  }
  return out;
}

/** Draw order: every edge before every vertex, then by owner (r, q), then code. */
export function compareJoins(a: JoinPlacement, b: JoinPlacement): number {
  return (
    Number(!isEdgeJoin(a)) - Number(!isEdgeJoin(b)) ||
    a.owner.r - b.owner.r ||
    a.owner.q - b.owner.q ||
    a.code - b.code
  );
}

const RANK_OFFSET = 2 ** 18;

/**
 * A number that sorts like {@link compareJoins}, for renderers that order
 * sprites by `zIndex` (the world board adds them in camera order, not draw
 * order). Exact for |q|, |r| < 2^18, far beyond any map.
 */
export function joinDrawRank(p: JoinPlacement): number {
  const vertex = isEdgeJoin(p) ? 0 : 1;
  return ((vertex * 2 ** 19 + (p.owner.r + RANK_OFFSET)) * 2 ** 19 + (p.owner.q + RANK_OFFSET)) * 8 + p.code;
}

/** Every join over `hexes`, in draw order (see {@link compareJoins}). */
export function joinPlacements(
  hexes: readonly Axial[],
  terrainAt: (q: number, r: number) => Terrain | undefined,
  joinable: ReadonlySet<Terrain>,
  hexSize: number,
): JoinPlacement[] {
  return hexes.flatMap((h) => joinsOwnedBy(h, terrainAt, joinable, hexSize)).sort(compareJoins);
}

/** The variant (texture, URL, ...) a placement draws, from its terrain's groups. */
export function joinVariant<T>(groups: JoinGroups<T>, p: JoinPlacement): T {
  const variants = groups.get(p.terrain)![p.code];
  return variants[joinVariantIndex(p.owner.q, p.owner.r, p.code, variants.length)];
}
