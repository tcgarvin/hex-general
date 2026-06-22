import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";

import { Centered, ErrorBanner, Loading } from "@/components/ui";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { GameView } from "@/lib/api/types";

import { GameContext } from "./GameContext";

/**
 * Polls `GET /games/{id}` and provides the latest view to its subtree.
 *
 * Per roadmap decision 8 the client learns turn progress by polling (no Realtime),
 * so a returning/waiting player sees turn/lock changes within one interval. A turn
 * advance will trigger a replay fetch in Phase 10; for now the live GameView is the
 * demonstrable. Renders a loader until the first view arrives.
 */
const POLL_INTERVAL_MS = 4000;

export function GameProvider({
  gameId,
  children,
}: {
  gameId: string;
  children: React.ReactNode;
}) {
  const [view, setView] = useState<GameView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const { data, error: apiError } = await api.GET("/games/{game_id}", {
          params: { path: { game_id: gameId } },
        });
        if (cancelled) {
          return;
        }
        if (apiError) {
          setError(describeApiError(apiError));
          return;
        }
        setError(null);
        setView(data);
      } catch (err) {
        if (!cancelled) {
          setError(describeApiError(err));
        }
      }
    }

    poll();
    const id = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [gameId, nonce]);

  if (view === null) {
    if (error !== null) {
      return (
        <Centered>
          <ErrorBanner message={error} />
          <Link to="/" className="text-sm text-indigo-400 hover:underline">
            ← Back to dispatcher
          </Link>
        </Centered>
      );
    }
    return <Loading label="Loading game…" />;
  }

  return <GameContext value={{ view, refresh }}>{children}</GameContext>;
}
