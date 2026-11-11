import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import { AXIAL_DIRECTIONS, hexKey, type Axial, type Hex } from "./hex";
import { roadConnectedEdges, roadCourseEdges, roadTileRefs } from "./roadTiles";

type Terrain = components["schemas"]["Terrain"];

function hex(q: number, r: number, terrain: Terrain, road = false): Hex {
  return { coord: { q, r }, terrain, road };
}

/** Build a `hexAt` probe from a list of hexes. */
function hexProbe(hexes: Hex[]) {
  const byKey = new Map(hexes.map((h) => [`${h.coord.q},${h.coord.r}`, h]));
  return (q: number, r: number): Hex | undefined => byKey.get(`${q},${r}`);
}

/** Hexes on the given AXIAL_DIRECTIONS neighbours of (0,0). */
function neighbours(dirs: number[], terrain: Terrain, road: boolean): Hex[] {
  return dirs.map((i) => hex(AXIAL_DIRECTIONS[i].q, AXIAL_DIRECTIONS[i].r, terrain, road));
}

const ORIGIN: Axial = { q: 0, r: 0 };

/** `roadTileRefs` over `hexes` as the whole map, with its course graph. */
function refsFor(hexes: Hex[]) {
  return roadTileRefs(hexes, roadCourseEdges(hexes, hexProbe(hexes)));
}

describe("roadConnectedEdges", () => {
  it("returns the edges whose neighbour is road-flagged, ascending", () => {
    const probe = hexProbe(neighbours([5, 2], "plains", true));
    expect(roadConnectedEdges(ORIGIN, probe)).toEqual([2, 5]);
  });

  it("counts bridge neighbours even without a road flag (the crossing carries the road)", () => {
    const probe = hexProbe(neighbours([0], "bridge", false));
    expect(roadConnectedEdges(ORIGIN, probe)).toEqual([0]);
  });

  it("ignores non-road neighbours and off-map (undefined) directions", () => {
    const probe = hexProbe([
      ...neighbours([2], "plains", true),
      ...neighbours([5], "plains", false),
    ]);
    expect(roadConnectedEdges(ORIGIN, probe)).toEqual([2]);
  });
});

describe("roadTileRefs", () => {
  it("resolves a plains N/S through-road to the vertical straight, unmirrored", () => {
    const hexes = [hex(0, 0, "plains", true), ...neighbours([2, 5], "plains", true)];
    const refs = refsFor(hexes);
    expect(refs.get("0,0")).toEqual({ lookup: "road/plains_straight_v", mirror: false });
  });

  it("resolves a mirrored gentle turn with the river mirror convention", () => {
    // {2,4} is turn_n_se mirrored (see ORIENTED_PAIRS in riverTiles.ts).
    const hexes = [hex(0, 0, "hills", true), ...neighbours([2, 4], "plains", true)];
    const refs = refsFor(hexes);
    expect(refs.get("0,0")).toEqual({ lookup: "road/hills_turn_n_se", mirror: true });
  });

  it("keys the lookup by the hex's own terrain (settlements resolve at render time)", () => {
    const hexes = [hex(0, 0, "town", true), ...neighbours([0], "plains", true)];
    const refs = refsFor(hexes);
    expect(refs.get("0,0")).toEqual({ lookup: "road/town_end_se", mirror: false });
  });

  it("omits hexes whose mask has no handled shape (defensive fallback to vector)", () => {
    // A solid road blob (radius-2 disc, 42 chords) is past the pruning cap and
    // keeps its raw masks: the centre's six edges are unmodelled, so
    // orientedRiverTile returns null.
    const hexes = disc(2).map((c) => hex(c.q, c.r, "plains", true));
    const refs = refsFor(hexes);
    expect(refs.has("0,0")).toBe(false);
  });

  it("omits river and bridge hexes (their own oriented art carries the crossing)", () => {
    const hexes = [hex(0, 0, "bridge", true), ...neighbours([2, 5], "plains", true)];
    const refs = refsFor(hexes);
    expect(refs.has("0,0")).toBe(false);
  });

  it("omits hexes that are not road-flagged", () => {
    const hexes = [hex(0, 0, "plains", false), ...neighbours([2, 5], "plains", true)];
    const refs = refsFor(hexes);
    expect(refs.has("0,0")).toBe(false);
  });
});

/** Every coord within `radius` of the origin. */
function disc(radius: number): Axial[] {
  const out: Axial[] = [];
  for (let q = -radius; q <= radius; q++) {
    for (let r = Math.max(-radius, -q - radius); r <= Math.min(radius, -q + radius); r++) out.push({ q, r });
  }
  return out;
}

/** A map: plains everywhere in `area`, road on the coords in `roads`. */
function roadMap(area: readonly Axial[], roads: readonly Axial[]): Hex[] {
  const onRoad = new Set(roads.map((c) => hexKey(c.q, c.r)));
  return area.map((c) => hex(c.q, c.r, "plains", onRoad.has(hexKey(c.q, c.r))));
}

/** Coords from `at` stepping `steps` times in direction `dir` (excluding `at`). */
function ray(at: Axial, dir: number, steps: number): Axial[] {
  const d = AXIAL_DIRECTIONS[dir];
  return Array.from({ length: steps }, (_, i) => ({ q: at.q + d.q * (i + 1), r: at.r + d.r * (i + 1) }));
}

/**
 * Triangles {a, a+d, a+d+1} whose three edges all survive in `course`, counted
 * once per corner (so three times each); 0 means none.
 */
function courseTriangles(course: Map<string, number[]>): number {
  let count = 0;
  for (const [key, edges] of course) {
    const [q, r] = key.split(",").map(Number);
    for (let d = 0; d < 6; d++) {
      const e = (d + 1) % 6;
      if (!edges.includes(d) || !edges.includes(e)) continue;
      const b = AXIAL_DIRECTIONS[d];
      // From b = a + d_d, the third hex a + d_e is in direction d + 2.
      if (course.get(hexKey(q + b.q, r + b.r))!.includes((d + 2) % 6)) count++;
    }
  }
  return count;
}

/** Connected components of an edge-mask graph, as a canonical string. */
function components(edges: Map<string, readonly number[]>): string {
  const seen = new Set<string>();
  const groups: string[] = [];
  for (const start of edges.keys()) {
    if (seen.has(start)) continue;
    const members: string[] = [];
    const stack = [start];
    seen.add(start);
    while (stack.length > 0) {
      const k = stack.pop()!;
      members.push(k);
      const [q, r] = k.split(",").map(Number);
      for (const dir of edges.get(k)!) {
        const n = hexKey(q + AXIAL_DIRECTIONS[dir].q, r + AXIAL_DIRECTIONS[dir].r);
        if (!seen.has(n)) {
          seen.add(n);
          stack.push(n);
        }
      }
    }
    groups.push(members.sort().join(" "));
  }
  return groups.sort().join(" | ");
}

describe("roadCourseEdges (junction pruning for roads)", () => {
  it("prunes a T-junction onto a straight road to one fork, no triangle", () => {
    // A N/S road through the origin; a branch leaves east from (1,0), which also
    // touches (0,1): the raw masks make a triangle loop at the join.
    const roads = [ORIGIN, ...ray(ORIGIN, 2, 3), ...ray(ORIGIN, 5, 3), ...ray(ORIGIN, 0, 3)];
    const hexes = roadMap(disc(5), roads);
    const course = roadCourseEdges(hexes, hexProbe(hexes));
    expect(courseTriangles(course)).toBe(0);
    expect(course.get("0,0")).toEqual([0, 2, 5]); // the one fork
    expect(course.get("0,1")).toEqual([2, 5]); // the through road stays straight
    expect(course.get("1,0")).toEqual([0, 3]); // the branch runs straight in
    expect(refsFor(hexes).get("0,0")).toEqual({ lookup: "road/plains_fork_se_n_s", mirror: false });
  });

  it("keeps a genuine three-way meeting connected to all three arms", () => {
    // Three roads arriving at the three hexes of one triangle.
    const a = ORIGIN;
    const b = { q: 1, r: 0 };
    const c = { q: 0, r: 1 };
    const roads = [a, b, c, ...ray(a, 2, 3), ...ray(b, 0, 3), ...ray(c, 4, 3)];
    const hexes = roadMap(disc(5), roads);
    const course = roadCourseEdges(hexes, hexProbe(hexes));
    expect(courseTriangles(course)).toBe(0);
    expect(components(course).split(" | ")).toHaveLength(1);
    // Every arm still reaches the meeting.
    expect(course.get("0,0")).toContain(2);
    expect(course.get("1,0")).toContain(0);
    expect(course.get("0,1")).toContain(4);
  });

  it("leaves a Y meeting without triangles untouched", () => {
    const roads = [ORIGIN, ...ray(ORIGIN, 0, 3), ...ray(ORIGIN, 2, 3), ...ray(ORIGIN, 4, 3)];
    const hexes = roadMap(disc(5), roads);
    expect(roadCourseEdges(hexes, hexProbe(hexes)).get("0,0")).toEqual([0, 2, 4]);
  });

  it("never changes which road hexes are connected (random small road sets)", () => {
    // mulberry32: a fixed-seed PRNG, so the cases are the same every run.
    let seed = 0x5eed;
    const random = (): number => {
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    const area = disc(4);
    for (let trial = 0; trial < 60; trial++) {
      const density = 0.25 + random() * 0.35;
      const hexes = area.map((c) => hex(c.q, c.r, random() < 0.1 ? "bridge" : "plains", random() < density));
      const probe = hexProbe(hexes);
      const raw = new Map<string, number[]>();
      for (const h of hexes) {
        if (h.road || h.terrain === "bridge") raw.set(hexKey(h.coord.q, h.coord.r), roadConnectedEdges(h.coord, probe));
      }
      const course = roadCourseEdges(hexes, probe);
      expect([...course.keys()].sort()).toEqual([...raw.keys()].sort());
      expect(components(course)).toBe(components(raw));
      // Pruning only removes edges, symmetrically.
      for (const [key, edges] of course) {
        expect(raw.get(key)).toEqual(expect.arrayContaining(edges));
        const [q, r] = key.split(",").map(Number);
        for (const dir of edges) {
          const n = hexKey(q + AXIAL_DIRECTIONS[dir].q, r + AXIAL_DIRECTIONS[dir].r);
          expect(course.get(n)).toContain((dir + 3) % 6);
        }
      }
    }
  });

  it("prunes every junction of a world-sized road network quickly", () => {
    // 350×350 plains (the world's size); N/S roads every 12 columns, each with an
    // eastward branch every 12 rows: ~840 T-junctions, ~11k road hexes.
    const width = 350;
    const height = 350;
    const roads: Axial[] = [];
    for (let col = 2; col < width - 8; col += 12) {
      for (let row = 0; row < height; row++) roads.push({ q: col, r: row - (col - (col & 1)) / 2 });
      for (let row = 6; row < height - 6; row += 12) {
        const at = { q: col, r: row - (col - (col & 1)) / 2 };
        roads.push(...ray(at, 0, 6));
      }
    }
    const area: Axial[] = [];
    for (let row = 0; row < height; row++) {
      for (let col = 0; col < width; col++) area.push({ q: col, r: row - (col - (col & 1)) / 2 });
    }
    const hexes = roadMap(area, roads);
    const byKey = new Map(hexes.map((h) => [hexKey(h.coord.q, h.coord.r), h]));
    const start = performance.now();
    const course = roadCourseEdges(hexes, (q, r) => byKey.get(hexKey(q, r)));
    const elapsed = performance.now() - start;
    const raw = new Map(roads.map((c) => [hexKey(c.q, c.r), roadConnectedEdges(c, (q, r) => byKey.get(hexKey(q, r)))]));
    expect(courseTriangles(raw)).toBeGreaterThan(800 * 3); // each triangle counts from its 3 corners
    expect(courseTriangles(course)).toBe(0);
    expect(elapsed).toBeLessThan(3000);
  });
});
