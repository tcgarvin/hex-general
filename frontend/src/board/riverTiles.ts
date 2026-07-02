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
// the three straights (two opposite connected edges), the six gentle turns
// (edges 120° apart), and the SE/SW/S junction fork. Sharp turns, other forks and
// remaining masks fall back to the generic `00` tile until their later slices.

import type { components } from "@/lib/api/schema";

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

// --- Course-graph pruning ---------------------------------------------------
//
// Raw adjacency over-connects river junctions: where a tributary meets a main
// course, the hexes *around* the junction are also mutually adjacent, so each
// picks up edges to river hexes its water never actually flows toward (in graph
// terms: hex-grid triangles whose chords are not part of any course). Classifying
// on the raw mask would render a braided multi-channel blob at every junction.
// `riverCourseEdges` derives the course graph instead: among the edges that lie
// on a triangle of mutually-adjacent river hexes, it drops the subset that
// minimises the summed "shape awkwardness" of the affected hexes, subject to
// never disconnecting a river. On twin_rivers this resolves the tributary
// junction into one true fork + a straight + two gentle turns.

// Awkwardness of a hex's connected-edge set. Calm course shapes (straights,
// gentle turns) are cheap; a fork is expensive but sometimes the only honest
// answer; fake termini and sharp turns are worse than any gentle routing.
function shapePenalty(edges: number[]): number {
  const n = edges.length;
  if (n === 0) return 0;
  if (n === 1) return 3; // end
  if (n === 2) {
    const raw = edges[1] - edges[0];
    const gap = Math.min(raw, 6 - raw); // circular distance between the edges
    if (gap === 3) return 0; // straight
    if (gap === 2) return 1; // gentle turn
    return 4; // sharp turn
  }
  return [5, 9, 14, 20][n - 3]; // 3..6-way forks
}

// Beyond this many triangle-chord candidates the exhaustive subset search is no
// longer cheap (2^k); such a map is a river *blob*, not courses with junctions,
// and pruning would be guesswork anyway — render the raw masks.
const MAX_CHORD_CANDIDATES = 16;

interface RiverNode {
  readonly key: string;
  readonly coord: Axial;
  /** Ascending direction indices of raw river-like adjacency. */
  edges: number[];
}

/**
 * The per-hex connected-edge sets of the *course graph*: raw river/bridge
 * adjacency with junction-triangle chords pruned (see module note above).
 * Returns a map from {@link hexKey} to ascending direction indices; only
 * river-like hexes among `coords` appear. Feed the result to
 * {@link orientedRiverTile} instead of a raw {@link connectedEdges} mask.
 */
export function riverCourseEdges(
  coords: readonly Axial[],
  terrainAt: (q: number, r: number) => Terrain | undefined,
): Map<string, number[]> {
  const nodes = new Map<string, RiverNode>();
  for (const c of coords) {
    if (!isRiverLike(terrainAt(c.q, c.r))) continue;
    const key = hexKey(c.q, c.r);
    nodes.set(key, { key, coord: c, edges: connectedEdges(c, terrainAt) });
  }

  // Undirected edges, each once, id = "keyA|keyB" with keyA < keyB.
  const edgeId = (a: string, b: string): string => (a < b ? `${a}|${b}` : `${b}|${a}`);
  const neighborKey = (n: RiverNode, dir: number): string =>
    hexKey(n.coord.q + AXIAL_DIRECTIONS[dir].q, n.coord.r + AXIAL_DIRECTIONS[dir].r);

  // Triangles of mutually-adjacent river hexes are exactly {A, A+d_i, A+d_(i+1)}
  // (two consecutive directions); collect every edge that lies on one.
  const chordIds = new Set<string>();
  for (const n of nodes.values()) {
    for (let i = 0; i < 6; i++) {
      const b = neighborKey(n, i);
      const c = neighborKey(n, (i + 1) % 6);
      if (nodes.has(b) && nodes.has(c)) {
        chordIds.add(edgeId(n.key, b));
        chordIds.add(edgeId(n.key, c));
        chordIds.add(edgeId(b, c));
      }
    }
  }

  const result = (dropped: Set<string>): Map<string, number[]> => {
    const out = new Map<string, number[]>();
    for (const n of nodes.values()) {
      out.set(
        n.key,
        n.edges.filter((dir) => !dropped.has(edgeId(n.key, neighborKey(n, dir)))),
      );
    }
    return out;
  };

  if (chordIds.size === 0 || chordIds.size > MAX_CHORD_CANDIDATES) {
    return result(new Set());
  }

  // Connected components of the course graph under a given dropped-edge set;
  // pruning must never split a river, so components must match the raw graph's.
  const componentSignature = (dropped: Set<string>): string => {
    const seen = new Set<string>();
    const labels: string[] = [];
    for (const start of nodes.keys()) {
      if (seen.has(start)) continue;
      const member: string[] = [];
      const stack = [start];
      seen.add(start);
      while (stack.length > 0) {
        const k = stack.pop()!;
        member.push(k);
        const n = nodes.get(k)!;
        for (const dir of n.edges) {
          const nb = neighborKey(n, dir);
          if (!seen.has(nb) && !dropped.has(edgeId(k, nb))) {
            seen.add(nb);
            stack.push(nb);
          }
        }
      }
      labels.push(member.sort().join(","));
    }
    return labels.sort().join(";");
  };

  const chords = [...chordIds].sort();
  const affected = new Set<string>();
  for (const id of chords) {
    const [a, b] = id.split("|");
    affected.add(a);
    affected.add(b);
  }
  const rawSignature = componentSignature(new Set());

  let best: Set<string> = new Set();
  let bestScore = Infinity;
  let bestSize = Infinity;
  for (let mask = 0; mask < 1 << chords.length; mask++) {
    const dropped = new Set<string>();
    for (let i = 0; i < chords.length; i++) {
      if (mask & (1 << i)) dropped.add(chords[i]);
    }
    if (componentSignature(dropped) !== rawSignature) continue;
    let score = 0;
    for (const key of affected) {
      const n = nodes.get(key)!;
      score += shapePenalty(
        n.edges.filter((dir) => !dropped.has(edgeId(key, neighborKey(n, dir)))),
      );
    }
    // Deterministic: lower score, then fewer drops, then lower bitmask (chords
    // are lexicographically sorted, so the whole ordering is stable).
    if (score < bestScore || (score === bestScore && dropped.size < bestSize)) {
      best = dropped;
      bestScore = score;
      bestSize = dropped.size;
    }
  }
  return result(best);
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

// Handled three-edge shapes (forks), keyed like ORIENTED_PAIRS. Named through-
// course first, branch last. {0,4,5} (SE→SW course, S branch — twin_rivers'
// tributary junction) is mirror-symmetric, so one generated tile covers it;
// further fork orientations gain entries (and mirror pairs) as their art lands.
const ORIENTED_TRIPLES: Readonly<Record<string, OrientedTile>> = {
  "0,4,5": { name: "fork_se_sw_s", mirror: false },
};

/**
 * Choose the oriented tile + mirror flag for a river/bridge hex from its
 * connected-edge set (ideally the pruned {@link riverCourseEdges} mask), or
 * `null` if the shape isn't handled yet (the caller falls back to the generic
 * `00` tile). Handles the straights, gentle turns, and the SE/SW/S fork; sharp
 * turns and the remaining fork orientations come in later slices.
 */
export function orientedRiverTile(edges: number[]): OrientedTile | null {
  if (edges.length === 2) {
    return ORIENTED_PAIRS[`${edges[0]},${edges[1]}`] ?? null;
  }
  if (edges.length === 3) {
    return ORIENTED_TRIPLES[edges.join(",")] ?? null;
  }
  return null;
}
