import { describe, expect, it } from "vitest";

import { eventLines, unitName } from "./eventText";
import { events, type EventFixture } from "./testFixtures";

// A stub name lookup: ids 1/2 are own/enemy units, anything else is "a unit".
const nameOf = (id: number): string =>
  id === 1 ? "Blue Armor" : id === 2 ? "Red Infantry" : "a unit";

const linesFor = (fixture: EventFixture): string[] =>
  eventLines(events([fixture])[0], nameOf).map((l) => l.text);

describe("unitName", () => {
  it("capitalizes owner slot and unit type", () => {
    expect(unitName("blue", "armor")).toBe("Blue Armor");
    expect(unitName("red", "artillery")).toBe("Red Artillery");
  });
});

describe("eventLines", () => {
  it("renders a barrage that destroys a defender with its bonuses", () => {
    expect(
      linesFor({
        kind: "barrage_resolved",
        artillery_id: 1,
        target: { q: 5, r: 6 },
        hit: {
          owner: "red",
          unit_type: "infantry",
          damage: 4,
          destroyed: true,
          hp_after: 0,
          terrain_bonus: 2,
          hold_bonus: 0,
        },
      }),
    ).toEqual([
      "Blue Armor barraged Red Infantry at (5,6).",
      "Red Infantry had +2 terrain defense.",
      "Dealt 4 damage.",
      "Red Infantry was destroyed.",
    ]);
  });

  it("renders a barrage into an empty hex", () => {
    expect(
      linesFor({ kind: "barrage_resolved", artillery_id: 1, target: { q: 0, r: 0 }, hit: null }),
    ).toEqual(["Blue Armor barraged (0,0) — no unit there."]);
  });

  it("renders combat with attacker, defender bonus, damage, destruction, and entry", () => {
    expect(
      linesFor({
        kind: "combat_resolved",
        coord: { q: 3, r: 3 },
        pulse: 1,
        entrant_id: 1,
        entrant_continued: true,
        participants: [
          {
            unit_id: 1,
            owner: "blue",
            unit_type: "armor",
            role: "attacker",
            damage_dealt: 3,
            damage_taken: 1,
            destroyed: false,
            hp_after: 9,
            terrain_bonus: 0,
            hold_bonus: 0,
          },
          {
            unit_id: 2,
            owner: "red",
            unit_type: "infantry",
            role: "defender",
            damage_dealt: 1,
            damage_taken: 3,
            destroyed: true,
            hp_after: 0,
            terrain_bonus: 2,
            hold_bonus: 0,
          },
        ],
      }),
    ).toEqual([
      "Blue Armor attacked Red Infantry at (3,3).",
      "Red Infantry had +2 terrain defense.",
      "Blue Armor dealt 3 damage.",
      "Red Infantry dealt 1 damage.",
      "Red Infantry was destroyed.",
      "Blue Armor took the hex and continued moving.",
    ]);
  });

  it("renders the ZOC-lock move_blocked case across three lines", () => {
    expect(
      linesFor({
        kind: "move_blocked",
        unit_id: 1,
        origin: { q: 0, r: 0 },
        target: { q: 1, r: 0 },
        reason: "zoc_locked",
        pulse: 1,
      }),
    ).toEqual([
      "Blue Armor began the pulse in enemy ZOC.",
      "Its move to (1,0) would enter another enemy-controlled hex.",
      "That move is illegal — Blue Armor held position.",
    ]);
  });

  it("renders a non-ZOC block with its reason", () => {
    expect(
      linesFor({
        kind: "move_blocked",
        unit_id: 1,
        origin: { q: 0, r: 0 },
        target: { q: 1, r: 0 },
        reason: "occupied",
        pulse: 1,
      }),
    ).toEqual(["Blue Armor's move to (1,0) was blocked — the hex was already occupied."]);
  });

  it("renders capture, repair, economy, recruitment, and traffic jam", () => {
    expect(
      linesFor({
        kind: "settlement_captured",
        unit_id: 1,
        coord: { q: 2, r: 2 },
        captured_by: "blue",
        previous_owner: "red",
        settlement: "city",
      }),
    ).toEqual(["Blue Armor captured the city at (2,2) from Red."]);

    expect(
      linesFor({ kind: "unit_repaired", unit_id: 1, hp_before: 6, hp_after: 8, in_settlement: true }),
    ).toEqual(["Blue Armor repaired 6→8 HP in a settlement."]);

    expect(
      linesFor({
        kind: "economy_resolved",
        player: "blue",
        income: 5,
        upkeep: 3,
        net: 2,
        lost_to_cap: 1,
        bank_before: 19,
        bank_after: 20,
      }),
    ).toEqual([
      "Blue: +5 income, −3 upkeep (net +2). Bank 19→20.",
      "1 muster lost to the bank cap.",
    ]);

    expect(
      linesFor({
        kind: "unit_recruited",
        unit_id: 9,
        coord: { q: 1, r: 1 },
        cost: 4,
        owner: "blue",
        unit_type: "armor",
        bank_after: 2,
      }),
    ).toEqual(["Blue recruited Armor at (1,1) (−4, bank 2)."]);

    expect(
      linesFor({ kind: "traffic_jam", coord: { q: 4, r: 4 }, unit_ids: [1, 3, 5], pulse: 2 }),
    ).toEqual(["Traffic jam at (4,4): 3 friendly units collided and stopped."]);
  });
});
