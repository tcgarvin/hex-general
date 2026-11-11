// React driver for the playback transport (`controller.ts`): a rAF loop that
// feeds elapsed time into the pure reducer while playing, and the
// `prefers-reduced-motion` media query. No Pixi here; the caller samples
// `frameAt(playback, position.beat, position.phase)` and hands it to the renderer.

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import {
  initialPlayback,
  playbackPosition,
  playbackReducer,
  type PlaybackState,
  type Speed,
} from "./controller";

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(REDUCED_MOTION).matches;
}

export interface PlaybackControls {
  state: PlaybackState;
  /** What to draw now. `beat` is -1 only when there are no beats. */
  position: { beat: number; phase: number };
  play: () => void;
  pause: () => void;
  toggle: () => void;
  stepForward: () => void;
  stepBack: () => void;
  /** Jump to the start of beat `k` (e.g. from the "your fights" list); pauses. */
  jumpToBeat: (k: number) => void;
  /** Scrub to position `t` in [0, beatCount]. */
  seek: (t: number) => void;
  setSpeed: (speed: Speed) => void;
}

/**
 * Drive playback over `beatCount` beats, beat `k` lasting `durationMs(k)` at 1x.
 * `resetKey` (the turn) rewinds to the start, paused, whenever it changes.
 */
export function usePlayback({
  beatCount,
  durationMs,
  resetKey,
}: {
  beatCount: number;
  durationMs: (beat: number) => number;
  resetKey: number | string;
}): PlaybackControls {
  const [state, dispatch] = useReducer(playbackReducer, beatCount, (n) => initialPlayback(n, prefersReducedMotion()));

  // A new turn (or a different beat list) starts over: React's "adjust state
  // when a prop changes" pattern, so no render shows the old position.
  const [key, setKey] = useState({ resetKey, beatCount });
  if (key.resetKey !== resetKey || key.beatCount !== beatCount) {
    setKey({ resetKey, beatCount });
    dispatch({ type: "reset", beatCount });
  }

  const durationRef = useRef(durationMs);
  useEffect(() => {
    durationRef.current = durationMs;
  }, [durationMs]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(REDUCED_MOTION);
    const onChange = () => dispatch({ type: "reducedMotion", on: query.matches });
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (!state.playing) return;
    let last: number | null = null;
    let frame = requestAnimationFrame(function loop(now) {
      if (last !== null) dispatch({ type: "tick", dt: now - last, durationMs: (k) => durationRef.current(k) });
      last = now;
      frame = requestAnimationFrame(loop);
    });
    return () => cancelAnimationFrame(frame);
  }, [state.playing]);

  const play = useCallback(() => dispatch({ type: "play" }), []);
  const pause = useCallback(() => dispatch({ type: "pause" }), []);
  const toggle = useCallback(() => dispatch({ type: "toggle" }), []);
  const stepForward = useCallback(() => dispatch({ type: "step", by: 1 }), []);
  const stepBack = useCallback(() => dispatch({ type: "step", by: -1 }), []);
  const seek = useCallback((t: number) => dispatch({ type: "seek", t }), []);
  const jumpToBeat = useCallback((k: number) => {
    dispatch({ type: "pause" });
    dispatch({ type: "seek", t: k });
  }, []);
  const setSpeed = useCallback((speed: Speed) => dispatch({ type: "speed", speed }), []);

  return {
    state,
    position: playbackPosition(state),
    play,
    pause,
    toggle,
    stepForward,
    stepBack,
    jumpToBeat,
    seek,
    setSpeed,
  };
}
