// Small pieces of the world screen shared by the desktop shell and the phone
// layout: header readouts, the countdown, HP pips, costs as glyphs, report lines.

import { useEffect, useRef, useState } from "react";

import { Glyph } from "@/components/Glyph";
import type { GlyphName } from "@/ui-art/glyphs";

import { RESOURCES, type Stocks } from "./economy";
import { stocksText, TONE_CLASS } from "./labels";
import type { TurnPlayback } from "./playback/useTurnPlayback";
import type { Engagement } from "./playback/watchList";
import type { ReportItem } from "./turnReport";

/**
 * A header readout. With `glyph` alone the glyph names it (resources, army size:
 * the glyph carries the label and its tooltip); with `glyph` and `text` both show
 * (the clock); with neither, the text label.
 */
export function Stat({
  label,
  glyph,
  text = glyph === undefined,
  children,
}: {
  label: string;
  glyph?: GlyphName;
  text?: boolean;
  children: React.ReactNode;
}) {
  return (
    <span className="flex items-baseline gap-1.5">
      {glyph !== undefined && <Glyph name={glyph} label={text ? undefined : label} className="self-center" />}
      {text && <span className="font-label text-[11px] uppercase tracking-wider text-ivory-400">{label}</span>}
      {children}
    </span>
  );
}

/** `compact` (the phone's top bar): the time left alone, in brass, without labels. */
export function Countdown({ clock, nextTickAt, compact = false }: { clock: string; nextTickAt: string | null; compact?: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(id);
  }, []);
  if (compact) {
    return <span className="font-data text-brass-400">{countdownText(clock, nextTickAt, now)}</span>;
  }
  if (nextTickAt === null) {
    return (
      <Stat label="Clock" glyph="turn" text>
        {clock === "manual" ? "manual" : "unscheduled"}
      </Stat>
    );
  }
  const at = new Date(nextTickAt);
  const seconds = Math.round((at.getTime() - now) / 1000);
  const minutes = Math.ceil(seconds / 60);
  const when = at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return (
    <Stat label="Resolves" glyph="turn" text>
      <span className="font-data">
        {seconds <= 0
          ? "resolving…"
          : minutes >= 60
            ? `${Math.floor(minutes / 60)}h ${minutes % 60}m`
            : `${minutes}m`}
      </span>{" "}
      <span className="hidden text-ivory-400 xl:inline">({when})</span>
    </Stat>
  );
}

/**
 * A cost or income as the header shows stocks: each resource's glyph and amount
 * ("free" for nothing). The tooltip and accessible name spell it out.
 */
export function StocksGlyphs({ stocks, className = "" }: { stocks: Stocks; className?: string }) {
  const text = stocksText(stocks);
  const parts = RESOURCES.filter((r) => stocks[r] !== 0);
  if (parts.length === 0) return <span className={className}>free</span>;
  return (
    // Plain inline layout: the glyphs sit on the text baseline like the digits.
    <span role="img" aria-label={text} title={text} className={className}>
      {parts.map((r) => (
        <span key={r} className="mr-1.5 whitespace-nowrap last:mr-0">
          <Glyph name={r} size={13} className="mr-0.5" />
          {stocks[r]}
        </span>
      ))}
    </span>
  );
}

/**
 * HP as a number in the data face plus ten pips, amber at 6 and below, red at 3
 * and below, so a glance tells a fresh unit from a spent one.
 */
export function HpPips({ hp, onDark = false }: { hp: number; onDark?: boolean }) {
  const tone = hp <= 3 ? "bg-invalid" : hp <= 6 ? "bg-warn" : "bg-valid";
  const text = hp <= 3 ? "text-invalid" : hp <= 6 && !onDark ? "text-[#9a6510]" : hp <= 6 ? "text-warn" : "";
  return (
    <span className="flex shrink-0 items-center gap-1" title={`${hp} of 10 HP`}>
      <span className={`w-4 text-right font-data text-sm tabular-nums ${text}`}>{hp}</span>
      <span className="flex gap-px" aria-hidden>
        {Array.from({ length: 10 }, (_, i) => (
          <span
            key={i}
            className={`h-2.5 w-[3px] rounded-[1px] ${i < hp ? tone : onDark ? "bg-parchment-200/25" : "bg-ink-900/15"}`}
          />
        ))}
      </span>
    </span>
  );
}

/** What the report panel needs for turn playback. */
export interface ReportPlay {
  turnPlayback: TurnPlayback;
  watching: boolean;
  /** The beat on the board during playback (`-1`: not playing). */
  beat: number;
  /** The beat each report item shows in (`-1`: none), aligned with `items`. */
  itemBeats: readonly number[] | null;
  fights: readonly Engagement[];
  onPlay: () => void;
  /** Watch from this beat, paused there (or jump to it if already watching). */
  onBeat: (beat: number) => void;
}

export function PlayButton({ play, turn }: { play: ReportPlay; turn: number }) {
  const status = play.turnPlayback.status;
  if (status === "none" || status === "waiting" || play.watching) return null;
  if (status === "empty") {
    return <span className="font-label text-[11px] uppercase tracking-wide text-wood-500">Nothing moved in your sight</span>;
  }
  return (
    <button
      type="button"
      onClick={play.onPlay}
      title={`Watch turn ${turn} on the board, pulse by pulse`}
      aria-label={`Play turn ${turn}`}
      className="fhq-btn whitespace-nowrap text-xs"
    >
      ▶ Play
    </button>
  );
}

export function ReportLine({ item, current, onClick }: { item: ReportItem; current: boolean; onClick: (() => void) | null }) {
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (current) ref.current?.scrollIntoView({ block: "nearest" });
  }, [current]);
  const body = (
    <>
      <span className={TONE_CLASS[item.tone]}>{item.text}</span>
      {item.detail.map((d) => (
        <span key={d} className="block pl-3 text-wood-500">
          {d}
        </span>
      ))}
    </>
  );
  const highlight = current ? "bg-brass-400/40 ring-1 ring-brass-600" : "";
  return (
    <li ref={ref} className="text-xs leading-snug" aria-current={current || undefined}>
      {onClick === null ? (
        <span className={`block rounded-sm px-1 py-0.5 ${highlight}`}>{body}</span>
      ) : (
        <button
          type="button"
          onClick={onClick}
          className={`block w-full rounded-sm px-1 py-0.5 text-left hover:bg-parchment-400/40 ${highlight}`}
        >
          {body}
        </button>
      )}
    </li>
  );
}

/** The phone's countdown: "2h 05m", "12m", "resolving…", "manual". */
function countdownText(clock: string, nextTickAt: string | null, now: number): string {
  if (nextTickAt === null) return clock === "manual" ? "manual" : "unscheduled";
  const seconds = Math.round((new Date(nextTickAt).getTime() - now) / 1000);
  const minutes = Math.ceil(seconds / 60);
  if (seconds <= 0) return "resolving…";
  return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m` : `${minutes}m`;
}
