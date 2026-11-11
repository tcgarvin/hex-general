import { describe, expect, it } from "vitest";

import type { Axial } from "@/board/hex";

import { columnCosts, findRoute } from "./routing";
import { addWaypoint, MAX_WAYPOINTS, removeWaypoint } from "./waypoints";
import { decodeWorldMap, offsetToAxial } from "./worldMap";

// Five rows of open plain with a river down column 3: columns 4-5 are cut off.
const map = decodeWorldMap({ width: 6, height: 5, terrain: "...~..".repeat(5), roads: "......".repeat(5), sites: [] });
const costs = columnCosts(map);
const route = (f: Axial, t: Axial, v: readonly Axial[]) => findRoute(costs, f, t, v);
const at = (col: number, row: number) => offsetToAxial(col, row);
const from = at(0, 0);
const travel = { destination: at(2, 4), via: [] };

describe("adding a waypoint", () => {
  it("adds it last and routes through it", () => {
    const added = addWaypoint(route, map, from, travel, at(0, 4));
    if ("error" in added) throw new Error(added.error);
    expect(added.via).toEqual([at(0, 4)]);
    expect(added.route).toContainEqual(at(0, 4));
    expect(added.route.at(-1)).toEqual(travel.destination);
    const second = addWaypoint(route, map, from, { ...travel, via: added.via }, at(2, 2));
    if ("error" in second) throw new Error(second.error);
    expect(second.via).toEqual([at(0, 4), at(2, 2)]);
  });

  it("refuses a waypoint it can't route through, with the reason", () => {
    expect(addWaypoint(route, map, from, travel, at(5, 2))).toEqual({
      error: `No route through (${at(5, 2).q},${at(5, 2).r}): it can't be reached by land`,
    });
    expect(addWaypoint(route, map, from, travel, at(3, 2))).toEqual({
      error: `A waypoint must be on land: (${at(3, 2).q},${at(3, 2).r}) is river`,
    });
    expect(addWaypoint(route, map, from, travel, from)).toEqual({ error: "The unit already stands there" });
    expect(addWaypoint(route, map, from, travel, travel.destination)).toEqual({ error: "That is the destination" });
    expect(addWaypoint(route, map, from, { ...travel, via: [at(1, 1)] }, at(1, 1))).toEqual({
      error: `(${at(1, 1).q},${at(1, 1).r}) is already a waypoint`,
    });
  });

  it("stops at the server's limit", () => {
    const full = { ...travel, via: [at(0, 1), at(0, 2), at(0, 3), at(1, 3)] };
    expect(full.via).toHaveLength(MAX_WAYPOINTS);
    expect(addWaypoint(route, map, from, full, at(1, 1))).toEqual({ error: "At most 4 waypoints on one Travel order" });
  });

  it("removes one by its place in the list", () => {
    expect(removeWaypoint([at(0, 1), at(0, 2), at(0, 3)], 1)).toEqual([at(0, 1), at(0, 3)]);
  });
});
