// Mask-driven baked road tile selection (ROADMAP Phase 8 T5).
//
// A road hex's baked tile — painterly road stroke composited over the hex's own
// ratified terrain base — is chosen by *which edges connect* to neighbouring
// road-carrying hexes, exactly like river/bridge hexes (see `riverTiles.ts`).
// Roads reuse the river shape inventory verbatim: the same edge-index geometry,
// the same oriented-tile names (straights, gentle turns, ends, forks), and the
// same render-time L↔R mirror convention, via `orientedRiverTile`.
//
// A road hex renders its baked tile only when one exists for its
// `(terrain, shape)` pair — the texture lookup key
// is `"road/{terrain}_{shape}"` in `LoadedTiles.byOriented`, e.g.
// `"road/plains_straight_v"` from `tiles/road/plains_straight_v_00.png`.
// Anything unresolved (settlement hexes, and every terrain but plains: the
// 2026-11-09 Codex set bakes plains roads only, all shapes including forks)
// simply keeps its base terrain tile.
//
// Roads use the same course-graph pruning as rivers (`courseGraphEdges`, see
// `courseGraph.ts`) over the road-carrying hexes: where a road joins another
// mid-run, the hexes around the join are mutually adjacent, and the raw masks
// would draw a small triangle loop; the course graph keeps one fork. Drawing only: engine movement goes by hex flag.

import { AXIAL_DIRECTIONS, hexKey, type Axial, type Hex } from "./hex";
import { courseGraphEdges } from "./courseGraph";
import { orientedRiverTile } from "./riverTiles";


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

/**
 * The per-hex connected-edge sets of the road course graph: road adjacency
 * ({@link roadConnectedEdges}) with junction-triangle chords pruned. Keyed by
 * {@link hexKey}, ascending direction indices; every road-carrying hex among
 * `hexes` (road-flagged or bridge) appears. `hexes` must hold every
 * road-carrying hex next to one in it (pass the whole map). Build once per map.
 */
export function roadCourseEdges(
  hexes: readonly Hex[],
  hexAt: (q: number, r: number) => Hex | undefined,
): Map<string, number[]> {
  return courseGraphEdges(
    hexes.map((h) => h.coord),
    (q, r) => isRoadLike(hexAt(q, r)),
  );
}

/** A resolved baked-road-tile reference for one hex. */
export interface RoadTileRef {
  /** `byOriented` lookup key, e.g. `"road/plains_turn_n_se"`. */
  readonly lookup: string;
  /** Render with a horizontal (left↔right) flip, per the river mirror tables. */
  readonly mirror: boolean;
}

/**
 * The baked-road-tile reference for every road-flagged hex whose course-graph
 * mask (`roadCourse`, from {@link roadCourseEdges} over the same map) maps to a
 * handled oriented shape, keyed by {@link hexKey}. River and bridge hexes are
 * excluded (their own oriented art carries the water/deck/ramps); hexes whose
 * mask is unhandled (4+-way blobs, defensive cases) are simply absent. Whether
 * the referenced texture actually exists is the render site's decision — a
 * missing `byOriented` group falls back to the vector road overlay, so this map
 * stays a pure geometry computation.
 */
export function roadTileRefs(
  hexes: readonly Hex[],
  roadCourse: ReadonlyMap<string, readonly number[]>,
): Map<string, RoadTileRef> {
  const refs = new Map<string, RoadTileRef>();
  for (const h of hexes) {
    if (!h.road || h.terrain === "river" || h.terrain === "bridge") continue;
    const key = hexKey(h.coord.q, h.coord.r);
    const oriented = orientedRiverTile(roadCourse.get(key)!);
    if (oriented === null) continue;
    refs.set(key, {
      lookup: `road/${h.terrain}_${oriented.name}`,
      mirror: oriented.mirror,
    });
  }
  return refs;
}
