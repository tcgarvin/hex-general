import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import { AXIAL_DIRECTIONS, hexKey, type Axial } from "./hex";
import { connectedEdges, orientedRiverTile, riverCourseEdges } from "./riverTiles";

type Terrain = components["schemas"]["Terrain"];

/** Build a `terrainAt` probe from an explicit map of "q,r" → terrain. */
function terrainProbe(cells: Record<string, Terrain>) {
  return (q: number, r: number): Terrain | undefined => cells[`${q},${r}`];
}

/** Place `terrain` on the neighbours of (0,0) in the given AXIAL_DIRECTIONS slots. */
function neighbours(dirs: number[], terrain: Terrain): Record<string, Terrain> {
  const cells: Record<string, Terrain> = {};
  for (const i of dirs) {
    const d = AXIAL_DIRECTIONS[i];
    cells[`${d.q},${d.r}`] = terrain;
  }
  return cells;
}

const ORIGIN: Axial = { q: 0, r: 0 };

describe("connectedEdges", () => {
  it("returns the edges whose neighbour is a river", () => {
    const probe = terrainProbe(neighbours([2, 5], "river"));
    expect(connectedEdges(ORIGIN, probe)).toEqual([2, 5]);
  });

  it("counts bridge neighbours as connected (the river runs under the bridge)", () => {
    const probe = terrainProbe(neighbours([0, 3], "bridge"));
    expect(connectedEdges(ORIGIN, probe)).toEqual([0, 3]);
  });

  it("ignores non-river neighbours and off-map (undefined) directions", () => {
    const probe = terrainProbe({ ...neighbours([2], "river"), ...neighbours([5], "plains") });
    expect(connectedEdges(ORIGIN, probe)).toEqual([2]);
  });

  it("returns edges in ascending AXIAL_DIRECTIONS order", () => {
    const probe = terrainProbe(neighbours([5, 2, 0], "river"));
    expect(connectedEdges(ORIGIN, probe)).toEqual([0, 2, 5]);
  });
});

describe("orientedRiverTile", () => {
  it("maps each one-edge end mask to its generated tile", () => {
    expect(orientedRiverTile([0])).toEqual({ name: "end_se", mirror: false });
    expect(orientedRiverTile([4])).toEqual({ name: "end_se", mirror: true });
    expect(orientedRiverTile([1])).toEqual({ name: "end_ne", mirror: false });
    expect(orientedRiverTile([3])).toEqual({ name: "end_ne", mirror: true });
    expect(orientedRiverTile([2])).toEqual({ name: "end_n", mirror: false });
    expect(orientedRiverTile([5])).toEqual({ name: "end_s", mirror: false });
  });

  it("maps the vertical axis to straight_v, unmirrored (self-symmetric)", () => {
    expect(orientedRiverTile([2, 5])).toEqual({ name: "straight_v", mirror: false });
  });

  it("maps the 30°/210° diagonal axis to straight_d, unmirrored", () => {
    expect(orientedRiverTile([0, 3])).toEqual({ name: "straight_d", mirror: false });
  });

  it("maps the 150°/330° diagonal axis to straight_d, mirrored", () => {
    expect(orientedRiverTile([1, 4])).toEqual({ name: "straight_d", mirror: true });
  });

  it("maps each gentle turn (edges 120° apart) to its generated tile", () => {
    expect(orientedRiverTile([0, 2])).toEqual({ name: "turn_n_se", mirror: false });
    expect(orientedRiverTile([3, 5])).toEqual({ name: "turn_s_nw", mirror: false });
    expect(orientedRiverTile([0, 4])).toEqual({ name: "turn_se_sw", mirror: false });
    expect(orientedRiverTile([1, 3])).toEqual({ name: "turn_ne_nw", mirror: false });
  });

  it("maps the two mirror-covered turn orientations with mirror set", () => {
    expect(orientedRiverTile([2, 4])).toEqual({ name: "turn_n_se", mirror: true });
    expect(orientedRiverTile([1, 5])).toEqual({ name: "turn_s_nw", mirror: true });
  });

  it("covers every two-edge mask except the sharp (adjacent-edge) turns", () => {
    for (let a = 0; a < 6; a++) {
      for (let b = a + 1; b < 6; b++) {
        const sharp = b - a === 1 || (a === 0 && b === 5);
        expect(orientedRiverTile([a, b]) === null).toBe(sharp);
      }
    }
  });

  it("maps the SE/SW/S junction fork to its generated tile (mirror-symmetric)", () => {
    expect(orientedRiverTile([0, 4, 5])).toEqual({ name: "fork_se_sw_s", mirror: false });
  });

  it("returns null for unhandled masks (sharp turns, 4+-edge blobs, isolated) → generic fallback", () => {
    // Sharp turns are disallowed by design ruling (user, 2026-07-03): map
    // authoring must never create adjacent-edge connections, so this is a
    // defensive fallback, not "art pending".
    expect(orientedRiverTile([0, 1])).toBeNull(); // adjacent edges → sharp turn
    expect(orientedRiverTile([1, 2])).toBeNull(); // adjacent edges → sharp turn
    expect(orientedRiverTile([0, 1, 2, 3])).toBeNull(); // 4-edge mask, not modelled
    expect(orientedRiverTile([])).toBeNull(); // isolated
  });

  // Ratified fork inventory (name, edges, mirror coverage): see
  // /home/timg/.claude/jobs/37d5bab1/tmp/fork-inventory.md. All twenty
  // three-edge masks (C(6,3)) resolve to a tile: twelve base masks (11
  // generated here + the shipped `fork_se_sw_s`) plus eight L↔R mirrors under
  // the render-time edge permutation M: 0↔4, 1↔3, 2→2, 5→5.
  const MIRROR: Record<number, number> = { 0: 4, 1: 3, 2: 2, 3: 1, 4: 0, 5: 5 };

  /** Every ascending 3-of-6 edge combination, e.g. "0,1,2" .. "3,4,5". */
  function allTripleMasks(): number[][] {
    const masks: number[][] = [];
    for (let a = 0; a < 6; a++) {
      for (let b = a + 1; b < 6; b++) {
        for (let c = b + 1; c < 6; c++) {
          masks.push([a, b, c]);
        }
      }
    }
    return masks;
  }

  it("resolves all 20 three-edge masks (C(6,3)) to a non-null oriented tile", () => {
    const masks = allTripleMasks();
    expect(masks).toHaveLength(20);
    for (const mask of masks) {
      expect(orientedRiverTile(mask)).not.toBeNull();
    }
  });

  it("is mirror-consistent: M(mask) resolves to the same tile with mirror flipped, except self-symmetric masks (mirror: false on both sides)", () => {
    for (const mask of allTripleMasks()) {
      const mirroredMask = mask.map((e) => MIRROR[e]).sort((x, y) => x - y);
      const self = orientedRiverTile(mask)!;
      const mirrored = orientedRiverTile(mirroredMask)!;
      expect(mirrored.name).toBe(self.name);
      if (mirroredMask.join(",") === mask.join(",")) {
        // Fixed point of M: mirror-symmetric, must never set the mirror flag.
        expect(self.mirror).toBe(false);
        expect(mirrored.mirror).toBe(false);
      } else {
        expect(mirrored.mirror).toBe(!self.mirror);
      }
    }
  });

  it.each([
    ["0,2,5", { name: "fork_se_n_s", mirror: false }],
    ["2,4,5", { name: "fork_se_n_s", mirror: true }],
    ["1,2,5", { name: "fork_ne_n_s", mirror: false }],
    ["2,3,5", { name: "fork_ne_n_s", mirror: true }],
    ["0,1,3", { name: "fork_se_ne_nw", mirror: false }],
    ["1,3,4", { name: "fork_se_ne_nw", mirror: true }],
    ["0,2,3", { name: "fork_se_n_nw", mirror: false }],
    ["1,2,4", { name: "fork_se_n_nw", mirror: true }],
    ["0,3,4", { name: "fork_se_nw_sw", mirror: false }],
    ["0,1,4", { name: "fork_se_nw_sw", mirror: true }],
    ["0,3,5", { name: "fork_se_nw_s", mirror: false }],
    ["1,4,5", { name: "fork_se_nw_s", mirror: true }],
    ["0,2,4", { name: "fork_se_n_sw", mirror: false }],
    ["1,3,5", { name: "fork_ne_nw_s", mirror: false }],
    ["1,2,3", { name: "fork_ne_n_nw", mirror: false }],
    ["0,1,2", { name: "fork_se_ne_n", mirror: false }],
    ["2,3,4", { name: "fork_se_ne_n", mirror: true }],
    ["0,1,5", { name: "fork_se_ne_s", mirror: false }],
    ["3,4,5", { name: "fork_se_ne_s", mirror: true }],
    ["0,4,5", { name: "fork_se_sw_s", mirror: false }],
  ])("spot-check: mask %s → %o", (maskKey, expected) => {
    const mask = maskKey.split(",").map(Number);
    expect(orientedRiverTile(mask)).toEqual(expected);
  });
});

describe("riverCourseEdges", () => {
  /** Probe + coord list over an explicit "q,r" → terrain map. */
  function world(cells: Record<string, Terrain>) {
    const coords = Object.keys(cells).map((k) => {
      const [q, r] = k.split(",").map(Number);
      return { q, r };
    });
    return { coords, probe: terrainProbe(cells) };
  }

  it("leaves a triangle-free course untouched (matches raw connectedEdges)", () => {
    // A vertical three-hex run: end / straight_v / end.
    const { coords, probe } = world({
      "0,-1": "river",
      "0,0": "river",
      "0,1": "river",
    });
    const course = riverCourseEdges(coords, probe);
    expect(course.get(hexKey(0, 0))).toEqual([2, 5]);
    expect(course.get(hexKey(0, -1))).toEqual([5]);
    expect(course.get(hexKey(0, 1))).toEqual([2]);
  });

  it("excludes non-river hexes from the result", () => {
    const { coords, probe } = world({ "0,0": "river", "1,0": "plains" });
    const course = riverCourseEdges(coords, probe);
    expect(course.has(hexKey(0, 0))).toBe(true);
    expect(course.has(hexKey(1, 0))).toBe(false);
  });

  it("resolves a tributary junction into one fork + calm course shapes", () => {
    // The twin_rivers junction: a horizontal (zigzagging) main course meeting a
    // vertical tributary. Raw adjacency makes J, E, W *and* T mutual forks; the
    // course graph keeps only J's.
    //
    //   … W2  W   J   E   E2 …      (main course, zigzag row)
    //              T               (tributary head, directly S of J)
    //              T2              (tributary continues S)
    const J = { q: 0, r: 0 };
    const E = { q: 1, r: 0 }; // J's SE neighbour
    const W = { q: -1, r: 1 }; // J's SW neighbour
    const T = { q: 0, r: 1 }; // J's S neighbour — adjacent to E and W too
    const { coords, probe } = world({
      "0,0": "river",
      "1,0": "river",
      "-1,1": "river",
      "0,1": "river",
      "2,-1": "river", // E2: E's NE neighbour, main course continuing east
      "-2,1": "river", // W2: W's NW neighbour, main course continuing west
      "0,2": "river", // T2: tributary continuing south
    });
    const course = riverCourseEdges(coords, probe);
    // J keeps all three edges: the one true fork.
    expect(course.get(hexKey(J.q, J.r))).toEqual([0, 4, 5]);
    expect(orientedRiverTile(course.get(hexKey(J.q, J.r))!)).toEqual({
      name: "fork_se_sw_s",
      mirror: false,
    });
    // E and W lose their artifact edge to T → gentle turns (existing art).
    expect(course.get(hexKey(E.q, E.r))).toEqual([1, 3]);
    expect(course.get(hexKey(W.q, W.r))).toEqual([1, 3]);
    // T loses both artifact edges → plain straight_v.
    expect(course.get(hexKey(T.q, T.r))).toEqual([2, 5]);
  });

  it("never disconnects a river when pruning", () => {
    // A bare triangle of three mutually-adjacent river hexes: any single chord
    // drop keeps it connected, dropping two would strand a hex. Whatever the
    // penalty optimum, every hex must keep at least one edge.
    const { coords, probe } = world({
      "0,0": "river",
      "1,0": "river",
      "0,1": "river",
    });
    const course = riverCourseEdges(coords, probe);
    for (const edges of course.values()) {
      expect(edges.length).toBeGreaterThan(0);
    }
  });
});
