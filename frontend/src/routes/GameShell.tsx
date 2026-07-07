import { useCallback, useMemo, useEffect, useRef, useState } from "react";
import { Navigate, useParams, useSearchParams } from "react-router";

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
import { useReplay } from "@/replay/useReplay";
import { useReplayBoard } from "@/replay/useReplayBoard";

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

  // Replay is a *mode* of this one surface, not a separate route: the `?turn=N`
  // search param selects it (null ⇒ planning). Keeping it in the URL makes the
  // state back/refresh-friendly and gives the "Watch last replay" menu link a
  // target — all without unmounting the board (and its Pixi canvas) beneath it.
  const [searchParams, setSearchParams] = useSearchParams();
  const turnParam = searchParams.get("turn");
  const parsedTurn = turnParam !== null ? parseInt(turnParam, 10) : NaN;
  const replayTurn = Number.isNaN(parsedTurn) || parsedTurn < 1 ? null : parsedTurn;
  const inReplay = replayTurn !== null;

  // Enter replay in place when the poll detects a turn advance (set the param
  // instead of navigating). prevTurnRef starts null so first load never fires.
  //
  // Safe against dropping a mid-planning player's staged orders: a turn only
  // advances after the engine resolves, which the server does only once *every*
  // player has locked (api: GameService.submit_orders). So when this fires the
  // local player is already locked — their orders are committed server-side and
  // the planning surface is read-only — and there are no unlocked staged orders
  // to lose. This relies on "turn advances ⇒ you locked"; a future async turn
  // timer that auto-resolves un-locked players (SPEC §17, out of scope for v0.1)
  // would break that and must guard this transition on the locked state.
  const prevTurnRef = useRef<number | null>(null);
  useEffect(() => {
    if (
      prevTurnRef.current !== null &&
      view.turn > prevTurnRef.current &&
      !inReplay // don't yank a manually-opened replay
    ) {
      const completed = prevTurnRef.current;
      setSearchParams(
        (prev) => {
          prev.set("turn", String(completed));
          return prev;
        },
        { replace: true },
      );
    }
    prevTurnRef.current = view.turn;
  }, [view.turn, inReplay, setSearchParams]);

  const startPlanning = useCallback(() => {
    setSearchParams(
      (prev) => {
        prev.delete("turn");
        return prev;
      },
      { replace: true },
    );
    refresh();
  }, [setSearchParams, refresh]);

  // Replay data + board bundle. Both hooks run unconditionally (stable hook
  // order); they idle when `replayTurn` is null. `replayBundle` is null while
  // planning or while the replay is still loading.
  const { data: replayData, error: replayError } = useReplay(view.game_id, replayTurn);
  const replayBundle = useReplayBoard(replayData, replayTurn ?? -1, view, startPlanning);

  // If a replay can't load, don't strand the user in the chrome-less load-hold —
  // drop back to planning for the (already-advanced) live turn.
  useEffect(() => {
    if (replayTurn !== null && replayError !== null) {
      startPlanning();
    }
  }, [replayTurn, replayError, startPlanning]);

  // The board only *presents* replay once its bundle has loaded. During the brief
  // fetch after a turn advances, `inReplay` is already true but the bundle is
  // null: showing the replay branch then would drop the whole dock (Board draws
  // no panels when both `planning` and `replayControls` are undefined), so the
  // roster/dock would vanish and pop back a beat later. Instead we hold the
  // (read-only) planning chrome until the replay is ready, then swap in one step.
  // (A non-null bundle only ever exists in replay mode, so it doubles as the
  // "replay ready" flag and narrows `replayBundle` for the render branch.)
  const replayLoading = inReplay && replayBundle === null;

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

  // In replay the header reflects the replayed turn's *end* economy (its
  // state_after) — not the live planning state, which may be a later turn when a
  // past replay is opened from the menu. Falls back to the live state until the
  // replay loads.
  const economySource =
    inReplay && replayData !== null ? replayData.replay.state_after : view.state;
  const economy = useMemo(
    () => economyFor(economySource, view.your_slot),
    [economySource, view.your_slot],
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
        turn={inReplay ? replayTurn : view.turn}
        turnLimit={view.state.config.victory.turn_limit}
        phase={inReplay ? "Replay" : phaseOf(view)}
        economy={economy}
        tally={inReplay ? null : active ? tally : null}
        warnings={inReplay ? 0 : (advisory?.count ?? 0)}
        view={view}
        onChanged={refresh}
      />

      <main className="relative flex-1 overflow-hidden">
        {view.status === "lobby" ? (
          <Lobby view={view} />
        ) : (
          // One persistent Board across the whole planning⇄replay cycle: the
          // element stays at this position so its Pixi canvas, textures and
          // camera never tear down — only these props swap between modes. We
          // swap to the replay branch only once the bundle is ready (non-null);
          // during the load-hold we keep the planning chrome (forced read-only)
          // so the dock never blanks between modes.
          <div className="absolute inset-0">
            {replayBundle !== null ? (
              <Board
                state={replayBundle.boardState}
                yourSlot={view.your_slot}
                replayControls={replayBundle.replayControls}
                replayAnim={replayBundle.replayAnim}
                replayLog={replayBundle.replayLog}
              />
            ) : (
              <Board
                state={view.state}
                yourSlot={view.your_slot}
                visible={view.visible}
                planning={active ? planning : undefined}
                // While a just-resolved turn's replay is still loading, hold the
                // panels but read-only (the turn is over — no editing, no lock).
                locked={locked || replayLoading}
                advisory={advisory ?? undefined}
                lock={
                  active && !replayLoading
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
            )}
          </div>
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
