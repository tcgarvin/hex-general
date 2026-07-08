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
    <header className="fhq-wood flex h-12 shrink-0 items-center gap-5 border-b-2 border-brass-600/70 px-4 text-sm">
      <Link
        to="/"
        className="font-display text-base uppercase tracking-wide text-ivory-100"
      >
        hex-general
      </Link>

      <Stat label="Turn">
        <span className="font-data">
          {turn}
          {turnLimit > 0 && <span className="text-ivory-400"> / {turnLimit}</span>}
        </span>
      </Stat>

      <span className="font-label text-xs font-semibold uppercase tracking-wider text-brass-400">
        {phase}
      </span>

      {view.vs_bot && view.bot_policy !== null && (
        <Stat label="Opponent">{opponentLabel(view.bot_policy)}</Stat>
      )}

      <Stat label="Muster">
        <span className="font-data">
          {economy.bank}
          <span className="text-ivory-400">/{economy.cap}</span>
        </span>{" "}
        <span className={economy.net >= 0 ? "text-valid" : "text-invalid"}>
          ({net})
        </span>
      </Stat>

      {tally !== null && (
        <Stat label="Orders">
          <span className="font-data">
            {tally.ordered}
            <span className="text-ivory-400">/{tally.total}</span>
          </span>
        </Stat>
      )}

      {idle > 0 && (
        <span className="rounded-sm border border-warn/50 bg-ink-900/60 px-2 py-0.5 font-label text-xs font-semibold tracking-wide text-warn">
          ⚠ {idle} IDLE
        </span>
      )}

      {warnings > 0 && (
        <span className="rounded-sm border border-invalid/60 bg-ink-900/60 px-2 py-0.5 font-label text-xs font-semibold tracking-wide text-red-400">
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

/** A labelled inline stat: small stencil-toned caption + value, on one line. */
function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-1.5 whitespace-nowrap text-ivory-100">
      <span className="font-label text-[10px] font-semibold uppercase tracking-wider text-ivory-400">
        {label}
      </span>
      <span>{children}</span>
    </span>
  );
}
