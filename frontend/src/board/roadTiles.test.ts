import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import { AXIAL_DIRECTIONS, type Axial } from "./hex";
import { roadConnectedEdges, roadTileRefs } from "./roadTiles";

type Hex = components["schemas"]["Hex"];
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
    const refs = roadTileRefs(hexes, hexProbe(hexes));
    expect(refs.get("0,0")).toEqual({ lookup: "road/plains_straight_v", mirror: false });
  });

  it("resolves a mirrored gentle turn with the river mirror convention", () => {
    // {2,4} is turn_n_se mirrored (see ORIENTED_PAIRS in riverTiles.ts).
    const hexes = [hex(0, 0, "hills", true), ...neighbours([2, 4], "plains", true)];
    const refs = roadTileRefs(hexes, hexProbe(hexes));
    expect(refs.get("0,0")).toEqual({ lookup: "road/hills_turn_n_se", mirror: true });
  });

  it("keys the lookup by the hex's own terrain (settlements resolve at render time)", () => {
    const hexes = [hex(0, 0, "town", true), ...neighbours([0], "plains", true)];
    const refs = roadTileRefs(hexes, hexProbe(hexes));
    expect(refs.get("0,0")).toEqual({ lookup: "road/town_end_se", mirror: false });
  });

  it("omits hexes whose mask has no handled shape (defensive fallback to vector)", () => {
    // Four connected edges are unmodelled — orientedRiverTile returns null.
    const hexes = [hex(0, 0, "plains", true), ...neighbours([0, 1, 3, 4], "plains", true)];
    const refs = roadTileRefs(hexes, hexProbe(hexes));
    expect(refs.has("0,0")).toBe(false);
  });

  it("omits river and bridge hexes (their own oriented art carries the crossing)", () => {
    const hexes = [hex(0, 0, "bridge", true), ...neighbours([2, 5], "plains", true)];
    const refs = roadTileRefs(hexes, hexProbe(hexes));
    expect(refs.has("0,0")).toBe(false);
  });

  it("omits hexes that are not road-flagged", () => {
    const hexes = [hex(0, 0, "plains", false), ...neighbours([2, 5], "plains", true)];
    const refs = roadTileRefs(hexes, hexProbe(hexes));
    expect(refs.has("0,0")).toBe(false);
  });
});
