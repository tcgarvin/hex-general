// Order-arrow geometry (planning layer). Pure math: turns a staged move path
// (hex-center pixels) into filled-polygon outlines for a tapered ribbon arrow —
// narrow at the unit, widening toward a triangular head at the destination.
// Rendering (colors, white casing, pulse badges) lives in Board.tsx; this
// module only computes outlines so it can be unit-tested headlessly.
//
// A path may be split by expected enemy contact (SPEC §8.4): the unit halts
// before `stopSteps + 1`-th hex, so the ribbon is returned in two pieces that
// share a flat seam — the reachable run (headless when a blocked run follows)
// and the blocked tail (which carries the head). `casing` is always the whole
// arrow as one polygon, so a translucent outline behind both pieces composites
// once, with no double-darkened seam.

export interface Pt {
  x: number;
  y: number;
}

/** Ribbon + head dimensions, in world pixels (pre-zoom). */
export interface ArrowDims {
  /** Gap between the origin hex center and the ribbon tail (clears the sprite). */
  startOffset: number;
  /** Half-width of the ribbon at the tail. */
  tailHalf: number;
  /** Half-width of the ribbon where it meets the head base. */
  headBaseHalf: number;
  /** Head length, base to apex. */
  headLen: number;
  /** Half-width of the head at its base. */
  headHalf: number;
  /** How far the apex pokes past the destination hex center. */
  headNose: number;
}

export interface ArrowOutlines {
  /** The whole arrow as one polygon (for the casing pass). */
  casing: number[];
  /** Origin → stop. Null when the unit can't move at all (stopSteps = 0). */
  reachable: number[] | null;
  /** Stop → destination, with the head. Null when nothing is blocked. */
  blocked: number[] | null;
}

function sub(a: Pt, b: Pt): Pt {
  return { x: a.x - b.x, y: a.y - b.y };
}

function add(a: Pt, b: Pt): Pt {
  return { x: a.x + b.x, y: a.y + b.y };
}

function mul(a: Pt, k: number): Pt {
  return { x: a.x * k, y: a.y * k };
}

function len(a: Pt): number {
  return Math.hypot(a.x, a.y);
}

function norm(a: Pt): Pt {
  const l = len(a);
  return { x: a.x / l, y: a.y / l };
}

function perp(a: Pt): Pt {
  return { x: -a.y, y: a.x };
}

/** Segment direction at each vertex, averaged at interior bends. */
function vertexDirs(pts: readonly Pt[]): Pt[] {
  const segs = pts.slice(1).map((p, i) => norm(sub(p, pts[i])));
  return pts.map((_, i) => {
    if (i === 0) return segs[0];
    if (i === pts.length - 1) return segs[segs.length - 1];
    return norm(add(segs[i - 1], segs[i]));
  });
}

/** Cumulative polyline length at each vertex. */
function cumLengths(pts: readonly Pt[]): number[] {
  const out = [0];
  for (let i = 1; i < pts.length; i++) out.push(out[i - 1] + len(sub(pts[i], pts[i - 1])));
  return out;
}

/**
 * Outline of a tapered strip along `pts` with the given per-vertex half-widths,
 * optionally capped with a triangular head continuing from the last vertex.
 * Interior bends get miter-compensated offsets (clamped) so the ribbon keeps
 * its width through a turn. Returns a flat `[x0, y0, x1, y1, …]` polygon.
 */
function stripOutline(
  pts: readonly Pt[],
  halves: readonly number[],
  head: { dir: Pt; base: Pt; half: number; apex: Pt } | null,
): number[] {
  const dirs = vertexDirs(pts);
  const segs = pts.slice(1).map((p, i) => norm(sub(p, pts[i])));
  const left: Pt[] = [];
  const right: Pt[] = [];
  for (let i = 0; i < pts.length; i++) {
    const n = perp(dirs[i]);
    // Miter compensation: the averaged normal shortens by cos(θ/2) at a bend.
    const segN = perp(segs[Math.min(i, segs.length - 1)]);
    const cos = Math.max(0.5, n.x * segN.x + n.y * segN.y);
    const off = halves[i] / cos;
    left.push(add(pts[i], mul(n, off)));
    right.push(add(pts[i], mul(n, -off)));
  }

  const ring: Pt[] = [...left];
  if (head !== null) {
    const hn = perp(head.dir);
    ring.push(add(head.base, mul(hn, head.half)));
    ring.push(head.apex);
    ring.push(add(head.base, mul(hn, -head.half)));
  }
  ring.push(...right.reverse());
  return ring.flatMap((p) => [p.x, p.y]);
}

/** Point at arc length `s` along the polyline (clamped to its ends). */
function pointAt(pts: readonly Pt[], cum: readonly number[], s: number): Pt {
  if (s <= 0) return pts[0];
  if (s >= cum[cum.length - 1]) return pts[pts.length - 1];
  let i = 1;
  while (cum[i] < s) i++;
  const t = (s - cum[i - 1]) / (cum[i] - cum[i - 1]);
  return add(pts[i - 1], mul(sub(pts[i], pts[i - 1]), t));
}

/** Move-arrow dims plus the arch of the barrage lob. */
export interface BarrageArrowDims extends ArrowDims {
  /** Mid-arc rise as a fraction of the shot distance. */
  sagFactor: number;
  /** Cap on the mid-arc rise, world px. */
  sagMax: number;
}

/**
 * Ribbon outline for a barrage: a quadratic arc from the gun to the target,
 * bowed toward screen-up so it reads as a shell lobbed over the field (a
 * dead-vertical shot bows west, for determinism). The sampled arc gets the
 * same trim/taper/head treatment as move arrows, but trimmed by arc length —
 * the move trim shifts endpoints along a segment, which assumes hex-pitch
 * segments, not fine arc samples. A negative `headNose` parks the apex short
 * of the target center (at the reticle edge).
 */
export function barrageArrowOutline(origin: Pt, target: Pt, dims: BarrageArrowDims): number[] {
  const chord = sub(target, origin);
  const d = len(chord);
  if (d < 1e-6) return [];

  const n = perp(norm(chord));
  const bow = n.y < 0 || (n.y === 0 && n.x < 0) ? n : mul(n, -1);
  const sag = Math.min(d * dims.sagFactor, dims.sagMax);
  // A quadratic Bézier rises to half its control offset at the midpoint.
  const ctrl = add(add(origin, mul(chord, 0.5)), mul(bow, 2 * sag));

  const SAMPLES = 24;
  const arc: Pt[] = [];
  for (let i = 0; i <= SAMPLES; i++) {
    const t = i / SAMPLES;
    const a = 1 - t;
    arc.push({
      x: a * a * origin.x + 2 * a * t * ctrl.x + t * t * target.x,
      y: a * a * origin.y + 2 * a * t * ctrl.y + t * t * target.y,
    });
  }

  const cum = cumLengths(arc);
  const total = cum[cum.length - 1];
  let startTrim = dims.startOffset;
  let endTrim = dims.headLen - dims.headNose - 1; // 1px overlap into the head
  const budget = total * 0.85;
  if (startTrim + endTrim > budget) {
    const k = budget / (startTrim + endTrim);
    startTrim *= k;
    endTrim *= k;
  }

  // Shaft polyline between the trims, each vertex tagged with its arc length
  // so the tail → head-base taper is linear in distance travelled.
  const sEnd = total - endTrim;
  const pts: Pt[] = [pointAt(arc, cum, startTrim)];
  const arcLens: number[] = [startTrim];
  for (let i = 0; i < arc.length; i++) {
    if (cum[i] > startTrim && cum[i] < sEnd) {
      pts.push(arc[i]);
      arcLens.push(cum[i]);
    }
  }
  pts.push(pointAt(arc, cum, sEnd));
  arcLens.push(sEnd);

  const span = sEnd - startTrim;
  const halves = arcLens.map(
    (s) =>
      dims.tailHalf +
      (dims.headBaseHalf - dims.tailHalf) * (span === 0 ? 1 : (s - startTrim) / span),
  );

  // Head: a straight triangle along the chord from the shaft end to the
  // target — a good tangent stand-in over the last few px of a gentle arc.
  const hd = norm(sub(target, pts[pts.length - 1]));
  const apex = add(target, mul(hd, dims.headNose));
  const base = add(apex, mul(hd, -dims.headLen));

  return stripOutline(pts, halves, { dir: hd, base, half: dims.headHalf, apex });
}

/**
 * Ribbon-arrow outlines for a staged move. `centers` is the origin hex center
 * followed by each path hex center (so length = path length + 1); `stopSteps`
 * is how many of those steps the unit is expected to complete before enemy
 * contact halts it (`path.length` when unblocked, 0 when it can't move at all).
 */
export function moveArrowOutlines(
  centers: readonly Pt[],
  stopSteps: number,
  dims: ArrowDims,
): ArrowOutlines {
  if (centers.length < 2) return { casing: [], reachable: null, blocked: null };

  // Trim the polyline: lift the tail off the origin sprite and tuck the shaft
  // just under the head. On short paths, scale both trims down so a sliver of
  // shaft always survives.
  const pts = centers.map((p) => ({ ...p }));
  const total = cumLengths(pts)[pts.length - 1];
  const endDir = norm(sub(pts[pts.length - 1], pts[pts.length - 2]));
  const apex = add(centers[centers.length - 1], mul(endDir, dims.headNose));
  const base = add(apex, mul(endDir, -dims.headLen));

  let startTrim = dims.startOffset;
  let endTrim = dims.headLen - dims.headNose - 1; // 1px overlap into the head
  const budget = total * 0.85;
  if (startTrim + endTrim > budget) {
    const k = budget / (startTrim + endTrim);
    startTrim *= k;
    endTrim *= k;
  }
  const startDir = norm(sub(pts[1], pts[0]));
  pts[0] = add(pts[0], mul(startDir, startTrim));
  pts[pts.length - 1] = add(centers[centers.length - 1], mul(endDir, -endTrim));

  // Half-width at each vertex: linear in arc length, tail → head base.
  const cum = cumLengths(pts);
  const span = cum[cum.length - 1];
  const halves = cum.map(
    (d) => dims.tailHalf + (dims.headBaseHalf - dims.tailHalf) * (span === 0 ? 1 : d / span),
  );

  const head = { dir: endDir, base, half: dims.headHalf, apex };
  const casing = stripOutline(pts, halves, head);

  const steps = centers.length - 1;
  if (stopSteps >= steps) return { casing, reachable: casing, blocked: null };
  if (stopSteps <= 0) return { casing, reachable: null, blocked: casing };

  // Split at the stop hex center; both pieces reuse the trimmed endpoints and
  // interpolated widths, so they share an exact flat seam.
  const reachable = stripOutline(pts.slice(0, stopSteps + 1), halves.slice(0, stopSteps + 1), null);
  const blocked = stripOutline(pts.slice(stopSteps), halves.slice(stopSteps), head);
  return { casing, reachable, blocked };
}
