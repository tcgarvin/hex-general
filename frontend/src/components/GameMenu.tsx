import { useState } from "react";
import { Link } from "react-router";

import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import { SLOTS, type GameView } from "@/lib/api/types";

import { Button, ErrorBanner } from "./ui";

/**
 * The "Game" dropdown in the game-screen header — the React home for everything
 * about the match that isn't the board itself. Today it holds the per-slot
 * presence/lock readout and the resign action; it's the natural place to grow
 * chat and richer concede options (kdice-style flagging) later.
 *
 * The board (Pixi) owns planning + the lock control; this owns out-of-band game
 * actions. Lifecycle status (turn/phase/outcome) lives in the header and the
 * board's lock panel, so this menu only surfaces a headline when the game is over.
 */
export function GameMenu({
  view,
  onChanged,
}: {
  view: GameView;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);

  const over = view.victory.reason !== "ongoing";
  const canResign = view.status === "active" && !over;
  const lastResolvedTurn = view.turn - 1;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-sm border border-brass-600/50 bg-ink-900/30 px-3 py-1.5 font-label text-sm font-medium text-ivory-100 hover:bg-ink-900/60"
      >
        <span>Game</span>
        <span className="text-brass-400">▾</span>
      </button>
      {open && (
        <>
          <button
            className="fixed inset-0 z-10 cursor-default"
            aria-hidden
            tabIndex={-1}
            onClick={() => setOpen(false)}
          />
          <div className="fhq-wood-deep absolute right-0 z-20 mt-1 flex w-72 flex-col gap-3 rounded-sm border border-brass-600/50 p-3 text-sm shadow-lg shadow-black/50">
            {over && <Outcome view={view} />}

            <Slots view={view} />

            {lastResolvedTurn >= 1 && (
              <Link
                to={`/game/${view.game_id}?turn=${lastResolvedTurn}`}
                onClick={() => setOpen(false)}
                className="self-start text-xs font-medium text-brass-400 hover:text-parchment-200"
              >
                Watch last replay (turn {lastResolvedTurn})
              </Link>
            )}

            {canResign && <ResignControl view={view} onResigned={onChanged} />}
          </div>
        </>
      )}
    </div>
  );
}

/** Game-over headline: who won and how (UI-SPEC §3). */
function Outcome({ view }: { view: GameView }) {
  const result =
    view.victory.winner === null
      ? "Draw."
      : view.victory.winner === view.your_slot
        ? "You win!"
        : "You lose.";
  return (
    <p className="rounded-sm border border-brass-600/60 bg-ink-900/50 px-3 py-2 font-label text-ivory-100">
      Game over — {result} ({view.victory.reason.replace("_", " ")})
    </p>
  );
}

/** Per-slot presence + lock state — who's still planning (UI-SPEC §3). */
function Slots({ view }: { view: GameView }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      {SLOTS.map((slot) => {
        const present = view.slots_present.includes(slot);
        const locked = view.locked[slot] === true;
        return (
          <div
            key={slot}
            className="rounded-sm border border-wood-500/40 bg-ink-900/30 p-2 text-xs"
          >
            <div className="flex items-center justify-between">
              <span className="font-medium capitalize text-ivory-100">{slot}</span>
              {slot === view.your_slot && (
                <span className="font-label text-[10px] uppercase text-brass-400">
                  you
                </span>
              )}
            </div>
            <p className="mt-0.5 text-ivory-400">
              {present ? (locked ? "locked in" : "planning") : "open slot"}
            </p>
          </div>
        );
      })}
    </div>
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
      <button
        onClick={() => setConfirming(true)}
        className="self-start text-xs font-medium text-red-400/80 hover:text-red-300"
      >
        Resign
      </button>
    );
  }

  return (
    <div className="flex flex-col gap-2 rounded-sm border border-invalid/60 bg-ink-900/50 p-2 text-xs text-red-300">
      <span>Confirm forfeit? This ends the game.</span>
      <div className="flex items-center gap-2">
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
