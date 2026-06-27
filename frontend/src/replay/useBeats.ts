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
