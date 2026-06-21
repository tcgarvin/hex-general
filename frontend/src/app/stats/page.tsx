"use client";

import { useEffect, useState } from "react";

import { API_URL } from "@/lib/api";

type Stats = { active_users: number; game_count: number };

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; activeUsers: number; gameCount: number };

/**
 * Live stats page.
 *
 * Fetches from the Cloud Run API in the browser, which in turn reads the count
 * from Supabase. This single page exercises the whole stack:
 * browser -> Cloud Run (FastAPI) -> Supabase (Postgres).
 */
export default function StatsPage() {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    fetch(`${API_URL}/stats`)
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`API responded ${res.status}`);
        }
        return (await res.json()) as Stats;
      })
      .then((data) => {
        if (!cancelled) {
          setState({
            status: "ready",
            activeUsers: data.active_users,
            gameCount: data.game_count,
          });
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            status: "error",
            message: err instanceof Error ? err.message : "Unknown error",
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-1 flex-col items-center justify-center bg-zinc-50 font-sans dark:bg-black">
      <main className="flex w-full max-w-md flex-col items-center gap-12 px-8 py-24 text-center">
        {state.status === "error" ? (
          <p className="max-w-xs text-sm text-red-600 dark:text-red-400">
            Couldn’t reach the API: {state.message}
          </p>
        ) : (
          <div className="flex w-full justify-center gap-16">
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

        <p className="text-xs leading-5 text-zinc-400 dark:text-zinc-600">
          browser → Cloud Run → Supabase
        </p>
      </main>
    </div>
  );
}

/** A single labelled stat; renders a placeholder until its value loads. */
function Stat({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="flex flex-col items-center gap-4">
      <h2 className="text-sm font-medium uppercase tracking-widest text-zinc-500 dark:text-zinc-400">
        {label}
      </h2>
      {value === undefined ? (
        <p className="text-6xl font-semibold text-zinc-300 dark:text-zinc-700">…</p>
      ) : (
        <p className="text-7xl font-semibold tabular-nums text-black dark:text-zinc-50">
          {value}
        </p>
      )}
    </div>
  );
}
