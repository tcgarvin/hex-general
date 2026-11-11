import { useEffect, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; activeUsers: number; gameCount: number };

/**
 * Public stats page. Exercises the whole stack (browser → Cloud Run → Supabase)
 * and is the target of the `verify-stack` smoke test, so it stays a real route that
 * fetches an absolute API URL via the typed client. It is public, so it carries the
 * site header itself (with a "Sign in" link when there is no session).
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
    <div className="flex min-h-dvh flex-col bg-[#1a1d22] text-ivory-100">
      <AppHeader homeTo="/" />
      <main className="flex flex-1 flex-col items-center justify-center gap-10 px-4 py-10 text-center">
        {state.status === "error" ? (
          <p className="fhq-note max-w-xs border-invalid! px-4 py-2 text-sm text-invalid">
            Couldn’t reach the API: {state.message}
          </p>
        ) : (
          // The API's own definitions: active_users counts browser sessions seen
          // in the last 5 minutes (api/app/stats.py ACTIVE_WINDOW), and game_count
          // is every row of the games table, finished worlds included.
          <div className="flex w-full max-w-lg flex-col gap-6 sm:flex-row">
            <Stat
              label="Sessions online"
              caption="Browser sessions seen in the last 5 minutes"
              value={state.status === "ready" ? state.activeUsers : undefined}
            />
            <Stat
              label="Games created"
              caption="Every world ever created, finished ones included"
              value={state.status === "ready" ? state.gameCount : undefined}
            />
          </div>
        )}
        <p className="font-data text-xs text-ivory-400">browser → Cloud Run → Supabase</p>
      </main>
    </div>
  );
}

/** One figure on a parchment card, with a caption saying what it counts. */
function Stat({
  label,
  caption,
  value,
}: {
  label: string;
  caption: string;
  value: number | undefined;
}) {
  return (
    <section className="fhq-frame fhq-frame-lg flex flex-1 flex-col items-center gap-2 p-4">
      <h2 className="fhq-plate font-display text-sm uppercase tracking-wide">{label}</h2>
      <p className="font-data text-6xl tabular-nums">{value ?? "…"}</p>
      <p className="text-xs text-wood-700">{caption}</p>
    </section>
  );
}
