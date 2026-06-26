import { useMemo, useState } from "react";
import { Navigate, useParams } from "react-router";

import { Board } from "@/board/Board";
import { Button } from "@/components/ui";
import { GameProvider } from "@/game/GameProvider";
import { useGame } from "@/game/useGame";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import { type GameView } from "@/lib/api/types";
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
  // The planning surface (roster + bottom dock) is shown for the whole of your
  // active turn — including after you lock — so the panels don't vanish out from
  // under you. `locked` flips them to a read-only, greyed presentation; only
  // `isPlanning` (active, ongoing, *not* locked) permits order edits.
  const active = view.status === "active" && view.victory.reason === "ongoing";
  const locked = view.locked[view.your_slot] === true;

  // Once decided, the board swaps the lock control for a win/lose/draw indicator
  // (there are no more turns to lock). The full outcome lives in the "Game" menu.
  const gameOver =
    view.victory.reason !== "ongoing"
      ? {
          outcome:
            view.victory.winner === null
              ? ("draw" as const)
              : view.victory.winner === view.your_slot
                ? ("win" as const)
                : ("lose" as const),
          reason: view.victory.reason.replace("_", " "),
        }
      : undefined;

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
  // Advisory ZOC / traffic predictions (SPEC §16.1). Computed for the whole active
  // turn so the locked, read-only panels keep showing what your committed plan
  // risks — not just while it's still editable.
  const advisory = useMemo(
    () =>
      active
        ? planAdvisory(view.state.units ?? [], view.your_slot, planning.stagedByUnit)
        : null,
    [active, view.state.units, view.your_slot, planning.stagedByUnit],
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
        tally={active ? tally : null}
        warnings={advisory?.count ?? 0}
        view={view}
        onChanged={refresh}
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
                visible={view.visible}
                planning={active ? planning : undefined}
                locked={locked}
                advisory={advisory ?? undefined}
                lock={
                  active
                    ? {
                        ordered: tally.ordered,
                        total: tally.total,
                        idle: tally.idleUnitIds.length,
                        submitting: locking,
                        error: lockError,
                        locked,
                        onLock: () => void lockOrders(),
                      }
                    : undefined
                }
                gameOver={gameOver}
              />
            </div>
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
