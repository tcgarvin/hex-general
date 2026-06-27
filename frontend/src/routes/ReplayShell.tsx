import { useMemo } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router";

import { Board } from "@/board/Board";
import type { ReplayControlsProps } from "@/board/panels/ReplayControlPanel";
import { ErrorBanner, Loading } from "@/components/ui";
import { GameProvider } from "@/game/GameProvider";
import { useGame } from "@/game/useGame";
import { economyFor } from "@/planning/economy";
import { useBeats } from "@/replay/useBeats";
import { usePlayback } from "@/replay/usePlayback";
import { useReplay } from "@/replay/useReplay";
import type { GameState, GameView } from "@/lib/api/types";

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
      postTurnState={data.replay.state_after}
      events={data.replay.events}
      view={view}
      onRefresh={refresh}
    />
  );
}

interface ReplayBoardProps {
  gameId: string;
  turn: number;
  postTurnState: GameState;
  events: { [key: string]: unknown }[];
  view: GameView;
  onRefresh: () => void;
}

function ReplayBoard({
  gameId,
  turn,
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
            state={postTurnState}
            yourSlot={view.your_slot}
            replayControls={replayControls}
          />
        </div>
      </main>
    </div>
  );
}
