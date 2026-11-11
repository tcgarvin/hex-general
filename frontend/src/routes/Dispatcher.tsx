import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router";

import { Button, Centered, ErrorBanner, Loading } from "@/components/ui";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";

type ActiveWorld = components["schemas"]["ActiveWorld"];

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "active"; world: ActiveWorld }
  | { status: "start" };

/**
 * Post-login dispatcher (journeys J1/J4). Asks the API for the caller's active
 * world; if present, redirects straight into its world, otherwise shows the start
 * screen (the list of running worlds to join).
 */
export function Dispatcher() {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/worlds/active")
      .then(({ data, error }) => {
        if (cancelled) {
          return;
        }
        if (error) {
          setState({ status: "error", message: describeApiError(error) });
        } else if (data.active_world !== null) {
          setState({ status: "active", world: data.active_world });
        } else {
          setState({ status: "start" });
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
    return <Loading fill label="Finding your game…" />;
  }
  if (state.status === "error") {
    return (
      <Centered fill>
        <ErrorBanner message={state.message} />
      </Centered>
    );
  }
  if (state.status === "active") {
    return <Navigate to={`/world/${state.world.game_id}`} replace />;
  }
  return <StartScreen />;
}

/** Start screen: pick a running world to join, or head to the account page. */
function StartScreen() {
  return (
    <Centered fill>
      <h1 className="text-2xl font-semibold">Join a world</h1>

      <WorldList />

      <Link to="/account" className="text-sm text-indigo-400 hover:underline">
        Account
      </Link>
    </Centered>
  );
}

type WorldSummary = components["schemas"]["WorldSummary"];

/** The running worlds (W6): join one to take its first open slot. */
function WorldList() {
  const navigate = useNavigate();
  const [worlds, setWorlds] = useState<WorldSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [joining, setJoining] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/worlds")
      .then(({ data, error: apiError }) => {
        if (cancelled) return;
        if (apiError) setError(describeApiError(apiError));
        else setWorlds(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(describeApiError(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function join(gameId: string) {
    setJoining(gameId);
    setError(null);
    const { error: apiError } = await api.POST("/worlds/{game_id}/join", {
      params: { path: { game_id: gameId } },
    });
    if (apiError) {
      setError(describeApiError(apiError));
      setJoining(null);
      return;
    }
    navigate(`/world/${gameId}`);
  }

  if (worlds === null && error === null) return null;
  return (
    <div className="flex w-full max-w-sm flex-col gap-2 rounded-lg border border-zinc-800 p-5">
      <h2 className="text-lg font-semibold">Join a world</h2>
      {worlds !== null && worlds.length === 0 && (
        <p className="text-sm text-zinc-400">No worlds are running.</p>
      )}
      {worlds?.map((w) => (
        <div key={w.game_id} className="flex items-center justify-between gap-3 text-sm">
          <span>
            Turn {w.turn} · {w.players}/{w.slots} players ({w.humans} human)
          </span>
          <Button onClick={() => void join(w.game_id)} disabled={joining !== null}>
            {joining === w.game_id ? "Joining…" : "Join"}
          </Button>
        </div>
      ))}
      {error !== null && <ErrorBanner message={error} />}
    </div>
  );
}
