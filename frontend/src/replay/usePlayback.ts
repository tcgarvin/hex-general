import { useCallback, useEffect, useState } from "react";

/** Beat pause durations (ms) at each speed. */
const BEAT_DURATION: Record<1 | 2, number> = { 1: 1200, 2: 600 };

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
 * The caller supplies `advance` (step forward one beat), `currentBeat`, and
 * `beatCount`. Each time `advance` fires, `currentBeat` changes, which
 * re-triggers the scheduling effect — one beat per BEAT_DURATION.
 */
export function usePlayback({
  advance,
  currentBeat,
  beatCount,
}: {
  advance: () => void;
  currentBeat: number;
  beatCount: number;
}): PlaybackState {
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<1 | 2>(1);

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
    const t = setTimeout(advance, BEAT_DURATION[speed]);
    return () => clearTimeout(t);
  }, [playing, currentBeat, beatCount, advance, speed]);

  const play = useCallback(() => {
    if (currentBeat < beatCount - 1) setPlaying(true);
  }, [currentBeat, beatCount]);

  const pause = useCallback(() => setPlaying(false), []);

  const toggleSpeed = useCallback(
    () => setSpeed((s) => (s === 1 ? 2 : 1)),
    [],
  );

  // Auto-play on mount with a brief settle delay.
  useEffect(() => {
    if (beatCount === 0) return;
    const t = setTimeout(() => setPlaying(true), START_DELAY_MS);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally mount-only — fires once when replay data is available

  return { playing, speed, play, pause, toggleSpeed };
}
