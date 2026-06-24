import { useMemo, useState } from "react";
import { Navigate, useParams } from "react-router";

import { useAuth } from "@/auth/useAuth";
import { Board } from "@/board/Board";
import { Button, ErrorBanner } from "@/components/ui";
import { GameProvider } from "@/game/GameProvider";
import { useGame } from "@/game/useGame";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import { SLOTS, type GameView } from "@/lib/api/types";
import { economyFor } from "@/planning/economy";
import { usePlanning, type Planning } from "@/planning/usePlanning";
import { orderTally } from "@/planning/warnings";

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
  const { signOut } = useAuth();

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

  return (
    <div className="flex h-dvh flex-col bg-zinc-950 text-zinc-100">
      <GameHeader
        turn={view.turn}
        turnLimit={view.state.config.victory.turn_limit}
        phase={phaseOf(view)}
        economy={economy}
        tally={isPlanning ? tally : null}
        onSignOut={() => void signOut()}
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
              />
            </div>
            <PlanningDock
              view={view}
              planning={isPlanning ? planning : null}
              tally={tally}
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
 * Right-docked planning chrome (migration scaffold for the Pixi panels, UI-SPEC
 * §5–§11). Adapts to the lifecycle: your-move shows the palette + lock; otherwise
 * the read-only waiting / game-over status.
 */
function PlanningDock({
  view,
  planning,
  tally,
  onChanged,
}: {
  view: GameView;
  planning: Planning | null;
  tally: ReturnType<typeof orderTally>;
  onChanged: () => void;
}) {
  return (
    <div className="absolute right-3 top-3 flex max-h-[calc(100%-1.5rem)] w-80 flex-col gap-3 overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-900/95 p-3 text-sm shadow-xl backdrop-blur">
      <StatusLine view={view} hasPlanning={planning !== null} />

      {planning !== null && (
        <PlanningControls
          view={view}
          planning={planning}
          tally={tally}
          onLocked={onChanged}
        />
      )}

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

/** Format a staged order for the review list across all order kinds. */
function describeOrder({ unit, order }: Planning["staged"][number]): string {
  switch (order.kind) {
    case "move": {
      const dest = order.path[order.path.length - 1];
      const hexes = order.path.length === 1 ? "1 hex" : `${order.path.length} hexes`;
      return `${unit.type} → (${dest.q}, ${dest.r}) · ${hexes}`;
    }
    case "barrage":
      return `${unit.type} ⤬ barrage (${order.target.q}, ${order.target.r})`;
    case "rest":
      return `${unit.type} · rest`;
    case "hold":
      return `${unit.type} · hold`;
  }
}

/** Your-move planning: selected-unit palette, staged review, lock control. */
function PlanningControls({
  view,
  planning,
  tally,
  onLocked,
}: {
  view: GameView;
  planning: Planning;
  tally: ReturnType<typeof orderTally>;
  onLocked: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const count = planning.staged.length;
  const selected = planning.selectedUnit;
  const idle = tally.idleUnitIds.length;

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
    <div className="flex flex-col gap-3">
      {selected !== null ? (
        <OrderActions planning={planning} unit={selected} />
      ) : (
        <p className="rounded-md border border-zinc-800 bg-zinc-800/40 px-3 py-2 text-xs text-zinc-400">
          Select one of your units on the board to give it orders.
        </p>
      )}

      {count > 0 && (
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>Staged orders</span>
            <button
              onClick={planning.clearAll}
              className="text-indigo-300 hover:text-white"
            >
              Clear all
            </button>
          </div>
          <ul className="flex flex-col gap-1">
            {planning.staged.map((entry) => (
              <li
                key={entry.unit.id}
                className="flex items-center justify-between rounded-md bg-zinc-800/60 px-3 py-1.5"
              >
                <span className="capitalize">{describeOrder(entry)}</span>
                <button
                  onClick={() => planning.clearOrder(entry.unit.id)}
                  aria-label={`Remove order for ${entry.unit.type}`}
                  className="text-zinc-400 hover:text-white"
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-col gap-1 rounded-md border border-indigo-700 bg-indigo-950/40 p-2">
        <Button onClick={() => void lockTurn()} disabled={submitting}>
          {submitting ? "Locking…" : "Lock orders"}
        </Button>
        <p className="text-center font-mono text-xs text-indigo-200">
          {tally.ordered} / {tally.total} set
          {idle > 0 && <span className="text-amber-300"> · ⚠ {idle} idle</span>}
        </p>
        {error !== null && <ErrorBanner message={error} />}
      </div>
    </div>
  );
}

/** Per-unit order palette for the selected unit (UI-SPEC §8). */
function OrderActions({
  planning,
  unit,
}: {
  planning: Planning;
  unit: NonNullable<Planning["selectedUnit"]>;
}) {
  const staged = planning.stagedByUnit[unit.id];
  const isArtillery = unit.type === "artillery";
  const aiming = planning.mode === "barrage";

  return (
    <div className="flex flex-col gap-2 rounded-md border border-indigo-800 bg-indigo-950/30 px-3 py-2">
      <div className="flex items-center justify-between text-xs text-indigo-200">
        <span className="capitalize">
          {unit.type} at ({unit.coord.q}, {unit.coord.r}) · {unit.hp} hp
        </span>
        {staged !== undefined && (
          <button
            onClick={() => planning.clearOrder(unit.id)}
            className="text-indigo-300 hover:text-white"
          >
            Clear
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {isArtillery && (
          <>
            <OrderButton
              label="Move"
              active={!aiming && staged?.kind === "move"}
              onClick={() => planning.enterMove(unit.id)}
            />
            <OrderButton
              label={aiming ? "Aiming…" : "Barrage"}
              active={aiming || staged?.kind === "barrage"}
              onClick={() => planning.enterBarrage(unit.id)}
            />
          </>
        )}
        <OrderButton
          label="Rest"
          active={staged?.kind === "rest"}
          onClick={() => planning.setBareOrder(unit.id, "rest")}
        />
        <OrderButton
          label="Hold"
          active={staged?.kind === "hold"}
          onClick={() => planning.setBareOrder(unit.id, "hold")}
        />
      </div>
      {aiming ? (
        <p className="text-xs text-orange-300">
          Click a highlighted hex to barrage it (range 3), or pick another order.
        </p>
      ) : (
        !isArtillery && (
          <p className="text-xs text-indigo-300/70">
            Click within the range outline to plan a move, or assign rest/hold.
          </p>
        )
      )}
    </div>
  );
}

/** A single toggle button in the order palette. */
function OrderButton({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-md border px-2.5 py-1 text-xs font-medium ${
        active
          ? "border-indigo-400 bg-indigo-600 text-white"
          : "border-indigo-800 text-indigo-200 hover:bg-indigo-900/40"
      }`}
    >
      {label}
    </button>
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
