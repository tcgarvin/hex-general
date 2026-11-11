import { describe, expect, it } from "vitest";

import { turnReport, unitBook } from "../turnReport";
import { beatDurationMs, cameraHint } from "./interpolate";
import { BLANK_BEAT_MS, beatMarks, beatTitle, playDurationMs, QUIET_PACE, reportItemBeats, turnHasPlayback } from "./session";
import { casePlayback, FIXTURE_CASES, FIXTURE_MAP } from "./testFixtures";

const turn11 = FIXTURE_CASES.find((x) => x.viewer === "b001" && x.turn === 11)!;

describe("playback session helpers", () => {
  it("titles pulses by their place among the turn's pulses", () => {
    const pb = casePlayback(turn11);
    expect(pb.beats.map((_, k) => beatTitle(pb.beats, k))).toEqual([
      "Barrage",
      "Pulse 1 of 4",
      "Pulse 2 of 4",
      "Pulse 3 of 4",
      "Pulse 4 of 4",
      "Captures",
      "Supply and building",
    ]);
  });

  it("hurries through beats without the viewer's action and pauses briefly on bookkeeping", () => {
    for (const c of FIXTURE_CASES) {
      const pb = casePlayback(c);
      pb.beats.forEach((beat, k) => {
        const d = playDurationMs(pb, k);
        if (beat.actions.every((a) => a.kind === "steady" || a.kind === "correct")) expect(d).toBe(BLANK_BEAT_MS);
        else if (cameraHint(pb, k) === null) expect(d).toBe(Math.round(beatDurationMs(beat) * QUIET_PACE));
        else expect(d).toBe(beatDurationMs(beat));
      });
    }
  });

  it("marks the viewer's fights for the scrubber", () => {
    const pb = casePlayback(turn11);
    expect(beatMarks(pb)).toEqual(["own_fight", "own_fight", null, null, null, null, null]);
  });

  it("maps report lines to the beats that show their events", () => {
    const c = turn11;
    const book = unitBook(c.replay.events, c.replay.state_after.units, c.viewer);
    const items = turnReport(c.replay.events, c.viewer, book, {
      sites: FIXTURE_MAP.sites,
      settlements: c.replay.state_after.settlements,
      units: c.replay.state_after.units,
    });
    const pb = casePlayback(c);
    const beats = reportItemBeats(items, pb.beats);
    items.forEach((item, i) => {
      if (item.eventIndex === undefined) expect(beats[i]).toBe(-1);
      else expect(pb.beats[beats[i]].eventIndices).toContain(item.eventIndex);
    });
    const fight = items.findIndex((it) => it.section === "fighting" && it.text.includes("(38,20)"));
    expect(pb.beats[beats[fight]].phase).not.toBe("economy");
  });

  it("knows a turn with only bookkeeping has nothing to play", () => {
    expect(turnHasPlayback(turn11.replay.events)).toBe(true);
    const quiet = turn11.replay.events.filter((e) => e.kind === "stocks_resolved" || e.kind === "unit_repaired");
    expect(turnHasPlayback(quiet)).toBe(false);
  });
});
