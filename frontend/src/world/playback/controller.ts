// Turn playback, step 4: the transport, as a pure reducer.
//
// Position is one number `t` in [0, beatCount]: beat `floor(t)` at phase
// `t - floor(t)`, with `t = beatCount` the end of the last beat (the turn's
// final state). Playing advances `t` by elapsed time over each beat's length,
// so a speed change keeps the position. Steps snap to beat boundaries. With
// reduced motion (`prefers-reduced-motion`) playing never tweens: it holds each
// boundary for the beat's length, then jumps to the next.

export type Speed = 0.5 | 1 | 2 | 4;
export const SPEEDS: readonly Speed[] = [0.5, 1, 2, 4];

export interface PlaybackState {
  beatCount: number;
  t: number;
  playing: boolean;
  speed: Speed;
  reducedMotion: boolean;
  /** Reduced motion only: ms spent holding the current boundary. */
  held: number;
}

export type PlaybackAction =
  | { type: "play" }
  | { type: "pause" }
  | { type: "toggle" }
  | { type: "step"; by: 1 | -1 }
  | { type: "seek"; t: number }
  | { type: "speed"; speed: Speed }
  | { type: "reducedMotion"; on: boolean }
  /** `dt` ms of wall time passed; `durationMs(k)` is beat k's length at 1x. */
  | { type: "tick"; dt: number; durationMs: (beat: number) => number }
  | { type: "reset"; beatCount: number };

/**
 * The most wall time one tick may advance playback by. A throttled or stalled
 * tab delivers frames seconds apart; uncapped, one frame would jump whole beats
 * and the viewer would never see them. Capped, playback slows down instead.
 */
export const MAX_TICK_MS = 100;

export function initialPlayback(beatCount: number, reducedMotion = false): PlaybackState {
  return { beatCount, t: 0, playing: false, speed: 1, reducedMotion, held: 0 };
}

const clampT = (s: PlaybackState, t: number) => Math.min(s.beatCount, Math.max(0, t));

export function playbackReducer(s: PlaybackState, a: PlaybackAction): PlaybackState {
  switch (a.type) {
    case "play":
      if (s.beatCount === 0) return s;
      // Playing from the end replays the turn from the start.
      return { ...s, playing: true, t: s.t >= s.beatCount ? 0 : s.t, held: 0 };
    case "pause":
      return s.playing ? { ...s, playing: false } : s;
    case "toggle":
      return playbackReducer(s, { type: s.playing ? "pause" : "play" });
    case "step": {
      const t = a.by > 0 ? Math.floor(s.t) + 1 : Math.ceil(s.t) - 1;
      return { ...s, playing: false, t: clampT(s, t), held: 0 };
    }
    case "seek":
      return { ...s, t: clampT(s, a.t), held: 0 };
    case "speed":
      return { ...s, speed: a.speed };
    case "reducedMotion":
      // Leaving tweening mid-beat lands on the beat's start, so nothing is drawn half-done.
      return { ...s, reducedMotion: a.on, t: a.on ? Math.floor(s.t) : s.t, held: 0 };
    case "tick":
      return s.playing ? advance(s, Math.min(a.dt, MAX_TICK_MS) * s.speed, a.durationMs) : s;
    case "reset":
      return initialPlayback(a.beatCount, s.reducedMotion);
  }
}

function advance(s: PlaybackState, ms: number, durationMs: (beat: number) => number): PlaybackState {
  let { t, held } = s;
  let left = ms;
  while (left > 0 && t < s.beatCount) {
    const k = Math.floor(t);
    const length = Math.max(1, durationMs(k));
    if (s.reducedMotion) {
      const need = length - held;
      if (left < need) {
        held += left;
        left = 0;
      } else {
        left -= need;
        held = 0;
        t = k + 1;
      }
    } else {
      const need = (k + 1 - t) * length;
      if (left < need) {
        t += left / length;
        left = 0;
      } else {
        left -= need;
        t = k + 1;
      }
    }
  }
  const done = t >= s.beatCount;
  return { ...s, t: done ? s.beatCount : t, held: done ? 0 : held, playing: !done };
}

/** The beat and phase to draw for position `t` (the end draws the last beat at phase 1). */
export function playbackPosition(s: Pick<PlaybackState, "t" | "beatCount">): { beat: number; phase: number } {
  if (s.beatCount === 0) return { beat: -1, phase: 0 };
  if (s.t >= s.beatCount) return { beat: s.beatCount - 1, phase: 1 };
  const beat = Math.floor(s.t);
  return { beat, phase: s.t - beat };
}
