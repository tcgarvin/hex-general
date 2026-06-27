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
      const replayRequest = api.GET("/games/{game_id}/turns/{turn}", {
        params: { path: { game_id: gameId, turn } },
      });
      // State at the start of the turn is the previous turn's state_after; turn 1
      // has no predecessor. Fetch both concurrently to avoid a serial round-trip.
      const prevRequest =
        turn > 1
          ? api.GET("/games/{game_id}/turns/{turn}", {
              params: { path: { game_id: gameId, turn: turn - 1 } },
            })
          : null;

      const { data: replay, error: replayError } = await replayRequest;
      const prevResult = prevRequest ? await prevRequest : null;
      if (cancelled) return;
      if (replayError) {
        setError(describeApiError(replayError));
        setLoading(false);
        return;
      }

      const stateBefore: GameState | null =
        prevResult?.data?.state_after ?? null;

      setData({ replay, stateBefore });
      setLoading(false);
    }

    void fetch();
    return () => {
      cancelled = true;
    };
  }, [gameId, turn]);

  return { data, loading, error };
}
