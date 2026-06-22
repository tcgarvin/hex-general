import { useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; activeUsers: number; gameCount: number };

/**
 * Public stats page. Exercises the whole stack (browser → Cloud Run → Supabase)
 * and is the target of the `verify-stack` smoke test, so it stays a real route that
 * fetches an absolute API URL via the typed client.
 */
export function Stats() {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/stats")
      .then(({ data, error }) => {
        if (cancelled) {
          return;
        }
        if (error) {
          setState({ status: "error", message: describeApiError(error) });
        } else {
          setState({
            status: "ready",
            activeUsers: data.active_users,
            gameCount: data.game_count,
          });
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ status: "error", message: describeApiError(err) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-12 p-8 text-center">
      {state.status === "error" ? (
        <p className="max-w-xs text-sm text-red-400">
          Couldn’t reach the API: {state.message}
        </p>
      ) : (
        <div className="flex justify-center gap-16">
          <Stat
            label="Active users"
            value={state.status === "ready" ? state.activeUsers : undefined}
          />
          <Stat
            label="Games"
            value={state.status === "ready" ? state.gameCount : undefined}
          />
        </div>
      )}
      <p className="text-xs text-zinc-600">browser → Cloud Run → Supabase</p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="flex flex-col items-center gap-4">
      <h2 className="text-sm font-medium uppercase tracking-widest text-zinc-500">
        {label}
      </h2>
      <p className="text-7xl font-semibold tabular-nums">{value ?? "…"}</p>
    </div>
  );
}
