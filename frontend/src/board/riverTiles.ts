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
// the six ends (one connected edge — a river/bridge terminus), the three
// straights (two opposite connected edges), the six gentle turns (edges 120°
// apart), and all 20 three-edge junction forks (C(6,3) — every three-edge mask).
// Sharp turns (two adjacent connected edges) are disallowed by design ruling
// (user, 2026-07-03): a river course must never be authored to produce one, and
// `shapePenalty` (`courseGraph.ts`) already steers the course-graph pruner away from choosing one
// over a calmer routing. They are not "art pending" — no tile will ever be
// generated for them, and the generic `00` fallback that catches them is purely
// defensive (a bug in map authoring, not a missing-art gap). Masks of four or
// more connected edges are likewise not modelled and fall back to `00`.
//
// The river set ships art for every name `orientedRiverTile` returns (all twelve
// fork tiles since the 2026-11-09 Codex set); bridges ship the vertical straight and two
// turns. The fallback to the generic `00` tile for a missing texture is *not*
// here: it's the render-site lookup in `hexTile.ts` (`terrainTex.byOriented[name]
// ?? terrainTex.byOriented[\`${terrain}/00\`]`), which falls through to the "00"
// group whenever the named oriented group has no loaded variants.

import type { components } from "@/lib/api/schema";

import { courseGraphEdges } from "./courseGraph";
import { AXIAL_DIRECTIONS, hexKey, type Axial } from "./hex";

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

// --- Course graph -----------------------------------------------------------
//
// A river hex's connected edges come from the *course graph*, not raw
// adjacency: junction-triangle chords are pruned (`courseGraphEdges`, shared
// with roads, see `courseGraph.ts`) so a tributary junction draws as one fork
// instead of a braided blob, and a river's open end gets one outlet edge where
// the water visibly leaves the hex:
//
// * **Mouth rule.** Lake hexes are not river-like, but a river/bridge hex with at
//   most one river-like neighbour and at least one lake neighbour connects to
//   exactly one lake neighbour (`riverOutletDirection` picks it), so the river
//   runs its water to the lake shore instead of ending in a cap. The lake hex's
//   own tile never changes.
// * **Edge rule** (lower priority: only when the mouth rule gave nothing). Such a
//   hex on the map edge (a neighbour direction off the map, `terrainAt` ⇒
//   `undefined`) connects to exactly one off-map direction, chosen the same way,
//   so a river flows off the map instead of ending in a cap on the border.
//
// A hex with at most one river-like neighbour is on no triangle (a triangle
// needs two), so pruning never touches it and the outlet edge can be added
// after pruning without changing the pruning.

/** Circular distance between two direction indices (0..3). */
function directionGap(a: number, b: number): number {
  const raw = Math.abs(a - b);
  return Math.min(raw, 6 - raw);
}

/**
 * The one outlet direction for a river/bridge hex R, from R's river-like
 * neighbour directions `riverDirs` and its candidate outlet directions
 * `candidateDirs` (lake neighbours for the mouth rule, off-map directions for
 * the edge rule; never one of `riverDirs`), or `null` for no outlet.
 *
 * R gets no outlet with two or more river-like neighbours (a mid-course hex) or
 * no candidates. With no river-like neighbour it takes the lowest candidate
 * index. Otherwise, with `p` its one river connection, the calmest shape wins:
 * straight (`d` opposite `p`), then a gentle turn (`d` two steps from `p`);
 * never a sharp turn (`d` next to `p`), so sharp-only candidates give `null`.
 * Ties go to the lowest direction index.
 */
export function riverOutletDirection(
  riverDirs: readonly number[],
  candidateDirs: readonly number[],
): number | null {
  if (riverDirs.length > 1 || candidateDirs.length === 0) return null;
  if (riverDirs.length === 0) return Math.min(...candidateDirs);
  const p = riverDirs[0];
  let best: number | null = null;
  let bestGap = 0;
  for (const d of candidateDirs) {
    const gap = directionGap(d, p);
    if (gap < 2) continue; // sharp (or p itself)
    if (best === null || gap > bestGap || (gap === bestGap && d < best)) {
      best = d;
      bestGap = gap;
    }
  }
  return best;
}

/** Where a river leaves through a hex edge: into a lake, or off the map. */
export interface RiverOutlet {
  /** The river/bridge hex R. */
  readonly coord: Axial;
  /** Direction index (into {@link AXIAL_DIRECTIONS}) of the outlet edge. */
  readonly direction: number;
  /** `"lake"`: a mouth onto the lake hex there; `"edge"`: off the map. */
  readonly kind: "lake" | "edge";
}

/**
 * Every river outlet among `coords` (distinct coords; non-river hexes are
 * skipped): the mouth rule first, the edge rule for hexes it gave nothing.
 * At most one outlet per hex, in `coords` order.
 */
export function riverOutlets(
  coords: readonly Axial[],
  terrainAt: (q: number, r: number) => Terrain | undefined,
): RiverOutlet[] {
  const out: RiverOutlet[] = [];
  for (const c of coords) {
    if (!isRiverLike(terrainAt(c.q, c.r))) continue;
    const riverDirs: number[] = [];
    const lakeDirs: number[] = [];
    const offMapDirs: number[] = [];
    AXIAL_DIRECTIONS.forEach((d, i) => {
      const t = terrainAt(c.q + d.q, c.r + d.r);
      if (isRiverLike(t)) riverDirs.push(i);
      else if (t === "lake") lakeDirs.push(i);
      else if (t === undefined) offMapDirs.push(i);
    });
    const mouth = riverOutletDirection(riverDirs, lakeDirs);
    if (mouth !== null) {
      out.push({ coord: c, direction: mouth, kind: "lake" });
      continue;
    }
    const edge = riverOutletDirection(riverDirs, offMapDirs);
    if (edge !== null) out.push({ coord: c, direction: edge, kind: "edge" });
  }
  return out;
}

/**
 * The river mouths among `coords` (river hex + the direction of the lake hex it
 * runs into), for drawing mouth join art. Nothing draws them yet.
 */
export function riverMouths(
  coords: readonly Axial[],
  terrainAt: (q: number, r: number) => Terrain | undefined,
): RiverOutlet[] {
  return riverOutlets(coords, terrainAt).filter((o) => o.kind === "lake");
}

/**
 * The per-hex connected-edge sets of the river *course graph*: river/bridge
 * adjacency with junction-triangle chords pruned, plus each hex's outlet edge
 * (lake mouth or map edge; see the note above). Returns a map from
 * {@link hexKey} to ascending direction indices; only river-like hexes among
 * `coords` appear, and `coords` must hold every river-like hex next to one in
 * it. Feed the result to {@link orientedRiverTile} instead of a raw
 * {@link connectedEdges} mask.
 */
export function riverCourseEdges(
  coords: readonly Axial[],
  terrainAt: (q: number, r: number) => Terrain | undefined,
): Map<string, number[]> {
  const course = courseGraphEdges(coords, (q, r) => isRiverLike(terrainAt(q, r)));
  for (const outlet of riverOutlets(coords, terrainAt)) {
    const key = hexKey(outlet.coord.q, outlet.coord.r);
    course.set(key, [...course.get(key)!, outlet.direction].sort((a, b) => a - b));
  }
  return course;
}

export interface OrientedTile {
  /** Variant basename within the terrain's tile folder, e.g. "straight_v". */
  readonly name: string;
  /** Render with a horizontal (left↔right) flip. */
  readonly mirror: boolean;
}

// Every handled one-edge shape (a river/bridge terminus), keyed by the single
// connected edge. Same mirror convention as the pairs/triples tables below
// (M: 0↔4, 1↔3, 2→2, 5→5); {2} (due N) and {5} (due S) are fixed points of M,
// like the self-symmetric turns, so each needs only one generated tile.
const ORIENTED_SINGLES: Readonly<Record<string, OrientedTile>> = {
  "0": { name: "end_se", mirror: false },
  "4": { name: "end_se", mirror: true },
  "1": { name: "end_ne", mirror: false },
  "3": { name: "end_ne", mirror: true },
  "2": { name: "end_n", mirror: false },
  "5": { name: "end_s", mirror: false },
};

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

// All twenty three-edge shapes (forks), keyed like ORIENTED_PAIRS — C(6,3) = 20,
// so every three-edge mask is handled. Named through-course first, branch last.
// Twelve base masks (11 generated here + the shipped `fork_se_sw_s`) carry
// `mirror: false`; the other eight are the L↔R mirror of a base mask under the
// render-time edge permutation M: 0↔4, 1↔3, 2→2, 5→5 (same convention as
// ORIENTED_PAIRS' turn mirrors) and map to that base's tile name with
// `mirror: true`. Four masks are fixed points of M ({0,2,4}, {1,3,5}, {1,2,3},
// and the shipped {0,4,5}) — each is mirror-symmetric, so a single generated
// tile covers it and `mirror` is always `false`. See
// `/home/timg/.claude/jobs/37d5bab1/tmp/fork-inventory.md` for the ratified
// name/edge inventory this table is generated from.
const ORIENTED_TRIPLES: Readonly<Record<string, OrientedTile>> = {
  // Family A — straight course + 60° branch (6 base tiles / 12 masks).
  "0,2,5": { name: "fork_se_n_s", mirror: false },
  "2,4,5": { name: "fork_se_n_s", mirror: true },
  "1,2,5": { name: "fork_ne_n_s", mirror: false },
  "2,3,5": { name: "fork_ne_n_s", mirror: true },
  "0,1,3": { name: "fork_se_ne_nw", mirror: false },
  "1,3,4": { name: "fork_se_ne_nw", mirror: true },
  "0,2,3": { name: "fork_se_n_nw", mirror: false },
  "1,2,4": { name: "fork_se_n_nw", mirror: true },
  "0,3,4": { name: "fork_se_nw_sw", mirror: false },
  "0,1,4": { name: "fork_se_nw_sw", mirror: true },
  "0,3,5": { name: "fork_se_nw_s", mirror: false },
  "1,4,5": { name: "fork_se_nw_s", mirror: true },
  // Family B — symmetric 120° Y (2 tiles, self-mirror-symmetric).
  "0,2,4": { name: "fork_se_n_sw", mirror: false },
  "1,3,5": { name: "fork_ne_nw_s", mirror: false },
  // Family C — fans, 3 consecutive edges (3 tiles / 5 masks).
  "1,2,3": { name: "fork_ne_n_nw", mirror: false },
  "0,1,2": { name: "fork_se_ne_n", mirror: false },
  "2,3,4": { name: "fork_se_ne_n", mirror: true },
  "0,1,5": { name: "fork_se_ne_s", mirror: false },
  "3,4,5": { name: "fork_se_ne_s", mirror: true },
  // SE/SW/S junction fork (twin_rivers' tributary junction) — self-symmetric,
  // shipped ahead of the rest of the family in an earlier slice.
  "0,4,5": { name: "fork_se_sw_s", mirror: false },
};

/**
 * Choose the oriented tile + mirror flag for a river/bridge hex from its
 * connected-edge set (ideally the pruned {@link riverCourseEdges} mask), or
 * `null` if the shape isn't handled (the caller falls back to the generic `00`
 * tile). Handles the six ends, the three straights, the six gentle turns, and
 * all twenty three-edge fork orientations. Two-edge sharp turns (edges
 * adjacent in {@link AXIAL_DIRECTIONS}) are permanently unhandled by design
 * ruling — see the module header — not a gap awaiting art; masks of four or
 * more edges are likewise unmodelled. Both fall back to the generic `00` tile
 * as a defensive render, not an expected map state.
 */
export function orientedRiverTile(edges: readonly number[]): OrientedTile | null {
  if (edges.length === 1) {
    return ORIENTED_SINGLES[`${edges[0]}`] ?? null;
  }
  if (edges.length === 2) {
    return ORIENTED_PAIRS[`${edges[0]},${edges[1]}`] ?? null;
  }
  if (edges.length === 3) {
    return ORIENTED_TRIPLES[edges.join(",")] ?? null;
  }
  return null;
}
