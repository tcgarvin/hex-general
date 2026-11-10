// Course-graph pruning for linear map features drawn from edge masks (rivers and
// roads; see `riverTiles.ts` and `roadTiles.ts`).
//
// Raw adjacency over-connects junctions: where a tributary meets a main course
// (or a road joins another mid-run), the hexes *around* the junction are also
// mutually adjacent, so each picks up edges toward hexes its course never runs
// to (in graph terms: hex-grid triangles whose chords are not part of any
// course). Classifying on the raw mask would draw a braided blob or a small
// triangle loop at every junction. `courseGraphEdges` derives the course graph
// instead: among the edges that lie on a triangle of mutually-adjacent carrying
// hexes ("chords"), it drops the subset that minimises the summed shape
// awkwardness of the affected hexes, subject to never disconnecting the network.
//
// Pruning is local. Chords are partitioned into clusters (two chords are in the
// same cluster when they share a hex; clusters are the connected components of
// that relation), and each cluster is solved on its own by exhaustive search
// over its chord subsets. Clusters share no hex, and a hex's shape depends only
// on its own edges, so the summed penalty splits into one independent term per
// cluster: per-cluster optima add up to the joint optimum. A cluster with more
// than MAX_CHORD_CANDIDATES chords is a blob, not a junction, and is left
// unpruned (raw masks).
//
// Connectivity. Let N be the non-chord edges of the whole graph and B the graph
// (all carrying hexes, N). Dropping S from cluster C is accepted when every
// dropped edge's endpoints are still connected in B ∪ (C \ S). That is checked
// on B's components (one union-find pass per map) contracted to the cluster's
// own hexes, so a candidate subset costs O(|C|), not a map traversal.
//   * Exact for one cluster: if C holds every chord, B ∪ (C \ S) is the whole
//     graph minus S, and "each dropped edge's endpoints stay connected" is the
//     same as "the components do not change", the original whole-map rule.
//   * Safe in combination: each cluster's B ∪ (C \ S) uses only non-chord edges
//     and C's own kept chords, all of which survive every other cluster's drops
//     (clusters are edge-disjoint), so every dropped edge in the final graph
//     still has connected endpoints and no component splits.
//   * Stricter than the whole-map rule only where a dropped chord's endpoints
//     would reconnect solely through *another* cluster's chords (a long loop
//     between two junctions). There the drop would leave a visible dead end at
//     the junction, so refusing it is the better drawing anyway.
// Exactly one optimum is chosen per cluster: lowest score, then fewest drops,
// then the lowest bitmask over the cluster's chord ids sorted as strings (ids
// are "keyA|keyB" with keyA < keyB, keys from `hexKey`), so it is deterministic.

import { AXIAL_DIRECTIONS, hexKey, type Axial } from "./hex";

/**
 * Awkwardness of a hex's connected-edge set (ascending direction indices).
 * Calm course shapes (straights, gentle turns) are cheap; a fork is expensive
 * but sometimes the only honest answer; fake termini and sharp turns are worse
 * than any gentle routing.
 */
export function shapePenalty(edges: readonly number[]): number {
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

/**
 * Most chords one cluster may have and still be searched exhaustively (2^k
 * subsets). A bigger cluster is a blob, where pruning would be guesswork.
 */
export const MAX_CHORD_CANDIDATES = 16;

/** Union-find over `0..size-1` with path halving. */
class DisjointSets {
  private readonly parent: Int32Array;

  constructor(size: number) {
    this.parent = new Int32Array(size);
    this.reset(size);
  }

  /** Make the first `size` elements singletons again. */
  reset(size: number): void {
    for (let i = 0; i < size; i++) this.parent[i] = i;
  }

  find(x: number): number {
    const p = this.parent;
    while (p[x] !== x) {
      p[x] = p[p[x]];
      x = p[x];
    }
    return x;
  }

  union(a: number, b: number): void {
    const ra = this.find(a);
    const rb = this.find(b);
    if (ra !== rb) this.parent[ra] = rb;
  }
}

interface Chord {
  /** "keyA|keyB", keyA < keyB as strings. */
  readonly id: string;
  /** Node indices of the two ends. */
  readonly a: number;
  readonly b: number;
}

/**
 * The per-hex connected-edge sets of the course graph over the carrying hexes
 * among `coords`: raw adjacency (every direction whose neighbour `carries`) with
 * junction-triangle chords pruned (see the module note). Returns a map from
 * {@link hexKey} to ascending direction indices; only carrying hexes among
 * `coords` appear. `coords` must include every carrying hex next to a carrying
 * hex in it (pass the whole map, or every carrying hex); a carrying neighbour
 * missing from `coords` throws.
 */
export function courseGraphEdges(
  coords: readonly Axial[],
  carries: (q: number, r: number) => boolean,
): Map<string, number[]> {
  const keys: string[] = [];
  const nodeCoords: Axial[] = [];
  const indexOf = new Map<string, number>();
  for (const c of coords) {
    if (!carries(c.q, c.r)) continue;
    const key = hexKey(c.q, c.r);
    if (indexOf.has(key)) continue;
    indexOf.set(key, keys.length);
    keys.push(key);
    nodeCoords.push(c);
  }
  const n = keys.length;

  // neighbour[i * 6 + d]: node index of node i's neighbour in direction d, or -1.
  const neighbour = new Int32Array(n * 6).fill(-1);
  for (let i = 0; i < n; i++) {
    const c = nodeCoords[i];
    for (let d = 0; d < 6; d++) {
      const q = c.q + AXIAL_DIRECTIONS[d].q;
      const r = c.r + AXIAL_DIRECTIONS[d].r;
      const j = indexOf.get(hexKey(q, r));
      if (j !== undefined) {
        neighbour[i * 6 + d] = j;
      } else if (carries(q, r)) {
        throw new Error(`course graph: carrying hex (${q}, ${r}) next to ${keys[i]} is not among the coords`);
      }
    }
  }
  const edgeNumber = (a: number, b: number): number => (a < b ? a * n + b : b * n + a);

  // Triangles of mutually-adjacent carrying hexes are exactly {A, A+d_i, A+d_(i+1)}
  // (two consecutive directions); every edge on one is a chord.
  const chordIndex = new Map<number, number>(); // edgeNumber -> index into chords
  const chords: Chord[] = [];
  const addChord = (a: number, b: number): void => {
    const e = edgeNumber(a, b);
    if (chordIndex.has(e)) return;
    chordIndex.set(e, chords.length);
    chords.push(keys[a] < keys[b] ? { id: `${keys[a]}|${keys[b]}`, a, b } : { id: `${keys[b]}|${keys[a]}`, a: b, b: a });
  };
  for (let a = 0; a < n; a++) {
    for (let d = 0; d < 6; d++) {
      const b = neighbour[a * 6 + d];
      const c = neighbour[a * 6 + ((d + 1) % 6)];
      if (b >= 0 && c >= 0) {
        addChord(a, b);
        addChord(a, c);
        addChord(b, c);
      }
    }
  }

  const dropped = new Set<number>(); // edgeNumbers
  if (chords.length > 0) {
    // B: components over the non-chord edges only.
    const plain = new DisjointSets(n);
    // Clusters: hexes joined by chords (chords sharing a hex share a cluster).
    const byChord = new DisjointSets(n);
    for (let a = 0; a < n; a++) {
      for (let d = 0; d < 6; d++) {
        const b = neighbour[a * 6 + d];
        if (b > a && !chordIndex.has(edgeNumber(a, b))) plain.union(a, b);
      }
    }
    for (const ch of chords) byChord.union(ch.a, ch.b);
    const clusters = new Map<number, Chord[]>();
    for (const ch of chords) {
      const root = byChord.find(ch.a);
      const list = clusters.get(root);
      if (list === undefined) clusters.set(root, [ch]);
      else list.push(ch);
    }
    for (const cluster of clusters.values()) {
      if (cluster.length > MAX_CHORD_CANDIDATES) continue;
      cluster.sort((x, y) => (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));
      for (const ch of solveCluster(cluster, neighbour, plain, edgeNumber)) {
        dropped.add(edgeNumber(ch.a, ch.b));
      }
    }
  }

  const out = new Map<string, number[]>();
  for (let a = 0; a < n; a++) {
    const edges: number[] = [];
    for (let d = 0; d < 6; d++) {
      const b = neighbour[a * 6 + d];
      if (b >= 0 && !dropped.has(edgeNumber(a, b))) edges.push(d);
    }
    out.set(keys[a], edges);
  }
  return out;
}

/**
 * The chords of one cluster (sorted by id) to drop: the lowest-penalty subset
 * whose drops keep every dropped edge's ends connected through the non-chord
 * components (`plain`) and the cluster's kept chords.
 */
function solveCluster(
  cluster: readonly Chord[],
  neighbour: Int32Array,
  plain: DisjointSets,
  edgeNumber: (a: number, b: number) => number,
): Chord[] {
  const local = new Map<number, number>(); // edgeNumber -> bit in the subset mask
  cluster.forEach((ch, bit) => local.set(edgeNumber(ch.a, ch.b), bit));

  // Per affected hex: the mask bits of its incident chords, and its penalty for
  // each subset of those chords dropped (indexed by the compacted bits).
  const hexes = [...new Set(cluster.flatMap((ch) => [ch.a, ch.b]))];
  const hexBits: number[][] = [];
  const hexTable: number[][] = [];
  for (const h of hexes) {
    const dirs: number[] = [];
    const dirBit: number[] = []; // chord bit per entry of dirs, or -1
    for (let d = 0; d < 6; d++) {
      const nb = neighbour[h * 6 + d];
      if (nb < 0) continue;
      dirs.push(d);
      dirBit.push(local.get(edgeNumber(h, nb)) ?? -1);
    }
    const bits = dirBit.filter((b) => b >= 0);
    const table: number[] = [];
    for (let sub = 0; sub < 1 << bits.length; sub++) {
      const kept = dirs.filter((_, k) => {
        const t = bits.indexOf(dirBit[k]);
        return t < 0 || (sub & (1 << t)) === 0;
      });
      table.push(shapePenalty(kept));
    }
    hexBits.push(bits);
    hexTable.push(table);
  }

  // Chord ends as small labels: their component in B, renumbered 0..m-1.
  const labelOf = new Map<number, number>();
  const label = (h: number): number => {
    const root = plain.find(h);
    let l = labelOf.get(root);
    if (l === undefined) {
      l = labelOf.size;
      labelOf.set(root, l);
    }
    return l;
  };
  const endA = cluster.map((ch) => label(ch.a));
  const endB = cluster.map((ch) => label(ch.b));
  const sets = new DisjointSets(labelOf.size);

  const connectedWithout = (mask: number): boolean => {
    sets.reset(labelOf.size);
    for (let j = 0; j < cluster.length; j++) {
      if ((mask & (1 << j)) === 0) sets.union(endA[j], endB[j]);
    }
    for (let j = 0; j < cluster.length; j++) {
      if ((mask & (1 << j)) !== 0 && sets.find(endA[j]) !== sets.find(endB[j])) return false;
    }
    return true;
  };

  let bestMask = 0;
  let bestScore = Infinity;
  let bestSize = Infinity;
  for (let mask = 0; mask < 1 << cluster.length; mask++) {
    let score = 0;
    for (let k = 0; k < hexes.length; k++) {
      const bits = hexBits[k];
      let sub = 0;
      for (let t = 0; t < bits.length; t++) {
        if ((mask & (1 << bits[t])) !== 0) sub |= 1 << t;
      }
      score += hexTable[k][sub];
    }
    const size = popcount(mask);
    // Lower score, then fewer drops; masks ascend, so the first such wins.
    if (score > bestScore || (score === bestScore && size >= bestSize)) continue;
    if (!connectedWithout(mask)) continue;
    bestMask = mask;
    bestScore = score;
    bestSize = size;
  }
  return cluster.filter((_, j) => (bestMask & (1 << j)) !== 0);
}

function popcount(x: number): number {
  let c = 0;
  while (x !== 0) {
    x &= x - 1;
    c++;
  }
  return c;
}
