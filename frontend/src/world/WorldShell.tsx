// The world screen (WORLD_SPEC §11, W6): header, board, and planning panels.
//
// Slice 1 was the read side (map, units, fog, clock, economy). Slice 2 adds
// planning: select a unit and click a destination to travel (the route is
// previewed), or give it a move, barrage, rest, hold or deploy order; recruit at
// held sites; start a building; pick who disbands first in a shortage; and
// submit, revising as often as you like until the turn resolves.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import { hexKey, type Axial } from "@/board/hex";
import { AccountMenu } from "@/components/AccountMenu";
import { Centered, ErrorBanner, Loading } from "@/components/ui";
import type { components } from "@/lib/api/schema";

import { NEUTRAL_OWNER } from "./colors";
import {
  economyOf,
  forecast,
  levelOf,
  RESOURCES,
  stocksOf,
  sub,
  type Building,
  type Economy,
  type Forecast,
  type Stocks,
} from "./economy";
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
import { siteIconUrl } from "./siteArt";
import { turnReport, turnTrails, unitBook, type ReportItem, type TurnTrails } from "./turnReport";
import { useTurnReport, type ReportLoad } from "./useTurnReport";
import { useWorld, type WorldView } from "./useWorld";
import { useWorldPlan, type PlannedRoute, type WorldPlanning } from "./useWorldPlan";
import { WorldBoard } from "./WorldBoard";
import type { WorldOverlay } from "./WorldRenderer";
import type { WorldMap } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];
type Unit = components["schemas"]["Unit"];

export function WorldShell() {
  const { id } = useParams<{ id: string }>();
  if (id === undefined) throw new Error("WorldShell needs a world id route param");
  const load = useWorld(id);
  if (load.status === "loading") return <Loading fill label="Loading the world…" />;
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
  const economy = useMemo(() => economyOf(view.state), [view.state]);
  const outlook = useMemo(() => forecast(view.state, map, view.your_slot), [view, map]);
  const base = useMemo(() => baseOf(view.state, map, view.your_slot), [view, map]);
  const [panTo, setPanTo] = useState<{ hex: Axial; seq: number } | null>(null);
  const jump = useCallback((hex: Axial) => setPanTo((p) => ({ hex, seq: (p?.seq ?? 0) + 1 })), []);
  const report = useTurnReport(view.game_id, view.turn > 0 ? view.turn - 1 : null);
  const digest = useMemo(() => {
    if (report.status !== "ready") return null;
    const { events, state_after } = report.replay;
    const book = unitBook(events, state_after.units ?? [], view.your_slot);
    return { items: turnReport(events, view.your_slot, book), trails: turnTrails(events, book) };
  }, [report, view.your_slot]);
  // The report opens with each new turn; closing it also hides the trails.
  const [reportClosedFor, setReportClosedFor] = useState<number | null>(null);
  const reportOpen = reportClosedFor !== view.turn;
  const trails = reportOpen ? (digest?.trails ?? null) : null;
  const overlay = useMemo(() => overlayOf(planning, view.state.units, trails), [planning, view.state.units, trails]);

  const { selectUnit } = planning;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") selectUnit(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectUnit]);

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
        />
        <div className="pointer-events-none absolute left-3 top-3 flex max-h-[calc(100%-260px)] w-72 flex-col gap-3">
          <ArmyPanel view={view} planning={planning} onJump={jump} />
          <ReportPanel
            load={report}
            items={digest?.items ?? null}
            open={reportOpen}
            onToggle={() => setReportClosedFor(reportOpen ? view.turn : null)}
            onJump={jump}
          />
        </div>
        <div className="pointer-events-none absolute right-3 top-3 flex max-h-[calc(100%-24px)] w-80 flex-col gap-3 overflow-y-auto">
          <BasePanel state={view.state} economy={economy} slot={view.your_slot} planning={planning} />
          {planning.selectedUnit !== null ? (
            <UnitPanel unit={planning.selectedUnit} planning={planning} />
          ) : (
            planning.selectedHex !== null && (
              <HexPanel
                map={map}
                state={view.state}
                economy={economy}
                slot={view.your_slot}
                hex={planning.selectedHex}
                armySize={outlook.armySize}
                planning={planning}
                onClose={() => planning.selectUnit(null)}
              />
            )
          )}
        </div>
        <PlanBar view={view} economy={economy} armySize={outlook.armySize} planning={planning} />
      </div>
    </div>
  );
}

function baseOf(state: WorldState, map: WorldMap, slot: string): Axial | null {
  const held = state.settlements.find(
    (s) => s.controlled_by === slot && map.siteAt(s.coord.q, s.coord.r)?.kind === "base",
  );
  return held?.coord ?? state.units.find((u) => u.owner === slot)?.coord ?? null;
}

function overlayOf(planning: WorldPlanning, units: readonly Unit[], trails: TurnTrails | null): WorldOverlay {
  const selectedId = planning.selectedUnit?.id ?? null;
  const barrages = units.flatMap((u) => {
    const order = planning.plan.orders[u.id];
    return order?.kind === "barrage" ? [{ from: u.coord, target: order.target }] : [];
  });
  return {
    selectedUnit: planning.selectedUnit?.coord ?? null,
    selectedHex: planning.selectedUnit === null ? planning.selectedHex : null,
    routes: planning.routes.map((r) => ({ from: r.from, path: r.path, kind: r.kind, selected: r.unitId === selectedId })),
    barrages,
    reach: [...planning.moveReach.values()].map((path) => path[path.length - 1]),
    recruits: Object.values(planning.plan.recruits).map((r) => r.coord),
    lastTurn: trails,
  };
}

// --- header -----------------------------------------------------------------------

const RESOURCE_LABEL = { manpower: "Manpower", fuel: "Fuel", steel: "Steel" } as const;

function WorldHeader({ view, plan }: { view: WorldView; plan: Forecast }) {
  const player = view.state.players.find((p) => p.id === view.your_slot);
  const stocks = player === undefined ? null : stocksOf(player);
  return (
    <header className="fhq-wood flex h-12 shrink-0 items-center gap-5 border-b-2 border-brass-600/70 px-4 text-sm">
      <Link to="/" className="font-display text-base uppercase tracking-wide text-ivory-100">
        hex-general
      </Link>
      <Stat label="Turn">
        <span className="font-data">{view.turn}</span>
      </Stat>
      <Countdown clock={view.clock} nextTickAt={view.next_tick_at ?? null} />
      {stocks !== null &&
        RESOURCES.map((r) => (
          <Stat key={r} label={RESOURCE_LABEL[r]}>
            <span className={`font-data ${plan.short.includes(r as "fuel" | "manpower") ? "text-invalid" : ""}`}>
              {stocks[r]}
              <span className="text-ivory-400">/{plan.storage}</span>
            </span>{" "}
            <span className={plan.net[r] >= 0 ? "text-valid" : "text-invalid"}>
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
        <span className="font-label text-xs font-semibold uppercase tracking-wider text-invalid">
          Out of {plan.short.join(" and ")} next turn: a unit will disband
        </span>
      )}
      <div className="ml-auto">
        <AccountMenu />
      </div>
    </header>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="font-label text-[11px] uppercase tracking-wider text-ivory-400">{label}</span>
      {children}
    </span>
  );
}

function Countdown({ clock, nextTickAt }: { clock: string; nextTickAt: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 15_000);
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
      <span className="text-ivory-400">({when})</span>
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
  className = "",
}: {
  title: string;
  children: React.ReactNode;
  onClose?: () => void;
  className?: string;
}) {
  return (
    <section
      className={`pointer-events-auto rounded-sm border border-wood-700 bg-parchment-200 p-3 text-sm text-ink-900 shadow-lg ${className}`}
    >
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-display text-sm uppercase tracking-wide">{title}</h2>
        {onClose !== undefined && (
          <button type="button" onClick={onClose} className="font-label text-xs uppercase text-wood-500 hover:text-ink-900">
            Close
          </button>
        )}
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
      className={`rounded-sm border px-2 py-0.5 font-label text-xs uppercase tracking-wide transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
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

function orderText(order: WorldOrder | undefined, route: PlannedRoute | undefined): string {
  if (order === undefined) {
    return route?.kind === "standing" ? `travelling, ${etaText(route.eta)}` : "no orders (holds)";
  }
  switch (order.kind) {
    case "travel":
      return `travel, ${etaText(route?.eta ?? null)}`;
    case "move":
      return `move ${order.path.length} hex${order.path.length === 1 ? "" : "es"}`;
    case "march":
      return "march";
    case "barrage":
      return "barrage";
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

function ArmyPanel({
  view,
  planning,
  onJump,
}: {
  view: WorldView;
  planning: WorldPlanning;
  onJump: (hex: Axial) => void;
}) {
  const units = view.state.units.filter((u) => u.owner === view.your_slot).sort((a, b) => a.id - b.id);
  return (
    <Panel title={`Army (${units.length})`} className="flex min-h-0 flex-col">
      {units.length === 0 ? (
        <p className="text-xs text-wood-500">No units in the field.</p>
      ) : (
        <ul className="-mx-1 flex min-h-0 flex-col overflow-y-auto">
          {units.map((u) => {
            const route = planning.routes.find((r) => r.unitId === u.id);
            const selected = planning.selectedUnit?.id === u.id;
            return (
              <li key={u.id}>
                <button
                  type="button"
                  onClick={() => {
                    planning.selectUnit(u.id);
                    onJump(u.coord);
                  }}
                  className={`flex w-full items-baseline justify-between gap-2 rounded-sm px-1 py-0.5 text-left ${
                    selected ? "bg-wood-700 text-parchment-200" : "hover:bg-parchment-400/40"
                  }`}
                >
                  <span>
                    {UNIT_LABEL[u.type]}
                    {u.posture === "column" ? " ▭" : ""} <span className="font-data text-xs opacity-70">{u.hp}hp</span>
                  </span>
                  <span className="truncate text-xs opacity-80">{orderText(planning.plan.orders[u.id], route)}</span>
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
const TONE_CLASS = { good: "text-valid", bad: "text-invalid", neutral: "" } as const;

function ReportPanel({
  load,
  items,
  open,
  onToggle,
  onJump,
}: {
  load: ReportLoad;
  items: readonly ReportItem[] | null;
  open: boolean;
  onToggle: () => void;
  onJump: (hex: Axial) => void;
}) {
  if (load.status === "none") return null;
  const title = `Turn ${load.turn} report`;
  if (!open) {
    return (
      <button
        type="button"
        onClick={onToggle}
        className="pointer-events-auto self-start rounded-sm border border-wood-700 bg-parchment-200 px-3 py-1 font-label text-xs uppercase tracking-wide text-ink-900 shadow"
      >
        Show {title.toLowerCase()}
      </button>
    );
  }
  return (
    <Panel title={title} onClose={onToggle} className="flex min-h-0 flex-col">
      {load.status === "loading" && <p className="text-xs text-wood-500">Loading…</p>}
      {load.status === "error" && <p className="text-xs text-invalid">{load.message}</p>}
      {items !== null && (
        <div className="-mx-1 flex min-h-0 flex-col gap-2 overflow-y-auto px-1">
          {items.length === 0 && <p className="text-xs text-wood-500">A quiet turn.</p>}
          {(["fighting", "base", "movement"] as const).map((section) => {
            const list = items.filter((i) => i.section === section);
            return list.length === 0 ? null : (
              <div key={section}>
                <p className="font-label text-[11px] uppercase tracking-wider text-wood-500">{SECTION_LABEL[section]}</p>
                <ul className="flex flex-col">
                  {list.map((item, k) => (
                    <ReportLine key={k} item={item} onJump={onJump} />
                  ))}
                </ul>
              </div>
            );
          })}
          {items.some((i) => i.section === "seen") && (
            <details>
              <summary className="cursor-pointer font-label text-[11px] uppercase tracking-wider text-wood-500">
                {SECTION_LABEL.seen} ({items.filter((i) => i.section === "seen").length})
              </summary>
              <ul className="flex flex-col">
                {items
                  .filter((i) => i.section === "seen")
                  .map((item, k) => (
                    <ReportLine key={k} item={item} onJump={onJump} />
                  ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Panel>
  );
}

function ReportLine({ item, onJump }: { item: ReportItem; onJump: (hex: Axial) => void }) {
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
  const at = item.at;
  return (
    <li className="text-xs leading-snug">
      {at === null ? (
        <span className="block px-1 py-0.5">{body}</span>
      ) : (
        <button
          type="button"
          onClick={() => onJump(at)}
          className="block w-full rounded-sm px-1 py-0.5 text-left hover:bg-parchment-400/40"
        >
          {body}
        </button>
      )}
    </li>
  );
}

// --- the selected unit ----------------------------------------------------------------

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

function UnitPanel({ unit, planning }: { unit: Unit; planning: WorldPlanning }) {
  const order = planning.plan.orders[unit.id];
  const route = planning.routes.find((r) => r.unitId === unit.id);
  const kinds = orderKindsFor(unit).filter((k) => k !== "march");
  const disbandRank = planning.plan.disband.indexOf(unit.id);
  const active: OrderKind | null =
    planning.mode === "move" || planning.mode === "barrage" ? planning.mode : (order?.kind ?? null);

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
      <p className="font-data text-xs text-wood-500">
        {unit.hp}/10 HP · {unit.posture === "column" ? "packed column" : "deployed"}
        {unit.disordered ? " · disordered" : ""}
      </p>
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
            : `Ordered: ${orderText(order, route)}.`}
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
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-wood-500/40 pt-2 text-xs">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={disbandRank >= 0} onChange={() => planning.toggleDisband(unit.id)} />
          {disbandRank >= 0 ? `Disband first in a shortage (#${disbandRank + 1})` : "Disband first in a shortage"}
        </label>
        {order !== undefined && (
          <Chip onClick={() => planning.setOrder(unit.id, null)} title="Drop this turn's order">
            Clear
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
  return (
    <Panel title="Base">
      <ul className="flex flex-col gap-1.5">
        {(Object.keys(BUILDING_LABEL) as Building[]).map((b) => {
          const level = levelOf(player, b);
          const option = buildOption(state, economy, slot, b);
          const staged = planning.plan.build === b;
          return (
            <li key={b} className="flex items-center justify-between gap-2">
              <span>
                {BUILDING_LABEL[b]} <span className="font-data">{level}</span>
                <span className="text-wood-500">/{economy.buildings[b].length}</span>
              </span>
              {option === null ? (
                <span className="text-xs text-wood-500">complete</span>
              ) : (
                <span className="flex items-center gap-2">
                  <span className="font-data text-xs text-wood-500">
                    {stocksText(option.cost)}, {option.turns}t
                  </span>
                  <Chip
                    active={staged}
                    disabled={!staged && option.blocked !== null}
                    title={option.blocked ?? `Start level ${option.level} this turn`}
                    onClick={() => planning.setBuild(staged ? null : b)}
                  >
                    {staged ? "Queued" : "Build"}
                  </Chip>
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {work !== null &&
        (building ? (
          <p className="mt-2 text-xs">
            Building {BUILDING_LABEL[work.building]} {work.level}, done turn{" "}
            <span className="font-data">{work.completes_on}</span>.
          </p>
        ) : (
          <p className="mt-2 text-xs">
            {BUILDING_LABEL[work.building]} {work.level} completes as this turn resolves; you can queue the next
            build now.
          </p>
        ))}
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

const TIER_LABEL = ["", "Militia", "Garrison", "Strongpoint", "Fortress"];

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
  const units = state.units.filter((u) => u.coord.q === hex.q && u.coord.r === hex.r);
  const owner = settlement?.controlled_by ?? null;
  const staged = planning.plan.recruits[hexKey(hex.q, hex.r)];
  const fielded = state.units.filter((u) => u.owner === slot).length + Object.keys(planning.plan.recruits).length;
  const full = staged === undefined && fielded >= armySize;
  const options = recruitOptions(state, map, economy, slot, hex).map((o) =>
    full && o.blocked === null ? { ...o, blocked: `army is at its size of ${armySize}` } : o,
  );
  const reasons = [...new Set(options.flatMap((o) => (o.blocked === null || staged?.type === o.type ? [] : [o.blocked])))];
  return (
    <Panel title={site !== undefined ? SITE_LABEL[site.kind] : (cell?.terrain ?? "Hex")} onClose={onClose}>
      <div className="flex items-center gap-2">
        {site !== undefined && <img src={siteIconUrl(site.kind)} alt="" className="h-12 w-12 shrink-0" />}
        <p className="font-data text-xs text-wood-500">
          ({hex.q}, {hex.r}) · {cell?.terrain}
          {cell?.road ? " · road" : ""}
        </p>
      </div>
      {site !== undefined && (
        <div className="mt-1 flex flex-col gap-0.5">
          {site.kind !== "base" && <span>Produces {stocksText(economy.siteIncome[site.kind])} a turn.</span>}
          {site.garrison > 0 && (
            <span>
              Garrison tier {site.garrison}: {TIER_LABEL[site.garrison]}
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
          <div className="flex flex-wrap gap-1">
            {options.map((o) => {
              const on = staged?.type === o.type;
              return (
                <Chip
                  key={o.type}
                  active={on}
                  disabled={!on && o.blocked !== null}
                  title={o.blocked ?? stocksText(o.cost)}
                  onClick={() => planning.setRecruit(hex, on ? null : o.type)}
                >
                  {UNIT_LABEL[o.type]} · {stocksText(o.cost)}
                </Chip>
              );
            })}
          </div>
          {reasons.length > 0 && <p className="mt-1 text-xs text-wood-500">Can't: {reasons.join("; ")}.</p>}
        </div>
      )}
      {units.length > 0 && (
        <ul className="mt-2 flex flex-col gap-0.5">
          {units.map((u) => (
            <li key={u.id} className="flex justify-between">
              {u.owner === slot ? (
                <button type="button" className="text-left underline-offset-2 hover:underline" onClick={() => planning.selectUnit(u.id)}>
                  Your {u.type}
                  {u.posture === "column" ? " (column)" : ""}
                </button>
              ) : (
                <span>
                  {u.owner === NEUTRAL_OWNER ? "Garrison" : u.owner} {u.type}
                  {u.posture === "column" ? " (column)" : ""}
                </span>
              )}
              <span className="font-data">{u.hp} HP</span>
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
      ? "Plan submitted: you can still change it until the turn resolves"
      : "Nothing submitted this turn";
  return (
    <div className="pointer-events-none absolute bottom-3 left-1/2 flex -translate-x-1/2 flex-col items-center gap-2">
      {planning.droppedTurn !== null && (
        <div className="pointer-events-auto rounded-sm border border-brass-600 bg-parchment-200 px-3 py-1.5 text-xs text-ink-900 shadow">
          Turn {planning.droppedTurn} resolved before your last changes were sent; they were dropped.
        </div>
      )}
      {(problems.length > 0 || planning.submitError !== null) && (
        <div className="pointer-events-auto rounded-sm border border-invalid bg-parchment-200 px-3 py-1.5 text-xs text-invalid shadow">
          {[...problems, ...(planning.submitError !== null ? [planning.submitError] : [])].map((p) => (
            <p key={p}>{p}</p>
          ))}
        </div>
      )}
      <div className="fhq-wood pointer-events-auto flex items-center gap-4 rounded-sm border-2 border-brass-600/70 px-4 py-2 text-sm shadow-lg">
        <span className="font-data text-xs text-ivory-400">
          {orders} order{orders === 1 ? "" : "s"} · {recruits} recruit{recruits === 1 ? "" : "s"}
          {plan.build !== null ? ` · ${BUILDING_LABEL[plan.build]}` : ""}
          {plan.disband.length > 0 ? ` · ${plan.disband.length} on disband list` : ""}
        </span>
        {(cost.manpower > 0 || cost.fuel > 0 || cost.steel > 0) && (
          <span className="text-xs">
            Spends {stocksText(cost)}{" "}
            <span className="text-ivory-400">
              (leaves {left.manpower}/{left.fuel}/{left.steel})
            </span>
          </span>
        )}
        <span className={`text-xs ${planning.dirty ? "text-brass-400" : "text-ivory-400"}`}>{status}</span>
        {planning.dirty && planning.submitted && (
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
