import { describe, expect, it } from "vitest";

import { coord, gameState, hex, settlement } from "@/replay/testFixtures";

import { unitCapFor } from "./economy";

describe("unitCapFor", () => {
  it("uses the flat unit cap when the game has no command cap", () => {
    const s = gameState({});
    expect(unitCapFor(s, "blue")).toBe(s.config.unit_cap);
  });

  it("grows the command cap with holdings, up to its ceiling", () => {
    const city = coord(0, 0);
    const town = coord(1, 0);
    const s = gameState({
      hexes: [hex(city, "city"), hex(town, "town")],
      settlements: [settlement(city, "blue"), settlement(town, "blue")],
    });
    s.config.unit_cap = 15;
    s.config.command_cap = { base: 4, per_town: 1, per_city: 2, ceiling: 15 };
    expect(unitCapFor(s, "blue")).toBe(7);
    expect(unitCapFor(s, "red")).toBe(4);
    s.config.command_cap = { base: 40, per_town: 1, per_city: 2, ceiling: 15 };
    expect(unitCapFor(s, "blue")).toBe(15);
  });
});
