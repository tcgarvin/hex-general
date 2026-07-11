import { useCallback, useEffect, useRef, useState } from "react";

/** Dwell (ms) a beat holds after its animation finishes, at each speed. The
 * full per-beat delay is this plus the beat's animation duration, so long
 * beats (combat, barrage) finish playing before auto-play advances. */
const BEAT_DWELL: Record<1 | 2, number> = { 1: 700, 2: 350 };

/** Delay before auto-play starts — gives the page time to settle. */
const START_DELAY_MS = 700;

export interface PlaybackState {
  playing: boolean;
  speed: 1 | 2;
  play: () => void;
  pause: () => void;
  toggleSpeed: () => void;
}

/**
 * Playback controller for the replay beat engine. Drives auto-advancing beats
 * at 1× or 2× speed. Auto-play starts on mount after a brief settle delay.
 *
 * The caller supplies `advance` (step forward one beat), `currentBeat`,
 * `beatCount`, and `animDurationMs` (how long the given beat's animation runs
 * at a speed). Each time `advance` fires, `currentBeat` changes, which
 * re-triggers the scheduling effect — each beat holds for its animation plus
 * BEAT_DWELL before the next advance.
 *
 * `turn` is a reset key: the game surface keeps this hook mounted across the
 * whole planning⇄replay cycle, so when a new turn's replay begins we re-arm the
 * one-shot auto-play and drop back to paused/1×. Without it only the first
 * replay of a session would auto-play.
 */
export function usePlayback({
  advance,
  currentBeat,
  beatCount,
  turn,
  animDurationMs,
}: {
  advance: () => void;
  currentBeat: number;
  beatCount: number;
  turn: number;
  /** Animation duration (ms) of the beat at `beatIndex` at `speed`; 0 for a
   * snap beat (or the pre-turn position, index -1). */
  animDurationMs: (beatIndex: number, speed: 1 | 2) => number;
}): PlaybackState {
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<1 | 2>(1);

  // Re-arm on a new replay (this hook stays mounted across the whole
  // planning⇄replay cycle). Drop back to paused/1× when the turn changes, using
  // the React docs' "adjusting state when a prop changes" pattern (prev value in
  // state). Auto-play is re-armed separately, keyed on `turn` in its effect.
  const [prevTurn, setPrevTurn] = useState(turn);
  if (prevTurn !== turn) {
    setPrevTurn(turn);
    setPlaying(false);
    setSpeed(1);
  }

  // Advance one beat per interval while playing. When currentBeat changes
  // (the previous advance fired) this effect re-runs and queues the next one.
  // When the final beat is reached, stop via a deferred call so the linter's
  // no-synchronous-setState-in-effect rule (react-hooks/set-state-in-effect) is
  // satisfied — the `setTimeout` call is async, not in the effect body proper.
  useEffect(() => {
    if (!playing) return;
    if (currentBeat >= beatCount - 1) {
      const t = setTimeout(() => setPlaying(false), 0);
      return () => clearTimeout(t);
    }
    const t = setTimeout(advance, animDurationMs(currentBeat, speed) + BEAT_DWELL[speed]);
    return () => clearTimeout(t);
  }, [playing, currentBeat, beatCount, advance, speed, animDurationMs]);

  const play = useCallback(() => {
    if (currentBeat < beatCount - 1) setPlaying(true);
  }, [currentBeat, beatCount]);

  const pause = useCallback(() => setPlaying(false), []);

  const toggleSpeed = useCallback(
    () => setSpeed((s) => (s === 1 ? 2 : 1)),
    [],
  );

  // Auto-play once per replay, after a brief settle delay, the first time that
  // turn's replay data is available (beatCount > 0). Keying the guard ref on
  // `turn` (written in the effect, not render) fires it exactly once per turn
  // even as beatCount settles, and re-arms cleanly for the next turn.
  const autoPlayedTurnRef = useRef<number | null>(null);
  useEffect(() => {
    if (beatCount === 0 || autoPlayedTurnRef.current === turn) return;
    autoPlayedTurnRef.current = turn;
    const t = setTimeout(() => setPlaying(true), START_DELAY_MS);
    return () => clearTimeout(t);
  }, [beatCount, turn]);

  return { playing, speed, play, pause, toggleSpeed };
}
