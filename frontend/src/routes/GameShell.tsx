import { useMemo, useState } from "react";
import { Navigate, useParams } from "react-router";

import { Board } from "@/board/Board";
import { Button, ErrorBanner } from "@/components/ui";
import { GameProvider } from "@/game/GameProvider";
import { useGame } from "@/game/useGame";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import { SLOTS, type GameView } from "@/lib/api/types";
import { economyFor } from "@/planning/economy";
import { usePlanning } from "@/planning/usePlanning";
import { orderTally, planAdvisory } from "@/planning/warnings";

import { GameHeader, type Phase } from "./GameHeader";

/**
 * Persistent game shell (journey J3). Per UI-SPEC §2 the game screen is a thin
 * React header over a full-bleed board surface. The planning chrome (roster,
 * selected-unit detail, order palette, lock) is migrating into the Pixi scene;
 * until then it lives in a docked React overlay — functional, soon-to-be-Pixi.
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

/** Map the lifecycle (UI-SPEC §3) to the header phase label. */
function phaseOf(view: GameView): Phase {
  if (view.victory.reason !== "ongoing") return "Game over";
  if (view.status === "lobby") return "Lobby";
  if (view.locked[view.your_slot] === true) return "Locked";
  return "Planning";
}

function Dashboard() {
  const { view, refresh } = useGame();

  const planning = usePlanning(view.state, view.your_slot, view.turn);
  const isPlanning =
    view.status === "active" &&
    view.victory.reason === "ongoing" &&
    view.locked[view.your_slot] !== true;

  const economy = useMemo(
    () => economyFor(view.state, view.your_slot),
    [view.state, view.your_slot],
  );
  const ownUnits = useMemo(
    () => (view.state.units ?? []).filter((u) => u.owner === view.your_slot),
    [view.state.units, view.your_slot],
  );
  const tally = useMemo(
    () => orderTally(ownUnits, planning.stagedByUnit),
    [ownUnits, planning.stagedByUnit],
  );
  // Advisory ZOC / traffic predictions (SPEC §16.1) — only while planning.
  const advisory = useMemo(
    () =>
      isPlanning
        ? planAdvisory(view.state.units ?? [], view.your_slot, planning.stagedByUnit)
        : null,
    [isPlanning, view.state.units, view.your_slot, planning.stagedByUnit],
  );

  // Lock submission (UI-SPEC §11). The lock control now lives in the Pixi bottom
  // dock; this owns the async POST + its in-flight / error state and hands them to
  // the board, which renders them into `LockPanel`.
  const [locking, setLocking] = useState(false);
  const [lockError, setLockError] = useState<string | null>(null);
  async function lockOrders() {
    setLocking(true);
    setLockError(null);
    const { error: apiError } = await api.POST("/games/{game_id}/orders", {
      params: { path: { game_id: view.game_id } },
      body: { orders: planning.orders, recruitments: planning.recruitments },
    });
    setLocking(false);
    if (apiError) {
      setLockError(describeApiError(apiError));
      return;
    }
    refresh();
  }

  return (
    <div className="flex h-dvh flex-col bg-zinc-950 text-zinc-100">
      <GameHeader
        turn={view.turn}
        turnLimit={view.state.config.victory.turn_limit}
        phase={phaseOf(view)}
        economy={economy}
        tally={isPlanning ? tally : null}
        warnings={advisory?.count ?? 0}
      />

      <main className="relative flex-1 overflow-hidden">
        {view.status === "lobby" ? (
          <Lobby view={view} />
        ) : (
          <>
            <div className="absolute inset-0">
              <Board
                state={view.state}
                yourSlot={view.your_slot}
                planning={isPlanning ? planning : undefined}
                advisory={advisory ?? undefined}
                lock={
                  isPlanning
                    ? {
                        ordered: tally.ordered,
                        total: tally.total,
                        idle: tally.idleUnitIds.length,
                        submitting: locking,
                        error: lockError,
                        onLock: () => void lockOrders(),
                      }
                    : undefined
                }
              />
            </div>
            <PlanningDock
              view={view}
              hasPlanning={isPlanning}
              onChanged={refresh}
            />
          </>
        )}
      </main>
    </div>
  );
}

/** Lobby state: waiting for an opponent + the share link (UI-SPEC §3). */
function Lobby({ view }: { view: GameView }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 p-8">
      <p className="text-sm text-zinc-400">
        Waiting for an opponent to join. Share the invite below.
      </p>
      {view.invite_token !== null && <ShareInvite view={view} />}
    </div>
  );
}

/**
 * Right-docked status chrome. The planning controls (selected-unit detail, order
 * palette, lock) have moved into the Pixi bottom dock (UI-SPEC §6/§8/§11); what
 * remains here is the lifecycle status, per-slot presence, and the resign action.
 */
function PlanningDock({
  view,
  hasPlanning,
  onChanged,
}: {
  view: GameView;
  hasPlanning: boolean;
  onChanged: () => void;
}) {
  return (
    <div className="absolute right-3 top-3 flex max-h-[calc(100%-1.5rem)] w-64 flex-col gap-3 overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-900/95 p-3 text-sm shadow-xl backdrop-blur">
      <StatusLine view={view} hasPlanning={hasPlanning} />

      <Slots view={view} />

      {view.status === "active" && view.victory.reason === "ongoing" && (
        <ResignControl view={view} onResigned={onChanged} />
      )}
    </div>
  );
}

/** The headline lifecycle line at the top of the dock. */
function StatusLine({ view, hasPlanning }: { view: GameView; hasPlanning: boolean }) {
  if (view.victory.reason !== "ongoing") {
    const outcome =
      view.victory.winner === null
        ? "Draw."
        : view.victory.winner === view.your_slot
          ? "You win!"
          : "You lose.";
    return (
      <p className="rounded-md border border-indigo-700 bg-indigo-950/40 px-3 py-2 text-indigo-100">
        Game over — {outcome} ({view.victory.reason.replace("_", " ")})
      </p>
    );
  }
  if (!hasPlanning) {
    return (
      <p className="rounded-md border border-zinc-700 bg-zinc-800/60 px-3 py-2 text-zinc-300">
        Locked in — waiting on the other player…
      </p>
    );
  }
  return null;
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
    <div className="flex flex-col gap-2 rounded-md border border-red-900 bg-red-950/30 p-2 text-xs text-red-300">
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

/** Per-slot presence + lock state. */
function Slots({ view }: { view: GameView }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      {SLOTS.map((slot) => {
        const present = view.slots_present.includes(slot);
        const locked = view.locked[slot] === true;
        return (
          <div key={slot} className="rounded-md border border-zinc-800 p-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="font-medium capitalize">{slot}</span>
              {slot === view.your_slot && (
                <span className="text-[10px] text-indigo-400">you</span>
              )}
            </div>
            <p className="mt-0.5 text-zinc-400">
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
    <div className="flex w-full max-w-md flex-col gap-2 rounded-lg border border-zinc-800 p-4">
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
