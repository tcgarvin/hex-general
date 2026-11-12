import { describe, expect, it } from "vitest";

import { axialDistance, hexKey } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import {
  contactSources,
  contactZone,
  defaultPlanMode,
  fogText,
  haltHex,
  haltText,
  routeFog,
  runsIntoContact,
  stopShort,
} from "./contact";
import { decodeWorldMap, offsetToAxial } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];

describe("contactZone", () => {
  it("covers exactly the hexes within the radius", () => {
    const zone = contactZone([{ q: 0, r: 0 }], 3);
    expect(zone.size).toBe(37);
    for (const key of zone) {
      const [q, r] = key.split(",").map(Number);
      expect(axialDistance({ q, r }, { q: 0, r: 0 })).toBeLessThanOrEqual(3);
    }
  });

  it("flags a route that passes near a source, origin included", () => {
    const zone = contactZone([{ q: 10, r: 0 }], 3);
    expect(runsIntoContact({ q: 0, r: 0 }, [{ q: 1, r: 0 }, { q: 2, r: 0 }], zone)).toBe(false);
    expect(runsIntoContact({ q: 0, r: 0 }, [{ q: 6, r: 0 }, { q: 7, r: 0 }], zone)).toBe(true);
    expect(runsIntoContact({ q: 8, r: 0 }, [], zone)).toBe(true);
  });
});

describe("contactSources", () => {
  const map = decodeWorldMap({
    width: 4,
    height: 1,
    terrain: "....",
    roads: "....",
    sites: [
      { coord: offsetToAxial(0, 0), kind: "farm", garrison: 1, level: 1 },
      { coord: offsetToAxial(1, 0), kind: "farm", garrison: 2, level: 1 },
      { coord: offsetToAxial(2, 0), kind: "base", garrison: 0, level: 1 },
    ],
  });

  it("counts visible hostiles and still-neutral garrisoned sites, not your own units or taken sites", () => {
    const state = {
      units: [
        { id: 1, owner: "me", type: "infantry", coord: offsetToAxial(3, 0), hp: 10, posture: "deployed", facing: "right", disordered: false },
        { id: 2, owner: "rival", type: "armor", coord: offsetToAxial(3, 0), hp: 10, posture: "column", facing: "right", disordered: false },
      ],
      settlements: [
        { coord: offsetToAxial(0, 0), controlled_by: null },
        { coord: offsetToAxial(1, 0), controlled_by: "rival" },
      ],
    } as unknown as WorldState;
    const keys = contactSources(state, map, "me").map((a) => hexKey(a.q, a.r));
    expect(keys.sort()).toEqual([hexKey(0, 0), hexKey(3, offsetToAxial(3, 0).r)].sort());
  });

  it("counts the hexes beside a site whose garrison has several units", () => {
    const wide = decodeWorldMap({
      width: 9,
      height: 9,
      terrain: ".".repeat(81),
      roads: ".".repeat(81),
      sites: [{ coord: { q: 4, r: 2 }, kind: "steel_mill", garrison: 3, level: 1 }],
    });
    const state = { units: [], settlements: [{ coord: { q: 4, r: 2 }, controlled_by: null }] } as unknown as WorldState;
    const zone = contactZone(contactSources(state, wide, "me"));
    // Four from the site, three from the gun that may stand beside it.
    expect(zone.has(hexKey(4, 6))).toBe(true);
    expect(zone.has(hexKey(4, 7))).toBe(false);
  });
});

const line = (...qs: number[]) => qs.map((q) => ({ q, r: 0 }));

describe("haltHex", () => {
  const zone = contactZone([{ q: 10, r: 0 }], 3);

  it("stops on the last route hex before the zone", () => {
    expect(haltHex({ q: 0, r: 0 }, line(1, 2, 3, 4, 5, 6, 7, 8), zone)).toEqual({ q: 6, r: 0 });
  });

  it("stops where it stands when the first step is into the zone, or it is already inside", () => {
    expect(haltHex({ q: 6, r: 0 }, line(7, 8), zone)).toEqual({ q: 6, r: 0 });
    expect(haltHex({ q: 8, r: 0 }, line(9), zone)).toEqual({ q: 8, r: 0 });
  });

  it("is null for a route clear of the zone", () => {
    expect(haltHex({ q: 0, r: 0 }, line(1, 2, 3), zone)).toBeNull();
  });

  it("reads as prose", () => {
    expect(haltText({ q: 6, r: 0 }, { q: 0, r: 0 })).toBe("halts on contact at (6,0), disordered");
    expect(haltText({ q: 6, r: 0 }, { q: 6, r: 0 })).toBe("halts on contact where it stands, disordered");
  });
});

describe("defaultPlanMode", () => {
  const zone = contactZone([{ q: 10, r: 0 }], 3);

  it("is Move for a deployed unit inside the zone, Travel otherwise", () => {
    expect(defaultPlanMode({ coord: { q: 8, r: 0 }, posture: "deployed" }, zone)).toBe("move");
    expect(defaultPlanMode({ coord: { q: 8, r: 0 }, posture: "column" }, zone)).toBe("travel");
    expect(defaultPlanMode({ coord: { q: 0, r: 0 }, posture: "deployed" }, zone)).toBe("travel");
  });
});

describe("stopShort", () => {
  const path = line(1, 2, 3, 4, 5, 6, 7, 8);

  it("ends the travel at the halt, deployed, keeping only the waypoints before it", () => {
    const order = { destination: { q: 8, r: 0 }, via: line(2, 5, 7) };
    expect(stopShort(order, path, { q: 6, r: 0 })).toEqual({
      kind: "travel",
      destination: { q: 6, r: 0 },
      via: line(2, 5),
      onArrival: "deployed",
    });
  });

  it("drops a waypoint on the halt hex itself", () => {
    expect(stopShort({ destination: { q: 8, r: 0 }, via: line(6) }, path, { q: 6, r: 0 }).via).toEqual([]);
  });

  it("refuses a halt that is not on the route", () => {
    expect(() => stopShort({ destination: { q: 8, r: 0 }, via: [] }, path, { q: 0, r: 5 })).toThrow();
  });
});

describe("routeFog", () => {
  const seen = (...qs: number[]) => new Set(qs.map((q) => hexKey(q, 0)));

  it("finds nothing unscouted on a route in view", () => {
    const fog = routeFog(line(1, 2), seen(0, 1, 2));
    expect(fog).toEqual({ unscouted: [false, false], count: 0, trailing: false });
    expect(fogText(fog)).toBeNull();
  });

  it("counts the unscouted tail", () => {
    const fog = routeFog(line(1, 2, 3, 4), seen(1, 2));
    expect(fog).toEqual({ unscouted: [false, false, true, true], count: 2, trailing: true });
    expect(fogText(fog)).toBe("Last 2 hexes unscouted: a column that meets something hidden there halts, disordered.");
    expect(fogText(routeFog(line(1, 2), seen(1)))).toMatch(/^Last hex unscouted: /);
  });

  it("words unscouted hexes in the middle and a route wholly out of sight", () => {
    const gap = routeFog(line(1, 2, 3, 4), seen(1, 4));
    expect(gap.trailing).toBe(false);
    expect(fogText(gap)).toMatch(/^2 hexes of the route are unscouted: /);
    expect(fogText(routeFog(line(1, 2, 3), seen()))).toMatch(/^All 3 hexes of the route are unscouted: /);
    expect(fogText(routeFog(line(1), seen()))).toMatch(/^The destination is unscouted: /);
  });
});
