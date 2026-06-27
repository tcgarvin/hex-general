import { useMemo } from "react";
import { Link, Navigate, useParams } from "react-router";

import { Centered, ErrorBanner, Loading } from "@/components/ui";
import { GameProvider } from "@/game/GameProvider";
import { useGame } from "@/game/useGame";
import { economyFor } from "@/planning/economy";
import { useReplay } from "@/replay/useReplay";

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
        <Centered>
          <p className="text-zinc-400">
            Turn {turn} replay — {data.replay.events.length} events
          </p>
          <p className="text-xs text-zinc-600">
            Beat engine and board animation coming in Slice 2–3.
          </p>
          <Link
            to={`/game/${gameId}`}
            className="mt-4 text-sm text-indigo-400 hover:underline"
          >
            ← Back to game
          </Link>
        </Centered>
      </main>
    </div>
  );
}
