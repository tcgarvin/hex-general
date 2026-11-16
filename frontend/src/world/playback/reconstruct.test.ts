import { describe, expect, it } from "vitest";

import { hexKey } from "@/board/hex";

import { unitBook } from "../turnReport";
import type { WorldReplay } from "../useTurnReport";
import { applyBeat, buildPlayback, unitsAtBeat } from "./reconstruct";
import { casePlayback, FIXTURE_CASES, fixtureTerrain } from "./testFixtures";
import type { PlaybackUnit } from "./timeline";
import { visibleHexKeys } from "./visibility";

const view = (u: Pick<PlaybackUnit, "id" | "coord" | "hp" | "posture">) => ({ id: u.id, coord: u.coord, hp: u.hp, posture: u.posture });
const byId = (a: { id: number }, b: { id: number }) => a.id - b.id;

// b003's infantry 14 before the turn-8 shelling (one hit of 1 lands on it).
const HP_BEFORE_SHELLING = 3;

describe("reconstruction on recorded turns", () => {
  for (const c of FIXTURE_CASES) {
    for (const rewind of c.before === null ? [true] : [false, true]) {
      const name = `${c.viewer} turn ${c.turn}${rewind ? " (rewound)" : ""}`;

      it(`${name}: applying every beat to the start picture gives state_after`, () => {
        const pb = casePlayback(c, { rewind });
        let snap: ReadonlyMap<number, PlaybackUnit> = pb.snapshots[0];
        for (const beat of pb.beats) snap = applyBeat(snap, beat);
        const drawn = [...snap.values()].map(view).sort(byId);
        const after = c.replay.state_after.units.map(view).sort(byId);
        expect(drawn).toEqual(after);
        expect(pb.snapshots.at(-1)!.size).toBe(c.replay.state_after.units.length);
      });

      it(`${name}: the viewer's surviving units need no fog fix-ups`, () => {
        const pb = casePlayback(c, { rewind });
        const ownAlive = new Set(c.replay.state_after.units.filter((u) => u.owner === c.viewer).map((u) => u.id));
        const fixes = pb.beats.flatMap((b) =>
          b.actions.filter(
            (a) => (a.kind === "relocate" || a.kind === "correct" || a.kind === "reveal") && ownAlive.has(a.unit.id),
          ),
        );
        expect(fixes).toEqual([]);
      });
    }

    if (c.before !== null) {
      it(`${c.viewer} turn ${c.turn}: the start picture is the previous turn's state_after`, () => {
        const pb = casePlayback(c);
        expect(pb.start).toBe("previous_turn");
        expect([...pb.snapshots[0].keys()].sort((a, b) => a - b)).toEqual(
          c.before!.units.map((u) => u.id).sort((a, b) => a - b),
        );
      });
    }

    it(`${c.viewer} turn ${c.turn}: beats run barrage, pulses in order, then end-of-turn phases`, () => {
      const pb = casePlayback(c);
      const rank = (b: (typeof pb.beats)[number]) =>
        b.phase === "barrage" ? 0 : b.phase === "pulse" ? 1 + b.pulse! / 100 : 10 + ["capture", "repair", "economy", "recruit", "muster", "settle"].indexOf(b.phase);
      const ranks = pb.beats.map(rank);
      expect([...ranks].sort((a, b) => a - b)).toEqual(ranks);
      expect(new Set(ranks).size).toBe(ranks.length);
      for (const b of pb.beats) {
        for (const i of b.eventIndices) {
          const e = c.replay.events[i];
          if ("pulse" in e) expect(b.pulse).toBe(e.pulse);
        }
      }
      const shown = pb.beats.flatMap((b) => b.eventIndices).sort((a, b) => a - b);
      expect(shown).toEqual(c.replay.events.map((_, i) => i));
    });

    it(`${c.viewer} turn ${c.turn}: a unit destroyed in a beat is not drawn after it`, () => {
      const pb = casePlayback(c);
      pb.beats.forEach((b, k) => {
        for (const a of b.actions) {
          const dead =
            a.kind === "combat" ? a.sides.filter((s) => s.destroyed) : a.kind === "barrage" ? a.strikes.filter((s) => s.destroyed) : [];
          for (const s of dead) {
            for (let later = k + 1; later < pb.snapshots.length; later++) expect(pb.snapshots[later].has(s.unitId)).toBe(false);
          }
        }
      });
    });

    it(`${c.viewer} turn ${c.turn}: the visibility mirror matches the engine at both ends`, () => {
      const pb = casePlayback(c);
      const keys = (list: [number, number][]) => new Set(list.map(([q, r]) => hexKey(q, r)));
      expect(pb.visible.at(-1)).toEqual(keys(c.visible_after));
      if (c.before !== null) {
        expect(visibleHexKeys(c.before.units, c.before.settlements, c.viewer, fixtureTerrain)).toEqual(keys(c.visible_before));
        expect(pb.visible[0]).toEqual(keys(c.visible_before));
      }
    });
  }

  it("covers the event kinds playback must animate", () => {
    const kinds = new Set(FIXTURE_CASES.flatMap((c) => c.replay.events.map((e) => e.kind)));
    for (const k of ["unit_moved", "unit_packed", "unit_deployed", "combat_resolved", "barrage_resolved", "settlement_captured", "world_unit_recruited"]) {
      expect(kinds).toContain(k);
    }
  });

  it("covers the shapes the server redacts and other players crossing the fog", () => {
    const served = FIXTURE_CASES.flatMap((c) => c.replay.events.map((e) => ({ viewer: c.viewer, e })));
    expect(served.some(({ e }) => e.kind === "barrage_resolved" && e.artillery_id === null)).toBe(true);
    expect(
      served.some(
        ({ viewer, e }) => e.kind === "world_unit_recruited" && e.owner !== viewer && e.cost === null && e.stocks_after === null,
      ),
    ).toBe(true);
    const isPlayer = (owner: string, viewer: string) => owner !== viewer && owner !== "neutral";
    let revealed = false;
    let concealed = false;
    for (const c of FIXTURE_CASES) {
      if (c.before === null) continue;
      const ownerOf = new Map(c.before.units.map((u) => [u.id, u.owner]));
      for (const a of casePlayback(c).beats.flatMap((b) => b.actions)) {
        if (a.kind === "reveal" && isPlayer(a.unit.owner, c.viewer)) revealed = true;
        if (a.kind === "conceal" && isPlayer(ownerOf.get(a.unitId) ?? c.viewer, c.viewer)) concealed = true;
      }
    }
    expect(revealed).toBe(true);
    expect(concealed).toBe(true);
  });

  it("lands a barrage from a gun the viewer cannot see", () => {
    const c = FIXTURE_CASES.find((x) => x.turn === 8 && x.viewer === "b003")!;
    const pb = casePlayback(c);
    const k = pb.beats.findIndex((b) => b.phase === "barrage" && b.actions.some((a) => a.kind === "barrage" && a.gunId === null));
    const unseen = pb.beats[k].actions.find((a) => a.kind === "barrage" && a.gunId === null)!;
    expect(unseen).toMatchObject({ from: null, target: { q: 10, r: 7 }, strikes: [{ unitId: 14, owner: "b003", hpAfter: 2 }] });
    expect(pb.snapshots[k].get(14)!.hp).toBe(HP_BEFORE_SHELLING);
    expect(pb.snapshots[k + 1].get(14)!.hp).toBe(2);
  });

  it("draws another player's recruit, served without its cost", () => {
    const c = FIXTURE_CASES.find((x) => x.turn === 34 && x.viewer === "b002")!;
    const pb = casePlayback(c);
    const k = pb.beats.findIndex((b) => b.actions.some((a) => a.kind === "recruit" && a.unitId === 90));
    expect(pb.beats[k].phase).toBe("recruit");
    expect(pb.snapshots[k].has(90)).toBe(false);
    expect(pb.snapshots[k + 1].get(90)).toMatchObject({ owner: "b001", type: "infantry", coord: { q: 43, r: -10 }, post: null });
  });

  it("musters a militia unit back at its starting HP in the last beat", () => {
    const c = FIXTURE_CASES.find((x) => x.turn === 8 && x.viewer === "b003")!;
    const pb = casePlayback(c);
    const k = pb.beats.findIndex((b) => b.actions.some((a) => a.kind === "recruit" && a.unitId === 77));
    expect(k).toBe(pb.beats.length - 1);
    expect(pb.beats[k].phase).toBe("muster");
    expect(pb.snapshots[k].has(77)).toBe(false);
    expect(pb.snapshots[k + 1].get(77)).toMatchObject({
      owner: "neutral",
      type: "infantry",
      coord: { q: 15, r: 9 },
      hp: 5,
      post: { q: 15, r: 9 },
    });
  });

  it("heals a militia unit in the repair beat", () => {
    const c = FIXTURE_CASES.find((x) => x.turn === 23 && x.viewer === "b001")!;
    const pb = casePlayback(c);
    const k = pb.beats.findIndex((b) => b.phase === "repair");
    expect(pb.beats[k].actions).toContainEqual(expect.objectContaining({ kind: "repair", unitId: 68, hpBefore: 6, hpAfter: 7 }));
    expect(pb.snapshots[k].get(68)!.hp).toBe(6);
    expect(pb.snapshots[k + 1].get(68)).toMatchObject({ hp: 7, post: { q: 44, r: -6 } });
  });

  it("resolves barrage guns and recruits from the board", () => {
    const c = FIXTURE_CASES.find((x) => x.turn === 9 && x.viewer === "b000")!;
    const pb = casePlayback(c);
    const barrage = pb.beats.flatMap((b) => b.actions).find((a) => a.kind === "barrage")!;
    expect(barrage.kind === "barrage" && barrage.from).toEqual(c.before!.units.find((u) => u.id === 4)!.coord);
    const combat = pb.beats.flatMap((b) => b.actions).find((a) => a.kind === "combat" && a.entrantTo !== null)!;
    expect(combat.kind === "combat" && combat.entrantTo).toEqual({ q: 10, r: 17 });
    expect(pb.control[0].get(hexKey(10, 17))).toBe(null);
    expect(pb.control.at(-1)!.get(hexKey(10, 17))).toBe("b000");
  });

  it("draws every site's owner as of the played turn, not just the ones that change", () => {
    const c = FIXTURE_CASES.find((x) => x.turn === 9 && x.viewer === "b000")!;
    const pb = casePlayback(c);
    const end = new Map(c.replay.state_after.settlements.map((s) => [hexKey(s.coord.q, s.coord.r), s.controlled_by ?? null]));
    // The end boundary is exactly the turn's own end state, whatever the live board holds now.
    expect(new Map(pb.control.at(-1)!)).toEqual(end);
    // The start differs only where a site changed hands during the turn: the
    // viewer's capture.
    const changed = [...end].filter(([key, owner]) => pb.control[0].get(key) !== owner).map(([key]) => key);
    expect(changed).toEqual([hexKey(10, 17)]);
    expect(pb.control[0].size).toBe(end.size);
  });
});

// Small hand-built turns for the fog cases the recorded ones do not pin down.
const unit = (id: number, owner: string, q: number, r: number, extra: Partial<PlaybackUnit> = {}) => ({
  id,
  owner,
  type: "infantry" as const,
  coord: { q, r },
  hp: 10,
  posture: "deployed" as const,
  facing: "right" as const,
  disordered: false,
  ...extra,
});

type Settlements = WorldReplay["state_after"]["settlements"];

function state(units: ReturnType<typeof unit>[], settlements: Settlements = []) {
  return {
    turn: 5,
    units,
    settlements,
    players: [],
    resigned: [],
    standing_orders: [],
    config: {} as WorldReplay["state_after"]["config"],
  } as WorldReplay["state_after"];
}

const plains = () => "plains" as const;

function build(
  events: WorldReplay["events"],
  before: ReturnType<typeof unit>[] | null,
  after: ReturnType<typeof unit>[],
  sites: { before: Settlements; after: Settlements } = { before: [], after: [] },
) {
  const replay: WorldReplay = { turn: 4, events, state_after: state(after, sites.after) };
  return buildPlayback({
    replay,
    viewer: "me",
    before: before === null ? null : state(before, sites.before),
    terrainAt: plains,
    book: unitBook(events, replay.state_after.units, "me"),
  });
}

describe("fog: units entering and leaving sight", () => {
  const move = (pulse: number, id: number, from: [number, number], to: [number, number]) => ({
    kind: "unit_moved" as const,
    pulse,
    unit_id: id,
    origin: { q: from[0], r: from[1] },
    dest: { q: to[0], r: to[1] },
  });

  it("an enemy first seen mid-turn appears at the beat of its first event", () => {
    // My infantry walks east; an enemy 9 hexes away walks toward it from pulse 3.
    const events = [move(1, 1, [0, 0], [1, 0]), move(2, 1, [1, 0], [2, 0]), move(3, 2, [9, 0], [8, 0]), move(4, 2, [8, 0], [7, 0])];
    const pb = build(events, [unit(1, "me", 0, 0)], [unit(1, "me", 2, 0), unit(2, "them", 7, 0)]);
    const at = (k: number) => unitsAtBeat(pb, k);
    expect(at(0).before.map((u) => u.id)).toEqual([1]);
    expect(at(1).after.map((u) => u.id)).toEqual([1]);
    expect(pb.beats[2].actions[0]).toMatchObject({ kind: "reveal", unit: { id: 2, coord: { q: 9, r: 0 } } });
    expect(at(2).after.find((u) => u.id === 2)!.coord).toEqual({ q: 8, r: 0 });
  });

  it("an enemy that sits still is revealed when the viewer's sight reaches it", () => {
    // My unit walks from (0,0) to (3,0); the enemy at (9,0) is within sight 6 from pulse 3.
    const events = [move(1, 1, [0, 0], [1, 0]), move(2, 1, [1, 0], [2, 0]), move(3, 1, [2, 0], [3, 0])];
    const pb = build(events, [unit(1, "me", 0, 0)], [unit(1, "me", 3, 0), unit(2, "them", 9, 0)]);
    expect(pb.snapshots[2].has(2)).toBe(false);
    expect(pb.beats[2].actions[0]).toMatchObject({ kind: "reveal", unit: { id: 2 } });
    expect(pb.snapshots[3].has(2)).toBe(true);
  });

  it("an enemy left behind fades out once it drops out of sight", () => {
    const events = [move(1, 1, [0, 0], [1, 0]), move(2, 1, [1, 0], [2, 0]), move(3, 1, [2, 0], [3, 0])];
    // From (1,0) the enemy at (-6,0) is 7 away, past sight 6: it fades out in pulse 1.
    const pb = build(events, [unit(1, "me", 0, 0), unit(2, "them", -6, 0)], [unit(1, "me", 3, 0)]);
    expect(pb.snapshots[0].has(2)).toBe(true);
    expect(pb.beats[0].actions.at(-1)).toEqual({ kind: "conceal", unitId: 2 });
    expect(pb.snapshots[1].has(2)).toBe(false);
  });

  it("an enemy that moved unseen fades out and back in where it turns up", () => {
    const events = [move(1, 2, [7, 0], [6, 0])];
    const pb = build(events, [unit(1, "me", 0, 0), unit(2, "them", 2, 0)], [unit(1, "me", 0, 0), unit(2, "them", 6, 0)]);
    expect(pb.beats[0].actions[0]).toMatchObject({ kind: "relocate", unit: { id: 2, coord: { q: 7, r: 0 } } });
    expect(pb.snapshots[1].get(2)!.coord).toEqual({ q: 6, r: 0 });
  });

  it("rewinding places the viewer's units and an enemy in sight from the start", () => {
    const events = [move(1, 1, [0, 0], [1, 0]), move(2, 2, [2, 0], [3, 0])];
    const pb = build(events, null, [unit(1, "me", 1, 0), unit(2, "them", 3, 0)]);
    expect(pb.start).toBe("rewound");
    expect([...pb.snapshots[0].values()].map((u) => [u.id, u.coord.q])).toEqual([
      [1, 0],
      [2, 2],
    ]);
  });

  it("disorder held at the start expires at the end of the turn", () => {
    const pb = build([], [unit(1, "me", 0, 0, { disordered: true })], [unit(1, "me", 0, 0)]);
    expect(pb.beats).toHaveLength(1);
    expect(pb.beats[0].actions).toEqual([{ kind: "steady", unitId: 1 }]);
    expect(pb.snapshots[1].get(1)!.disordered).toBe(false);
  });
});

describe("militia", () => {
  const FARM = { q: 3, r: 0 };
  const farm = (owner: string | null) => [{ coord: FARM, controlled_by: owner, muster_clock: 0 }];

  it("a militia retake leaves the site neutral, not owned by 'neutral'", () => {
    // My farm at (3,0) stands empty; its militia picket walks back onto it.
    const events = [
      { kind: "unit_moved" as const, pulse: 1, unit_id: 50, origin: { q: 4, r: 0 }, dest: FARM },
      {
        kind: "settlement_captured" as const,
        coord: FARM,
        settlement: "plains" as const,
        site: "farm" as const,
        captured_by: "neutral",
        previous_owner: "me",
        unit_id: 50,
      },
    ];
    const militia = (q: number) => unit(50, "neutral", q, 0, { hp: 4, post: FARM });
    const pb = build(events, [unit(1, "me", 1, 0), militia(4)], [unit(1, "me", 1, 0), militia(3)], {
      before: farm("me"),
      after: farm(null),
    });
    const key = hexKey(FARM.q, FARM.r);
    const capture = pb.beats.findIndex((b) => b.phase === "capture");
    expect(pb.control[capture].get(key)).toBe("me");
    for (let b = capture + 1; b < pb.control.length; b++) expect(pb.control[b].get(key)).toBeNull();
    expect(pb.snapshots.at(-1)!.get(50)).toMatchObject({ coord: FARM, post: FARM });
  });

  it("skips an event kind this client does not know", () => {
    const events = [
      { kind: "unit_moved" as const, pulse: 1, unit_id: 1, origin: { q: 0, r: 0 }, dest: { q: 1, r: 0 } },
      { kind: "something_new", unit_id: 1 },
    ] as unknown as WorldReplay["events"];
    const pb = build(events, [unit(1, "me", 0, 0)], [unit(1, "me", 1, 0)]);
    expect(pb.beats.flatMap((b) => b.eventIndices)).toEqual([0]);
    expect(pb.snapshots.at(-1)!.get(1)!.coord).toEqual({ q: 1, r: 0 });
  });
});
