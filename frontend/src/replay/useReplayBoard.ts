import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ReplayAnimOverrides, ReplayControlsProps } from "@/board/Board";
import { axialToPixel, HEX_SIZE } from "@/board/hex";
import type { components } from "@/lib/api/schema";
import type { GameState, GameView, ReplayEvent } from "@/lib/api/types";
import { DEFAULT_FACING, facingAfterStep } from "@/planning/facing";

import { applyBeats } from "./applyBeats";
import { buildReplayLog, nameLookup, type UnitRef } from "./replayLog";
import type { ReplayData } from "./useReplay";
import { useBeats } from "./useBeats";
import { usePlayback } from "./usePlayback";

type Unit = components["schemas"]["Unit"];
type Facing = components["schemas"]["Facing"];

// --- animation constants ---

/** Duration of unit slide and fade animations, by playback speed. */
const ANIM_DURATION_MS: Record<1 | 2, number> = { 1: 500, 2: 250 };

const easeInOutCubic = (t: number): number =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

interface AnimData {
  /** World-pixel start and end for each sliding unit, plus the step's facing. */
  slides: Map<
    number,
    { fromPx: { x: number; y: number }; toPx: { x: number; y: number }; facing: Facing }
  >;
  /** Unit IDs of destroyed units that should fade out to 0. */
  fadingOutIds: Set<number>;
  /** Recruited units that should fade in from 0. */
  fadingInUnits: Map<number, Unit>;
}

function extractAnimData(
  events: ReplayEvent[],
  preBeatUnitsById: ReadonlyMap<number, Unit>,
  postBeatUnitsById: ReadonlyMap<number, Unit>,
): AnimData {
  const slides = new Map<
    number,
    { fromPx: { x: number; y: number }; toPx: { x: number; y: number }; facing: Facing }
  >();
  const fadingOutIds = new Set<number>();
  const fadingInUnits = new Map<number, Unit>();

  for (const e of events) {
    if (e.kind === "unit_moved") {
      const { unit_id, origin, dest } = e;
      // Only record the first move per unit (each unit steps once per pulse).
      if (!slides.has(unit_id)) {
        const fromPx = axialToPixel(origin.q, origin.r, HEX_SIZE);
        const toPx = axialToPixel(dest.q, dest.r, HEX_SIZE);
        if (fromPx.x !== toPx.x || fromPx.y !== toPx.y) {
          // Face the direction of travel for the whole slide (the settled
          // post-beat state carries the same facing, so nothing snaps after).
          const prior = preBeatUnitsById.get(unit_id)?.facing ?? DEFAULT_FACING;
          slides.set(unit_id, { fromPx, toPx, facing: facingAfterStep(prior, origin, dest) });
        }
      }
    } else if (e.kind === "barrage_resolved") {
      const { hit } = e;
      if (hit != null && hit.destroyed) {
        fadingOutIds.add(hit.unit_id);
      }
    } else if (e.kind === "combat_resolved") {
      const { participants } = e;
      for (const p of participants) {
        if (p.destroyed) fadingOutIds.add(p.unit_id);
      }
    } else if (e.kind === "unit_recruited") {
      const { unit_id } = e;
      const unit = postBeatUnitsById.get(unit_id);
      if (unit !== undefined) fadingInUnits.set(unit_id, unit);
    }
  }

  // Remove fading-out units that aren't in the pre-beat state (guards against
  // event ordering surprises).
  for (const id of fadingOutIds) {
    if (!preBeatUnitsById.has(id)) fadingOutIds.delete(id);
  }

  return { slides, fadingOutIds, fadingInUnits };
}

/** Compute the `ReplayAnimOverrides` for the board at the given animation progress. */
function interpolateAnim(data: AnimData, progress: number): ReplayAnimOverrides {
  const eased = easeInOutCubic(progress);

  const slideOverrides = new Map<number, { x: number; y: number; facing: Facing }>();
  for (const [id, { fromPx, toPx, facing }] of data.slides) {
    slideOverrides.set(id, {
      x: fromPx.x + (toPx.x - fromPx.x) * eased,
      y: fromPx.y + (toPx.y - fromPx.y) * eased,
      facing,
    });
  }

  const fadingOut = new Map<number, number>();
  for (const id of data.fadingOutIds) {
    fadingOut.set(id, 1 - progress); // linear fade for destruction
  }

  const fadingIn = new Map<number, { unit: Unit; alpha: number }>();
  for (const [id, unit] of data.fadingInUnits) {
    fadingIn.set(id, { unit, alpha: progress });
  }

  return { slideOverrides, fadingOut, fadingIn };
}

// --- hook ---

/** The board-facing props a replay drives: the state to draw, the live
 * animation overlay, the transport controls, and the event log. */
export interface ReplayBoardBundle {
  boardState: GameState;
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
  const { playing, speed, play, pause, toggleSpeed } = usePlayback({
    advance,
    currentBeat,
    beatCount: beats.length,
    turn,
  });

  // --- per-beat state and animation ---

  // The beat whose boundary state the board is currently displaying. Lags behind
  // `currentBeat` while animation is in progress; snaps to match on rewind/jump
  // and after animation completes.
  const [displayBeat, setDisplayBeat] = useState(-1);

  // Active animation: null when settled, { data, progress } while a beat plays.
  // Stored in state (not a ref) so it can be safely read during render.
  const [animState, setAnimState] = useState<{ data: AnimData; progress: number } | null>(null);

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

    const data = extractAnimData(beat.events, preBeatById, postBeatById);

    // Skip animation when there's nothing to animate.
    if (data.slides.size === 0 && data.fadingOutIds.size === 0 && data.fadingInUnits.size === 0) {
      snap();
      return;
    }

    const duration = ANIM_DURATION_MS[speedRef.current];
    const start = performance.now();

    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      setAnimState((prev) => (prev !== null ? { data: prev.data, progress: t } : null));
      if (t < 1) {
        rafRef.current = requestAnimationFrame(step);
      } else {
        // Animation complete: advance displayBeat and clear the overlay.
        setDisplayBeat(currentBeat);
        setAnimState(null);
        rafRef.current = null;
      }
    };
    setAnimState({ data, progress: 0 });
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

  const replayAnim: ReplayAnimOverrides | undefined =
    animState !== null ? interpolateAnim(animState.data, animState.progress) : undefined;

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

  if (replayData === null || boardState === null) return null;

  return { boardState, replayAnim, replayControls, replayLog: { logBeats, onJumpToBeat } };
}
