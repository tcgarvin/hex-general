// Loading a world: the static map once, then the player's fogged view on a poll.
//
// The map never changes for the life of a world (the API serves it as
// `immutable`), so it is decoded once per page; the view polls like the 2-player
// game does (roadmap decision 8: polling, no Realtime).

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";

import { decodeWorldMap, type WorldMap } from "./worldMap";

export type WorldView = components["schemas"]["WorldView"];

const POLL_INTERVAL_MS = 10_000;

export type WorldLoad =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; map: WorldMap; view: WorldView; refresh: () => void; error: string | null };

export function useWorld(gameId: string): WorldLoad {
  const [map, setMap] = useState<WorldMap | null>(null);
  const [view, setView] = useState<WorldView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/worlds/{game_id}/map", { params: { path: { game_id: gameId } } })
      .then(({ data, error: apiError }) => {
        if (cancelled) return;
        if (apiError) setError(describeApiError(apiError));
        else {
          setMap(decodeWorldMap(data));
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(describeApiError(err));
      });
    return () => {
      cancelled = true;
    };
  }, [gameId]);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const { data, error: apiError } = await api.GET("/worlds/{game_id}", {
          params: { path: { game_id: gameId } },
        });
        if (cancelled) return;
        if (apiError) {
          setError(describeApiError(apiError));
          return;
        }
        setError(null);
        setView(data);
      } catch (err) {
        if (!cancelled) setError(describeApiError(err));
      }
    }
    void poll();
    const id = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [gameId, nonce]);

  if (map !== null && view !== null) return { status: "ready", map, view, refresh, error };
  if (error !== null) return { status: "error", message: error };
  return { status: "loading" };
}
