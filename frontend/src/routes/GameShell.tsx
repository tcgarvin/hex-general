import { useState } from "react";
import { Link, Navigate, useParams } from "react-router";

import { useAuth } from "@/auth/useAuth";
import { Board } from "@/board/Board";
import { Button, ErrorBanner } from "@/components/ui";
import { GameProvider } from "@/game/GameProvider";
import { useGame } from "@/game/useGame";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import { SLOTS, type GameView } from "@/lib/api/types";
import { usePlanning, type Planning } from "@/planning/usePlanning";

/**
 * Persistent game shell (journey J3). The polling GameProvider lives here and every
 * later phase mounts inside: Phase 7 renders a status dashboard (turn, slots, lock
 * state, share link, your-move/waiting banner), Phase 8 the board, Phase 9 planning.
 */
export function GameShell() {
  const { id } = useParams<{ id: string }>();
  if (id === undefined) {
    return <Navigate to="/" replace />;
  }
  return (
    <GameProvider gameId={id}>
      <Dashboard />
    </GameProvider>
  );
}

function Dashboard() {
  const { view, refresh } = useGame();
  const { signOut } = useAuth();

  const planning = usePlanning(view.state, view.your_slot, view.turn);
  const isPlanning =
    view.status === "active" &&
    view.victory.reason === "ongoing" &&
    view.locked[view.your_slot] !== true;

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-5xl flex-col gap-6 p-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Game {view.game_id.slice(0, 8)}</h1>
          <p className="text-sm text-zinc-400">
            Turn {view.turn} · {view.status} · you are{" "}
            <span className="font-medium text-zinc-200">{view.your_slot}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to="/account"
            className="rounded-md border border-zinc-700 px-3 py-2 text-sm font-medium text-zinc-200 hover:bg-zinc-800"
          >
            Account
          </Link>
          <Button variant="ghost" onClick={() => void signOut()}>
            Sign out
          </Button>
        </div>
      </header>

      <StatusBanner
        view={view}
        planning={isPlanning ? planning : null}
        onLocked={refresh}
      />

      {view.status === "active" && view.victory.reason === "ongoing" && (
        <ResignControl view={view} onResigned={refresh} />
      )}

      {view.status !== "lobby" && (
        <div className="h-[60vh] min-h-80">
          <Board
            state={view.state}
            yourSlot={view.your_slot}
            planning={isPlanning ? planning : undefined}
          />
        </div>
      )}

      <Slots view={view} />
      {view.invite_token !== null && <ShareInvite view={view} />}

      <p className="mt-auto text-xs text-zinc-600">
        Movement planning (Phase 9, Slice 1): select a unit, click within its
        range to lay an exact path, then lock. Barrage/Rest/Hold, recruitment, and
        replay arrive in later slices; the view refreshes live as the other player
        acts.
      </p>
    </div>
  );
}

/** The headline state: lobby / your-move / waiting / decided. */
function StatusBanner({
  view,
  planning,
  onLocked,
}: {
  view: GameView;
  planning: Planning | null;
  onLocked: () => void;
}) {
  if (view.victory.reason !== "ongoing") {
    const outcome =
      view.victory.winner === null
        ? "Draw."
        : view.victory.winner === view.your_slot
          ? "You win!"
          : "You lose.";
    return (
      <Panel tone="accent">
        Game over — {outcome} ({view.victory.reason.replace("_", " ")})
      </Panel>
    );
  }

  if (view.status === "lobby") {
    return <Panel>Waiting for an opponent to join. Share the invite below.</Panel>;
  }

  const youLocked = view.locked[view.your_slot] === true;
  if (!youLocked && planning !== null) {
    return <PlanningPanel view={view} planning={planning} onLocked={onLocked} />;
  }
  return <Panel>Orders locked. Waiting on the other player…</Panel>;
}

/** Format a staged move for the review list, e.g. "armor → (12, 4) · 3 hexes". */
function describeMove({ unit, path }: Planning["staged"][number]): string {
  const dest = path[path.length - 1];
  const hexes = path.length === 1 ? "1 hex" : `${path.length} hexes`;
  return `${unit.type} → (${dest.q}, ${dest.r}) · ${hexes}`;
}

/** Your-move planning panel: staged orders + lock (Phase 9, Slice 1). */
function PlanningPanel({
  view,
  planning,
  onLocked,
}: {
  view: GameView;
  planning: Planning;
  onLocked: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const count = planning.staged.length;

  async function lockTurn() {
    setSubmitting(true);
    setError(null);
    const { error: apiError } = await api.POST("/games/{game_id}/orders", {
      params: { path: { game_id: view.game_id } },
      body: { orders: planning.orders, recruitments: [] },
    });
    setSubmitting(false);
    if (apiError) {
      setError(describeApiError(apiError));
      return;
    }
    onLocked();
  }

  return (
    <Panel tone="accent">
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <span>
            {count === 0
              ? "It's your move. Select a unit, then click within its range to plan a move."
              : `It's your move. ${count} unit${count === 1 ? "" : "s"} ordered.`}
          </span>
          {count > 0 && (
            <button
              onClick={planning.clearAll}
              className="rounded-md border border-indigo-700 px-2 py-1 text-xs font-medium text-indigo-200 hover:bg-indigo-900/40"
            >
              Clear all
            </button>
          )}
        </div>

        {count > 0 && (
          <ul className="flex flex-col gap-1 text-sm">
            {planning.staged.map((move) => (
              <li
                key={move.unit.id}
                className="flex items-center justify-between rounded-md bg-indigo-950/50 px-3 py-1.5"
              >
                <span className="capitalize">{describeMove(move)}</span>
                <button
                  onClick={() => planning.clearOrder(move.unit.id)}
                  aria-label={`Remove order for ${move.unit.type}`}
                  className="text-indigo-300 hover:text-white"
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}

        <Button onClick={() => void lockTurn()} disabled={submitting}>
          {submitting
            ? "Locking…"
            : count === 0
              ? "Lock turn (no orders)"
              : `Lock ${count} order${count === 1 ? "" : "s"}`}
        </Button>
        {error !== null && <ErrorBanner message={error} />}
      </div>
    </Panel>
  );
}

/** Resign action with an inline two-step confirm (forfeit is irreversible). */
function ResignControl({
  view,
  onResigned,
}: {
  view: GameView;
  onResigned: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function resign() {
    setSubmitting(true);
    setError(null);
    const { error: apiError } = await api.POST("/games/{game_id}/resign", {
      params: { path: { game_id: view.game_id } },
    });
    setSubmitting(false);
    if (apiError) {
      setError(describeApiError(apiError));
      return;
    }
    setConfirming(false);
    onResigned();
  }

  if (!confirming) {
    return (
      <div className="flex items-center justify-end">
        <button
          onClick={() => setConfirming(true)}
          className="rounded-md border border-red-900 px-3 py-1.5 text-sm font-medium text-red-300 transition-colors hover:bg-red-950/50"
        >
          Resign
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex items-center gap-2 text-sm text-red-300">
        <span>Confirm forfeit? This ends the game.</span>
        <button
          onClick={() => void resign()}
          disabled={submitting}
          className="rounded-md bg-red-700 px-3 py-1.5 font-medium text-white transition-colors hover:bg-red-600 disabled:opacity-50"
        >
          {submitting ? "Resigning…" : "Yes, resign"}
        </button>
        <Button
          variant="ghost"
          onClick={() => setConfirming(false)}
          disabled={submitting}
        >
          Cancel
        </Button>
      </div>
      {error !== null && <ErrorBanner message={error} />}
    </div>
  );
}

/** Per-slot presence + lock state. */
function Slots({ view }: { view: GameView }) {
  return (
    <div className="grid grid-cols-2 gap-3">
      {SLOTS.map((slot) => {
        const present = view.slots_present.includes(slot);
        const locked = view.locked[slot] === true;
        return (
          <div
            key={slot}
            className="rounded-lg border border-zinc-800 p-4 text-sm"
          >
            <div className="flex items-center justify-between">
              <span className="font-medium capitalize">{slot}</span>
              {slot === view.your_slot && (
                <span className="text-xs text-indigo-400">you</span>
              )}
            </div>
            <p className="mt-1 text-zinc-400">
              {present ? (locked ? "locked in" : "planning") : "open slot"}
            </p>
          </div>
        );
      })}
    </div>
  );
}

/** Shareable invite link, shown while a slot is still open. */
function ShareInvite({ view }: { view: GameView }) {
  const [copied, setCopied] = useState(false);
  const link = `${window.location.origin}/join/${view.game_id}#invite=${view.invite_token}`;

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-zinc-800 p-4">
      <span className="text-sm font-medium text-zinc-300">Invite link</span>
      <code className="overflow-x-auto rounded bg-zinc-900 px-3 py-2 text-xs text-zinc-400">
        {link}
      </code>
      <Button
        variant="ghost"
        onClick={() => {
          navigator.clipboard.writeText(link).then(
            () => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            },
            () => setCopied(false),
          );
        }}
      >
        {copied ? "Copied!" : "Copy link"}
      </Button>
    </div>
  );
}

/** Simple highlighted panel. */
function Panel({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "accent";
}) {
  const styles =
    tone === "accent"
      ? "border-indigo-700 bg-indigo-950/40 text-indigo-100"
      : "border-zinc-800 bg-zinc-900/40 text-zinc-300";
  return (
    <div className={`rounded-lg border p-4 text-sm ${styles}`}>{children}</div>
  );
}
