// Mask-driven oriented river/bridge tile selection (ROADMAP Phase 8 T3/T4).
//
// Unlike plains/woods/hills — which pick a purely cosmetic variant from a coord
// hash (see `variantIndex` in `tiles.ts`) — a river/bridge hex's tile is
// determined by *which edges connect* to neighbouring river/bridge hexes, so the
// water (and a bridge's deck) lines up across the shared edge. The art crosses
// each connected edge at the edge centre, so two adjacent river hexes meet
// seamlessly.
//
// Per the ROADMAP orientation decision we never rotate the pixel art (60° rotation
// resamples and looks mushy); instead we generate distinct oriented tiles and use
// a render-time horizontal (left↔right) mirror to halve the art. Slice 1 handles
// the **straight** shape (two opposite connected edges); 60° turns and forks fall
// back to the generic `00` tile until their later slices.

import type { components } from "@/lib/api/schema";

import { AXIAL_DIRECTIONS, type Axial } from "./hex";

type Terrain = components["schemas"]["Terrain"];

/** River and bridge hexes both carry the river line, so each connects the other. */
function isRiverLike(terrain: Terrain | undefined): boolean {
  return terrain === "river" || terrain === "bridge";
}

/**
 * The directions (indices into {@link AXIAL_DIRECTIONS}, ascending) along which a
 * river/bridge hex connects to a neighbouring river/bridge hex. `terrainAt`
 * returns `undefined` off the map — a real "no neighbour" answer, not an error.
 */
export function connectedEdges(
  coord: Axial,
  terrainAt: (q: number, r: number) => Terrain | undefined,
): number[] {
  const edges: number[] = [];
  AXIAL_DIRECTIONS.forEach((d, i) => {
    if (isRiverLike(terrainAt(coord.q + d.q, coord.r + d.r))) edges.push(i);
  });
  return edges;
}

export interface OrientedTile {
  /** Variant basename within the terrain's tile folder, e.g. "straight_v". */
  readonly name: string;
  /** Render with a horizontal (left↔right) flip. */
  readonly mirror: boolean;
}

// Opposite axial directions are 3 apart in AXIAL_DIRECTIONS (each `d` and `d+3`
// are negatives), giving the three straight axes. Screen-angle geometry (flat-top):
//   {2,5} N/S edges (270°/90°)   → vertical, self-symmetric under mirror
//   {0,3} 30°/210° edges         → "\" diagonal, generated tile
//   {1,4} 150°/330° edges        → "/" diagonal = the {0,3} tile mirrored L↔R
const STRAIGHT_AXES: Readonly<Record<string, OrientedTile>> = {
  "2,5": { name: "straight_v", mirror: false },
  "0,3": { name: "straight_d", mirror: false },
  "1,4": { name: "straight_d", mirror: true },
};

/**
 * Choose the oriented tile + mirror flag for a river/bridge hex from its
 * connected-edge set, or `null` if the shape isn't handled yet (the caller falls
 * back to the generic `00` tile). Slice 1 handles only the straight shape — two
 * opposite connected edges; 60° turns and forks come in later slices.
 */
export function orientedRiverTile(edges: number[]): OrientedTile | null {
  if (edges.length === 2 && edges[1] - edges[0] === 3) {
    return STRAIGHT_AXES[`${edges[0]},${edges[1]}`] ?? null;
  }
  return null;
}
