import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router";

import { Board } from "@/board/Board";
import type { ReplayAnimOverrides, ReplayControlsProps } from "@/board/Board";
import { axialToPixel, HEX_SIZE } from "@/board/hex";
import { ErrorBanner, Loading } from "@/components/ui";
import { GameProvider } from "@/game/GameProvider";
import { useGame } from "@/game/useGame";
import type { components } from "@/lib/api/schema";
import type { GameState, GameView, ReplayEvent } from "@/lib/api/types";
import { economyFor } from "@/planning/economy";
import { applyBeats } from "@/replay/applyBeats";
import { useBeats } from "@/replay/useBeats";
import { usePlayback } from "@/replay/usePlayback";
import { useReplay } from "@/replay/useReplay";

type Unit = components["schemas"]["Unit"];

import { GameHeader } from "./GameHeader";

export function ReplayShell() {
  const { id, turn: turnStr } = useParams<{ id: string; turn: string }>();
  if (id === undefined || turnStr === undefined) {
    return <Navigate to="/" replace />;
  }
  const turn = parseInt(turnStr, 10);
  if (isNaN(turn) || turn < 1) {
    return <Navigate to="/" replace />;
  }
  return (
    <GameProvider gameId={id}>
      <ReplayView gameId={id} turn={turn} />
    </GameProvider>
  );
}

function ReplayView({ gameId, turn }: { gameId: string; turn: number }) {
  const { view, refresh } = useGame();
  const { data, loading, error } = useReplay(gameId, turn);

  const economy = useMemo(
    () =>
      data
        ? economyFor(data.replay.state_after, view.your_slot)
        : economyFor(view.state, view.your_slot),
    [data, view.state, view.your_slot],
  );

  if (loading) {
    return (
      <div className="flex h-dvh flex-col bg-zinc-950 text-zinc-100">
        <GameHeader
          turn={turn}
          turnLimit={view.state.config.victory.turn_limit}
          phase="Replay"
          economy={economy}
          tally={null}
          warnings={0}
          view={view}
          onChanged={refresh}
        />
        <main className="flex flex-1 items-center justify-center">
          <Loading label={`Loading replay for turn ${turn}…`} />
        </main>
      </div>
    );
  }

  if (error !== null || data === null) {
    return (
      <div className="flex h-dvh flex-col bg-zinc-950 text-zinc-100">
        <GameHeader
          turn={turn}
          turnLimit={view.state.config.victory.turn_limit}
          phase="Replay"
          economy={economy}
          tally={null}
          warnings={0}
          view={view}
          onChanged={refresh}
        />
        <main className="flex flex-1 flex-col items-center justify-center gap-4">
          <ErrorBanner message={error ?? "Failed to load replay."} />
          <Link
            to={`/game/${gameId}`}
            className="text-sm text-indigo-400 hover:underline"
          >
            ← Back to game
          </Link>
        </main>
      </div>
    );
  }

  return (
    <ReplayBoard
      gameId={gameId}
      turn={turn}
      preTurnState={data.stateBefore}
      postTurnState={data.replay.state_after}
      events={data.replay.events}
      view={view}
      onRefresh={refresh}
    />
  );
}

// --- animation constants ---

/** Duration of unit slide and fade animations, by playback speed. */
const ANIM_DURATION_MS: Record<1 | 2, number> = { 1: 500, 2: 250 };

const easeInOutCubic = (t: number): number =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

interface AnimData {
  /** World-pixel start and end for each sliding unit. */
  slides: Map<number, { fromPx: { x: number; y: number }; toPx: { x: number; y: number } }>;
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
  const slides = new Map<number, { fromPx: { x: number; y: number }; toPx: { x: number; y: number } }>();
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
          slides.set(unit_id, { fromPx, toPx });
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

  const slideOverrides = new Map<number, { x: number; y: number }>();
  for (const [id, { fromPx, toPx }] of data.slides) {
    slideOverrides.set(id, {
      x: fromPx.x + (toPx.x - fromPx.x) * eased,
      y: fromPx.y + (toPx.y - fromPx.y) * eased,
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

// --- main replay board ---

interface ReplayBoardProps {
  gameId: string;
  turn: number;
  /** State at the start of this turn; null for turn 1 (no previous turn data). */
  preTurnState: GameState | null;
  postTurnState: GameState;
  events: ReplayEvent[];
  view: GameView;
  onRefresh: () => void;
}

function ReplayBoard({
  gameId,
  turn,
  preTurnState,
  postTurnState,
  events,
  view,
  onRefresh,
}: ReplayBoardProps) {
  const navigate = useNavigate();

  const { beats, currentBeat, advance, rewind, jumpTo } = useBeats(events);
  const { playing, speed, play, pause, toggleSpeed } = usePlayback({
    advance,
    currentBeat,
    beatCount: beats.length,
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

    const duration = ANIM_DURATION_MS[speed];
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentBeat]);

  // Board state: pre-beat state while animating, settled post-beat state otherwise.
  const boardState = useMemo((): GameState => {
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
    onStartPlanning: () => void navigate(`/game/${gameId}`),
    gameOver,
  };

  const economy = useMemo(
    () => economyFor(postTurnState, view.your_slot),
    [postTurnState, view.your_slot],
  );

  return (
    <div className="flex h-dvh flex-col bg-zinc-950 text-zinc-100">
      <GameHeader
        turn={turn}
        turnLimit={postTurnState.config.victory.turn_limit}
        phase="Replay"
        economy={economy}
        tally={null}
        warnings={0}
        view={view}
        onChanged={onRefresh}
      />
      <main className="relative flex-1 overflow-hidden">
        <div className="absolute inset-0">
          <Board
            state={boardState}
            yourSlot={view.your_slot}
            replayControls={replayControls}
            replayAnim={replayAnim}
          />
        </div>
      </main>
    </div>
  );
}
