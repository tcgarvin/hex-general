// Loading a world: the static map once, then the player's fogged view on a poll.
//
// The map never changes for the life of a world (the API serves it as
// `immutable`), so it is decoded once per page; the view polls (roadmap
// decision 8: polling, no Realtime): every 10 s, and every 2 s once the turn is
// due so a resolving turn shows up promptly.
//
// A 403 or 404 means the world is gone or the caller is no longer in it (a tab
// left open on a deleted world): that is `gone`, not a transient error, and the
// screen stops offering the board.

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";

import { decodeWorldMap, type WorldMap } from "./worldMap";

export type WorldView = components["schemas"]["WorldView"];

const POLL_INTERVAL_MS = 10_000;
const DUE_POLL_INTERVAL_MS = 2_000;

export type WorldLoad =
  | { status: "loading" }
  | { status: "error"; message: string }
  /** `missing`: the world no longer exists (404); `not_player`: the caller holds no seat in it (403). */
  | { status: "gone"; reason: GoneReason }
  | { status: "ready"; map: WorldMap; view: WorldView; refresh: () => void; error: string | null };

/** How long to wait before the next poll: often once the turn is due, else the normal rate. */
export function pollDelay(nextTickAt: string | null | undefined, now: number): number {
  if (nextTickAt === null || nextTickAt === undefined) return POLL_INTERVAL_MS;
  const until = Date.parse(nextTickAt) - now;
  return Math.min(POLL_INTERVAL_MS, Math.max(DUE_POLL_INTERVAL_MS, until));
}

export type GoneReason = "missing" | "not_player";

/** A response status that means this world is not ours to view any more. */
const isGone = (status: number) => status === 403 || status === 404;
const goneReason = (status: number): GoneReason => (status === 404 ? "missing" : "not_player");

export function useWorld(gameId: string): WorldLoad {
  const [map, setMap] = useState<WorldMap | null>(null);
  const [view, setView] = useState<WorldView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [gone, setGone] = useState<GoneReason | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/worlds/{game_id}/map", { params: { path: { game_id: gameId } } })
      .then(({ data, error: apiError, response }) => {
        if (cancelled) return;
        if (apiError) {
          if (isGone(response.status)) setGone(goneReason(response.status));
          else setError(describeApiError(apiError));
        } else {
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
    let timer: ReturnType<typeof setTimeout> | null = null;
    let nextTickAt: string | null = null;
    async function poll() {
      try {
        const { data, error: apiError, response } = await api.GET("/worlds/{game_id}", {
          params: { path: { game_id: gameId } },
        });
        if (cancelled) return;
        if (apiError) {
          if (isGone(response.status)) {
            setGone(goneReason(response.status));
            return; // nothing more to poll for
          }
          setError(describeApiError(apiError));
        } else {
          setError(null);
          setView(data);
          nextTickAt = data.next_tick_at ?? null;
        }
      } catch (err) {
        if (!cancelled) setError(describeApiError(err));
      }
      if (!cancelled) timer = setTimeout(() => void poll(), pollDelay(nextTickAt, Date.now()));
    }
    void poll();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [gameId, nonce]);

  if (gone !== null) return { status: "gone", reason: gone };
  if (map !== null && view !== null) return { status: "ready", map, view, refresh, error };
  if (error !== null) return { status: "error", message: error };
  return { status: "loading" };
}
