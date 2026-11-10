// Which tile a world hex is drawn with: its terrain tile, or a painted site/base
// tile in its place (`siteTiles.ts`). Site tiles are painted on the plains ground
// with a plains rim, so one simply replaces the terrain tile on its hex.
//
// Farms, oil fields, steel mills and factories are fixed by the map, so their tiles
// are worked out once. A base's tile depends on game state (it is drawn once a
// player has founded it, i.e. it has a `Settlement`), so those are recomputed from
// the settlements whenever the scene changes. City sites and vacant base slots keep
// their terrain tile.

import { hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { baseTileKeys, SITE_TILE_KINDS, siteTileKey, type BaseStage } from "./siteTiles";
import type { MapSite, WorldMap } from "./worldMap";

type Settlement = components["schemas"]["Settlement"];

/** A painted tile drawn on `coord` instead of its terrain tile. */
export interface SiteTilePlacement {
  readonly coord: Axial;
  /** Glob key for `siteTileTexture`. */
  readonly key: string;
}

/** Painted tiles by `hexKey` of the hex they cover. */
export type SiteTileMap = ReadonlyMap<string, SiteTilePlacement>;

/** What a hex is drawn with. */
export type HexTileChoice = { readonly kind: "terrain" } | { readonly kind: "site"; readonly key: string };

const TERRAIN: HexTileChoice = { kind: "terrain" };

/**
 * The growth stage a founded base is drawn at. Every base is size one for now
 * (base growth to 2-4 hexes is deferred); when stages arrive this is the one
 * place that reads them, and {@link baseSiteTiles} already covers the footprint.
 */
export function baseStageOf(settlement: Settlement): BaseStage {
  void settlement;
  return 1;
}

/**
 * Painted tiles for the map's leveled sites (farm, oil field, steel mill,
 * factory). Static map data, so computed once. A level outside 1-3 throws (from
 * `siteTileKey`): that is a map the API should never have produced.
 */
export function staticSiteTiles(sites: readonly MapSite[]): Map<string, SiteTilePlacement> {
  const out = new Map<string, SiteTilePlacement>();
  for (const site of sites) {
    if (!(SITE_TILE_KINDS as readonly string[]).includes(site.kind)) continue;
    const { q, r } = site.coord;
    out.set(hexKey(q, r), { coord: site.coord, key: siteTileKey(site.kind, site.level, q, r) });
  }
  return out;
}

/**
 * Painted tiles for every founded base: each base settlement's footprint at its
 * stage, all hexes from one variant so the painted edges line up. A settlement on
 * a hex with no map site is a broken state and throws.
 */
export function baseSiteTiles(
  map: Pick<WorldMap, "siteAt">,
  settlements: readonly Settlement[],
): Map<string, SiteTilePlacement> {
  const out = new Map<string, SiteTilePlacement>();
  for (const settlement of settlements) {
    const { q, r } = settlement.coord;
    const site = map.siteAt(q, r);
    if (site === undefined) throw new Error(`settlement at (${q}, ${r}) has no map site`);
    if (site.kind !== "base") continue;
    for (const placement of baseTileKeys(settlement.coord, baseStageOf(settlement))) {
      out.set(hexKey(placement.coord.q, placement.coord.r), placement);
    }
  }
  return out;
}

/** The tile for the hex at `(q, r)`: a base tile, a leveled site tile, or its terrain. */
export function hexTileChoice(q: number, r: number, staticTiles: SiteTileMap, baseTiles: SiteTileMap): HexTileChoice {
  const key = hexKey(q, r);
  const placed = baseTiles.get(key) ?? staticTiles.get(key);
  return placed === undefined ? TERRAIN : { kind: "site", key: placed.key };
}

/** The hexes whose base tile differs between two {@link baseSiteTiles} results. */
export function changedBaseTiles(before: SiteTileMap, after: SiteTileMap): Axial[] {
  const out: Axial[] = [];
  for (const [key, placed] of before) {
    if (after.get(key)?.key !== placed.key) out.push(placed.coord);
  }
  for (const [key, placed] of after) {
    if (!before.has(key)) out.push(placed.coord);
  }
  return out;
}
