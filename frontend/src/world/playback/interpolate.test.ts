import { describe, expect, it } from "vitest";

import { cameraHint, cameraTrack, frameAt } from "./interpolate";
import { casePlayback, FIXTURE_CASES } from "./testFixtures";
import type { PlaybackUnit } from "./timeline";

const settled = (units: { id: number; coord: { q: number; r: number }; hp: number; posture: string }[]) =>
  units.map((u) => ({ id: u.id, q: u.coord.q, r: u.coord.r, hp: u.hp, posture: u.posture })).sort((a, b) => a.id - b.id);

describe("frameAt endpoints on recorded turns", () => {
  for (const c of FIXTURE_CASES) {
    it(`${c.viewer} turn ${c.turn}: phase 0 draws the beat's start and phase 1 its end`, () => {
      const pb = casePlayback(c);
      pb.beats.forEach((_, k) => {
        for (const [phase, snap] of [
          [0, pb.snapshots[k]],
          [1, pb.snapshots[k + 1]],
        ] as const) {
          const frame = frameAt(pb, k, phase);
          expect(settled(frame.units)).toEqual(settled([...snap.values()]));
          for (const u of frame.units) {
            expect(u.alpha).toBe(1);
            expect(u.at).toEqual(u.coord);
          }
        }
      });
    });
  }

  it("slides a moving unit along the line between its hexes", () => {
    const c = FIXTURE_CASES.find((x) => x.viewer === "b002" && x.turn === 6)!;
    const pb = casePlayback(c);
    const k = pb.beats.findIndex((b) => b.actions.some((a) => a.kind === "move" && a.unitId === 10));
    const mid = frameAt(pb, k, 0.5).units.find((u) => u.id === 10)!;
    expect(mid.moving).toBe(true);
    expect(mid.at.q).toBeGreaterThan(26);
    expect(mid.at.q).toBeLessThan(27);
    expect(mid.at.r).toBe(32);
  });

  it("fades out a unit destroyed in combat and shows the damage", () => {
    const c = FIXTURE_CASES.find((x) => x.viewer === "b002" && x.turn === 6)!;
    const pb = casePlayback(c);
    const k = pb.beats.findIndex((b) => b.actions.some((a) => a.kind === "combat"));
    const dying = (p: number) => frameAt(pb, k, p).units.find((u) => u.id === 61);
    expect(dying(0.3)!.alpha).toBe(1);
    expect(dying(0.7)!.alpha).toBeGreaterThan(0);
    expect(dying(0.7)!.alpha).toBeLessThan(1);
    expect(dying(0.7)!.hp).toBe(0);
    expect(dying(0.9)).toBeUndefined();
    const effects = frameAt(pb, k, 0.5).effects.map((e) => e.kind);
    expect(effects).toContain("combat");
    expect(effects).toContain("damage");
    // The barrage beat flies a shell from the gun to its target.
    const shell = frameAt(pb, 0, 0.25).effects.find((e) => e.kind === "shell");
    expect(shell).toMatchObject({ from: { q: 28, r: 31 }, to: { q: 25, r: 32 } });
  });
});

describe("frameAt with fog actions", () => {
  const u = (id: number, owner: string, q: number): PlaybackUnit => ({
    id,
    owner,
    type: "infantry",
    coord: { q, r: 0 },
    hp: 10,
    posture: "deployed",
    facing: "right",
    disordered: false,
  });
  const pb = {
    turn: 1,
    viewer: "me",
    start: "previous_turn" as const,
    beats: [
      {
        phase: "pulse" as const,
        pulse: 1,
        label: "Pulse 1",
        eventIndices: [],
        lines: [],
        actions: [
          { kind: "reveal" as const, unit: u(2, "them", 5) },
          { kind: "relocate" as const, unit: u(3, "them", 9) },
          { kind: "conceal" as const, unitId: 4 },
        ],
      },
    ],
    snapshots: [
      new Map([
        [3, u(3, "them", 7)],
        [4, u(4, "them", -4)],
      ]),
      new Map([
        [2, u(2, "them", 5)],
        [3, u(3, "them", 9)],
      ]),
    ],
    visible: [],
    control: [],
  };

  it("a unit coming into sight fades in, one lost to the fog fades out", () => {
    const alpha = (id: number, p: number) => frameAt(pb, 0, p).units.find((x) => x.id === id)?.alpha ?? 0;
    expect(alpha(2, 0)).toBe(0);
    expect(alpha(2, 0.1)).toBeCloseTo(0.5);
    expect(alpha(2, 0.5)).toBe(1);
    expect(alpha(4, 0.5)).toBe(1);
    expect(alpha(4, 0.9)).toBeCloseTo(0.5);
    expect(alpha(4, 1)).toBe(0);
  });

  it("an enemy that moved unseen dips out at its old hex and back in at the new", () => {
    const at = (p: number) => frameAt(pb, 0, p).units.find((x) => x.id === 3);
    expect(at(0.2)!.coord).toEqual({ q: 7, r: 0 });
    expect(at(0.4)).toBeUndefined();
    expect(at(0.6)!.coord).toEqual({ q: 9, r: 0 });
    expect(at(0.6)!.at).toEqual({ q: 9, r: 0 });
  });
});

describe("camera hints", () => {
  it("follow the viewer's fight and hold through beats without their action", () => {
    const c = FIXTURE_CASES.find((x) => x.viewer === "b002" && x.turn === 6)!;
    const pb = casePlayback(c);
    const k = pb.beats.findIndex((b) => b.actions.some((a) => a.kind === "combat"));
    expect(cameraHint(pb, k)).toMatchObject({ focus: "fight", hexes: [{ q: 25, r: 32 }] });
    const track = cameraTrack(pb);
    expect(track).toHaveLength(pb.beats.length);
    pb.beats.forEach((_, i) => {
      const own = cameraHint(pb, i);
      if (own !== null) expect(track[i]).toEqual(own);
      else if (i > 0) expect(track[i]).toEqual(track[i - 1]);
    });
  });
});
