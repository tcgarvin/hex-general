import { describe, expect, it } from "vitest";

import fixture from "./__fixtures__/routes.json";
import { columnCosts, findRoute, routePulses, travelEta } from "./routing";
import { decodeWorldMap, type CompactMap } from "./worldMap";

// Fixture: `cd api && uv run python scripts/route_fixture.py` (Python router's answers).
type Point = [number, number];
interface RouteCase {
  origin: Point;
  destination: Point;
  via: Point[];
  route: Point[] | null;
  pulses: number | null;
  etaPacked: number | null;
  etaDeployed: number | null;
}

const map = decodeWorldMap(fixture.map as CompactMap);
const costs = columnCosts(map);
const cases = fixture.cases as RouteCase[];
const axial = ([q, r]: Point) => ({ q, r });

describe("column router parity with Python", () => {
  it("has a meaningful fixture", () => {
    expect(cases.length).toBeGreaterThanOrEqual(40);
    expect(cases.some((c) => c.route === null)).toBe(true);
    expect(cases.some((c) => c.route?.length === 0)).toBe(true);
    expect(cases.some((c) => c.via.length > 0)).toBe(true);
  });

  cases.forEach((c, i) => {
    it(`case ${i}: ${c.origin} -> ${c.destination} via ${c.via.length}`, () => {
      const route = findRoute(costs, axial(c.origin), axial(c.destination), c.via.map(axial));
      expect(route === null ? null : route.map(({ q, r }) => [q, r])).toEqual(c.route);
      if (route === null) return;
      expect(routePulses(costs, route)).toBe(c.pulses);
      expect(travelEta(costs, route, { packed: true })).toBe(c.etaPacked);
      expect(travelEta(costs, route, { packed: false })).toBe(c.etaDeployed);
    });
  });

  it("plans the longest route quickly", () => {
    const longest = cases.reduce((a, b) => ((b.route?.length ?? 0) > (a.route?.length ?? 0) ? b : a));
    const start = performance.now();
    findRoute(costs, axial(longest.origin), axial(longest.destination), longest.via.map(axial));
    expect(performance.now() - start).toBeLessThan(500);
  });
});
