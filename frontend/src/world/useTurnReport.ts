// Fetch the viewer's fogged replay of one resolved world turn (W6 slice 3).

import { useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";

export type WorldReplay = components["schemas"]["WorldReplay"];

export type ReportLoad =
  | { status: "none" }
  | { status: "loading"; turn: number }
  | { status: "error"; turn: number; message: string }
  | { status: "ready"; turn: number; replay: WorldReplay };

/** The replay of resolved turn `turn`; `null` asks for nothing (no turn has resolved). */
export function useTurnReport(gameId: string, turn: number | null): ReportLoad {
  const [load, setLoad] = useState<ReportLoad>({ status: "none" });

  // Drop a stale turn's result as soon as the turn changes (React's "adjust state
  // when a prop changes" pattern); the effect only owns the fetch.
  const current = turn === null ? null : turn;
  const loadedTurn = load.status === "none" ? null : load.turn;
  if (current !== loadedTurn) {
    setLoad(current === null ? { status: "none" } : { status: "loading", turn: current });
  }

  useEffect(() => {
    if (turn === null) return;
    let cancelled = false;
    api
      .GET("/worlds/{game_id}/turns/{turn}", { params: { path: { game_id: gameId, turn } } })
      .then(({ data, error }) => {
        if (cancelled) return;
        if (error) setLoad({ status: "error", turn, message: describeApiError(error) });
        else setLoad({ status: "ready", turn, replay: data });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ status: "error", turn, message: describeApiError(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [gameId, turn]);

  return load;
}
