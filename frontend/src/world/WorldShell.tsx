// The world screen (WORLD_SPEC §11, W6): header, board, and planning panels.
//
// Slice 1 was the read side (map, units, fog, clock, economy). Slice 2 adds
// planning: select a unit and click a destination to travel (the route is
// previewed), or give it a move, barrage, rest, hold or deploy order; recruit at
// held sites; start a building; pick who disbands first in a shortage; and
// submit, revising as often as you like until the turn resolves.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { axialEqual, hexKey, type Axial } from "@/board/hex";
import { AccountMenu } from "@/components/AccountMenu";
import { ButtonLink, Card, Centered, ErrorBanner, Loading } from "@/components/ui";
import type { components } from "@/lib/api/schema";

import { NEUTRAL_OWNER } from "./colors";
import {
  economyOf,
  forecast,
  levelOf,
  RESOURCES,
  siteIncome,
  stocksOf,
  sub,
  type Building,
  type Economy,
  type Forecast,
  type Stocks,
} from "./economy";
import { HoverInfo } from "./HoverInfo";
import { MapControls } from "./MapControls";
import { overlayOf } from "./overlay";
import { PlaybackBar, type BeatJump } from "./PlaybackBar";
import { reportItemBeats } from "./playback/session";
import { useTurnPlayback, type TurnPlayback } from "./playback/useTurnPlayback";
import { engagements, type Engagement } from "./playback/watchList";
import {
  buildOption,
  inBarrageRange,
  orderKindsFor,
  planCost,
  planProblems,
  recruitOptions,
  type OrderKind,
  type WorldOrder,
} from "./plan";
import { hostileZoc, swapBlocked, swapNeighbours, swapPartner, swapRole } from "./relief";
import { siteIconUrl } from "./siteArt";
import { SITE_TILE_KINDS } from "./siteTiles";
import { turnReport, turnTrails, unitBook, type ReportItem } from "./turnReport";
import { useTurnReport, type ReportLoad } from "./useTurnReport";
import { useWorld, type WorldView } from "./useWorld";
import { useWorldHotkeys } from "./useWorldHotkeys";
import { useWorldPlan, type PlannedRoute, type WorldPlanning } from "./useWorldPlan";
import { WorldBoard } from "./WorldBoard";
import type { WorldRenderer } from "./WorldRenderer";
import type { MapSite, WorldMap } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];
type Unit = components["schemas"]["Unit"];

export function WorldShell() {
  const { id } = useParams<{ id: string }>();
  if (id === undefined) throw new Error("WorldShell needs a world id route param");
  const load = useWorld(id);
  if (load.status === "loading") return <Loading fill label="Loading the world…" />;
  if (load.status === "gone") return <WorldGone message={load.message} />;
  if (load.status === "error") {
    return (
      <Centered fill>
        <ErrorBanner message={load.message} />
        <Link to="/" className="text-sm text-brass-400 hover:underline">
          ← Back
        </Link>
      </Centered>
    );
  }
  return <WorldScreen map={load.map} view={load.view} error={load.error} refresh={load.refresh} />;
}

/**
 * The world was deleted, or the viewer is no longer in it: the board is
 * replaced (orders could only fail) by a plain notice and a way out.
 */
function WorldGone({ message }: { message: string }) {
  return (
    <div className="fixed inset-0 flex flex-col bg-[#1a1d22] text-ivory-100">
      <header className="fhq-wood flex h-12 shrink-0 items-center gap-5 border-b-2 border-brass-600/70 px-4 text-sm">
        <Link to="/worlds" className="whitespace-nowrap font-display text-base uppercase tracking-wide text-ivory-100">
          hex-general
        </Link>
        <div className="ml-auto">
          <AccountMenu />
        </div>
      </header>
      <Centered fill>
        <Card className="flex max-w-md flex-col gap-3">
          <h1 className="font-display text-lg uppercase tracking-wide">This world is closed to you</h1>
          <p className="text-sm">
            It may have been deleted, or you no longer hold a seat in it. Nothing here can be ordered any more.
          </p>
          <p className="font-data text-xs text-wood-500">Server: {message}</p>
          <ButtonLink to="/" className="self-start">
            Back to the start page
          </ButtonLink>
        </Card>
      </Centered>
    </div>
  );
}

/** How long the "Turn N resolved" banner stays up. */
const RESOLVED_BANNER_MS = 6000;

function WorldScreen({
  map,
  view,
  error,
  refresh,
}: {
  map: WorldMap;
  view: WorldView;
  error: string | null;
  refresh: () => void;
}) {
  const planning = useWorldPlan(view, map, refresh);
  useLeaveWarning(planning.dirty);
  const economy = useMemo(() => economyOf(view.state), [view.state]);
  const outlook = useMemo(() => forecast(view.state, map, view.your_slot), [view, map]);
  const base = useMemo(() => baseOf(view.state, map, view.your_slot), [view, map]);
  const [panTo, setPanTo] = useState<{ hex: Axial; seq: number; reveal?: boolean } | null>(null);
  const jump = useCallback((hex: Axial) => setPanTo((p) => ({ hex, seq: (p?.seq ?? 0) + 1 })), []);
  const reveal = useCallback((hex: Axial) => setPanTo((p) => ({ hex, seq: (p?.seq ?? 0) + 1, reveal: true })), []);
  const [renderer, setRenderer] = useState<WorldRenderer | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [hoveredUnit, setHoveredUnit] = useState<number | null>(null);
  // Dev builds only: `?turn=N` shows (and plays) an earlier resolved turn, for checks.
  const [search] = useSearchParams();
  const devTurn = import.meta.env.DEV ? Number(search.get("turn") ?? NaN) : NaN;
  const reportTurn =
    Number.isInteger(devTurn) && devTurn >= 0 && devTurn < view.turn ? devTurn : view.turn > 0 ? view.turn - 1 : null;
  const report = useTurnReport(view.game_id, reportTurn);
  const digest = useMemo(() => {
    if (report.status !== "ready") return null;
    const { events, state_after } = report.replay;
    const units = state_after.units ?? [];
    const book = unitBook(events, units, view.your_slot);
    const context = { sites: map.sites, settlements: state_after.settlements ?? [], units };
    return { book, items: turnReport(events, view.your_slot, book, context), trails: turnTrails(events, book) };
  }, [report, view.your_slot, map]);
  // Turn playback: watching the last resolved turn on the board (`PlaybackBar`).
  const lastTurn = report.status === "none" ? null : report.turn;
  const [watch, setWatch] = useState<{ turn: number; autoplay: boolean; jump: BeatJump | null } | null>(null);
  const watching = watch !== null && watch.turn === lastTurn ? watch : null;
  const turnPlayback = useTurnPlayback({ gameId: view.game_id, report, viewer: view.your_slot, map, wanted: watching !== null });
  const playback = watching !== null && turnPlayback.status === "ready" && renderer !== null ? turnPlayback.playback : null;
  const [beatNow, setBeatNow] = useState(-1);
  const watchTurn = useCallback(
    (beat: number | null) => {
      if (lastTurn === null) return;
      setWatch((w) => {
        const seq = (w?.jump?.seq ?? 0) + 1;
        if (w !== null && w.turn === lastTurn) return beat === null ? w : { ...w, jump: { beat, seq } };
        return { turn: lastTurn, autoplay: beat === null, jump: beat === null ? null : { beat, seq } };
      });
    },
    [lastTurn],
  );
  const exitWatch = useCallback(() => {
    setWatch(null);
    setBeatNow(-1);
  }, []);
  const fights = useMemo(
    () =>
      digest !== null && "provisional" in turnPlayback ? engagements(turnPlayback.provisional, digest.book) : [],
    [digest, turnPlayback],
  );
  const itemBeats = useMemo(
    () => (digest !== null && "provisional" in turnPlayback ? reportItemBeats(digest.items, turnPlayback.provisional.beats) : null),
    [digest, turnPlayback],
  );
  // The report opens with each new turn; closing it also hides the trails. On a
  // short screen it starts closed, so the Army list keeps its room.
  const short = useShortScreen();
  const [reportToggle, setReportToggle] = useState<{ turn: number; open: boolean } | null>(null);
  const reportOpen = reportToggle?.turn === view.turn ? reportToggle.open : !short;
  const trails = reportOpen ? (digest?.trails ?? null) : null;
  const resolved = useTurnResolved(view.turn);
  const overlay = useMemo(
    () => overlayOf(planning, view.state.units, trails, hoveredUnit),
    [planning, view.state.units, trails, hoveredUnit],
  );
  const canSubmit =
    !planning.submitting &&
    planProblems(planning.plan, view.state, economy, view.your_slot, outlook.armySize).length === 0 &&
    (planning.dirty || !planning.submitted);
  useWorldHotkeys({ renderer, planning, home: base, canSubmit, helpOpen, setHelpOpen, playback: watching !== null });
  // A unit standing on a site also shows that hex's panel (recruiting, garrison).
  const unitHex = planning.selectedUnit?.coord ?? null;
  const panelHex =
    unitHex === null ? planning.selectedHex : map.siteAt(unitHex.q, unitHex.r) !== undefined ? unitHex : null;

  return (
    <div className="fixed inset-0 flex flex-col bg-[#1a1d22] text-ivory-100">
      <WorldHeader view={view} plan={outlook} />
      {error !== null && (
        <div className="absolute left-1/2 top-14 z-10 -translate-x-1/2">
          <ErrorBanner message={error} />
        </div>
      )}
      <div className="relative flex-1">
        <WorldBoard
          map={map}
          view={view}
          focus={base}
          overlay={overlay}
          panTo={panTo}
          onHexClick={planning.onHexClick}
          onRenderer={setRenderer}
        />
        {renderer !== null && watching === null && <HoverInfo renderer={renderer} map={map} view={view} planning={planning} />}
        <MapControls renderer={renderer} home={base} helpOpen={helpOpen} setHelpOpen={setHelpOpen} />
        {resolved !== null && (
          <ResolvedBanner
            turn={resolved.turn}
            key={resolved.seq}
            onWatch={turnPlayback.status === "idle" ? () => watchTurn(null) : null}
          />
        )}
        {/* The minimap and its buttons sit under this column, bottom left. */}
        <div className="pointer-events-none absolute left-3 top-3 flex max-h-[calc(100%-244px)] w-60 flex-col gap-3 min-[1100px]:w-72">
          <ArmyPanel view={view} planning={planning} onJump={reveal} onHoverUnit={setHoveredUnit} />
          <ReportPanel
            load={report}
            items={digest?.items ?? null}
            open={reportOpen}
            flash={resolved?.seq ?? null}
            onToggle={() => setReportToggle({ turn: view.turn, open: !reportOpen })}
            onJump={jump}
            play={{
              turnPlayback,
              watching: watching !== null,
              beat: playback !== null ? beatNow : -1,
              itemBeats,
              fights,
              onPlay: () => watchTurn(null),
              onBeat: (beat) => watchTurn(beat),
            }}
          />
        </div>
        <div className="pointer-events-none absolute right-3 top-3 flex max-h-[calc(100%-24px)] w-64 flex-col gap-3 overflow-y-auto min-[1100px]:w-80">
          <BasePanel state={view.state} economy={economy} slot={view.your_slot} planning={planning} />
          {planning.selectedUnit !== null && <UnitPanel unit={planning.selectedUnit} view={view} planning={planning} />}
          {panelHex !== null && (
            <HexPanel
              map={map}
              state={view.state}
              economy={economy}
              slot={view.your_slot}
              hex={panelHex}
              armySize={outlook.armySize}
              planning={planning}
              onClose={() => planning.selectUnit(null)}
            />
          )}
        </div>
        {watching === null ? (
          <PlanBar view={view} economy={economy} armySize={outlook.armySize} planning={planning} />
        ) : playback !== null && renderer !== null ? (
          <PlaybackBar
            key={playback.turn}
            playback={playback}
            renderer={renderer}
            autoplay={watching.autoplay}
            jump={watching.jump}
            note={turnPlayback.status === "ready" ? turnPlayback.message : null}
            onBeat={setBeatNow}
            onExit={exitWatch}
          />
        ) : (
          <div className="fhq-wood pointer-events-auto absolute bottom-3 left-1/2 -translate-x-1/2 rounded-sm border-2 border-brass-600/70 px-4 py-2 font-label text-xs uppercase tracking-wide shadow-lg">
            Loading turn {watching.turn}…{" "}
            <button type="button" onClick={exitWatch} className="ml-2 text-ivory-400 hover:text-ivory-100">
              Cancel
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Ask before leaving the page while the plan has changes the server doesn't have.
 *
 * Not in dev builds: a Vite full reload with unsent changes would raise the native
 * prompt, which freezes automated browser sessions (and the unsent plan is restored
 * from sessionStorage after a reload anyway).
 */
function useLeaveWarning(dirty: boolean): void {
  useEffect(() => {
    if (!dirty || import.meta.env.DEV) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // Older browsers only prompt when a return value is set.
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);
}

/** Whether the viewport is short enough that the left column must ration its height. */
function useShortScreen(): boolean {
  const query = "(max-height: 640px)";
  const [short, setShort] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const onChange = () => setShort(list.matches);
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, []);
  return short;
}

/**
 * The turn that just resolved while the page was open, for a few seconds after
 * the poll brings it in (`seq` restarts the banner if another follows).
 */
function useTurnResolved(turn: number): { turn: number; seq: number } | null {
  const [seen, setSeen] = useState(turn);
  const [resolved, setResolved] = useState<{ turn: number; seq: number } | null>(null);
  if (turn !== seen) {
    setSeen(turn);
    if (turn > seen) setResolved({ turn: turn - 1, seq: turn });
  }
  useEffect(() => {
    if (resolved === null) return;
    const id = setTimeout(() => setResolved(null), RESOLVED_BANNER_MS);
    return () => clearTimeout(id);
  }, [resolved]);
  return resolved;
}

function ResolvedBanner({ turn, onWatch }: { turn: number; onWatch: (() => void) | null }) {
  return (
    <div
      role="status"
      className="fhq-wood pointer-events-none absolute left-1/2 top-4 z-20 -translate-x-1/2 animate-[fhq-drop_6s_ease-out_forwards] rounded-sm border-2 border-brass-600 px-6 py-2 text-center shadow-xl shadow-black/50"
    >
      <p className="font-display text-xl uppercase tracking-wider text-brass-400">Turn {turn} resolved</p>
      <p className="font-label text-xs uppercase tracking-wider text-ivory-100">
        The report is open on the left; orders for turn {turn + 1} start now
      </p>
      {onWatch !== null && (
        <button
          type="button"
          onClick={onWatch}
          className="pointer-events-auto mt-1.5 rounded-sm border border-brass-600 bg-brass-600 px-3 py-0.5 font-label text-xs font-semibold uppercase tracking-wider text-wood-900 hover:bg-brass-400"
        >
          Watch turn {turn}
        </button>
      )}
    </div>
  );
}

function baseOf(state: WorldState, map: WorldMap, slot: string): Axial | null {
  const held = state.settlements.find(
    (s) => s.controlled_by === slot && map.siteAt(s.coord.q, s.coord.r)?.kind === "base",
  );
  return held?.coord ?? state.units.find((u) => u.owner === slot)?.coord ?? null;
}

// --- header -----------------------------------------------------------------------

const RESOURCE_LABEL = { manpower: "Manpower", fuel: "Fuel", steel: "Steel" } as const;
const RESOURCE_ABBR = { manpower: "MP", fuel: "Fuel", steel: "Stl" } as const;

/**
 * One line at any width: below 1280 px the per-turn forecast goes, below 1100 px
 * the labels shorten, so the bar never wraps.
 */
function WorldHeader({ view, plan }: { view: WorldView; plan: Forecast }) {
  const player = view.state.players.find((p) => p.id === view.your_slot);
  const stocks = player === undefined ? null : stocksOf(player);
  return (
    <header className="fhq-wood flex h-12 shrink-0 items-center gap-3 overflow-hidden whitespace-nowrap border-b-2 border-brass-600/70 px-4 text-sm min-[1100px]:gap-5">
      <Link to="/worlds" title="All worlds" className="font-display text-base uppercase tracking-wide text-ivory-100">
        hex-general
      </Link>
      <Stat label="Turn">
        <span className="font-data">{view.turn}</span>
      </Stat>
      <Countdown clock={view.clock} nextTickAt={view.next_tick_at ?? null} />
      {stocks !== null &&
        RESOURCES.map((r) => (
          <Stat key={r} label={RESOURCE_LABEL[r]} abbr={RESOURCE_ABBR[r]}>
            <span className={`font-data ${plan.short.includes(r as "fuel" | "manpower") ? "text-invalid" : ""}`}>
              {stocks[r]}
              <span className="text-ivory-400">/{plan.storage}</span>
            </span>
            <span className={`hidden xl:inline ${plan.net[r] >= 0 ? "text-valid" : "text-invalid"}`}>
              ({plan.net[r] >= 0 ? "+" : ""}
              {plan.net[r]})
            </span>
          </Stat>
        ))}
      <Stat label="Army">
        <span className="font-data">
          {plan.units}
          <span className="text-ivory-400">/{plan.armySize}</span>
        </span>
      </Stat>
      {plan.short.length > 0 && (
        <span
          title={`Out of ${plan.short.join(" and ")} next turn: a unit will disband`}
          className="font-label text-xs font-semibold uppercase tracking-wider text-invalid"
        >
          <span className="hidden xl:inline">Out of {plan.short.join(" and ")} next turn: a unit will disband</span>
          <span className="xl:hidden">Short of {plan.short.join(", ")}</span>
        </span>
      )}
      <div className="ml-auto">
        <AccountMenu />
      </div>
    </header>
  );
}

function Stat({ label, abbr, children }: { label: string; abbr?: string; children: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="font-label text-[11px] uppercase tracking-wider text-ivory-400">
        {abbr === undefined ? (
          label
        ) : (
          <>
            <span className="hidden min-[1100px]:inline">{label}</span>
            <span className="min-[1100px]:hidden" title={label}>
              {abbr}
            </span>
          </>
        )}
      </span>
      {children}
    </span>
  );
}

function Countdown({ clock, nextTickAt }: { clock: string; nextTickAt: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(id);
  }, []);
  if (nextTickAt === null) {
    return <Stat label="Clock">{clock === "manual" ? "manual" : "unscheduled"}</Stat>;
  }
  const at = new Date(nextTickAt);
  const seconds = Math.round((at.getTime() - now) / 1000);
  const minutes = Math.ceil(seconds / 60);
  const when = at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return (
    <Stat label="Resolves">
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

// --- shared panel bits --------------------------------------------------------------

const BUILDING_LABEL: Record<Building, string> = {
  barracks: "Barracks",
  depot: "Depot",
  workshop: "Workshop",
};

const UNIT_LABEL = { infantry: "Infantry", artillery: "Artillery", armor: "Armor" } as const;

function stocksText(s: Stocks): string {
  const parts = RESOURCES.filter((r) => s[r] !== 0).map((r) => `${s[r]} ${r}`);
  return parts.length === 0 ? "free" : parts.join(", ");
}

function Panel({
  title,
  children,
  onClose,
  actions,
  className = "",
}: {
  title: string;
  children: React.ReactNode;
  onClose?: () => void;
  /** Extra header controls, left of Close. */
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`pointer-events-auto rounded-sm border border-wood-700 bg-parchment-200 p-3 text-sm text-ink-900 shadow-lg ${className}`}
    >
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-display text-sm uppercase tracking-wide">{title}</h2>
        <div className="flex items-center gap-2">
          {actions}
          {onClose !== undefined && (
            <button type="button" onClick={onClose} className="font-label text-xs uppercase text-wood-500 hover:text-ink-900">
              Close
            </button>
          )}
        </div>
      </div>
      {children}
    </section>
  );
}

function Chip({
  active = false,
  disabled = false,
  title,
  onClick,
  children,
}: {
  active?: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`whitespace-nowrap rounded-sm border px-2 py-0.5 font-label text-xs uppercase tracking-wide transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        active
          ? "border-wood-900 bg-wood-700 text-parchment-200"
          : "border-wood-500 bg-parchment-200 text-ink-900 hover:bg-parchment-400/40"
      }`}
    >
      {children}
    </button>
  );
}

// --- army ---------------------------------------------------------------------------

/** How a unit's swap reads: who it swaps with and whether it relieves or is relieved. */
interface SwapInfo {
  partner: Unit;
  role: "relieves" | "relieved" | "swaps";
}

const unitTag = (u: Unit) => `${UNIT_LABEL[u.type]} #${u.id}`;
const at = (a: Axial) => `(${a.q},${a.r})`;

function orderText(order: WorldOrder | undefined, route: PlannedRoute | undefined, swap: SwapInfo | null = null): string {
  if (order === undefined) {
    return route?.kind === "standing" ? `travelling, ${etaText(route.eta)}` : "no orders (holds)";
  }
  switch (order.kind) {
    case "travel":
      return `travel, ${etaText(route?.eta ?? null)}`;
    case "move":
      if (swap !== null) {
        if (swap.role === "relieves") return `relieve ${at(swap.partner.coord)}`;
        if (swap.role === "relieved") return `relieved by ${unitTag(swap.partner)}`;
        return `swap with ${unitTag(swap.partner)}`;
      }
      return route?.attack ? `attack ${at(route.attack)}` : `move ${order.path.length} hex${order.path.length === 1 ? "" : "es"}`;
    case "march":
      return "march";
    case "barrage":
      return `barrage ${at(order.target)}`;
    case "rest":
      return "rest";
    case "hold":
      return "hold";
    case "deploy":
      return "deploy";
  }
}

function etaText(eta: number | null): string {
  if (eta === null) return "";
  if (eta <= 1) return "arrives this turn";
  return `${eta} turns`;
}

/** The swap each own unit is half of under the plan, by unit id. */
function useSwaps(view: WorldView, planning: WorldPlanning): ReadonlyMap<number, SwapInfo> {
  const { units } = view.state;
  const slot = view.your_slot;
  const orders = planning.plan.orders;
  return useMemo(() => {
    const own = units.filter((u) => u.owner === slot);
    const zoc = hostileZoc(units, slot);
    const swaps = new Map<number, SwapInfo>();
    for (const u of own) {
      const partner = swapPartner(u, orders, own);
      if (partner !== null) swaps.set(u.id, { partner, role: swapRole(u, partner, zoc) });
    }
    return swaps;
  }, [units, slot, orders]);
}

/**
 * HP as a number in the data face plus ten pips, amber at 6 and below, red at 3
 * and below, so a glance tells a fresh unit from a spent one.
 */
function HpPips({ hp, onDark = false }: { hp: number; onDark?: boolean }) {
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

function ArmyPanel({
  view,
  planning,
  onJump,
  onHoverUnit,
}: {
  view: WorldView;
  planning: WorldPlanning;
  onJump: (hex: Axial) => void;
  onHoverUnit: (unitId: number | null) => void;
}) {
  const units = view.state.units.filter((u) => u.owner === view.your_slot).sort((a, b) => a.id - b.id);
  const swaps = useSwaps(view, planning);
  return (
    <Panel title={`Army (${units.length})`} className="flex min-h-0 shrink-0 flex-col">
      {units.length === 0 ? (
        <p className="text-xs text-wood-500">No units in the field.</p>
      ) : (
        // At least five rows before it scrolls; the report below takes what's left.
        <ul className="-mx-1 flex max-h-[max(7.75rem,32vh)] min-h-0 flex-col overflow-y-auto">
          {units.map((u) => {
            const route = planning.routes.find((r) => r.unitId === u.id);
            const selected = planning.selectedUnit?.id === u.id;
            const text = orderText(planning.plan.orders[u.id], route, swaps.get(u.id) ?? null);
            return (
              <li key={u.id} onMouseEnter={() => onHoverUnit(u.id)} onMouseLeave={() => onHoverUnit(null)}>
                <button
                  type="button"
                  onClick={() => {
                    planning.selectUnit(u.id);
                    onJump(u.coord);
                  }}
                  className={`flex h-6 w-full items-center gap-2 rounded-sm px-1 text-left ${
                    selected ? "bg-wood-700 text-parchment-200" : "hover:bg-parchment-400/40"
                  }`}
                >
                  <span className="w-[4.5rem] shrink-0 truncate">
                    {UNIT_LABEL[u.type]}
                    {u.posture === "column" ? " ▭" : ""}
                  </span>
                  <HpPips hp={u.hp} onDark={selected} />
                  <span className="ml-auto truncate text-xs" title={text}>
                    {text}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

// --- last turn ----------------------------------------------------------------------

const SECTION_LABEL = { fighting: "Fighting", base: "Base", movement: "Movement", seen: "Also seen" } as const;
// The status greens and reds are tuned for dark wood; on parchment they need more ink.
const TONE_CLASS = { good: "text-[#466428]", bad: "text-invalid", neutral: "" } as const;

/** What the report panel needs for turn playback. */
interface ReportPlay {
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

const FIGHT_RESULT: Record<Engagement["result"], { text: string; tone: keyof typeof TONE_CLASS }> = {
  won: { text: "Won", tone: "good" },
  took: { text: "Took", tone: "good" },
  shelled: { text: "Shelled", tone: "good" },
  held: { text: "Held", tone: "good" },
  lost: { text: "Lost", tone: "bad" },
  lost_site: { text: "Lost", tone: "bad" },
  under_fire: { text: "Shelled", tone: "bad" },
  traded: { text: "Traded", tone: "neutral" },
  repulsed: { text: "Repulsed", tone: "bad" },
  missed: { text: "Missed", tone: "neutral" },
};

function PlayButton({ play, turn }: { play: ReportPlay; turn: number }) {
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
      className="rounded-sm border border-brass-600 bg-brass-600 px-2 py-0.5 font-label text-xs font-semibold uppercase tracking-wider text-wood-900 transition-colors hover:bg-brass-400"
    >
      ▶ Play turn {turn}
    </button>
  );
}

function ReportPanel({
  load,
  items,
  open,
  flash,
  onToggle,
  onJump,
  play,
}: {
  load: ReportLoad;
  items: readonly ReportItem[] | null;
  open: boolean;
  /** Changes when a turn has just resolved: the panel flashes to draw the eye. */
  flash: number | null;
  onToggle: () => void;
  onJump: (hex: Axial) => void;
  play: ReportPlay;
}) {
  if (load.status === "none") return null;
  const title = `Turn ${load.turn} report`;
  const flashing = flash !== null ? "animate-[fhq-flash_1.1s_ease-out_3]" : "";
  if (!open) {
    return (
      <button
        type="button"
        key={flash ?? "still"}
        onClick={onToggle}
        className={`pointer-events-auto shrink-0 self-start rounded-sm border border-wood-700 bg-parchment-200 px-3 py-1 font-label text-xs uppercase tracking-wide text-ink-900 shadow ${flashing}`}
      >
        Show {title.toLowerCase()}
      </button>
    );
  }
  // During playback a line jumps to its beat; otherwise it jumps the camera.
  const lineProps = (item: ReportItem) => {
    const k = items === null ? -1 : items.indexOf(item);
    const beat = play.itemBeats?.[k] ?? -1;
    return {
      item,
      current: play.beat >= 0 && beat === play.beat,
      onClick: play.watching && beat >= 0 ? () => play.onBeat(beat) : item.at === null ? null : () => onJump(item.at!),
    };
  };
  return (
    <Panel
      key={flash ?? "still"}
      title={title}
      onClose={onToggle}
      actions={<PlayButton play={play} turn={load.turn} />}
      className={`flex min-h-[6rem] flex-col ${flashing}`}
    >
      {load.status === "loading" && <p className="text-xs text-wood-500">Loading…</p>}
      {load.status === "error" && <p className="text-xs text-invalid">{load.message}</p>}
      {items !== null && (
        <div className="-mx-1 flex min-h-0 flex-col gap-2 overflow-y-auto px-1">
          {play.fights.length > 0 && (
            <div>
              <p className="font-label text-[11px] uppercase tracking-wider text-wood-500">Your fights</p>
              <ul className="flex flex-col">
                {play.fights.map((f) => (
                  <li key={f.eventIndex} className="text-xs leading-snug">
                    <button
                      type="button"
                      onClick={() => play.onBeat(f.beat)}
                      title="Watch this fight"
                      className={`flex w-full items-baseline gap-2 rounded-sm px-1 py-0.5 text-left hover:bg-parchment-400/40 ${
                        play.beat === f.beat ? "bg-brass-400/40" : ""
                      }`}
                    >
                      <span className={`w-14 shrink-0 font-label uppercase ${TONE_CLASS[FIGHT_RESULT[f.result].tone]}`}>
                        {FIGHT_RESULT[f.result].text}
                      </span>
                      <span>{f.summary}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {items.length === 0 && <p className="text-xs text-wood-500">A quiet turn.</p>}
          {(["fighting", "base", "movement"] as const).map((section) => {
            const list = items.filter((i) => i.section === section);
            return list.length === 0 ? null : (
              <div key={section}>
                <p className="font-label text-[11px] uppercase tracking-wider text-wood-500">{SECTION_LABEL[section]}</p>
                <ul className="flex flex-col">
                  {list.map((item, k) => (
                    <ReportLine key={k} {...lineProps(item)} />
                  ))}
                </ul>
              </div>
            );
          })}
          {items.some((i) => i.section === "seen") && (
            <details open={play.watching || undefined}>
              <summary className="cursor-pointer font-label text-[11px] uppercase tracking-wider text-wood-500">
                {SECTION_LABEL.seen} ({items.filter((i) => i.section === "seen").length})
              </summary>
              <ul className="flex flex-col">
                {items
                  .filter((i) => i.section === "seen")
                  .map((item, k) => (
                    <ReportLine key={k} {...lineProps(item)} />
                  ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Panel>
  );
}

function ReportLine({ item, current, onClick }: { item: ReportItem; current: boolean; onClick: (() => void) | null }) {
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

const KIND_LABEL: Record<OrderKind, string> = {
  travel: "Travel",
  move: "Move",
  march: "March",
  barrage: "Barrage",
  rest: "Rest",
  hold: "Hold",
  deploy: "Deploy",
};

const KIND_HELP: Record<OrderKind, string> = {
  travel: "Click any hex: the unit packs into a column and marches there over as many turns as it takes.",
  move: "Click a highlighted hex: a short move this turn, fighting as it goes.",
  march: "",
  barrage: "Click a hex within 3: shell it this turn.",
  rest: "Stay put and repair.",
  hold: "Stay put and dig in.",
  deploy: "Unpack where it stands, ready to fight next turn.",
};

function UnitPanel({ unit, view, planning }: { unit: Unit; view: WorldView; planning: WorldPlanning }) {
  const order = planning.plan.orders[unit.id];
  const route = planning.routes.find((r) => r.unitId === unit.id);
  const kinds = orderKindsFor(unit).filter((k) => k !== "march");
  const disbandRank = planning.plan.disband.indexOf(unit.id);
  const active: OrderKind | null =
    planning.mode === "move" || planning.mode === "barrage" ? planning.mode : (order?.kind ?? null);
  const swap = useSwaps(view, planning).get(unit.id) ?? null;
  const own = view.state.units.filter((u) => u.owner === view.your_slot);
  const zoc = useMemo(() => hostileZoc(view.state.units, view.your_slot), [view.state.units, view.your_slot]);
  const neighbours = swapNeighbours(unit, own).filter((u) => u.id !== swap?.partner.id);

  const choose = (kind: OrderKind) => {
    if (kind === "travel" || kind === "move" || kind === "barrage") {
      planning.setMode(kind);
      return;
    }
    planning.setMode("travel");
    planning.setOrder(unit.id, { kind } as WorldOrder);
  };

  return (
    <Panel title={`${UNIT_LABEL[unit.type]} #${unit.id}`} onClose={() => planning.selectUnit(null)}>
      <div className="flex items-center gap-2 text-xs">
        <HpPips hp={unit.hp} />
        <span className="font-data text-wood-500">
          {unit.posture === "column" ? "packed column" : "deployed"}
          {unit.disordered ? " · disordered" : ""}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {kinds.map((k) => (
          <Chip key={k} active={active === k} onClick={() => choose(k)} title={KIND_HELP[k]}>
            {KIND_LABEL[k]}
          </Chip>
        ))}
      </div>
      <p className="mt-2 text-xs">
        {planning.mode === "move" || planning.mode === "barrage"
          ? KIND_HELP[planning.mode]
          : order === undefined
            ? route?.kind === "standing"
              ? `Carrying on its travel order: ${etaText(route.eta)}. Click a hex to send it elsewhere.`
              : KIND_HELP.travel
            : `Ordered: ${orderText(order, route, swap)}.`}
      </p>
      {order?.kind === "travel" && (
        <div className="mt-2 flex items-center gap-2 text-xs">
          <span>On arrival</span>
          <Chip active={order.onArrival === "deployed"} onClick={() => planning.setOnArrival(unit.id, "deployed")}>
            Deploy
          </Chip>
          <Chip active={order.onArrival === "column"} onClick={() => planning.setOnArrival(unit.id, "column")}>
            Stay packed
          </Chip>
        </div>
      )}
      {order?.kind === "barrage" && !inBarrageRange(unit, order.target) && (
        <p className="mt-1 text-xs text-invalid">Target out of range.</p>
      )}
      {swap !== null && (
        <p className="mt-2 text-xs">
          {swap.role === "relieves"
            ? `Steps up into ${at(swap.partner.coord)} as ${unitTag(swap.partner)} steps back here, both on the first pulse.`
            : swap.role === "relieved"
              ? `${unitTag(swap.partner)} takes this hex as this unit falls back to ${at(swap.partner.coord)}, both on the first pulse.`
              : `Swaps hexes with ${unitTag(swap.partner)} on the first pulse.`}
        </p>
      )}
      {neighbours.length > 0 && (
        <div className="mt-2 border-t border-wood-500/40 pt-2 text-xs">
          <p className="text-wood-500">Relief: swap places with a neighbour (or Shift+click it on the map).</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {neighbours.map((n) => {
              const blocked = swapBlocked(unit, n, zoc);
              return (
                <Chip key={n.id} disabled={blocked !== null} title={blocked ?? undefined} onClick={() => planning.orderSwap(n.id)}>
                  Swap with {unitTag(n)}
                </Chip>
              );
            })}
          </div>
          {neighbours.every((n) => swapBlocked(unit, n, zoc) !== null) && (
            <p className="mt-1 text-wood-500">Can't: {swapBlocked(unit, neighbours[0], zoc)}.</p>
          )}
        </div>
      )}
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-wood-500/40 pt-2 text-xs">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={disbandRank >= 0} onChange={() => planning.toggleDisband(unit.id)} />
          {disbandRank >= 0 ? `Disband first in a shortage (#${disbandRank + 1})` : "Disband first in a shortage"}
        </label>
        {order !== undefined && (
          <Chip
            onClick={() => planning.setOrder(unit.id, null)}
            title={swap !== null ? "Cancel the swap: both units lose their orders" : "Drop this turn's order"}
          >
            {swap !== null ? "Cancel swap" : "Clear"}
          </Chip>
        )}
      </div>
    </Panel>
  );
}

// --- base and sites -----------------------------------------------------------------

function BasePanel({
  state,
  economy,
  slot,
  planning,
}: {
  state: WorldState;
  economy: Economy;
  slot: string;
  planning: WorldPlanning;
}) {
  const player = state.players.find((p) => p.id === slot);
  if (player === undefined) return null;
  const work = player.construction ?? null;
  const building = work !== null && work.completes_on > state.turn;
  const queued = planning.plan.build;
  return (
    <Panel title="Base buildings">
      <ul className="flex flex-col gap-1.5">
        {(Object.keys(BUILDING_LABEL) as Building[]).map((b) => {
          const level = levelOf(player, b);
          const option = buildOption(state, economy, slot, b, planning.plan);
          const staged = queued === b;
          return (
            <li key={b} className="flex flex-col">
              <div className="flex items-center justify-between gap-2">
                <span className="whitespace-nowrap">
                  {BUILDING_LABEL[b]} <span className="font-data">{level}</span>
                  <span className="text-wood-500">/{economy.buildings[b].length}</span>
                </span>
                {option === null ? (
                  <span className="text-xs text-wood-500">complete</span>
                ) : (
                  <Chip
                    active={staged}
                    disabled={!staged && option.blocked.length > 0}
                    title={
                      staged
                        ? "Cancel this build"
                        : option.blocked.length > 0
                          ? option.blocked.join("; ")
                          : queued !== null
                            ? `Build this instead of ${BUILDING_LABEL[queued]}: one building at a time`
                            : `Start level ${option.level} this turn`
                    }
                    onClick={() => planning.setBuild(staged ? null : b)}
                  >
                    {staged ? "Queued: cancel" : queued !== null ? "Switch to this" : "Build"}
                  </Chip>
                )}
              </div>
              {option !== null && (
                <span className="text-xs">
                  <span className="font-data text-wood-500">
                    Lv {option.level}: {stocksText(option.cost)}, {option.turns} turns
                  </span>
                  {!staged && option.blocked.length > 0 && (
                    <span className="text-invalid"> · {option.blocked.join("; ")}</span>
                  )}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {/* Always one line, so the panels below never jump when a build starts or ends. */}
      <p className="mt-2 truncate text-xs">
        {work !== null && building ? (
          <>
            Building {BUILDING_LABEL[work.building]} {work.level}, done turn <span className="font-data">{work.completes_on}</span>.
          </>
        ) : work !== null ? (
          `${BUILDING_LABEL[work.building]} ${work.level} completes as this turn resolves.`
        ) : queued !== null ? (
          `${BUILDING_LABEL[queued]} starts when the turn resolves.`
        ) : (
          <span className="text-wood-500">Nothing under construction.</span>
        )}
      </p>
    </Panel>
  );
}

const SITE_LABEL = {
  base: "Base",
  city: "City",
  farm: "Farm",
  oil_field: "Oil field",
  steel_mill: "Steel mill",
  factory: "Factory",
} as const;

/** What defends a neutral site at each garrison tier (WORLD_SPEC §7). */
const GARRISON_TEXT = [
  "",
  "militia, one infantry at 5 HP",
  "one infantry",
  "an infantry and a gun",
  "two infantry and a gun",
];

/**
 * A site's panel title: leveled sites (farm, oil field, steel mill, factory) add
 * their level; a base says whose it is, so it never reads like the buildings panel.
 */
function siteTitle(site: MapSite, owner: string | null | undefined, slot: string): string {
  if (site.kind === "base") return owner === undefined ? "Vacant base site" : owner === slot ? "Your base" : "Enemy base";
  return (SITE_TILE_KINDS as readonly string[]).includes(site.kind)
    ? `${SITE_LABEL[site.kind]} · Level ${site.level}`
    : SITE_LABEL[site.kind];
}

function HexPanel({
  map,
  state,
  economy,
  slot,
  hex,
  armySize,
  planning,
  onClose,
}: {
  map: WorldMap;
  state: WorldState;
  economy: Economy;
  slot: string;
  hex: Axial;
  armySize: number;
  planning: WorldPlanning;
  onClose: () => void;
}) {
  const cell = map.hexAt(hex.q, hex.r);
  const site = map.siteAt(hex.q, hex.r);
  const settlement = state.settlements.find((s) => s.coord.q === hex.q && s.coord.r === hex.r);
  const units = state.units.filter((u) => axialEqual(u.coord, hex));
  const garrison = units.filter((u) => u.owner === NEUTRAL_OWNER);
  const owner = settlement?.controlled_by ?? null;
  const staged = planning.plan.recruits[hexKey(hex.q, hex.r)];
  const options = recruitOptions(state, map, economy, slot, hex, planning.plan, armySize);
  const neutral = settlement !== undefined && owner === null;
  return (
    <Panel title={site !== undefined ? siteTitle(site, settlement === undefined ? undefined : owner, slot) : (cell?.terrain ?? "Hex")} onClose={onClose}>
      <div className="flex items-center gap-2">
        {site !== undefined && <img src={siteIconUrl(site.kind)} alt="" className="h-12 w-12 shrink-0" />}
        <p className="font-data text-xs text-wood-500">
          ({hex.q}, {hex.r})
          {/* A non-city site stands on plains it is painted over; naming the terrain adds nothing. */}
          {site === undefined || site.kind === "city" ? ` · ${cell?.terrain}` : ""}
          {cell?.road ? " · road" : ""}
        </p>
      </div>
      {site !== undefined && (
        <div className="mt-1 flex flex-col gap-0.5">
          {site.kind !== "base" && <span>Produces {stocksText(siteIncome(economy, site))} a turn.</span>}
          {neutral && site.garrison > 0 && (
            <span>
              Garrison: {GARRISON_TEXT[site.garrison]}.
              {garrison.length > 0 ? (
                <span className="block font-data text-xs">
                  In sight: {garrison.map((u) => `${u.type} ${u.hp} HP`).join(", ")}
                </span>
              ) : (
                <span className="block text-xs text-wood-500">Out of sight: losses from past attacks don't show.</span>
              )}
            </span>
          )}
          <span>
            {settlement === undefined
              ? "Vacant base site."
              : owner === null
                ? "Neutral."
                : owner === slot
                  ? "Held by you."
                  : `Held by ${owner}.`}
          </span>
        </div>
      )}
      {options.length > 0 && (
        <div className="mt-2 border-t border-wood-500/40 pt-2">
          <p className="mb-1 font-label text-xs uppercase tracking-wide text-wood-500">Recruit here</p>
          <ul className="flex flex-col gap-1">
            {options.map((o) => {
              const on = staged?.type === o.type;
              return (
                <li key={o.type} className="flex flex-col">
                  <span className="flex items-center justify-between gap-2">
                    <span>
                      {UNIT_LABEL[o.type]} <span className="font-data text-xs text-wood-500">{stocksText(o.cost)}</span>
                    </span>
                    <Chip
                      active={on}
                      disabled={!on && o.blocked.length > 0}
                      title={on ? "Cancel this recruit" : o.blocked.length > 0 ? o.blocked.join("; ") : `Recruit for ${stocksText(o.cost)}`}
                      onClick={() => planning.setRecruit(hex, on ? null : o.type)}
                    >
                      {on ? "Queued: cancel" : staged !== undefined ? "Switch to this" : "Recruit"}
                    </Chip>
                  </span>
                  {!on && o.blocked.length > 0 && <span className="text-xs text-invalid">{o.blocked.join("; ")}</span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {units.length > garrison.length && (
        <ul className="mt-2 flex flex-col gap-0.5">
          {units
            .filter((u) => u.owner !== NEUTRAL_OWNER)
            .map((u) => (
              <li key={u.id} className="flex items-center justify-between gap-2">
                {u.owner === slot ? (
                  <button type="button" className="text-left underline-offset-2 hover:underline" onClick={() => planning.selectUnit(u.id)}>
                    Your {u.type}
                    {u.posture === "column" ? " (column)" : ""}
                  </button>
                ) : (
                  <span>
                    {u.owner} {u.type}
                    {u.posture === "column" ? " (column)" : ""}
                  </span>
                )}
                <HpPips hp={u.hp} />
              </li>
            ))}
        </ul>
      )}
    </Panel>
  );
}

// --- submit -------------------------------------------------------------------------

function PlanBar({
  view,
  economy,
  armySize,
  planning,
}: {
  view: WorldView;
  economy: Economy;
  armySize: number;
  planning: WorldPlanning;
}) {
  const { plan } = planning;
  const player = view.state.players.find((p) => p.id === view.your_slot);
  if (player === undefined) return null;
  const cost = planCost(plan, view.state, economy, view.your_slot);
  const left = sub(stocksOf(player), cost);
  const problems = planProblems(plan, view.state, economy, view.your_slot, armySize);
  const orders = Object.keys(plan.orders).length;
  const recruits = Object.keys(plan.recruits).length;
  const status = planning.dirty
    ? "Unsent changes"
    : planning.submitted
      ? "Submitted: editable until the turn resolves"
      : "Nothing submitted yet";
  return (
    <div className="pointer-events-none absolute bottom-3 left-1/2 flex max-w-[calc(100%-24px)] -translate-x-1/2 flex-col items-center gap-2">
      {planning.droppedTurn !== null && (
        <div className="pointer-events-auto rounded-sm border border-brass-600 bg-parchment-200 px-3 py-1.5 text-xs text-ink-900 shadow">
          Turn {planning.droppedTurn} resolved before your last changes were sent; they were dropped.
        </div>
      )}
      {planning.restored && (
        <div className="pointer-events-auto rounded-sm border border-brass-600 bg-parchment-200 px-3 py-1.5 text-xs text-ink-900 shadow">
          Unsent changes restored from before the page reloaded. Submit to send them, or revert.
        </div>
      )}
      {(problems.length > 0 || planning.submitError !== null) && (
        <div className="pointer-events-auto rounded-sm border border-invalid bg-parchment-200 px-3 py-1.5 text-xs text-invalid shadow">
          {[...problems, ...(planning.submitError !== null ? [planning.submitError] : [])].map((p) => (
            <p key={p}>{p}</p>
          ))}
        </div>
      )}
      <div className="fhq-wood pointer-events-auto flex items-center gap-4 whitespace-nowrap rounded-sm border-2 border-brass-600/70 px-4 py-2 text-sm shadow-lg">
        <span className="font-data text-xs text-ivory-400">
          {orders} order{orders === 1 ? "" : "s"} · {recruits} recruit{recruits === 1 ? "" : "s"}
          {plan.build !== null ? ` · ${BUILDING_LABEL[plan.build]}` : ""}
          {plan.disband.length > 0 ? ` · ${plan.disband.length} on disband list` : ""}
        </span>
        {(cost.manpower > 0 || cost.fuel > 0 || cost.steel > 0) && (
          <span className="text-xs" title={`Spends ${stocksText(cost)}; leaves ${stocksLeft(left)}`}>
            Spends {stocksText(cost)}
            <span className="hidden text-ivory-400 min-[1100px]:inline">, leaves {stocksLeft(left)}</span>
          </span>
        )}
        <span className={`text-xs ${planning.dirty ? "text-brass-400" : "text-ivory-400"}`}>{status}</span>
        {planning.dirty && (planning.submitted || planning.restored) && (
          <button type="button" onClick={planning.revert} className="font-label text-xs uppercase text-ivory-400 hover:text-ivory-100">
            Revert
          </button>
        )}
        <button
          type="button"
          onClick={() => void planning.submit()}
          disabled={planning.submitting || problems.length > 0 || (!planning.dirty && planning.submitted)}
          className="rounded-sm border border-brass-600 bg-brass-600 px-3 py-1 font-label text-xs font-semibold uppercase tracking-wider text-wood-900 transition-colors hover:bg-brass-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {planning.submitting ? "Sending…" : planning.submitted ? "Resubmit" : "Submit plan"}
        </button>
      </div>
    </div>
  );
}

/** "5 manpower, 20 fuel, 2 steel": what the stocks hold after the plan's spending. */
function stocksLeft(s: Stocks): string {
  return RESOURCES.map((r) => `${s[r]} ${r}`).join(", ");
}
