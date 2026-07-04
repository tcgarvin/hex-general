// The thin React header that sits over the full-bleed Pixi board (UI-SPEC §2, §4).
// Everything below this ~48px bar is (becoming) one Pixi surface; React's job on
// the game screen is just this status strip + site nav. Compact, opaque, status-
// heavy, left-to-right: brand · turn/phase · muster · orders · warnings · account.

import { Link } from "react-router";

import { AccountMenu } from "@/components/AccountMenu";
import { GameMenu } from "@/components/GameMenu";
import type { GameView } from "@/lib/api/types";
import { opponentLabel } from "@/lib/bots";
import type { Economy } from "@/planning/economy";
import type { OrderTally } from "@/planning/warnings";

/** The lifecycle label shown in the header (UI-SPEC §3). */
export type Phase = "Lobby" | "Planning" | "Locked" | "Replay" | "Game over";

export interface GameHeaderProps {
  turn: number;
  turnLimit: number;
  phase: Phase;
  economy: Economy;
  /** Order tally, present only while it is the viewer's move to plan. */
  tally: OrderTally | null;
  /** Count of units with an advisory ZOC/traffic problem (SPEC §16.1). */
  warnings: number;
  /** The full view, for the "Game" menu (presence, resign, outcome). */
  view: GameView;
  /** Refresh after an out-of-band game action (e.g. resign). */
  onChanged: () => void;
}

export function GameHeader({
  turn,
  turnLimit,
  phase,
  economy,
  tally,
  warnings,
  view,
  onChanged,
}: GameHeaderProps) {
  const net = economy.net >= 0 ? `+${economy.net}` : `${economy.net}`;
  const idle = tally?.idleUnitIds.length ?? 0;

  return (
    <header className="flex h-12 shrink-0 items-center gap-5 border-b border-zinc-800 bg-zinc-950 px-4 text-sm">
      <Link to="/" className="font-semibold tracking-tight text-zinc-100">
        hex-general
      </Link>

      <Stat label="Turn">
        {turn}
        {turnLimit > 0 && <span className="text-zinc-500"> / {turnLimit}</span>}
      </Stat>

      <span className="font-mono text-xs font-semibold uppercase tracking-wider text-indigo-300">
        {phase}
      </span>

      {view.vs_bot && view.bot_policy !== null && (
        <Stat label="Opponent">{opponentLabel(view.bot_policy)}</Stat>
      )}

      <Stat label="Muster">
        <span className="font-mono">
          {economy.bank}
          <span className="text-zinc-500">/{economy.cap}</span>
        </span>{" "}
        <span className={economy.net >= 0 ? "text-emerald-400" : "text-red-400"}>
          ({net})
        </span>
      </Stat>

      {tally !== null && (
        <Stat label="Orders">
          <span className="font-mono">
            {tally.ordered}
            <span className="text-zinc-500">/{tally.total}</span>
          </span>
        </Stat>
      )}

      {idle > 0 && (
        <span className="rounded bg-amber-500/15 px-2 py-0.5 font-mono text-xs font-semibold text-amber-300">
          ⚠ {idle} IDLE
        </span>
      )}

      {warnings > 0 && (
        <span className="rounded bg-red-500/15 px-2 py-0.5 font-mono text-xs font-semibold text-red-300">
          ⚠ {warnings} {warnings === 1 ? "WARNING" : "WARNINGS"}
        </span>
      )}

      <div className="ml-auto flex items-center gap-2">
        <GameMenu view={view} onChanged={onChanged} />
        <AccountMenu />
      </div>
    </header>
  );
}

/** A labelled inline stat: small grey caption + value, kept on one line. */
function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-1.5 whitespace-nowrap text-zinc-200">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      <span>{children}</span>
    </span>
  );
}
