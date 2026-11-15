import { describe, expect, it } from "vitest";

import { startingHp } from "./militia";
import { decodeWorldMap, offsetToAxial } from "./worldMap";

// A tier-1 farm (one infantry at 5 HP) and a tier-3 oil field (infantry and a gun).
const map = decodeWorldMap({
  width: 7,
  height: 3,
  terrain: ".......".repeat(3),
  roads: ".......".repeat(3),
  sites: [
    { coord: offsetToAxial(1, 1), kind: "farm", garrison: 1, level: 1 },
    { coord: offsetToAxial(5, 1), kind: "oil_field", garrison: 3, level: 1 },
  ],
});
const FARM = offsetToAxial(1, 1);
const OIL = offsetToAxial(5, 1);

describe("militia starting HP", () => {
  it("measures a militia unit against its site's tier and a player's unit against full HP", () => {
    expect(startingHp({ owner: "neutral", type: "infantry", post: FARM }, map)).toBe(5);
    expect(startingHp({ owner: "neutral", type: "artillery", post: OIL }, map)).toBe(10);
    expect(startingHp({ owner: "s1", type: "infantry", post: null }, map)).toBe(10);
    // A playback unit whose post is unknown counts at full HP.
    expect(startingHp({ owner: "neutral", type: "infantry", post: null }, map)).toBe(10);
  });
});
