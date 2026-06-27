import { describe, expect, it } from "vitest";

import { beatDisplayLabel, groupBeats, type AnyEvent } from "./beats";

// Helper: groupBeats takes the raw `{[key: string]: unknown}[]` event log shape.
function group(events: AnyEvent[]) {
  return groupBeats(events as { [key: string]: unknown }[]);
}

describe("groupBeats", () => {
  it("returns no beats for an empty event log", () => {
    expect(group([])).toEqual([]);
  });

  it("omits phases with no events", () => {
    // Only an economy event — no barrage, pulses, capture, repair, recruitment.
    const beats = group([{ kind: "economy_resolved", player: "p1", bank_after: 5 }]);
    expect(beats).toHaveLength(1);
    expect(beats[0].phase).toBe("economy");
    expect(beats[0].label).toBe("ECONOMY");
  });

  it("orders beats by phase: barrage → pulses → capture → repair → economy → recruitment", () => {
    // Deliberately shuffled input order; output must follow phase order.
    const beats = group([
      { kind: "unit_recruited", unit_id: 9, unit_type: "infantry", owner: "p1", coord: { q: 0, r: 0 } },
      { kind: "economy_resolved", player: "p1", bank_after: 5 },
      { kind: "unit_moved", unit_id: 1, pulse: 1, dest: { q: 1, r: 0 } },
      { kind: "settlement_captured", coord: { q: 2, r: 2 }, captured_by: "p1" },
      { kind: "barrage_resolved", hit: null },
      { kind: "unit_repaired", unit_id: 1, hp_after: 10 },
    ]);
    expect(beats.map((b) => b.phase)).toEqual([
      "barrage",
      "pulse",
      "capture",
      "repair",
      "economy",
      "recruitment",
    ]);
  });

  it("groups movement events by pulse number and emits pulses in ascending order", () => {
    // Out-of-order pulse numbers; multiple events share a pulse.
    const beats = group([
      { kind: "unit_moved", unit_id: 1, pulse: 2, dest: { q: 2, r: 0 } },
      { kind: "unit_moved", unit_id: 2, pulse: 1, dest: { q: 0, r: 1 } },
      { kind: "combat_resolved", unit_id: 3, pulse: 1, coord: { q: 1, r: 1 }, participants: [], entrant_id: null },
      { kind: "move_blocked", unit_id: 4, pulse: 3 },
    ]);
    const pulseBeats = beats.filter((b) => b.phase === "pulse");
    expect(pulseBeats.map((b) => b.pulse)).toEqual([1, 2, 3]);
    expect(pulseBeats.map((b) => b.label)).toEqual(["PULSE 1", "PULSE 2", "PULSE 3"]);
    // Pulse 1 gathered both the move and the combat event.
    expect(pulseBeats[0].events).toHaveLength(2);
  });

  it("ignores unknown event kinds entirely", () => {
    const beats = group([{ kind: "something_unmodeled", foo: "bar" }]);
    expect(beats).toEqual([]);
  });
});

describe("beatDisplayLabel", () => {
  const beats = group([
    { kind: "barrage_resolved", hit: null },
    { kind: "unit_moved", unit_id: 1, pulse: 1, dest: { q: 1, r: 0 } },
    { kind: "unit_moved", unit_id: 1, pulse: 2, dest: { q: 2, r: 0 } },
  ]);

  it("labels the pre-turn boundary", () => {
    expect(beatDisplayLabel(beats, -1)).toBe("PRE-TURN");
  });

  it("annotates pulse beats with 'of N' total pulses", () => {
    // beats[1] is PULSE 1, beats[2] is PULSE 2 → two pulses total.
    expect(beatDisplayLabel(beats, 1)).toBe("PULSE 1 of 2");
    expect(beatDisplayLabel(beats, 2)).toBe("PULSE 2 of 2");
  });

  it("uses the plain label for non-pulse beats", () => {
    expect(beatDisplayLabel(beats, 0)).toBe("BARRAGE");
  });

  it("returns a placeholder for an out-of-range index", () => {
    expect(beatDisplayLabel(beats, 99)).toBe("—");
  });
});
