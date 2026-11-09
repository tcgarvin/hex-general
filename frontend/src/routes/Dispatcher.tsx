import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router";

import { Button, Centered, ErrorBanner, Loading } from "@/components/ui";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import {
  DEFAULT_BOT_POLICY,
  DEFAULT_SCENARIO,
  type ActiveGame,
} from "@/lib/api/types";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "active"; game: ActiveGame }
  | { status: "start" };

/**
 * Post-login dispatcher (journeys J1/J4). Asks the API for the caller's active
 * game; if present, redirects straight into it, otherwise shows the start screen
 * (create a new game, or paste an invite to join one).
 */
export function Dispatcher() {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/games/active")
      .then(({ data, error }) => {
        if (cancelled) {
          return;
        }
        if (error) {
          setState({ status: "error", message: describeApiError(error) });
        } else if (data.active_game !== null) {
          setState({ status: "active", game: data.active_game });
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
    const path = state.game.kind === "world" ? "world" : "game";
    return <Navigate to={`/${path}/${state.game.game_id}`} replace />;
  }
  return <StartScreen />;
}

/**
 * Hub screen: three ways into a game. "Start Game" creates a human match here
 * (nothing to configure — one scenario) and drops into its lobby/invite screen;
 * "Play vs Bot" and "Join Game" route to their own spokes to collect details.
 */
function StartScreen() {
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function startHumanGame() {
    setCreating(true);
    setError(null);
    const { data, error: apiError } = await api.POST("/games", {
      body: {
        scenario: DEFAULT_SCENARIO,
        vs_bot: false,
        bot_policy: DEFAULT_BOT_POLICY,
      },
    });
    if (apiError) {
      setError(describeApiError(apiError));
      setCreating(false);
      return;
    }
    navigate(`/game/${data.game_id}`);
  }

  return (
    <Centered fill>
      <h1 className="text-2xl font-semibold">Start a match</h1>

      <div className="flex w-full max-w-sm flex-col gap-3 rounded-lg border border-zinc-800 p-5">
        <Button onClick={() => void startHumanGame()} disabled={creating}>
          {creating ? "Creating…" : "Start Game"}
        </Button>
        <Button
          variant="ghost"
          onClick={() => navigate("/play/bot")}
          disabled={creating}
        >
          Play vs Bot
        </Button>
        <Button
          variant="ghost"
          onClick={() => navigate("/join")}
          disabled={creating}
        >
          Join Game
        </Button>
      </div>

      <WorldList />

      {error !== null && <ErrorBanner message={error} />}

      <Link to="/account" className="text-sm text-indigo-400 hover:underline">
        Account & game history
      </Link>
    </Centered>
  );
}

type WorldSummary = components["schemas"]["WorldSummary"];

/**
 * The running worlds (W6): join one to take its first open slot. Shown under the
 * match buttons until the 2-player flow is retired at the cutover.
 */
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
