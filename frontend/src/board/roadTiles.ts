// Mask-driven baked road tile selection (ROADMAP Phase 8 T5).
//
// A road hex's baked tile — painterly road stroke composited over the hex's own
// ratified terrain base — is chosen by *which edges connect* to neighbouring
// road-carrying hexes, exactly like river/bridge hexes (see `riverTiles.ts`).
// Roads reuse the river shape inventory verbatim: the same edge-index geometry,
// the same oriented-tile names (straights, gentle turns, ends, forks), and the
// same render-time L↔R mirror convention, via `orientedRiverTile`.
//
// Unlike rivers, roads keep a *fallback renderer*: the vector overlay in
// `Board.tsx` (centre-to-edge brown stubs). A road hex renders its baked tile
// only when one exists for its `(terrain, shape)` pair — the texture lookup key
// is `"road/{terrain}_{shape}"` in `LoadedTiles.byOriented`, e.g.
// `"road/plains_straight_v"` from `tiles/road/plains_straight_v_00.png`.
// Anything unresolved (settlement hexes, fork masks, terrains without baked
// road art yet) simply keeps the vector overlay, so partial art coverage never
// breaks a road network's continuity.
//
// Roads use the raw adjacency mask, not the river course-graph pruning: road
// networks are authored as thin corridors, and a road hex genuinely connects to
// every adjacent road hex (there is no "water flows one way" reading to
// preserve). If a future map authors a road blob whose junction triangles
// over-connect, generalise `riverCourseEdges` rather than special-casing here.

import type { components } from "@/lib/api/schema";

import { AXIAL_DIRECTIONS, hexKey, type Axial } from "./hex";
import { orientedRiverTile } from "./riverTiles";

type Hex = components["schemas"]["Hex"];

/**
 * Whether a hex carries the road line for connectivity purposes: any
 * road-flagged hex, plus bridge hexes (a bridge is the road's river crossing —
 * its painterly tile bakes the road ramps, and its neighbours' road art must
 * run toward it).
 */
function isRoadLike(hex: Hex | undefined): boolean {
  return hex !== undefined && (hex.road || hex.terrain === "bridge");
}

/**
 * The directions (indices into {@link AXIAL_DIRECTIONS}, ascending) along which
 * a road hex connects to a neighbouring road-carrying hex. `hexAt` returns
 * `undefined` off the map — a real "no neighbour" answer, not an error.
 */
export function roadConnectedEdges(
  coord: Axial,
  hexAt: (q: number, r: number) => Hex | undefined,
): number[] {
  const edges: number[] = [];
  AXIAL_DIRECTIONS.forEach((d, i) => {
    if (isRoadLike(hexAt(coord.q + d.q, coord.r + d.r))) edges.push(i);
  });
  return edges;
}

/** A resolved baked-road-tile reference for one hex. */
export interface RoadTileRef {
  /** `byOriented` lookup key, e.g. `"road/plains_turn_n_se"`. */
  readonly lookup: string;
  /** Render with a horizontal (left↔right) flip, per the river mirror tables. */
  readonly mirror: boolean;
}

/**
 * The baked-road-tile reference for every road-flagged hex whose edge mask maps
 * to a handled oriented shape, keyed by {@link hexKey}. River and bridge hexes
 * are excluded (their own oriented art carries the water/deck/ramps); hexes
 * whose mask is unhandled (forks without art rulings, defensive cases) are
 * simply absent. Whether the referenced texture actually exists is the render
 * site's decision — a missing `byOriented` group falls back to the vector road
 * overlay, so this map stays a pure geometry computation.
 */
export function roadTileRefs(
  hexes: readonly Hex[],
  hexAt: (q: number, r: number) => Hex | undefined,
): Map<string, RoadTileRef> {
  const refs = new Map<string, RoadTileRef>();
  for (const h of hexes) {
    if (!h.road || h.terrain === "river" || h.terrain === "bridge") continue;
    const oriented = orientedRiverTile(roadConnectedEdges(h.coord, hexAt));
    if (oriented === null) continue;
    refs.set(hexKey(h.coord.q, h.coord.r), {
      lookup: `road/${h.terrain}_${oriented.name}`,
      mirror: oriented.mirror,
    });
  }
  return refs;
}
