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
// a render-time horizontal (left↔right) mirror to halve the art. Handled shapes:
// the three straights (two opposite connected edges) and the six gentle turns
// (edges 120° apart). Sharp turns, forks and other masks fall back to the generic
// `00` tile until their later slices.

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

// Every handled two-edge shape, keyed by its ascending connected-edge pair.
// Screen-angle geometry (flat-top, edge index → angle: 0=30° 1=330° 2=270°
// 3=210° 4=150° 5=90°, y-down).
//
// Straights — opposite axial directions are 3 apart in AXIAL_DIRECTIONS:
//   {2,5} N/S edges   → vertical, self-symmetric under mirror
//   {0,3} 30°/210°    → "\" diagonal, generated tile
//   {1,4} 150°/330°   → "/" diagonal = the {0,3} tile mirrored L↔R
//
// Gentle turns — edges 120° apart (circular distance 2). Four generated tiles
// (named after their connected edge compass points) cover all six orientations,
// two via the L↔R mirror; {0,4} and {1,3} are themselves mirror-symmetric axes.
const ORIENTED_PAIRS: Readonly<Record<string, OrientedTile>> = {
  "2,5": { name: "straight_v", mirror: false },
  "0,3": { name: "straight_d", mirror: false },
  "1,4": { name: "straight_d", mirror: true },
  "0,2": { name: "turn_n_se", mirror: false },
  "2,4": { name: "turn_n_se", mirror: true },
  "3,5": { name: "turn_s_nw", mirror: false },
  "1,5": { name: "turn_s_nw", mirror: true },
  "0,4": { name: "turn_se_sw", mirror: false },
  "1,3": { name: "turn_ne_nw", mirror: false },
};

/**
 * Choose the oriented tile + mirror flag for a river/bridge hex from its
 * connected-edge set, or `null` if the shape isn't handled yet (the caller falls
 * back to the generic `00` tile). Handles the straights and gentle turns; sharp
 * turns and forks come in later slices.
 */
export function orientedRiverTile(edges: number[]): OrientedTile | null {
  if (edges.length === 2) {
    return ORIENTED_PAIRS[`${edges[0]},${edges[1]}`] ?? null;
  }
  return null;
}
