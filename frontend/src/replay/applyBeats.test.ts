import { describe, expect, it } from "vitest";

import { applyBeatEvents, applyBeats } from "./applyBeats";
import type { Beat } from "./beats";
import {
  coord,
  events,
  type EventFixture,
  gameState,
  player,
  settlement,
  unit,
} from "./testFixtures";

// Build a Beat from a phase + raw events; only `events` matters to applyBeats.
function beat(fixtures: EventFixture[]): Beat {
  return { phase: "pulse", label: "PULSE 1", events: events(fixtures) };
}

describe("applyBeats", () => {
  it("returns the pre-turn state unchanged for upToIndex < 0", () => {
    const pre = gameState({ units: [unit(1, "p1", coord(0, 0))] });
    const beats = [beat([{ kind: "unit_moved", unit_id: 1, dest: { q: 5, r: 5 } }])];
    const result = applyBeats(pre, beats, -1);
    expect(result).toBe(pre);
    expect(result.units?.[0].coord).toEqual(coord(0, 0));
  });

  it("applies events cumulatively up to and including the target beat", () => {
    const pre = gameState({ units: [unit(1, "p1", coord(0, 0))] });
    const beats = [
      beat([{ kind: "unit_moved", unit_id: 1, dest: { q: 1, r: 0 } }]),
      beat([{ kind: "unit_moved", unit_id: 1, dest: { q: 2, r: 0 } }]),
      beat([{ kind: "unit_moved", unit_id: 1, dest: { q: 3, r: 0 } }]),
    ];
    expect(applyBeats(pre, beats, 0).units?.[0].coord).toEqual(coord(1, 0));
    expect(applyBeats(pre, beats, 1).units?.[0].coord).toEqual(coord(2, 0));
    expect(applyBeats(pre, beats, 2).units?.[0].coord).toEqual(coord(3, 0));
  });

  it("clamps an out-of-range upToIndex to the last beat", () => {
    const pre = gameState({ units: [unit(1, "p1", coord(0, 0))] });
    const beats = [beat([{ kind: "unit_moved", unit_id: 1, dest: { q: 1, r: 0 } }])];
    expect(applyBeats(pre, beats, 99).units?.[0].coord).toEqual(coord(1, 0));
  });

  it("does not mutate the pre-turn state", () => {
    const pre = gameState({ units: [unit(1, "p1", coord(0, 0))] });
    applyBeats(pre, [beat([{ kind: "unit_moved", unit_id: 1, dest: { q: 9, r: 9 } }])], 0);
    expect(pre.units?.[0].coord).toEqual(coord(0, 0));
  });
});

describe("applyBeatEvents — movement", () => {
  it("moves a unit to its destination", () => {
    const state = gameState({ units: [unit(1, "p1", coord(0, 0))] });
    const next = applyBeatEvents(state, events([{ kind: "unit_moved", unit_id: 1, dest: { q: 2, r: 1 } }]));
    expect(next.units?.[0].coord).toEqual(coord(2, 1));
  });

  it("resolves an edge-swap (two units trading hexes in one beat)", () => {
    // p1 at A→B, p2 at B→A within the same pulse; both land on the other's hex.
    const a = coord(0, 0);
    const b = coord(1, 0);
    const state = gameState({ units: [unit(1, "p1", a), unit(2, "p2", b)] });
    const next = applyBeatEvents(state, events([
      { kind: "unit_moved", unit_id: 1, dest: b },
      { kind: "unit_moved", unit_id: 2, dest: a },
    ]));
    const u1 = next.units?.find((u) => u.id === 1);
    const u2 = next.units?.find((u) => u.id === 2);
    expect(u1?.coord).toEqual(b);
    expect(u2?.coord).toEqual(a);
  });

  it("leaves positions unchanged for move_blocked / traffic_jam", () => {
    const state = gameState({ units: [unit(1, "p1", coord(0, 0))] });
    const next = applyBeatEvents(state, events([
      { kind: "move_blocked", unit_id: 1 },
      { kind: "traffic_jam", unit_id: 1 },
    ]));
    // No change → reducer returns the same state reference.
    expect(next).toBe(state);
  });
});

describe("applyBeatEvents — facing (U0 mirror)", () => {
  it("faces a unit rightward / leftward from a horizontal step", () => {
    const state = gameState({
      units: [unit(1, "p1", coord(0, 0), { facing: "left" }), unit(2, "p1", coord(5, 0), { facing: "right" })],
    });
    const next = applyBeatEvents(state, events([
      { kind: "unit_moved", unit_id: 1, dest: { q: 1, r: 0 } },
      { kind: "unit_moved", unit_id: 2, dest: { q: 4, r: 0 } },
    ]));
    expect(next.units?.find((u) => u.id === 1)?.facing).toBe("right");
    expect(next.units?.find((u) => u.id === 2)?.facing).toBe("left");
  });

  it("preserves facing on a purely vertical step (same q)", () => {
    const state = gameState({ units: [unit(1, "p1", coord(2, 2), { facing: "left" })] });
    const next = applyBeatEvents(state, events([
      { kind: "unit_moved", unit_id: 1, dest: { q: 2, r: 3 } },
    ]));
    expect(next.units?.[0].facing).toBe("left");
  });

  it("sets the combat entrant's facing from its entering step", () => {
    const contested = coord(2, 0);
    const state = gameState({
      units: [unit(1, "p1", coord(1, 0), { facing: "left" }), unit(2, "p2", contested)],
    });
    const next = applyBeatEvents(state, events([
      {
        kind: "combat_resolved",
        coord: contested,
        participants: [
          { unit_id: 1, hp_after: 8, destroyed: false },
          { unit_id: 2, hp_after: 0, destroyed: true },
        ],
        entrant_id: 1,
      },
    ]));
    expect(next.units?.find((u) => u.id === 1)?.facing).toBe("right");
  });

  it("gives a recruit the engine default facing (nearest enemy)", () => {
    // Enemy sits to the recruit's left → the recruit faces left.
    const state = gameState({ units: [unit(1, "p2", coord(0, 0))] });
    const next = applyBeatEvents(state, events([
      { kind: "unit_recruited", unit_id: 2, unit_type: "infantry", owner: "p1", coord: { q: 3, r: 0 } },
    ]));
    expect(next.units?.find((u) => u.id === 2)?.facing).toBe("left");
  });
});

describe("applyBeatEvents — barrage", () => {
  it("applies damage from a non-destroying hit", () => {
    const state = gameState({ units: [unit(1, "p2", coord(0, 0), { hp: 10 })] });
    const next = applyBeatEvents(state, events([
      { kind: "barrage_resolved", hit: { unit_id: 1, hp_after: 4, destroyed: false } },
    ]));
    expect(next.units?.[0].hp).toBe(4);
  });

  it("removes a unit destroyed by barrage", () => {
    const state = gameState({ units: [unit(1, "p2", coord(0, 0))] });
    const next = applyBeatEvents(state, events([
      { kind: "barrage_resolved", hit: { unit_id: 1, hp_after: 0, destroyed: true } },
    ]));
    expect(next.units).toHaveLength(0);
  });

  it("is a no-op when the barrage missed (hit === null)", () => {
    const state = gameState({ units: [unit(1, "p2", coord(0, 0))] });
    const next = applyBeatEvents(state, events([{ kind: "barrage_resolved", hit: null }]));
    expect(next).toBe(state);
  });
});

describe("applyBeatEvents — combat", () => {
  it("damages survivors, removes the destroyed, and moves the entrant onto the hex", () => {
    const contested = coord(1, 1);
    const attackerStart = coord(0, 1);
    const state = gameState({
      units: [
        unit(1, "p1", attackerStart, { hp: 10 }), // entrant/attacker
        unit(2, "p2", contested, { hp: 10 }), // defender, destroyed
        unit(3, "p2", coord(1, 0), { hp: 10 }), // a damaged survivor elsewhere
      ],
    });
    const next = applyBeatEvents(state, events([
      {
        kind: "combat_resolved",
        coord: contested,
        participants: [
          { unit_id: 1, hp_after: 6, destroyed: false },
          { unit_id: 2, hp_after: 0, destroyed: true },
          { unit_id: 3, hp_after: 3, destroyed: false },
        ],
        entrant_id: 1,
      },
    ]));
    expect(next.units?.find((u) => u.id === 2)).toBeUndefined();
    expect(next.units?.find((u) => u.id === 1)?.hp).toBe(6);
    expect(next.units?.find((u) => u.id === 1)?.coord).toEqual(contested);
    expect(next.units?.find((u) => u.id === 3)?.hp).toBe(3);
  });

  it("does not move any unit when the entrant did not survive (entrant_id null)", () => {
    const contested = coord(1, 1);
    const start = coord(0, 1);
    const state = gameState({
      units: [unit(1, "p1", start, { hp: 10 }), unit(2, "p2", contested, { hp: 10 })],
    });
    const next = applyBeatEvents(state, events([
      {
        kind: "combat_resolved",
        coord: contested,
        participants: [
          { unit_id: 1, hp_after: 0, destroyed: true },
          { unit_id: 2, hp_after: 2, destroyed: false },
        ],
        entrant_id: null,
      },
    ]));
    // Attacker destroyed; defender stays put on the contested hex.
    expect(next.units?.find((u) => u.id === 1)).toBeUndefined();
    expect(next.units?.find((u) => u.id === 2)?.coord).toEqual(contested);
  });
});

describe("applyBeatEvents — capture / repair / economy", () => {
  it("reassigns settlement control on capture", () => {
    const c = coord(2, 2);
    const state = gameState({ settlements: [settlement(c, "p2")] });
    const next = applyBeatEvents(state, events([
      { kind: "settlement_captured", coord: c, captured_by: "p1" },
    ]));
    expect(next.settlements?.[0].controlled_by).toBe("p1");
  });

  it("restores HP on repair", () => {
    const state = gameState({ units: [unit(1, "p1", coord(0, 0), { hp: 4 })] });
    const next = applyBeatEvents(state, events([{ kind: "unit_repaired", unit_id: 1, hp_after: 10 }]));
    expect(next.units?.[0].hp).toBe(10);
  });

  it("updates a player's bank on economy resolution", () => {
    const state = gameState({ players: [player("p1", 2), player("p2", 7)] });
    const next = applyBeatEvents(state, events([
      { kind: "economy_resolved", player: "p1", bank_after: 9 },
    ]));
    expect(next.players?.find((p) => p.id === "p1")?.bank).toBe(9);
    expect(next.players?.find((p) => p.id === "p2")?.bank).toBe(7);
  });
});

describe("applyBeatEvents — recruitment", () => {
  it("adds a new unit at full HP derived from UNIT_STATS", () => {
    const state = gameState({ units: [] });
    const next = applyBeatEvents(state, events([
      { kind: "unit_recruited", unit_id: 42, unit_type: "armor", owner: "p1", coord: { q: 3, r: 0 } },
    ]));
    expect(next.units).toHaveLength(1);
    const recruit = next.units?.[0];
    expect(recruit?.id).toBe(42);
    expect(recruit?.type).toBe("armor");
    expect(recruit?.owner).toBe("p1");
    expect(recruit?.coord).toEqual(coord(3, 0));
    expect(recruit?.hp).toBe(10); // armor maxHp
  });

  it("reconciles destruction then recruitment across beats", () => {
    // Unit 1 dies in combat in beat 0; a fresh unit 2 is recruited in beat 1.
    const pre = gameState({ units: [unit(1, "p1", coord(0, 0))] });
    const beats: Beat[] = [
      {
        phase: "pulse",
        label: "PULSE 1",
        events: events([
          {
            kind: "combat_resolved",
            coord: { q: 0, r: 0 },
            participants: [{ unit_id: 1, hp_after: 0, destroyed: true }],
            entrant_id: null,
          },
        ]),
      },
      {
        phase: "recruitment",
        label: "RECRUITMENT",
        events: events([
          { kind: "unit_recruited", unit_id: 2, unit_type: "infantry", owner: "p1", coord: { q: 1, r: 1 } },
        ]),
      },
    ];
    const afterCombat = applyBeats(pre, beats, 0);
    expect(afterCombat.units).toHaveLength(0);
    const afterRecruit = applyBeats(pre, beats, 1);
    expect(afterRecruit.units?.map((u) => u.id)).toEqual([2]);
  });
});
