import { describe, expect, it } from "vitest";

import { coord, events, unit } from "@/replay/testFixtures";

import { turnReport, turnTrails, unitBook } from "./turnReport";

const ME = "s1";

function participant(id: number, owner: string, destroyed: boolean, type = "infantry") {
  return {
    unit_id: id,
    owner,
    unit_type: type,
    role: owner === ME ? "attacker" : "defender",
    damage_dealt: 3,
    damage_taken: 3,
    destroyed,
    hp_after: destroyed ? 0 : 7,
    terrain_bonus: 0,
    hold_bonus: 0,
  };
}

const log = events([
  { kind: "unit_moved", pulse: 2, unit_id: 1, origin: coord(1, 0), dest: coord(2, 0) },
  { kind: "unit_moved", pulse: 1, unit_id: 1, origin: coord(0, 0), dest: coord(1, 0) },
  { kind: "unit_moved", pulse: 1, unit_id: 9, origin: coord(5, 5), dest: coord(5, 6) },
  {
    kind: "combat_resolved",
    pulse: 3,
    coord: coord(3, 0),
    participants: [participant(1, ME, false), participant(50, "neutral", true)],
    entrant_id: 1,
    entrant_continued: true,
  },
  {
    kind: "combat_resolved",
    pulse: 3,
    coord: coord(8, 8),
    participants: [participant(9, "s2", false), participant(51, "neutral", false)],
  },
  {
    kind: "settlement_captured",
    coord: coord(3, 0),
    settlement: "town",
    captured_by: ME,
    previous_owner: null,
    unit_id: 1,
  },
  { kind: "unit_repaired", unit_id: 2, hp_before: 6, hp_after: 8, in_settlement: true },
  {
    kind: "stocks_resolved",
    player: ME,
    income: { manpower: 5, fuel: 3, steel: 1 },
    upkeep: { manpower: 4, fuel: 0, steel: 0 },
    before: { manpower: 10, fuel: 10, steel: 10 },
    after: { manpower: 11, fuel: 13, steel: 11 },
    lost_to_storage: { manpower: 0, fuel: 0, steel: 0 },
  },
  {
    kind: "unit_disbanded",
    unit_id: 3,
    unit_type: "armor",
    owner: ME,
    coord: coord(0, 0),
    shortage: "fuel",
    chosen: true,
  },
  {
    kind: "travel_ended",
    unit_id: 2,
    owner: ME,
    coord: coord(4, 4),
    destination: coord(4, 4),
    reason: "arrived",
  },
]);

const units = [unit(1, ME, coord(3, 0)), unit(2, ME, coord(4, 4)), unit(9, "s2", coord(5, 6))];

describe("turnReport", () => {
  const book = unitBook(log, units, ME);
  const items = turnReport(log, ME, book);

  it("puts the viewer's fights first and tones them by who was lost", () => {
    expect(items[0]).toMatchObject({ section: "fighting", tone: "good", at: coord(3, 0) });
    expect(items[0].text).toContain("Your infantry");
    expect(items[1]).toMatchObject({ section: "fighting", tone: "good" }); // the capture
  });

  it("reports the base: stocks, disbands, repairs, as the viewer", () => {
    const base = items.filter((i) => i.section === "base");
    expect(base.map((i) => i.tone)).toEqual(["neutral", "bad", "good"]);
    expect(base[0].text).toMatch(/^You: earned/);
    expect(base[1].text).toContain("Your armor");
    expect(base[2].text).toBe("1 of your units repaired.");
  });

  it("folds other players' fights into 'seen'", () => {
    const seen = items.filter((i) => i.section === "seen");
    expect(seen).toHaveLength(1);
    expect(seen[0].at).toEqual(coord(8, 8));
    expect(items[items.length - 1].section).toBe("seen");
  });

  it("reports the viewer's arrivals under movement", () => {
    expect(items.filter((i) => i.section === "movement")).toEqual([
      expect.objectContaining({ tone: "neutral", at: coord(4, 4) }),
    ]);
  });
});

describe("turnTrails", () => {
  it("chains each unit's steps in pulse order and marks fights and captures", () => {
    const trails = turnTrails(log, unitBook(log, units, ME));
    expect(trails.moves).toEqual([
      { unitId: 1, owner: ME, path: [coord(0, 0), coord(1, 0), coord(2, 0)] },
      { unitId: 9, owner: "s2", path: [coord(5, 5), coord(5, 6)] },
    ]);
    expect(trails.fights).toEqual([coord(3, 0), coord(8, 8)]);
    expect(trails.captures).toEqual([{ coord: coord(3, 0), owner: ME }]);
  });
});
