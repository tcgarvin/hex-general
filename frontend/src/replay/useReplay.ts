import { useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { GameState, TurnReplay } from "@/lib/api/types";

export interface ReplayData {
  replay: TurnReplay;
  /** State at the start of the turn (state_after of turn N-1); null for turn 0
   * (the first resolved turn — its pre-turn state is the scenario setup, which
   * the turns API doesn't expose). */
  stateBefore: GameState | null;
}

/**
 * Fetches a turn's replay (and the previous turn's end state). `turn` is
 * `number | null` because the unified game surface calls this unconditionally to
 * keep hook order stable: `null` means "not in replay mode" — no fetch, and the
 * data is cleared so a stale replay never leaks into the next one.
 */
export function useReplay(gameId: string, turn: number | null) {
  const [data, setData] = useState<ReplayData | null>(null);
  const [loading, setLoading] = useState(turn !== null);
  const [error, setError] = useState<string | null>(null);

  // Clear the previous turn's result synchronously the moment `turn` changes, so
  // a stale replay never flashes while the next one loads (or after leaving
  // replay mode). React docs' "adjusting state when a prop changes" pattern; the
  // effect below owns only the async fetch.
  const [prevTurn, setPrevTurn] = useState(turn);
  if (prevTurn !== turn) {
    setPrevTurn(turn);
    setData(null);
    setError(null);
    setLoading(turn !== null);
  }

  useEffect(() => {
    if (turn === null) return;

    let cancelled = false;
    const requestedTurn = turn;

    async function fetch() {
      const replayRequest = api.GET("/games/{game_id}/turns/{turn}", {
        params: { path: { game_id: gameId, turn: requestedTurn } },
      });
      // State at the start of the turn is the previous turn's state_after; turns
      // are 0-indexed, so only turn 0 has no predecessor. Fetch both concurrently
      // to avoid a serial round-trip.
      const prevRequest =
        requestedTurn > 0
          ? api.GET("/games/{game_id}/turns/{turn}", {
              params: { path: { game_id: gameId, turn: requestedTurn - 1 } },
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
