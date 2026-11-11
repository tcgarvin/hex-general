// Painted site and base hex tiles (ratified 2026-11-10, round 5 replacements
// 2026-11-11; see siteTiles/README.md).
// Single-hex sites pick `<kind>/l<level>_<nn>.png`; the player base grows over 1-4
// hexes and is drawn from one `base/stage<N>_<v>/` variant directory. Variant choice
// is the cosmetic coordinate hash `variantIndex` and must match `scripts/board-mock.py`
// (`SiteSet.choose` / `SiteSet.footprint_tiles`).

import { Assets, type Texture } from "pixi.js";

import { variantIndex } from "@/board/tiles";
import type { Axial } from "@/board/hex";

/** Site kinds that have a single-hex painted tile per level. */
export const SITE_TILE_KINDS = ["farm", "oil_field", "steel_mill", "factory"] as const;
export type SiteTileKind = (typeof SITE_TILE_KINDS)[number];

/** Levels with painted single-hex site tiles. */
export const SITE_TILE_LEVELS = [1, 2, 3] as const;
export type SiteTileLevel = (typeof SITE_TILE_LEVELS)[number];

/** Base stages; stage N covers the first N hexes of {@link BASE_FOOTPRINT}. */
export const BASE_STAGES = [1, 2, 3, 4] as const;
export type BaseStage = (typeof BASE_STAGES)[number];

/**
 * Axial offsets from the base's anchor hex, in `h0..h3` order. Stage N covers the
 * first N. A test asserts this equals the art source's `positions.json`.
 */
export const BASE_FOOTPRINT: readonly Axial[] = [
  { q: 0, r: 0 },
  { q: 1, r: 0 },
  { q: 0, r: 1 },
  { q: 1, r: 1 },
];

// Keys look like "./siteTiles/farm/l1_00.png" or "./siteTiles/base/stage2_a/h0.png".
const SITE_TILE_MODULES = import.meta.glob<string>("./siteTiles/**/*.png", {
  eager: true,
  query: "?url",
  import: "default",
});

/** Glob keys sorted like `tiles.ts` (locale order), so index <-> file is stable. */
const SORTED_KEYS: readonly string[] = Object.keys(SITE_TILE_MODULES).sort((a, b) => a.localeCompare(b));

function assertKind(kind: string): asserts kind is SiteTileKind {
  if (!(SITE_TILE_KINDS as readonly string[]).includes(kind)) {
    throw new Error(`unknown site tile kind: ${JSON.stringify(kind)}`);
  }
}

function assertLevel(level: number): asserts level is SiteTileLevel {
  if (!(SITE_TILE_LEVELS as readonly number[]).includes(level)) {
    throw new Error(`site tile level out of range 1-3: ${level}`);
  }
}

function assertStage(stage: number): asserts stage is BaseStage {
  if (!(BASE_STAGES as readonly number[]).includes(stage)) {
    throw new Error(`base stage out of range 1-4: ${stage}`);
  }
}

/** The absolute hexes a base of `stage` anchored at `anchor` covers, in `h0..h<N-1>` order. */
export function baseFootprint(anchor: Axial, stage: number): Axial[] {
  assertStage(stage);
  return BASE_FOOTPRINT.slice(0, stage).map((o) => ({ q: anchor.q + o.q, r: anchor.r + o.r }));
}

/**
 * Glob key of the tile for a single-hex site at `(q, r)`: the variant is
 * `variantIndex(q, r, count)` over that kind+level's locale-sorted files.
 * Throws on an unknown kind or a level outside 1-3.
 */
export function siteTileKey(kind: string, level: number, q: number, r: number): string {
  assertKind(kind);
  assertLevel(level);
  const prefix = `./siteTiles/${kind}/l${level}_`;
  const keys = SORTED_KEYS.filter((k) => k.startsWith(prefix));
  if (keys.length === 0) throw new Error(`no site tiles for ${kind} level ${level}`);
  return keys[variantIndex(q, r, keys.length)];
}

/**
 * Tile keys for every hex of a base of `stage` anchored at `anchor`. One variant
 * directory (`stage<N>_*`, chosen by the anchor's hash) supplies all hexes so the
 * painted internal edges line up; draw them together.
 */
export function baseTileKeys(anchor: Axial, stage: number): { coord: Axial; key: string }[] {
  assertStage(stage);
  const prefix = `./siteTiles/base/stage${stage}_`;
  const dirs = [...new Set(SORTED_KEYS.filter((k) => k.startsWith(prefix)).map((k) => k.split("/")[3]))].sort();
  if (dirs.length === 0) throw new Error(`no base tiles for stage ${stage}`);
  const dir = dirs[variantIndex(anchor.q, anchor.r, dirs.length)];
  return baseFootprint(anchor, stage).map((coord, i) => {
    const key = `./siteTiles/base/${dir}/h${i}.png`;
    if (!(key in SITE_TILE_MODULES)) throw new Error(`missing base tile ${key}`);
    return { coord, key };
  });
}

/** Loaded textures keyed by glob key (see {@link siteTileKey}, {@link baseTileKeys}). */
export type LoadedSiteTiles = ReadonlyMap<string, Texture>;

// Filled once by loadSiteTileTextures so renderers can look textures up by key.
let loaded: LoadedSiteTiles = new Map();

/**
 * Load every site/base tile and make them available to {@link siteTileTexture}.
 * These are 128px painterly tiles, so linear filtering (as `tiles.ts` does for
 * >64px tiles). A load failure rejects.
 */
export async function loadSiteTileTextures(): Promise<LoadedSiteTiles> {
  const entries = await Promise.all(
    SORTED_KEYS.map(async (key) => {
      const texture: Texture = await Assets.load(SITE_TILE_MODULES[key]);
      texture.source.scaleMode = "linear";
      return [key, texture] as const;
    }),
  );
  loaded = new Map(entries);
  return loaded;
}

/** The texture for a key from {@link siteTileKey}/{@link baseTileKeys}; throws if not loaded. */
export function siteTileTexture(key: string): Texture {
  const texture = loaded.get(key);
  if (texture === undefined) throw new Error(`site tile texture not loaded: ${key}`);
  return texture;
}

/** All glob keys, locale-sorted (for tests and preloaders). */
export function allSiteTileKeys(): readonly string[] {
  return SORTED_KEYS;
}
