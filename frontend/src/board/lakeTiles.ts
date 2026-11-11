// Lake tile selection (shared by the 2-player board and the world board).
//
// A lake hex is painted whole per *neighbour mask* (round 2 lake art,
// `tiles/lake/m<MM>_<VV>.png`, ratified 2026-11-10): bit d is set when the
// neighbour in `AXIAL_DIRECTIONS[d]` is lake, so every water edge of one tile
// meets the water edge of the next and every land edge carries a shore. There
// is one tile (or a few variants) for each of the 64 masks; never mirrored.
// The variant is the usual coord hash (`variantIndex`). Port of `lake_mask` /
// `lake_tile_key` in `scripts/board-mock.py`, pinned against it by the shared
// fixture `__fixtures__/lakes.json` (`scripts/lake-fixture.py`).

import type { components } from "@/lib/api/schema";

import { AXIAL_DIRECTIONS, hexKey, type Axial } from "./hex";

type Terrain = components["schemas"]["Terrain"];

/** Number of lake neighbour masks (6 bits). */
export const LAKE_MASK_COUNT = 64;

/**
 * The lake tile rule: bit d set when the neighbour in `AXIAL_DIRECTIONS[d]` is
 * lake. `terrainAt` returns `undefined` off the map, which counts as land.
 */
export function lakeMask(coord: Axial, terrainAt: (q: number, r: number) => Terrain | undefined): number {
  let mask = 0;
  AXIAL_DIRECTIONS.forEach((d, i) => {
    if (terrainAt(coord.q + d.q, coord.r + d.r) === "lake") mask |= 1 << i;
  });
  return mask;
}

/** The `byOriented` key of a lake mask's tiles: `"lake/m<mask, two digits>"`. */
export function lakeTileKey(mask: number): string {
  if (!Number.isInteger(mask) || mask < 0 || mask >= LAKE_MASK_COUNT) {
    throw new Error(`lake mask must be 0..${LAKE_MASK_COUNT - 1}, got ${mask}`);
  }
  return `lake/m${String(mask).padStart(2, "0")}`;
}

/** {@link lakeMask} of every lake hex among `coords`, by {@link hexKey}. */
export function lakeMasks(
  coords: readonly Axial[],
  terrainAt: (q: number, r: number) => Terrain | undefined,
): Map<string, number> {
  const masks = new Map<string, number>();
  for (const c of coords) {
    if (terrainAt(c.q, c.r) === "lake") masks.set(hexKey(c.q, c.r), lakeMask(c, terrainAt));
  }
  return masks;
}
