import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import { AXIAL_DIRECTIONS, type Axial } from "./hex";
import { connectedEdges, orientedRiverTile } from "./riverTiles";

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

  it("returns null for unhandled masks (ends, sharp turns, forks) → generic fallback", () => {
    expect(orientedRiverTile([2])).toBeNull(); // end (1 edge)
    expect(orientedRiverTile([0, 1])).toBeNull(); // adjacent edges → sharp turn
    expect(orientedRiverTile([2, 3, 5])).toBeNull(); // fork (3 edges)
    expect(orientedRiverTile([])).toBeNull(); // isolated
  });
});
