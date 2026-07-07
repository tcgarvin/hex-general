import { useCallback, useMemo, useState } from "react";

import type { ReplayEvent } from "@/lib/api/types";

import { groupBeats, type Beat } from "./beats";

export interface BeatsState {
  beats: Beat[];
  /** Current beat index: -1 = pre-turn state, 0..N-1 = a resolved beat. */
  currentBeat: number;
  advance: () => void;
  rewind: () => void;
  jumpTo: (index: number) => void;
}

export function useBeats(rawEvents: ReplayEvent[]): BeatsState {
  const beats = useMemo(() => groupBeats(rawEvents), [rawEvents]);
  const [currentBeat, setCurrentBeat] = useState(-1);

  // Reset to the pre-turn state whenever a new replay's events arrive. This hook
  // now outlives a single replay (the game surface stays mounted across the
  // planning⇄replay cycle), so without this a later turn would resume mid-way
  // through the previous turn's beats. Uses the React docs' "adjusting state when
  // a prop changes" pattern (prev value in state) — no effect, no stale frame.
  const [prevEvents, setPrevEvents] = useState(rawEvents);
  if (prevEvents !== rawEvents) {
    setPrevEvents(rawEvents);
    setCurrentBeat(-1);
  }

  const advance = useCallback(() => {
    setCurrentBeat((cur) => Math.min(cur + 1, beats.length - 1));
  }, [beats.length]);

  const rewind = useCallback(() => {
    setCurrentBeat((cur) => Math.max(cur - 1, -1));
  }, []);

  const jumpTo = useCallback(
    (index: number) => {
      setCurrentBeat(Math.max(-1, Math.min(index, beats.length - 1)));
    },
    [beats.length],
  );

  return { beats, currentBeat, advance, rewind, jumpTo };
}
