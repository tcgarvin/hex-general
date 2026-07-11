import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ReplayControlsProps } from "@/board/Board";
import type { Axial } from "@/board/hex";
import type { GameState, GameView, ReplayEvent } from "@/lib/api/types";

import {
  beatAnimDurationMs,
  buildAnimTimeline,
  sampleTimeline,
  type AnimTimeline,
  type ReplayAnimOverrides,
} from "./animFx";
import { applyBeats } from "./applyBeats";
import { buildReplayLog, nameLookup, type UnitRef } from "./replayLog";
import type { ReplayData } from "./useReplay";
import { useBeats } from "./useBeats";
import { usePlayback } from "./usePlayback";
import { visibleHexes } from "./visibility";

// --- hook ---

/** The board-facing props a replay drives: the state to draw, the live
 * animation overlay, the transport controls, and the event log. */
export interface ReplayBoardBundle {
  boardState: GameState;
  /** The viewer's fog-of-war visible set for `boardState` (recomputed per beat). */
  visible: Axial[];
  replayAnim: ReplayAnimOverrides | undefined;
  replayControls: ReplayControlsProps;
  replayLog: { logBeats: ReturnType<typeof buildReplayLog>; onJumpToBeat: (i: number) => void };
}

/** Stable empty event list so the beat engine's memo/reset don't churn while
 * planning (no replay data). */
const NO_EVENTS: ReplayEvent[] = [];

/**
 * Drives a turn replay against the persistent board. Called unconditionally by
 * the unified game surface so hook order stays stable across the planning⇄replay
 * cycle; returns `null` while there is no replay data (planning mode), in which
 * case the beat engine idles over an empty event list.
 *
 * `turn` is the reset key for the transport (see `usePlayback`); `onStartPlanning`
 * is what the "Start planning" control invokes to leave replay mode.
 */
export function useReplayBoard(
  replayData: ReplayData | null,
  turn: number,
  view: GameView,
  onStartPlanning: () => void,
): ReplayBoardBundle | null {
  const events = replayData?.replay.events ?? NO_EVENTS;
  // State at the start of this turn; null for turn 1 (no previous turn data).
  const preTurnState = replayData?.stateBefore ?? null;
  const postTurnState = replayData?.replay.state_after ?? null;

  const { beats, currentBeat, advance, rewind, jumpTo } = useBeats(events);
  // Auto-play holds each beat for its animation plus a dwell, so a long combat
  // beat finishes playing before the transport moves on.
  const animDurationMs = useCallback(
    (beatIndex: number, speed: 1 | 2) => beatAnimDurationMs(beats[beatIndex], speed),
    [beats],
  );
  const { playing, speed, play, pause, toggleSpeed } = usePlayback({
    advance,
    currentBeat,
    beatCount: beats.length,
    turn,
    animDurationMs,
  });

  // --- per-beat state and animation ---

  // The beat whose boundary state the board is currently displaying. Lags behind
  // `currentBeat` while animation is in progress; snaps to match on rewind/jump
  // and after animation completes.
  const [displayBeat, setDisplayBeat] = useState(-1);

  // Active animation: null when settled, { timeline, progress } while a beat
  // plays. Stored in state (not a ref) so it can be safely read during render.
  const [animState, setAnimState] = useState<{
    timeline: AnimTimeline;
    progress: number;
  } | null>(null);

  const rafRef = useRef<number | null>(null);
  // The value of currentBeat as seen by the previous render.
  const prevBeatRef = useRef(-1);
  // Latest playback speed, read inside the step-forward effect without making
  // speed a dependency — a speed change mid-animation shouldn't restart it.
  const speedRef = useRef(speed);
  useEffect(() => {
    speedRef.current = speed;
  }, [speed]);

  const cancelAnim = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  useEffect(() => cancelAnim, [cancelAnim]);

  // Respond to currentBeat changes: animate on step-forward, snap otherwise.
  useEffect(() => {
    const prev = prevBeatRef.current;
    prevBeatRef.current = currentBeat;

    cancelAnim();

    const snap = () => {
      setDisplayBeat(currentBeat);
      setAnimState(null);
    };

    if (preTurnState === null) {
      snap();
      return;
    }

    // Rewind, jump to start, or multi-step jump: snap immediately.
    if (currentBeat <= prev || currentBeat > prev + 1 || currentBeat < 0) {
      snap();
      return;
    }

    // Advance by exactly one beat: animate the transition.
    const beat = beats[currentBeat];
    if (beat === undefined || beat.events.length === 0) {
      snap();
      return;
    }

    const preBeatState = applyBeats(preTurnState, beats, prev);
    const postBeatState = applyBeats(preTurnState, beats, currentBeat);
    const preBeatById = new Map((preBeatState.units ?? []).map((u) => [u.id, u]));
    const postBeatById = new Map((postBeatState.units ?? []).map((u) => [u.id, u]));

    const timeline = buildAnimTimeline(beat, preBeatById, postBeatById);
    const duration = beatAnimDurationMs(beat, speedRef.current);

    // Skip animation when there's nothing to animate.
    if (timeline === null || duration <= 0) {
      snap();
      return;
    }

    const start = performance.now();

    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      setAnimState((prev) => (prev !== null ? { timeline: prev.timeline, progress: t } : null));
      if (t < 1) {
        rafRef.current = requestAnimationFrame(step);
      } else {
        // Animation complete: advance displayBeat and clear the overlay.
        setDisplayBeat(currentBeat);
        setAnimState(null);
        rafRef.current = null;
      }
    };
    setAnimState({ timeline, progress: 0 });
    rafRef.current = requestAnimationFrame(step);
    // `beats`/`preTurnState` are stable per replay, so in practice only a
    // `currentBeat` change drives this; listing them keeps the effect correct
    // if that ever stops holding. `speed` is read via `speedRef`, by design.
  }, [currentBeat, beats, preTurnState, cancelAnim]);

  // Board state: pre-beat state while animating, settled post-beat state otherwise.
  const boardState = useMemo((): GameState | null => {
    if (postTurnState === null) return null;
    if (preTurnState === null) return postTurnState;
    return applyBeats(preTurnState, beats, displayBeat);
  }, [preTurnState, postTurnState, beats, displayBeat]);

  // Fog of war for the displayed beat (SPEC §9): recomputed from the viewer's
  // own units and settlements in `boardState`, so the veil follows their army
  // through the turn. Mid-beat animation keeps the pre-beat fog — the veil
  // steps when `displayBeat` settles, like every other state change.
  const visible = useMemo(
    (): Axial[] | null =>
      boardState === null ? null : visibleHexes(boardState, view.your_slot),
    [boardState, view.your_slot],
  );

  const replayAnim: ReplayAnimOverrides | undefined =
    animState !== null ? sampleTimeline(animState.timeline, animState.progress) : undefined;

  const gameOver = view.victory.reason !== "ongoing";

  const replayControls: ReplayControlsProps = {
    beats,
    currentBeat,
    playing,
    speed,
    onRewind: () => jumpTo(-1),
    onBack: rewind,
    onPlayPause: playing ? pause : play,
    onForward: advance,
    onSkip: () => jumpTo(beats.length - 1),
    onToggleSpeed: toggleSpeed,
    onStartPlanning,
    gameOver,
  };

  // Event-log model (UI-SPEC §13.3): name units by id from the union of the pre-
  // and post-turn states (post wins, so it covers units recruited this turn).
  const logBeats = useMemo(() => {
    const unitsById = new Map<number, UnitRef>();
    for (const u of preTurnState?.units ?? []) unitsById.set(u.id, { owner: u.owner, type: u.type });
    for (const u of postTurnState?.units ?? []) unitsById.set(u.id, { owner: u.owner, type: u.type });
    return buildReplayLog(beats, nameLookup(unitsById));
  }, [beats, preTurnState, postTurnState]);

  const onJumpToBeat = useCallback(
    (beatIndex: number) => {
      pause();
      jumpTo(beatIndex);
    },
    [pause, jumpTo],
  );

  if (replayData === null || boardState === null || visible === null) return null;

  return { boardState, visible, replayAnim, replayControls, replayLog: { logBeats, onJumpToBeat } };
}
