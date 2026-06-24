import { useEffect, useState } from "react";
import { Link } from "react-router";

import { useAuth } from "@/auth/useAuth";
import { Centered, ErrorBanner, Loading } from "@/components/ui";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { GameHistoryEntry, MeView } from "@/lib/api/types";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; me: MeView; history: GameHistoryEntry[] };

/**
 * Account screen (journey J4 extension). Shows the caller's identity (email,
 * dev role, sign-out) and their game history — scenario, slot, outcome, turn,
 * and date — pulled from `GET /games/history`. Finished games still link into
 * the read-only game shell.
 */
export function Account() {
  const { user } = useAuth();
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.GET("/me"), api.GET("/games/history")])
      .then(([me, history]) => {
        if (cancelled) {
          return;
        }
        if (me.error) {
          setState({ status: "error", message: describeApiError(me.error) });
        } else if (history.error) {
          setState({ status: "error", message: describeApiError(history.error) });
        } else {
          setState({ status: "ready", me: me.data, history: history.data });
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

  if (state.status === "loading") {
    return <Loading fill label="Loading your account…" />;
  }
  if (state.status === "error") {
    return (
      <Centered fill>
        <ErrorBanner message={state.message} />
        <Link to="/" className="text-sm text-indigo-400 hover:underline">
          ← Back
        </Link>
      </Centered>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-6">
      <header>
        <h1 className="text-lg font-semibold">Account</h1>
        <p className="text-sm text-zinc-400">
          {user?.email ?? "Signed in"}
          {state.me.is_dev && (
            <span className="ml-2 rounded bg-indigo-950 px-1.5 py-0.5 text-xs text-indigo-300">
              dev
            </span>
          )}
        </p>
      </header>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-medium text-zinc-300">Game history</h2>
        {state.history.length === 0 ? (
          <p className="rounded-lg border border-zinc-800 p-4 text-sm text-zinc-400">
            No games yet. Start one from the home screen.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {state.history.map((entry) => (
              <HistoryRow key={entry.game_id} entry={entry} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** One game-history row, linking into the (read-only when finished) game shell. */
function HistoryRow({ entry }: { entry: GameHistoryEntry }) {
  return (
    <li>
      <Link
        to={`/game/${entry.game_id}`}
        className="flex items-center justify-between rounded-lg border border-zinc-800 p-4 text-sm transition-colors hover:bg-zinc-900/50"
      >
        <div className="flex flex-col gap-0.5">
          <span className="font-medium capitalize">
            {entry.scenario.replace(/_/g, " ")}
          </span>
          <span className="text-xs text-zinc-500">
            you are {entry.your_slot} · turn {entry.turn} ·{" "}
            {formatDate(entry.created_at)}
          </span>
        </div>
        <OutcomeBadge outcome={entry.outcome} />
      </Link>
    </li>
  );
}

/** Color-coded outcome chip (win/loss/draw/in-progress). */
function OutcomeBadge({ outcome }: { outcome: string }) {
  const styles: Record<string, string> = {
    win: "border-emerald-800 bg-emerald-950/50 text-emerald-300",
    loss: "border-red-900 bg-red-950/50 text-red-300",
    draw: "border-zinc-700 bg-zinc-900 text-zinc-300",
    in_progress: "border-indigo-800 bg-indigo-950/50 text-indigo-300",
  };
  const label = outcome === "in_progress" ? "in progress" : outcome;
  const style = styles[outcome] ?? styles.draw;
  return (
    <span
      className={`rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize ${style}`}
    >
      {label}
    </span>
  );
}

/** Format an ISO timestamp as a short local date, falling back to the raw value. */
function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString();
}
