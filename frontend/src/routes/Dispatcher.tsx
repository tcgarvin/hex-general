import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router";

import { Button, Centered, ErrorBanner, Loading } from "@/components/ui";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
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
    return <Navigate to={`/game/${state.game.game_id}`} replace />;
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

      {error !== null && <ErrorBanner message={error} />}

      <Link to="/account" className="text-sm text-indigo-400 hover:underline">
        Account & game history
      </Link>
    </Centered>
  );
}
