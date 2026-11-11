import { describe, expect, it } from "vitest";

import { coord, events, unit } from "@/replay/testFixtures";

import { contactCause, turnReport, turnTrails, unitBook, type ReportContext } from "./turnReport";

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
    expect(items[1]).toMatchObject({ section: "fighting", tone: "good" }); // the capture
  });

  it("writes a fight as one line with both sides' losses and HP", () => {
    expect(items[0].text).toBe("Attack at (3,0): your infantry −3 → 7 HP vs garrison infantry destroyed.");
    expect(items[0].detail).toEqual(["Your infantry took the hex and moved on."]);
  });

  it("reports the base: income, disbands, repairs, then the stocks", () => {
    const base = items.filter((i) => i.section === "base");
    expect(base.map((i) => i.tone)).toEqual(["neutral", "bad", "good", "neutral"]);
    expect(base[0].text).toBe("Earned 5 manpower, 3 fuel, 1 steel; upkeep 4 manpower.");
    expect(base[1].text).toContain("Your armor");
    expect(base[2].text).toBe("1 of your units repaired.");
    expect(base[3].text).toBe("Stocks now 11 manpower, 13 fuel, 11 steel.");
  });

  it("points each line at the event it came from", () => {
    expect(items[0].eventIndex).toBe(3);
    const base = items.filter((i) => i.section === "base");
    expect(base.map((i) => i.eventIndex)).toEqual([7, 8, 6, 7]);
    for (const item of items) expect(log[item.eventIndex ?? -1]).toBeDefined();
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

describe("turnReport: units out of sight", () => {
  it("calls a gun that fired from out of sight an unseen gun, not by its id", () => {
    const shot = events([
      {
        kind: "barrage_resolved",
        artillery_id: 16,
        target: coord(3, 3),
        hit: {
          unit_id: 50,
          unit_type: "infantry",
          owner: "neutral",
          terrain_bonus: 2,
          hold_bonus: 1,
          damage: 1,
          hp_after: 9,
          destroyed: false,
          defense_penalty: 0,
        },
        extra_hits: [],
      },
    ]);
    const book = unitBook(shot, [], ME);
    expect(book.nameOf(16)).toBe("An unseen gun");
    expect(book.nameOf(77)).toBe("An unseen unit");
    const text = turnReport(shot, ME, book).map((i) => i.text).join(" ");
    expect(text).toContain("An unseen gun barraged Garrison infantry");
    expect(text).not.toContain("16");
  });
});

describe("turnReport: spending and contact", () => {
  const stocks = (manpower: number, fuel: number, steel: number) => ({ manpower, fuel, steel });
  const spendLog = events([
    {
      kind: "stocks_resolved",
      player: ME,
      income: stocks(5, 3, 1),
      upkeep: stocks(4, 3, 0),
      before: stocks(10, 20, 10),
      after: stocks(11, 20, 11),
      lost_to_storage: stocks(0, 0, 0),
    },
    { kind: "construction_started", player: ME, building: "barracks", level: 1, completes_on: 2, cost: stocks(5, 0, 8) },
    { kind: "world_unit_recruited", unit_id: 7, unit_type: "infantry", owner: ME, coord: coord(0, 0), cost: stocks(3, 0, 0) },
  ]);

  it("lists spending before the stocks and ends on what is left after it", () => {
    const book = unitBook(spendLog, [], ME);
    const base = turnReport(spendLog, ME, book).map((i) => i.text);
    expect(base).toEqual([
      "Earned 5 manpower, 3 fuel, 1 steel; upkeep 4 manpower, 3 fuel.",
      "Began Barracks 1 (5 manpower, 8 steel), done turn 2.",
      "Recruited infantry at (0,0) (3 manpower).",
      "Stocks now 3 manpower, 20 fuel, 3 steel.",
    ]);
  });

  const steelMill = { coord: coord(10, 0), kind: "steel_mill" as const, garrison: 2, level: 1 };
  const context: ReportContext = { sites: [steelMill], settlements: [{ coord: coord(10, 0), controlled_by: null }], units: [] };
  const contactLog = events([
    { kind: "unit_deployed", pulse: 6, unit_id: 4, origin: coord(7, 0), dest: coord(6, 0), cause: "contact", disordered: true },
    { kind: "travel_ended", unit_id: 4, owner: ME, coord: coord(6, 0), destination: coord(12, 0), reason: "contact" },
  ]);

  it("folds a contact halt into one line naming the fogged garrison it met", () => {
    const book = unitBook(contactLog, [unit(4, ME, coord(6, 0))], ME);
    const items = turnReport(contactLog, ME, book, context);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ section: "movement", at: coord(6, 0), eventIndex: 0 });
    expect(items[0].text).toBe(
      "Your infantry halted and deployed at (6,0) on contact with the garrison at the steel mill (10,0).",
    );
    expect(items[0].detail).toEqual([
      "No room at (7,0).",
      "Disordered next turn: weaker defence, can't attack.",
      "It was bound for (12,0).",
    ]);
  });

  it("folds a travel that never set off into one line naming the garrison", () => {
    const stuckLog = events([
      { kind: "move_blocked", pulse: 5, unit_id: 4, origin: coord(7, 0), target: coord(8, 0), reason: "pack_interrupted" },
      { kind: "travel_ended", unit_id: 4, owner: ME, coord: coord(7, 0), destination: coord(12, 0), reason: "contact" },
    ]);
    const items = turnReport(stuckLog, ME, unitBook(stuckLog, [unit(4, ME, coord(7, 0))], ME), context);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ eventIndex: 0, at: coord(7, 0), detail: ["It was bound for (12,0)."] });
    expect(items[0].text).toBe(
      "Your infantry could not set off: contact with the garrison at the steel mill (10,0). It stayed deployed at (7,0).",
    );
  });

  it("names a several-unit garrison met one hex further out, by the unit beside its site", () => {
    const spread: ReportContext = {
      sites: [{ coord: coord(10, 0), kind: "steel_mill", garrison: 3, level: 2 }],
      settlements: [{ coord: coord(10, 0), controlled_by: null }],
      units: [],
    };
    expect(contactCause(coord(5, 0), ME, spread)).toBe("the garrison at the steel mill (10,0)");
    expect(contactCause(coord(4, 0), ME, spread)).toBe("the enemy");
  });

  it("says the enemy when a player's unit is in sight, or nothing on the map explains it", () => {
    expect(contactCause(coord(6, 0), ME, { ...context, units: [unit(9, "s2", coord(8, 0))] })).toBe("the enemy");
    expect(contactCause(coord(0, 0), ME, context)).toBe("the enemy"); // the mill is out of reach
    expect(contactCause(coord(6, 0), ME, { ...context, settlements: [{ coord: coord(10, 0), controlled_by: "s2" }] })).toBe(
      "the enemy",
    );
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
