import { useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { GameState, TurnReplay } from "@/lib/api/types";

export interface ReplayData {
  replay: TurnReplay;
  /** State at the start of the turn (state_after of turn N-1); null for turn 1. */
  stateBefore: GameState | null;
}

export function useReplay(gameId: string, turn: number) {
  const [data, setData] = useState<ReplayData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetch() {
      const { data: replay, error: replayError } = await api.GET(
        "/games/{game_id}/turns/{turn}",
        { params: { path: { game_id: gameId, turn } } },
      );
      if (cancelled) return;
      if (replayError) {
        setError(describeApiError(replayError));
        setLoading(false);
        return;
      }

      let stateBefore: GameState | null = null;
      if (turn > 1) {
        const { data: prev } = await api.GET("/games/{game_id}/turns/{turn}", {
          params: { path: { game_id: gameId, turn: turn - 1 } },
        });
        if (!cancelled && prev) {
          stateBefore = prev.state_after;
        }
      }

      if (!cancelled) {
        setData({ replay, stateBefore });
        setLoading(false);
      }
    }

    void fetch();
    return () => {
      cancelled = true;
    };
  }, [gameId, turn]);

  return { data, loading, error };
}
