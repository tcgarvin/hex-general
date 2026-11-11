import { describe, expect, it } from "vitest";

import { axialDistance, hexKey } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { contactSources, contactZone, runsIntoContact } from "./contact";
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
