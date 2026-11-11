import { describe, expect, it } from "vitest";

import { initialPlayback, playbackPosition, playbackReducer, type PlaybackAction, type PlaybackState } from "./controller";

const durations = [1000, 500, 2000];
const durationMs = (k: number) => durations[k];
const run = (s: PlaybackState, ...actions: PlaybackAction[]) => actions.reduce(playbackReducer, s);
const tick = (dt: number): PlaybackAction => ({ type: "tick", dt, durationMs });

describe("playback reducer", () => {
  it("starts paused at the start picture", () => {
    const s = initialPlayback(3);
    expect(s).toMatchObject({ t: 0, playing: false, speed: 1 });
    expect(playbackPosition(s)).toEqual({ beat: 0, phase: 0 });
  });

  it("advances through beats by their own lengths", () => {
    const s = run(initialPlayback(3), { type: "play" }, tick(500));
    expect(playbackPosition(s)).toEqual({ beat: 0, phase: 0.5 });
    const s2 = run(s, tick(750)); // 500 finishes beat 0, 250 is half of beat 1
    expect(playbackPosition(s2)).toEqual({ beat: 1, phase: 0.5 });
  });

  it("playing to the end pauses on the final state", () => {
    const s = run(initialPlayback(3), { type: "play" }, tick(10_000));
    expect(s.playing).toBe(false);
    expect(s.t).toBe(3);
    expect(playbackPosition(s)).toEqual({ beat: 2, phase: 1 });
    // Play again from the end starts over.
    expect(run(s, { type: "play" })).toMatchObject({ playing: true, t: 0 });
  });

  it("steps snap to beat boundaries and clamp at both ends", () => {
    let s = run(initialPlayback(3), { type: "seek", t: 1.4 });
    expect(run(s, { type: "step", by: 1 }).t).toBe(2);
    expect(run(s, { type: "step", by: -1 }).t).toBe(1);
    s = run(s, { type: "step", by: 1 }, { type: "step", by: 1 }, { type: "step", by: 1 }, { type: "step", by: 1 });
    expect(s.t).toBe(3);
    s = run(s, ...Array.from({ length: 6 }, () => ({ type: "step", by: -1 }) as const));
    expect(s.t).toBe(0);
  });

  it("stepping pauses playback", () => {
    const s = run(initialPlayback(3), { type: "play" }, { type: "step", by: 1 });
    expect(s).toMatchObject({ playing: false, t: 1 });
  });

  it("changing speed keeps the position and scales time", () => {
    const s = run(initialPlayback(3), { type: "play" }, tick(250), { type: "speed", speed: 4 });
    expect(s.t).toBeCloseTo(0.25);
    expect(run(s, tick(125)).t).toBeCloseTo(0.75);
    expect(run(s, { type: "speed", speed: 0.5 }, tick(500)).t).toBeCloseTo(0.5);
  });

  it("seek clamps and does not start playback", () => {
    expect(run(initialPlayback(3), { type: "seek", t: 9 })).toMatchObject({ t: 3, playing: false });
    expect(run(initialPlayback(3), { type: "seek", t: -2 }).t).toBe(0);
  });

  it("ticks while paused change nothing", () => {
    const s = initialPlayback(3);
    expect(run(s, tick(500))).toBe(s);
  });

  it("reduced motion holds each boundary for the beat's length, then jumps", () => {
    let s = run(initialPlayback(3, true), { type: "play" }, tick(900));
    expect(s.t).toBe(0);
    s = run(s, tick(200));
    expect(s.t).toBe(1);
    expect(Number.isInteger(run(s, tick(300)).t)).toBe(true);
    // Turning it on mid-beat lands on the beat's start.
    const tweening = run(initialPlayback(3), { type: "seek", t: 1.6 }, { type: "reducedMotion", on: true });
    expect(tweening.t).toBe(1);
  });

  it("an empty turn never plays", () => {
    const s = run(initialPlayback(0), { type: "play" });
    expect(s.playing).toBe(false);
    expect(playbackPosition(s)).toEqual({ beat: -1, phase: 0 });
  });

  it("reset returns to a paused start and keeps the motion preference", () => {
    const s = run(initialPlayback(3, true), { type: "play" }, tick(5000), { type: "reset", beatCount: 5 });
    expect(s).toMatchObject({ beatCount: 5, t: 0, playing: false, reducedMotion: true });
  });
});
