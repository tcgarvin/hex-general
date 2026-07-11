import { describe, expect, it } from "vitest";

import { axialToPixel, HEX_SIZE } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { beatAnimDurationMs, buildAnimTimeline, sampleTimeline } from "./animFx";
import type { Beat } from "./beats";
import { coord, events, unit, type EventFixture } from "./testFixtures";

type Unit = components["schemas"]["Unit"];

const px = (q: number, r: number) => axialToPixel(q, r, HEX_SIZE);

function beat(phase: Beat["phase"], fixtures: EventFixture[]): Beat {
  return { phase, label: phase.toUpperCase(), events: events(fixtures) };
}

function byId(units: Unit[]): Map<number, Unit> {
  return new Map(units.map((u) => [u.id, u]));
}

const NO_UNITS = new Map<number, Unit>();

describe("beatAnimDurationMs", () => {
  it("gives a plain move pulse the base duration and halves it at 2×", () => {
    const b = beat("pulse", [
      { kind: "unit_moved", unit_id: 1, origin: coord(0, 0), dest: coord(1, 0), pulse: 1 },
    ]);
    expect(beatAnimDurationMs(b, 1)).toBe(500);
    expect(beatAnimDurationMs(b, 2)).toBe(250);
  });

  it("extends pulses containing combat beyond blocked-only and plain pulses", () => {
    const combat = beat("pulse", [
      { kind: "combat_resolved", coord: coord(1, 0), participants: [], pulse: 1 },
    ]);
    const blocked = beat("pulse", [
      {
        kind: "move_blocked",
        unit_id: 1,
        origin: coord(0, 0),
        target: coord(1, 0),
        reason: "zoc_locked",
        pulse: 1,
      },
    ]);
    const plain = beat("pulse", [
      { kind: "unit_moved", unit_id: 1, origin: coord(0, 0), dest: coord(1, 0), pulse: 1 },
    ]);
    expect(beatAnimDurationMs(combat, 1)).toBeGreaterThan(beatAnimDurationMs(blocked, 1));
    expect(beatAnimDurationMs(blocked, 1)).toBeGreaterThan(beatAnimDurationMs(plain, 1));
  });

  it("returns 0 (snap) for economy beats and for no beat", () => {
    const b = beat("economy", [{ kind: "economy_resolved", player: "blue", bank_after: 5 }]);
    expect(beatAnimDurationMs(b, 1)).toBe(0);
    expect(beatAnimDurationMs(undefined, 1)).toBe(0);
  });
});

describe("buildAnimTimeline — pulse", () => {
  it("returns null when there is nothing to animate", () => {
    const b = beat("economy", [{ kind: "economy_resolved", player: "blue", bank_after: 5 }]);
    expect(buildAnimTimeline(b, NO_UNITS, NO_UNITS)).toBeNull();
  });

  it("slides a moved unit across the full window in a combat-free pulse", () => {
    const pre = byId([unit(1, "blue", coord(0, 0))]);
    const b = beat("pulse", [
      { kind: "unit_moved", unit_id: 1, origin: coord(0, 0), dest: coord(1, 0), pulse: 1 },
    ]);
    const tl = buildAnimTimeline(b, pre, pre)!;
    expect(tl.slides).toHaveLength(1);
    expect(tl.slides[0].win).toEqual({ t0: 0, t1: 1 });

    const mid = sampleTimeline(tl, 0.5).slideOverrides.get(1)!;
    const from = px(0, 0);
    const to = px(1, 0);
    expect(mid.x).toBeGreaterThan(from.x);
    expect(mid.x).toBeLessThan(to.x);
    expect(mid.facing).toBe("right");
    // Holds the destination at the end of the animation.
    const end = sampleTimeline(tl, 1).slideOverrides.get(1)!;
    expect(end.x).toBeCloseTo(to.x);
  });

  it("bounces a blocked unit toward the refused hex and back, with a ✕ mark", () => {
    const pre = byId([unit(1, "blue", coord(0, 0))]);
    const b = beat("pulse", [
      {
        kind: "move_blocked",
        unit_id: 1,
        origin: coord(0, 0),
        target: coord(1, 0),
        reason: "zoc_locked",
        pulse: 1,
      },
    ]);
    const tl = buildAnimTimeline(b, pre, pre)!;
    expect(tl.lunges).toHaveLength(1);
    expect(tl.marks).toHaveLength(1);

    const from = px(0, 0);
    // Mid-bounce the unit has left its hex, moving toward the target…
    const midBump = sampleTimeline(tl, 0.28).slideOverrides.get(1)!;
    expect(midBump.x).toBeGreaterThan(from.x);
    // …and it never reaches the refused hex (bump is a fraction of the step).
    expect(midBump.x).toBeLessThan(px(1, 0).x * 0.5);
    // After the bounce window there is no override (back at rest).
    expect(sampleTimeline(tl, 0.9).slideOverrides.has(1)).toBe(false);
    // The ✕ sits on the shared edge, visible mid-window.
    const marks = sampleTimeline(tl, 0.5).fx.marks;
    expect(marks).toHaveLength(1);
    expect(marks[0].x).toBeCloseTo((from.x + px(1, 0).x) / 2);
    expect(marks[0].alpha).toBe(1);
  });

  it("nudges jammed units toward the congested hex with jam-coloured marks", () => {
    const pre = byId([unit(1, "blue", coord(0, 0)), unit(2, "blue", coord(2, 0))]);
    const b = beat("pulse", [
      { kind: "traffic_jam", coord: coord(1, 0), unit_ids: [1, 2], pulse: 1 },
    ]);
    const tl = buildAnimTimeline(b, pre, pre)!;
    expect(tl.lunges).toHaveLength(2);
    expect(tl.marks).toHaveLength(2);
    expect(tl.marks[0].color).toBe(0xffa726); // jam amber, not blocked red
    // Jam nudge is shallower than a blocked bounce.
    const from = px(0, 0);
    const apex = Math.abs(tl.lunges[0].toPx.x - from.x);
    expect(apex).toBeLessThan(Math.abs(px(1, 0).x - from.x) * 0.25);
  });

  it("animates a combat: moves first, attacker lunge, damage floaters, death fade, entrant occupation", () => {
    const pre = byId([
      unit(1, "blue", coord(0, 0)), // attacker, will win and occupy
      unit(2, "red", coord(1, 0), { hp: 3 }), // defender, destroyed
    ]);
    const b = beat("pulse", [
      {
        kind: "combat_resolved",
        coord: coord(1, 0),
        pulse: 1,
        entrant_id: 1,
        entrant_continued: false,
        participants: [
          {
            unit_id: 1,
            owner: "blue",
            unit_type: "infantry",
            role: "attacker",
            damage_dealt: 3,
            damage_taken: 2,
            hp_after: 8,
            destroyed: false,
            terrain_bonus: 0,
            hold_bonus: 0,
          },
          {
            unit_id: 2,
            owner: "red",
            unit_type: "infantry",
            role: "defender",
            damage_dealt: 2,
            damage_taken: 3,
            hp_after: 0,
            destroyed: true,
            terrain_bonus: 0,
            hold_bonus: 0,
          },
        ],
      },
    ]);
    const tl = buildAnimTimeline(b, pre, pre)!;

    // Attacker lunges toward the contested hex partway through the beat.
    const atLunge = sampleTimeline(tl, 0.54).slideOverrides.get(1)!;
    expect(atLunge.x).toBeGreaterThan(px(0, 0).x);
    expect(atLunge.x).toBeLessThan(px(1, 0).x);

    // Both struck units emit damage floaters after contact.
    const floats = sampleTimeline(tl, 0.7).fx.floaters;
    expect(floats.map((f) => f.text).sort()).toEqual(["-2", "-3"]);
    expect(floats.every((f) => f.kind === "damage")).toBe(true);

    // The defender fades out after contact, never before.
    expect(sampleTimeline(tl, 0.3).fadingOut.has(2)).toBe(false);
    const fade = sampleTimeline(tl, 0.8).fadingOut.get(2)!;
    expect(fade).toBeGreaterThan(0);
    expect(fade).toBeLessThan(1);

    // The entrant ends the beat on the contested hex.
    const end = sampleTimeline(tl, 1).slideOverrides.get(1)!;
    expect(end.x).toBeCloseTo(px(1, 0).x);
    expect(end.y).toBeCloseTo(px(1, 0).y);
  });

  it("skips motion and floaters for combat participants missing from the pre-beat state (fog)", () => {
    const pre = byId([unit(2, "red", coord(1, 0))]); // attacker 1 is fogged
    const b = beat("pulse", [
      {
        kind: "combat_resolved",
        coord: coord(1, 0),
        pulse: 1,
        entrant_id: 1,
        entrant_continued: false,
        participants: [
          {
            unit_id: 1,
            owner: "blue",
            unit_type: "infantry",
            role: "attacker",
            damage_dealt: 1,
            damage_taken: 1,
            hp_after: 9,
            destroyed: false,
            terrain_bonus: 0,
            hold_bonus: 0,
          },
        ],
      },
    ]);
    const tl = buildAnimTimeline(b, pre, pre);
    expect(tl).toBeNull();
  });
});

describe("buildAnimTimeline — barrage", () => {
  const shot: EventFixture = {
    kind: "barrage_resolved",
    artillery_id: 9,
    target: coord(2, 0),
    hit: {
      unit_id: 5,
      owner: "red",
      unit_type: "infantry",
      damage: 2,
      hp_after: 1,
      destroyed: false,
      terrain_bonus: 0,
      hold_bonus: 0,
    },
  };

  it("arcs a shell from the gun to the target, then bursts and floats the damage", () => {
    const pre = byId([unit(9, "blue", coord(0, 0), { type: "artillery" }), unit(5, "red", coord(2, 0))]);
    const tl = buildAnimTimeline(beat("barrage", [shot]), pre, pre)!;
    expect(tl.shells).toHaveLength(1);
    expect(tl.bursts).toHaveLength(1);
    expect(tl.floaters).toHaveLength(1);

    // Shell mid-flight: between gun and target, lifted above the straight line.
    const inFlight = sampleTimeline(tl, 0.2).fx;
    expect(inFlight.shells).toHaveLength(1);
    expect(inFlight.bursts).toHaveLength(0);
    const s = inFlight.shells[0];
    expect(s.x).toBeGreaterThan(px(0, 0).x);
    expect(s.x).toBeLessThan(px(2, 0).x);
    expect(s.y).toBeLessThan(px(0, 0).y); // arc lift (screen-up is -y)

    // Impact: shell gone, burst expanding at the target.
    const impact = sampleTimeline(tl, 0.5).fx;
    expect(impact.shells).toHaveLength(0);
    expect(impact.bursts).toHaveLength(1);
    expect(impact.bursts[0].x).toBeCloseTo(px(2, 0).x);

    // Damage floater rises over the victim afterwards.
    const after = sampleTimeline(tl, 0.7).fx;
    expect(after.floaters).toHaveLength(1);
    expect(after.floaters[0].text).toBe("-2");
    expect(after.floaters[0].y).toBeLessThan(px(2, 0).y);
  });

  it("omits the shell when the firing artillery is fogged, keeping the burst", () => {
    const pre = byId([unit(5, "red", coord(2, 0))]);
    const tl = buildAnimTimeline(beat("barrage", [shot]), pre, pre)!;
    expect(tl.shells).toHaveLength(0);
    expect(tl.bursts).toHaveLength(1);
  });

  it("staggers floaters when two shots strike the same hex", () => {
    const pre = byId([
      unit(8, "blue", coord(0, 0), { type: "artillery" }),
      unit(9, "blue", coord(0, 1), { type: "artillery" }),
      unit(5, "red", coord(2, 0)),
    ]);
    const second = { ...shot, artillery_id: 8 };
    const tl = buildAnimTimeline(beat("barrage", [shot, second]), pre, pre)!;
    expect(tl.floaters).toHaveLength(2);
    expect(tl.floaters[0].px.x).not.toBeCloseTo(tl.floaters[1].px.x);
  });

  it("bursts on an empty hex with no floater or fade", () => {
    const pre = byId([unit(9, "blue", coord(0, 0), { type: "artillery" })]);
    const miss = { ...shot, hit: null };
    const tl = buildAnimTimeline(beat("barrage", [miss]), pre, pre)!;
    expect(tl.bursts).toHaveLength(1);
    expect(tl.floaters).toHaveLength(0);
    expect(tl.fadeOuts).toHaveLength(0);
  });
});

describe("buildAnimTimeline — capture, repair, recruitment", () => {
  it("pulses a team-colour ring on a captured settlement", () => {
    const b = beat("capture", [
      { kind: "settlement_captured", coord: coord(1, 1), captured_by: "blue" },
    ]);
    const tl = buildAnimTimeline(b, NO_UNITS, NO_UNITS)!;
    expect(tl.rings).toHaveLength(1);
    const frame = sampleTimeline(tl, 0.3).fx;
    expect(frame.rings).toHaveLength(1);
    expect(frame.rings[0].radius).toBeGreaterThan(HEX_SIZE * 0.5);
    expect(frame.rings[0].alpha).toBeGreaterThan(0);
  });

  it("floats +N heal text over a repaired unit", () => {
    const pre = byId([unit(3, "blue", coord(1, 1), { hp: 5 })]);
    const b = beat("repair", [
      { kind: "unit_repaired", unit_id: 3, hp_before: 5, hp_after: 7, in_settlement: true },
    ]);
    const tl = buildAnimTimeline(b, pre, pre)!;
    expect(tl.floaters).toHaveLength(1);
    expect(tl.floaters[0].text).toBe("+2");
    expect(tl.floaters[0].kind).toBe("heal");
  });

  it("fades in recruited units from the post-beat state", () => {
    const recruit = unit(7, "blue", coord(0, 0));
    const post = byId([recruit]);
    const b = beat("recruitment", [
      { kind: "unit_recruited", unit_id: 7, unit_type: "infantry", owner: "blue", coord: coord(0, 0) },
    ]);
    const tl = buildAnimTimeline(b, NO_UNITS, post)!;
    expect(tl.fadeIns).toHaveLength(1);
    const mid = sampleTimeline(tl, 0.5).fadingIn.get(7)!;
    expect(mid.alpha).toBeCloseTo(0.5, 1);
  });
});
